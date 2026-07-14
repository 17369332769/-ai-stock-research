"""预测、成绩单、相似行情 API（spec §7.4 / §7.5 / 验收 §15.6 §15.10 §15.11）。"""

from __future__ import annotations

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import FixedClock
from apps.api.app.core.enums import (
    RESEARCH_ONLY_DISCLAIMER,
    ConfidenceLabel,
    JobStatus,
    ModelStatus,
    PredictionHorizon,
)
from apps.api.tests.conftest import (
    AT_0944,
    AT_0950,
    AT_1500,
    SYMBOL,
    StubAnalogFinder,
    seed_instrument,
    seed_job,
    seed_membership,
    seed_model_version,
    seed_outcome,
    seed_prediction,
    seed_universe,
)


async def setup_member(session: AsyncSession) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950)


# ── 最新预测 ─────────────────────────────────────────────────────────────────
async def test_latest_prediction_shape_matches_spec(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    model = await seed_model_version(session)
    await seed_prediction(session, model)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=next_5d")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == SYMBOL
    assert body["horizon"] == "next_5d"
    assert body["probability_up"] == 0.38
    assert body["return_interval"] == {"p20": -0.041, "p80": 0.019}
    assert body["confidence"] == "low"
    assert body["model"] == {
        "key": "a_share_5d_lightgbm",
        "version": "2026.07.14.1",
        "better_than_baseline": False,
    }
    assert body["disclaimer"] == RESEARCH_ONLY_DISCLAIMER
    assert body["request_id"]


async def test_today_prediction_before_0945_is_unavailable(
    client: AsyncClient, session: AsyncSession, clock: FixedClock
) -> None:
    """验收 §15.6：今日预测在 09:45 前不可用。"""
    clock.set(AT_0944)
    await setup_member(session)
    model = await seed_model_version(
        session,
        model_key="a_share_today_lightgbm",
        horizon=PredictionHorizon.TODAY_CLOSE,
    )
    await seed_prediction(session, model, horizon=PredictionHorizon.TODAY_CLOSE, as_of=AT_0944)

    response = await client.get(
        f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=today_close"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_DATA"


async def test_today_prediction_at_0950_is_available(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    model = await seed_model_version(
        session,
        model_key="a_share_today_lightgbm",
        horizon=PredictionHorizon.TODAY_CLOSE,
    )
    await seed_prediction(
        session, model, horizon=PredictionHorizon.TODAY_CLOSE, as_of=AT_0950, target_at=AT_1500
    )

    response = await client.get(
        f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=today_close"
    )

    assert response.status_code == 200
    assert response.json()["horizon"] == "today_close"


async def test_missing_prediction_with_running_backfill_returns_202(
    client: AsyncClient, session: AsyncSession
) -> None:
    """spec §7：预测不存在但回补进行中 ⇒ 202 + 作业状态。"""
    await setup_member(session)
    await seed_model_version(session)
    await seed_job(session, status=JobStatus.RUNNING)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=next_5d")

    assert response.status_code == 202
    job = response.json()["data"]["backfill_job"]
    assert job["status"] == "running"
    assert job["total_steps"] == 3


async def test_missing_prediction_without_model_returns_503(
    client: AsyncClient, session: AsyncSession
) -> None:
    """没有可用（active）模型 ⇒ 503 MODEL_UNAVAILABLE。"""
    await setup_member(session)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=next_5d")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


async def test_candidate_model_never_serves_predictions(
    client: AsyncClient, session: AsyncSession
) -> None:
    """spec §9.4：candidate 状态永远不对 API 提供预测。"""
    await setup_member(session)
    await seed_model_version(session, status=ModelStatus.CANDIDATE)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=next_5d")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


async def test_missing_prediction_with_model_and_no_backfill_returns_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    """有模型、无回补在跑、仍无预测 ⇒ 确认样本不足 ⇒ 422。"""
    await setup_member(session)
    await seed_model_version(session)
    await seed_job(session, status=JobStatus.SUCCEEDED)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=next_5d")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_DATA"


async def test_latest_prediction_unknown_symbol_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    response = await client.get("/api/v1/stocks/999999/predictions/latest")
    assert response.status_code == 404


# ── 历史预测 ─────────────────────────────────────────────────────────────────
async def test_prediction_history_pagination(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    model = await seed_model_version(session)
    for i in range(3):
        await seed_prediction(session, model, as_of=AT_0950 - timedelta(days=i))

    first = (
        await client.get(f"/api/v1/stocks/{SYMBOL}/predictions/history?horizon=next_5d&limit=2")
    ).json()
    assert len(first["data"]) == 2
    assert first["page"]["has_more"] is True

    second = (
        await client.get(
            f"/api/v1/stocks/{SYMBOL}/predictions/history"
            f"?horizon=next_5d&limit=2&cursor={first['page']['next_cursor']}"
        )
    ).json()
    assert len(second["data"]) == 1
    assert second["page"]["has_more"] is False


# ── 成绩单 ───────────────────────────────────────────────────────────────────
async def test_scorecard_counts_and_metrics(client: AsyncClient, session: AsyncSession) -> None:
    """spec §7.4：settled + pending = eligible；未到目标时间的预测不进分母。"""
    await setup_member(session)
    model = await seed_model_version(session)

    settled = await seed_prediction(
        session, model, as_of=AT_0950 - timedelta(days=10), target_at=AT_0950 - timedelta(days=3)
    )
    await seed_outcome(session, settled, direction_correct=True)

    # 目标时间已到但尚未结算 ⇒ pending
    await seed_prediction(
        session, model, as_of=AT_0950 - timedelta(days=9), target_at=AT_0950 - timedelta(days=2)
    )
    # 目标时间未到 ⇒ 完全不进分母
    await seed_prediction(
        session, model, as_of=AT_0950, target_at=AT_0950 + timedelta(days=7)
    )

    response = await client.get("/api/v1/models/a_share_5d_lightgbm/scorecard?window=all")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["eligible_count"] == 2
    assert data["settled_count"] == 1
    assert data["pending_count"] == 1
    assert data["settled_count"] + data["pending_count"] == data["eligible_count"]
    assert data["direction_accuracy"] == 1.0
    assert data["better_than_baseline"] is False
    assert data["baseline_mae"] == 0.019


async def test_scorecard_window_20(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    model = await seed_model_version(session)
    for i in range(25):
        prediction = await seed_prediction(
            session,
            model,
            as_of=AT_0950 - timedelta(days=30 - i),
            target_at=AT_0950 - timedelta(days=25 - i),
        )
        await seed_outcome(session, prediction, direction_correct=(i % 2 == 0))

    body = (await client.get("/api/v1/models/a_share_5d_lightgbm/scorecard?window=20")).json()

    assert body["data"]["window"] == 20
    assert body["data"]["eligible_count"] == 20
    assert body["data"]["settled_count"] == 20


async def test_scorecard_unknown_model_returns_503(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.get("/api/v1/models/does_not_exist/scorecard")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


async def test_scorecard_without_settled_predictions_returns_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    """没有已结算样本 ⇒ 指标无定义 ⇒ 422，绝不返回 0.0 冒充命中率。"""
    await setup_member(session)
    model = await seed_model_version(session)
    await seed_prediction(session, model, target_at=AT_0950 + timedelta(days=7))

    response = await client.get("/api/v1/models/a_share_5d_lightgbm/scorecard?window=all")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_DATA"


async def test_scorecard_invalid_window_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.get("/api/v1/models/a_share_5d_lightgbm/scorecard?window=50")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_scorecard_model_missing_baselines_returns_503(
    client: AsyncClient, session: AsyncSession
) -> None:
    """基准指标缺失 ⇒ 模型未通过 §9.4 发布门槛 ⇒ fail closed。"""
    await setup_member(session)
    model = await seed_model_version(session, metrics={"auc": 0.6})
    prediction = await seed_prediction(
        session, model, as_of=AT_0950 - timedelta(days=10), target_at=AT_0950 - timedelta(days=3)
    )
    await seed_outcome(session, prediction)

    response = await client.get("/api/v1/models/a_share_5d_lightgbm/scorecard?window=all")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


async def test_prediction_with_medium_confidence_and_no_edge_fails_closed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """spec §9.4：better_than_baseline=false 时置信度只能 low —— 违反即 500，不展示给用户。"""
    await setup_member(session)
    model = await seed_model_version(session, better_than_baseline=False)
    await seed_prediction(session, model, confidence=ConfidenceLabel.MEDIUM)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=next_5d")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


# ── 历史相似行情 ─────────────────────────────────────────────────────────────
async def test_analogs_returns_hits(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/analogs?horizon=next_5d&limit=10")

    assert response.status_code == 200
    items = response.json()["data"]
    assert len(items) == 3
    first = items[0]
    assert first["feature_set_version"] == "v1"
    assert "forward_return_1d" in first
    assert "forward_return_5d" in first
    assert first["distance"] >= 0


async def test_analogs_with_small_candidate_pool_returns_422(
    client: AsyncClient, session: AsyncSession, analog_finder: StubAnalogFinder
) -> None:
    """spec §10：有效候选少于 30 个时关闭该功能并说明样本不足。"""
    # 注意字段名必须是 candidates_valid —— 写成别的名字只会凭空造一个没人读的属性，
    # 池子仍是默认的 120，接口返回 200，这条断言就成了永远不会失败的假测试。
    analog_finder.candidates_valid = 29
    await setup_member(session)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/analogs")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_DATA"
