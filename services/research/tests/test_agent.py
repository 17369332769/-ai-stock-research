"""Agent 工具、固定 Schema、重试与降级路径（spec §11 / §14.3）。

假 client 只用来驱动执行路径；**没有任何断言把假模型的话当成真实分析结论**。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import (
    CSI300_BENCHMARK_SYMBOL,
    NO_VERIFIABLE_CAUSE_TEXT,
    AnalysisType,
    Direction,
    EventHorizon,
)
from apps.api.app.core.settings import Settings
from services.research.agents.analyst import analyze_anomaly, analyze_document
from services.research.agents.client import AgentUnavailable, build_chat_client
from services.research.agents.repository import ModelPredictionSnapshot, QuoteSnapshot
from services.research.agents.runner import DegradeReason, run_agent
from services.research.agents.schema import (
    AGENT_OUTPUT_JSON_SCHEMA,
    AgentSchemaError,
    parse_agent_output,
    template_output,
)
from services.research.agents.tools import (
    TOOL_NAMES,
    AgentToolbox,
    ToolArgumentError,
    UnknownToolError,
)
from services.research.anomaly import AnomalyEvent, AnomalyRule, AnomalySignal
from services.research.tests.conftest import (
    AS_OF,
    SYMBOL,
    FakeRepository,
    FakeSession,
    ScriptedChatClient,
    make_document,
    make_document_snapshot,
    text_completion,
    tool_completion,
)


def _valid_payload(evidence_ids: list[str], direction: str = "positive") -> str:
    import json

    return json.dumps(
        {
            "summary": "公司签署重大合同，短期偏正面。",
            "direction": direction if evidence_ids else "unknown",
            "horizon": "short",
            "confidence": 0.6,
            "evidence_ids": evidence_ids,
            "unknowns": [],
            "risk_flags": [],
        },
        ensure_ascii=False,
    )


def _toolbox(repo: FakeRepository | None = None) -> AgentToolbox:
    return AgentToolbox(repo or FakeRepository(), symbol=SYMBOL, as_of=AS_OF)


def _anomaly_event() -> AnomalyEvent:
    signal = AnomalySignal(
        rule=AnomalyRule.INTRADAY_RETURN_SPIKE,
        label="5分钟收益异常",
        observed=0.05,
        threshold=0.01,
        fact="5分钟收益异常：10:05 这 5 分钟收益 +5.00%，超过阈值 1.00%",
    )
    return AnomalyEvent(
        symbol=SYMBOL, as_of=AS_OF, trading_day=AS_OF.date(), signals=(signal,)
    )


# ── 工具白名单：只有 5 个 ───────────────────────────────────────────────────


def test_exactly_five_tools() -> None:
    assert len(TOOL_NAMES) == 5
    assert {
        "get_quote_snapshot",
        "get_recent_bars",
        "get_documents",
        "get_benchmark_snapshot",
        "get_model_prediction",
    } == TOOL_NAMES
    specs = _toolbox().specs
    assert len(specs) == 5
    assert {spec["function"]["name"] for spec in specs} == set(TOOL_NAMES)


async def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(UnknownToolError):
        await _toolbox().call("read_local_file", {"path": "/etc/passwd"})


async def test_tools_never_expose_as_of_parameter() -> None:
    """as_of 由服务端绑定：工具参数表里根本没有它，模型无法移动 PIT 截止时间。"""
    for spec in _toolbox().specs:
        assert "as_of" not in spec["function"]["parameters"]["properties"]


async def test_tool_rejects_other_symbol() -> None:
    with pytest.raises(ToolArgumentError):
        await _toolbox().call("get_quote_snapshot", {"symbol": "000001"})


# ── PIT：只返回 as_of 之前可见的数据 ────────────────────────────────────────


async def test_get_documents_window_is_clamped_to_as_of() -> None:
    repo = FakeRepository()
    visible = make_document_snapshot(make_document(published_at=AS_OF - timedelta(hours=1)))
    future = make_document_snapshot(make_document(published_at=AS_OF + timedelta(hours=1)))
    repo.documents = [visible, future]

    toolbox = _toolbox(repo)
    result = await toolbox.call(
        "get_documents",
        {
            "symbol": SYMBOL,
            "start": (AS_OF - timedelta(days=1)).isoformat(),
            "end": (AS_OF + timedelta(days=30)).isoformat(),  # 模型想看未来
        },
    )
    assert result["end"] == AS_OF.isoformat()  # 窗口被夹回 as_of
    ids = [doc["document_id"] for doc in result["documents"]]
    assert str(visible.id) in ids
    assert str(future.id) not in ids
    assert toolbox.served_document_ids == frozenset({visible.id})


async def test_get_quote_snapshot_returns_latest_visible() -> None:
    repo = FakeRepository()
    repo.quotes[SYMBOL] = [
        QuoteSnapshot(
            symbol=SYMBOL,
            price=Decimal("100"),
            previous_close=Decimal("100"),
            open=None,
            high=None,
            low=None,
            volume=None,
            amount=None,
            volume_ratio=None,
            source="akshare",
            source_url=None,
            observed_at=AS_OF - timedelta(minutes=1),
        ),
        QuoteSnapshot(
            symbol=SYMBOL,
            price=Decimal("999"),
            previous_close=Decimal("100"),
            open=None,
            high=None,
            low=None,
            volume=None,
            amount=None,
            volume_ratio=None,
            source="akshare",
            source_url=None,
            observed_at=AS_OF + timedelta(minutes=1),  # 未来快照
        ),
    ]
    result = await _toolbox(repo).call("get_quote_snapshot", {"symbol": SYMBOL})
    assert result["quote"]["price"] == 100.0  # 未来的 999 不可见


async def test_benchmark_tool_is_bound_to_csi300() -> None:
    toolbox = _toolbox()
    result = await toolbox.call("get_benchmark_snapshot", {"symbol": CSI300_BENCHMARK_SYMBOL})
    assert result["symbol"] == CSI300_BENCHMARK_SYMBOL
    with pytest.raises(ToolArgumentError):
        await toolbox.call("get_benchmark_snapshot", {"symbol": SYMBOL})


async def test_model_prediction_is_passed_through_unchanged() -> None:
    """Agent 不得修改量化模型概率 —— 工具原样返回，并在返回体里写明禁止修改。"""
    repo = FakeRepository()
    repo.predictions[(SYMBOL, "next_5d")] = [
        ModelPredictionSnapshot(
            symbol=SYMBOL,
            horizon="next_5d",
            as_of=AS_OF - timedelta(minutes=5),
            reference_price=Decimal("100"),
            probability_up=Decimal("0.3800"),
            expected_return=Decimal("-0.011"),
            lower_return=Decimal("-0.041"),
            upper_return=Decimal("0.019"),
            confidence_label="low",
            data_cutoff=AS_OF - timedelta(minutes=5),
            model_key="a_share_5d_lightgbm",
            model_version="2026.07.14.1",
        )
    ]
    result = await _toolbox(repo).call("get_model_prediction", {"symbol": SYMBOL, "horizon": "next_5d"})
    assert result["prediction"]["probability_up"] == pytest.approx(0.38)
    assert "禁止修改" in result["prediction"]["note"]


async def test_invalid_timeframe_and_horizon_are_rejected() -> None:
    toolbox = _toolbox()
    with pytest.raises(ToolArgumentError):
        await toolbox.call("get_recent_bars", {"symbol": SYMBOL, "timeframe": "1m", "limit": 5})
    with pytest.raises(ToolArgumentError):
        await toolbox.call("get_model_prediction", {"symbol": SYMBOL, "horizon": "next_30d"})


# ── 固定输出 Schema ─────────────────────────────────────────────────────────


def test_json_schema_is_closed_and_complete() -> None:
    assert AGENT_OUTPUT_JSON_SCHEMA["additionalProperties"] is False
    assert set(AGENT_OUTPUT_JSON_SCHEMA["required"]) == {
        "summary",
        "direction",
        "horizon",
        "confidence",
        "evidence_ids",
        "unknowns",
        "risk_flags",
    }


def test_parse_accepts_fenced_json() -> None:
    output = parse_agent_output("```json\n" + _valid_payload([], "unknown") + "\n```")
    assert output.direction is Direction.UNKNOWN


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "我认为这是利好。",  # 没有 JSON
        '{"summary":"x"}',  # 缺字段
        '{"summary":"x","direction":"up","horizon":"short","confidence":0.5,'
        '"evidence_ids":[],"unknowns":[],"risk_flags":[]}',  # direction 取值非法
        '{"summary":"x","direction":"unknown","horizon":"unknown","confidence":1.5,'
        '"evidence_ids":[],"unknowns":[],"risk_flags":[]}',  # confidence 越界
    ],
)
def test_invalid_payloads_raise_schema_error(payload: str) -> None:
    with pytest.raises(AgentSchemaError):
        parse_agent_output(payload)


def test_template_output_is_always_unknown_with_fixed_text() -> None:
    output = template_output(summary_prefix="当日涨幅 5%。")
    assert output.direction is Direction.UNKNOWN
    assert output.horizon is EventHorizon.UNKNOWN
    assert output.confidence == 0.0
    assert output.evidence_ids == []
    assert NO_VERIFIABLE_CAUSE_TEXT in output.summary
    assert "当日涨幅 5%。" in output.summary


# ── Schema 失败最多重试一次 ─────────────────────────────────────────────────


async def test_schema_failure_retries_exactly_once_then_succeeds() -> None:
    document = make_document()
    repo = FakeRepository()
    repo.documents = [make_document_snapshot(document)]
    toolbox = _toolbox(repo)
    await toolbox.call("get_documents", {"symbol": SYMBOL})  # 让文档进入"已检索"集合

    client = ScriptedChatClient(
        [
            text_completion("这是一段不符合 Schema 的自由发挥"),
            text_completion(_valid_payload([str(document.id)])),
        ]
    )
    result = await run_agent(client=client, toolbox=toolbox, task_prompt="分析")
    assert not result.degraded
    assert result.model_name == "test-model"
    assert len(client.calls) == 2  # 恰好重试一次


async def test_two_schema_failures_fall_back_to_template() -> None:
    client = ScriptedChatClient(
        [text_completion("胡说八道"), text_completion("还是胡说八道")]
    )
    result = await run_agent(
        client=client, toolbox=_toolbox(), task_prompt="分析", summary_prefix="当日涨幅 5%。"
    )
    assert result.degraded
    assert result.degrade_reason == DegradeReason.SCHEMA_INVALID
    assert result.output.direction is Direction.UNKNOWN
    assert NO_VERIFIABLE_CAUSE_TEXT in result.output.summary
    assert result.model_provider is None and result.model_name is None
    assert len(client.calls) == 2  # 不会重试第二次


# ── 降级路径 ────────────────────────────────────────────────────────────────


def test_build_chat_client_returns_none_when_agent_disabled() -> None:
    settings = Settings(agent_base_url="", agent_model="", agent_api_key="")
    assert settings.agent_enabled is False
    assert build_chat_client(settings) is None


async def test_agent_disabled_degrades_to_template() -> None:
    result = await run_agent(client=None, toolbox=_toolbox(), task_prompt="分析")
    assert result.degraded
    assert result.degrade_reason == DegradeReason.AGENT_DISABLED
    assert result.output.direction is Direction.UNKNOWN
    assert NO_VERIFIABLE_CAUSE_TEXT in result.output.summary
    assert result.model_provider is None
    assert result.model_name is None


async def test_agent_unavailable_degrades_without_raising() -> None:
    client = ScriptedChatClient([AgentUnavailable("连接超时")])
    result = await run_agent(client=client, toolbox=_toolbox(), task_prompt="分析")
    assert result.degraded
    assert result.degrade_reason == DegradeReason.AGENT_UNAVAILABLE


async def test_tool_budget_exhaustion_degrades() -> None:
    client = ScriptedChatClient([tool_completion("get_quote_snapshot", {"symbol": SYMBOL})] * 3)
    result = await run_agent(
        client=client, toolbox=_toolbox(), task_prompt="分析", max_tool_rounds=3
    )
    assert result.degraded
    assert result.degrade_reason == DegradeReason.TOOL_BUDGET_EXHAUSTED


async def test_disabled_agent_document_analysis_leaves_model_fields_empty() -> None:
    """spec 要求 6：Agent 未配置 → unknown + 固定文案 + model_provider/model_name 留空。"""
    document = make_document()
    session = cast(AsyncSession, FakeSession([document]))
    draft = await analyze_document(
        session,
        repository=FakeRepository(),
        client=None,
        symbol=SYMBOL,
        document=make_document_snapshot(document),
        as_of=AS_OF,
    )
    assert draft.direction is Direction.UNKNOWN
    assert draft.horizon is EventHorizon.UNKNOWN
    assert draft.evidence == ()
    assert NO_VERIFIABLE_CAUSE_TEXT in draft.summary
    assert draft.model_provider is None
    assert draft.model_name is None
    assert draft.degraded and draft.degrade_reason == DegradeReason.AGENT_DISABLED

    row = draft.to_orm()
    assert row.analysis_type == AnalysisType.DOCUMENT.value
    assert row.evidence == []
    assert row.model_provider is None
    assert row.data_cutoff == AS_OF


async def test_disabled_agent_anomaly_keeps_deterministic_facts_first() -> None:
    """异动分析即便没有 Agent，也必须先给确定性量价事实，再写固定文案（spec §12）。"""
    event = _anomaly_event()
    session = cast(AsyncSession, FakeSession([]))
    draft = await analyze_anomaly(
        session, repository=FakeRepository(), client=None, event=event, as_of=AS_OF
    )
    assert draft.analysis_type is AnalysisType.ANOMALY
    assert draft.summary.startswith(event.facts_block.split("\n")[0])
    assert event.signals[0].fact in draft.summary
    assert NO_VERIFIABLE_CAUSE_TEXT in draft.summary
    assert draft.direction is Direction.UNKNOWN
    assert draft.evidence == ()
    assert draft.model_provider is None


# ── 证据反幻觉：引用未检索过 / 不存在的文档 → 整条降级 ──────────────────────


async def test_citing_never_retrieved_document_degrades_whole_analysis() -> None:
    document = make_document()
    repo = FakeRepository()
    repo.documents = [make_document_snapshot(document)]
    session = cast(AsyncSession, FakeSession([document]))

    # 模型不检索就直接引用（哪怕这个 ID 真的存在）
    client = ScriptedChatClient([text_completion(_valid_payload([str(document.id)]))])
    draft = await analyze_document(
        session,
        repository=repo,
        client=client,
        symbol=SYMBOL,
        document=make_document_snapshot(document),
        as_of=AS_OF,
    )
    assert draft.degraded
    assert draft.degrade_reason == DegradeReason.EVIDENCE_INVALID
    assert draft.evidence == ()  # 不返回部分证据
    assert draft.direction is Direction.UNKNOWN
    assert NO_VERIFIABLE_CAUSE_TEXT in draft.summary


async def test_full_document_analysis_with_evidence() -> None:
    document = make_document()
    repo = FakeRepository()
    repo.documents = [make_document_snapshot(document)]
    session = cast(AsyncSession, FakeSession([document]))

    client = ScriptedChatClient(
        [
            tool_completion("get_documents", {"symbol": SYMBOL}),
            text_completion(_valid_payload([str(document.id)])),
        ]
    )
    draft = await analyze_document(
        session,
        repository=repo,
        client=client,
        symbol=SYMBOL,
        document=make_document_snapshot(document),
        as_of=AS_OF,
    )
    assert not draft.degraded
    assert draft.direction is Direction.POSITIVE
    assert len(draft.evidence) == 1
    evidence = draft.evidence[0]
    assert evidence.document_id == document.id
    assert evidence.quote in (document.body_text or "")  # 引文逐字来自原文
    assert draft.model_provider == "openai_compatible"
    assert draft.model_name == "test-model"


# ── 日志不得泄漏密钥 ────────────────────────────────────────────────────────


def test_settings_repr_never_contains_api_key() -> None:
    settings = Settings(
        agent_base_url="http://127.0.0.1:11434/v1",
        agent_model="qwen-none",
        agent_api_key="sk-super-secret-key",
    )
    assert "sk-super-secret-key" not in repr(settings)


async def test_client_error_log_does_not_contain_api_key(caplog: pytest.LogCaptureFixture) -> None:
    respx = pytest.importorskip("respx")
    import httpx

    from services.research.agents.client import OpenAICompatibleChatClient

    secret = "sk-super-secret-key"
    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://127.0.0.1:11434/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        client = OpenAICompatibleChatClient(
            base_url="http://127.0.0.1:11434/v1", api_key=secret, model="test-model"
        )
        with caplog.at_level(logging.DEBUG), pytest.raises(AgentUnavailable):
            await client.complete([{"role": "user", "content": "hi"}], [])

    assert secret not in caplog.text


async def test_openai_compatible_client_parses_tool_calls() -> None:
    respx = pytest.importorskip("respx")
    import httpx

    from services.research.agents.client import OpenAICompatibleChatClient

    payload: dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_documents",
                                "arguments": '{"symbol": "600519"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("http://127.0.0.1:11434/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = OpenAICompatibleChatClient(
            base_url="http://127.0.0.1:11434/v1", api_key="sk-x", model="test-model"
        )
        completion = await client.complete([{"role": "user", "content": "hi"}], [])

    assert completion.wants_tools
    assert completion.tool_calls[0].name == "get_documents"
    assert completion.tool_calls[0].arguments == {"symbol": SYMBOL}
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-x"
