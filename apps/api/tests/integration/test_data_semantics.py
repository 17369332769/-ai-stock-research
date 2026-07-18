"""数据语义集成测试（spec §6 / §7 / §9 / §11.3 / 验收 §15.4 §15.8 §15.16 §15.17）。

这些用例吃真实的 PostgreSQL：约束、事务、有效期区间和账本不可变性都必须在库里成立，
而不只是在 Python 里"看起来对"。
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import (
    CSI300_CODE,
    NO_VERIFIABLE_CAUSE_TEXT,
    AnalysisType,
    Direction,
    JobStatus,
    ModelStatus,
    PredictionHorizon,
)
from apps.api.app.models.tables import Analysis, Document, Job, Prediction, WatchlistItem
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories import model_versions as model_versions_repo
from apps.api.app.repositories import predictions as predictions_repo
from apps.api.tests.conftest import (
    AT_0950,
    SYMBOL,
    seed_instrument,
    seed_membership,
    seed_model_version,
    seed_prediction,
    seed_quote,
    seed_universe,
)
from services.prediction.training.registry import activate

pytestmark = pytest.mark.integration


async def setup_member(session: AsyncSession) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950)


async def test_gate_passing_model_can_activate_without_beating_baseline(
    session: AsyncSession,
) -> None:
    """spec §9.4：未优于基准仍可 active，置信度由推理层强制为 low。"""
    row = await seed_model_version(
        session,
        status=ModelStatus.CANDIDATE,
        metrics={
            "better_than_baseline": False,
            "release_gate": {"passed": True, "reasons": []},
        },
    )

    await activate(session, model_key=row.model_key, version=row.version, now=AT_0950)

    assert row.status == ModelStatus.ACTIVE.value


# ── 成分有效期（验收 §15.16）─────────────────────────────────────────────────
async def test_membership_before_effective_from_is_not_member(session: AsyncSession) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950, effective_from=date(2026, 7, 1))

    assert not await instruments_repo.is_current_member(
        session, SYMBOL, CSI300_CODE, date(2026, 6, 30)
    )


async def test_membership_on_effective_from_is_member(session: AsyncSession) -> None:
    """区间是闭区间：调入当日即为成员。"""
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950, effective_from=date(2026, 7, 1))

    assert await instruments_repo.is_current_member(
        session, SYMBOL, CSI300_CODE, date(2026, 7, 1)
    )


async def test_membership_on_effective_to_is_still_member(session: AsyncSession) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(
        session, AT_0950, effective_from=date(2020, 1, 1), effective_to=date(2026, 6, 30)
    )

    assert await instruments_repo.is_current_member(
        session, SYMBOL, CSI300_CODE, date(2026, 6, 30)
    )


async def test_membership_after_effective_to_is_not_member(session: AsyncSession) -> None:
    """调出之后不再是成员 —— 历史有效期不被"当前 300 只"覆盖。"""
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(
        session, AT_0950, effective_from=date(2020, 1, 1), effective_to=date(2026, 6, 30)
    )

    assert not await instruments_repo.is_current_member(
        session, SYMBOL, CSI300_CODE, date(2026, 7, 1)
    )


# ── 事务原子性（spec §7.1）───────────────────────────────────────────────────
async def test_rejected_add_leaves_no_rows(client: AsyncClient, session: AsyncSession) -> None:
    """成员资格校验失败 ⇒ 既不写自选股，也不登记回补作业。"""
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(
        session, AT_0950, effective_from=date(2020, 1, 1), effective_to=date(2026, 6, 30)
    )

    response = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    assert response.status_code == 409

    items = (await session.execute(select(WatchlistItem))).scalars().all()
    jobs = (await session.execute(select(Job))).scalars().all()
    assert items == []
    assert jobs == []


async def test_backfill_job_is_idempotent_across_readd(
    client: AsyncClient, session: AsyncSession
) -> None:
    """删除后重新添加不会堆出第二个回补作业（idempotency_key 唯一）。"""
    await setup_member(session)

    first = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    assert first.status_code == 202
    await client.delete(f"/api/v1/watchlist/{SYMBOL}")
    second = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    assert second.json()["data"]["backfill_job"]["id"] == str(jobs[0].id)


async def test_readd_after_successful_backfill_returns_201_without_job(
    client: AsyncClient, session: AsyncSession
) -> None:
    """已经成功回补过的股票重新加回来，不需要再跑一遍回补 ⇒ 201，backfill_job=null。"""
    await setup_member(session)
    session.add(
        Job(
            id=uuid.uuid4(),
            job_type="instrument_backfill",
            symbol=SYMBOL,
            status=JobStatus.SUCCEEDED.value,
            completed_steps=3,
            total_steps=3,
            current_step="documents",
            warnings=[],
            idempotency_key=f"instrument_backfill:{SYMBOL}",
        )
    )
    await session.flush()

    response = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    assert response.status_code == 201
    assert response.json()["data"]["backfill_job"] is None


# ── 去重（验收 §15.4）────────────────────────────────────────────────────────
async def test_documents_are_deduplicated_by_content_hash(session: AsyncSession) -> None:
    await setup_member(session)
    content_hash = "a" * 64
    for i in range(2):
        session.add(
            Document(
                id=uuid.uuid4(),
                symbol=SYMBOL,
                document_type="announcement",
                title=f"同一份公告 {i}",
                body_text="正文",
                source="cninfo",
                source_url=f"http://example.invalid/{i}",
                published_at=AT_0950,
                observed_at=AT_0950,
                content_hash=content_hash,  # 内容相同 ⇒ 哈希相同
            )
        )

    with pytest.raises(IntegrityError):
        await session.flush()


# ── 预测账本不可变（验收 §15.8）──────────────────────────────────────────────
async def test_prediction_identity_is_unique(session: AsyncSession) -> None:
    """同一 (symbol, model_version, horizon, as_of) 不能写两次 ⇒ 原始预测不会被"覆盖"。"""
    await setup_member(session)
    model = await seed_model_version(session)
    await seed_prediction(session, model, as_of=AT_0950)

    duplicate = Prediction(
        id=uuid.uuid4(),
        symbol=SYMBOL,
        model_version_id=model.id,
        horizon=PredictionHorizon.NEXT_5D.value,
        as_of=AT_0950,
        target_at=AT_0950,
        reference_price=1,
        probability_up=0.5,
        expected_return=0,
        lower_return=0,
        upper_return=0,
        confidence_label="low",
        data_cutoff=AT_0950,
        features_snapshot={},
    )
    session.add(duplicate)

    with pytest.raises(IntegrityError):
        await session.flush()


def test_prediction_repository_exposes_no_update_path() -> None:
    """账本仓储只读 + 只追加：不提供任何 update/delete 方法（spec §3.4）。"""
    forbidden = {"update", "delete", "upsert", "save", "overwrite"}
    exposed = {name for name in dir(predictions_repo) if not name.startswith("_")}
    assert not (forbidden & exposed), f"预测仓储暴露了写方法：{sorted(forbidden & exposed)}"


# ── 模型可用性（spec §9.4）───────────────────────────────────────────────────
async def test_active_model_lookup_ignores_candidate_and_retired(
    session: AsyncSession,
) -> None:
    await setup_member(session)
    await seed_model_version(session, version="c1", status=ModelStatus.CANDIDATE)
    await seed_model_version(session, version="r1", status=ModelStatus.RETIRED)

    assert (
        await model_versions_repo.active_for_horizon(session, PredictionHorizon.NEXT_5D.value)
    ) is None

    await seed_model_version(session, version="a1", status=ModelStatus.ACTIVE)
    active = await model_versions_repo.active_for_horizon(
        session, PredictionHorizon.NEXT_5D.value
    )
    assert active is not None
    assert active.version == "a1"


# ── 行情 ─────────────────────────────────────────────────────────────────────
async def test_latest_quote_picks_most_recent_observation(session: AsyncSession) -> None:
    from apps.api.app.repositories import quotes as quotes_repo

    await setup_member(session)
    await seed_quote(session, AT_0950 - timedelta(minutes=5), price="1200.00")
    await seed_quote(session, AT_0950, price="1215.04")

    latest = await quotes_repo.latest(session, SYMBOL)

    assert latest is not None
    assert float(latest.price) == 1215.04


async def test_latest_many_returns_one_row_per_symbol(session: AsyncSession) -> None:
    from apps.api.app.repositories import quotes as quotes_repo

    await setup_member(session)
    await seed_quote(session, AT_0950 - timedelta(minutes=5), price="1200.00")
    await seed_quote(session, AT_0950, price="1215.04")

    quotes = await quotes_repo.latest_many(session, [SYMBOL])

    assert set(quotes) == {SYMBOL}
    assert float(quotes[SYMBOL].price) == 1215.04


# ── 证据展开：整条成功或整条失败（spec §11.3）───────────────────────────────
async def seed_document(session: AsyncSession, title: str = "回购公告") -> Document:
    row = Document(
        id=uuid.uuid4(),
        symbol=SYMBOL,
        document_type="announcement",
        title=title,
        body_text="公司拟以自有资金回购股份，回购金额不低于人民币 10 亿元。",
        source="cninfo",
        source_url="http://example.invalid/doc",
        published_at=AT_0950,
        observed_at=AT_0950,
        content_hash=uuid.uuid4().hex * 2,
    )
    session.add(row)
    await session.flush()
    return row


async def test_analysis_evidence_expands_fully(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    document = await seed_document(session)
    session.add(
        Analysis(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            analysis_type=AnalysisType.DOCUMENT.value,
            direction=Direction.POSITIVE.value,
            horizon="short",
            confidence=0.6,
            summary="公司公告回购",
            evidence=[
                {
                    "document_id": str(document.id),
                    "title": document.title,
                    "source_url": document.source_url,
                    "published_at": AT_0950.isoformat(),
                    "quote": "公司拟以自有资金回购股份",
                }
            ],
            data_cutoff=AT_0950,
        )
    )
    await session.flush()

    body = (await client.get(f"/api/v1/stocks/{SYMBOL}/analyses")).json()

    evidence = body["data"][0]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["document_id"] == str(document.id)
    assert evidence[0]["quote"] == "公司拟以自有资金回购股份"
    assert evidence[0]["source_url"]


async def test_analysis_with_dangling_evidence_fails_whole_analysis(
    client: AsyncClient, session: AsyncSession
) -> None:
    """任一 document_id 不存在 ⇒ 整条分析校验失败，**不返回部分证据**。"""
    await setup_member(session)
    real = await seed_document(session)
    missing_id = uuid.uuid4()

    session.add(
        Analysis(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            analysis_type=AnalysisType.DOCUMENT.value,
            direction=Direction.POSITIVE.value,
            horizon="short",
            confidence=0.6,
            summary="两条证据，其中一条指向不存在的文档",
            evidence=[
                {
                    "document_id": str(real.id),
                    "title": real.title,
                    "source_url": real.source_url,
                    "published_at": AT_0950.isoformat(),
                    "quote": "公司拟以自有资金回购股份",
                },
                {
                    "document_id": str(missing_id),
                    "title": "不存在的公告",
                    "source_url": "http://example.invalid/missing",
                    "published_at": AT_0950.isoformat(),
                    "quote": "幽灵引用",
                },
            ],
            data_cutoff=AT_0950,
        )
    )
    await session.flush()

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/analyses")

    assert response.status_code == 500
    # 关键：不能返回"只剩一条证据"的半成品
    assert "evidence" not in response.text


async def test_analysis_without_evidence_is_unknown_with_fixed_text(
    client: AsyncClient, session: AsyncSession
) -> None:
    """验收 §15.5 / spec §12：没有匹配公告或新闻时必须写「未找到可验证事件原因」。"""
    await setup_member(session)
    session.add(
        Analysis(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            analysis_type=AnalysisType.ANOMALY.value,
            direction=Direction.UNKNOWN.value,
            horizon="unknown",
            confidence=None,
            summary=f"当日放量上涨 5.2%，{NO_VERIFIABLE_CAUSE_TEXT}",
            evidence=[],
            data_cutoff=AT_0950,
        )
    )
    await session.flush()

    body = (await client.get(f"/api/v1/stocks/{SYMBOL}/analyses?type=anomaly")).json()

    item = body["data"][0]
    assert item["direction"] == "unknown"
    assert item["evidence"] == []
    assert NO_VERIFIABLE_CAUSE_TEXT in item["summary"]


# ── 成绩单边界 ───────────────────────────────────────────────────────────────
async def test_scorecard_eligibility_boundary_at_target_time(
    client: AsyncClient, session: AsyncSession
) -> None:
    """target_at == now ⇒ 目标时间已到 ⇒ 计入 eligible（闭区间）。"""
    await setup_member(session)
    model = await seed_model_version(session)
    boundary = await seed_prediction(
        session, model, as_of=AT_0950 - timedelta(days=5), target_at=AT_0950
    )
    from apps.api.tests.conftest import seed_outcome

    await seed_outcome(session, boundary, direction_correct=True)
    # 差一秒没到 ⇒ 不进分母
    await seed_prediction(
        session,
        model,
        as_of=AT_0950 - timedelta(days=4),
        target_at=AT_0950 + timedelta(seconds=1),
    )

    data = (
        await client.get("/api/v1/models/a_share_5d_lightgbm/scorecard?window=all")
    ).json()["data"]

    assert data["eligible_count"] == 1
    assert data["settled_count"] == 1
    assert data["pending_count"] == 0


async def test_today_close_latest_ignores_previous_session(
    client: AsyncClient, session: AsyncSession
) -> None:
    """今日预测只认当前交易日的那几条，不能把昨天已结算的预测当成"今天的"。"""
    await setup_member(session)
    model = await seed_model_version(
        session, model_key="a_share_today_lightgbm", horizon=PredictionHorizon.TODAY_CLOSE
    )
    yesterday_as_of = AT_0950 - timedelta(days=1)
    await seed_prediction(
        session,
        model,
        horizon=PredictionHorizon.TODAY_CLOSE,
        as_of=yesterday_as_of,
        target_at=yesterday_as_of,
    )

    response = await client.get(
        f"/api/v1/stocks/{SYMBOL}/predictions/latest?horizon=today_close"
    )

    # 今天还没生成今日预测 ⇒ 不能拿昨天那条充数
    assert response.status_code in (202, 422)
    assert response.status_code != 200
