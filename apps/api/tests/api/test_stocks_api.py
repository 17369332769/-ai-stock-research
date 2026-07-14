"""快照 / 股票池 / 搜索 / 文档 API（spec §7.1 / §7.2 / §7.3 / 验收 §15.2 §15.16）。"""

from __future__ import annotations

from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.tests.conftest import (
    AT_0950,
    OTHER_SYMBOL,
    SYMBOL,
    seed_instrument,
    seed_membership,
    seed_quote,
    seed_universe,
)


async def setup_member(session: AsyncSession, symbol: str = SYMBOL, name: str = "贵州茅台") -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950, symbol=symbol, name=name)
    await seed_membership(session, AT_0950, symbol=symbol)


# ── 快照 ─────────────────────────────────────────────────────────────────────
async def test_snapshot_shape_matches_spec(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    await seed_quote(session, AT_0950)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == SYMBOL
    assert body["name"] == "贵州茅台"
    assert body["quote"]["source"] == "eastmoney_via_akshare"
    assert body["quote"]["freshness"] == "fresh"
    assert isinstance(body["quote"]["price"], float)
    assert body["latest_predictions"] == []
    assert body["request_id"]


async def test_snapshot_stale_quote_returns_200_with_age(
    client: AsyncClient, session: AsyncSession
) -> None:
    """spec §7 / 验收 §15.2：超过 180 秒 ⇒ 200 + stale + age_seconds（不是错误）。"""
    await setup_member(session)
    await seed_quote(session, AT_0950 - timedelta(seconds=600))

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/snapshot")

    assert response.status_code == 200
    quote = response.json()["quote"]
    assert quote["freshness"] == "stale"
    assert quote["age_seconds"] == 600


async def test_snapshot_without_any_quote_returns_424(
    client: AsyncClient, session: AsyncSession
) -> None:
    """从未取得行情 ⇒ 424 PROVIDER_UNAVAILABLE（绝不返回默认价格）。"""
    await setup_member(session)

    response = await client.get(f"/api/v1/stocks/{SYMBOL}/snapshot")

    assert response.status_code == 424
    assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


async def test_snapshot_unknown_symbol_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    response = await client.get("/api/v1/stocks/999999/snapshot")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INSTRUMENT_NOT_FOUND"


async def test_snapshot_includes_relative_strength_when_benchmark_available(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    await seed_quote(session, AT_0950)
    # 基准 000300 也需要一条行情
    await seed_instrument(session, AT_0950, symbol="000300", name="沪深300", exchange="SSE")
    await seed_quote(session, AT_0950, symbol="000300", price="4000.0", previous_close="4014.0")

    body = (await client.get(f"/api/v1/stocks/{SYMBOL}/snapshot")).json()

    rs = body["relative_strength"]
    assert rs["benchmark"] == "000300"
    assert rs["benchmark_change_percent"] < 0
    assert rs["stock_change_percent"] > 0


async def test_snapshot_relative_strength_null_when_benchmark_missing(
    client: AsyncClient, session: AsyncSession
) -> None:
    """基准行情缺失时返回 null，而不是拿 0 冒充"大盘没动"。"""
    await setup_member(session)
    await seed_quote(session, AT_0950)

    body = (await client.get(f"/api/v1/stocks/{SYMBOL}/snapshot")).json()

    assert body["relative_strength"] is None


async def test_snapshot_marks_removed_member(client: AsyncClient, session: AsyncSession) -> None:
    """验收 §15.17：调出指数后历史页面仍可访问，并标记已调出。"""
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(
        session, AT_0950, effective_from=date(2020, 1, 1), effective_to=date(2026, 6, 30)
    )
    await seed_quote(session, AT_0950)

    body = (await client.get(f"/api/v1/stocks/{SYMBOL}/snapshot")).json()

    assert body["is_current_universe_member"] is False


# ── 股票池 ───────────────────────────────────────────────────────────────────
async def test_universe_members_returns_current_snapshot(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)

    response = await client.get("/api/v1/universes/CSI300/instruments")

    assert response.status_code == 200
    body = response.json()
    assert [i["symbol"] for i in body["data"]] == [SYMBOL]
    assert body["page"]["has_more"] is False


async def test_universe_members_respects_as_of(client: AsyncClient, session: AsyncSession) -> None:
    """验收 §15.16：指定历史日期时返回**当日真实成员**，不用当前成分回填历史。"""
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_instrument(session, AT_0950, symbol=OTHER_SYMBOL, name="平安银行", exchange="SZSE")
    # 600519 一直在；000001 在 2026-07-01 才调入
    await seed_membership(session, AT_0950, symbol=SYMBOL, effective_from=date(2020, 1, 1))
    await seed_membership(
        session, AT_0950, symbol=OTHER_SYMBOL, effective_from=date(2026, 7, 1)
    )

    old = await client.get("/api/v1/universes/CSI300/instruments?as_of=2026-06-01")
    new = await client.get("/api/v1/universes/CSI300/instruments?as_of=2026-07-14")

    assert [i["symbol"] for i in old.json()["data"]] == [SYMBOL]
    assert sorted(i["symbol"] for i in new.json()["data"]) == [OTHER_SYMBOL, SYMBOL]


async def test_universe_members_pagination(client: AsyncClient, session: AsyncSession) -> None:
    await seed_universe(session, AT_0950)
    for i in range(3):
        symbol = f"60000{i}"
        await seed_instrument(session, AT_0950, symbol=symbol, name=f"股票{i}")
        await seed_membership(session, AT_0950, symbol=symbol)

    first = await client.get("/api/v1/universes/CSI300/instruments?limit=2")
    body = first.json()
    assert len(body["data"]) == 2
    assert body["page"]["has_more"] is True

    second = await client.get(
        f"/api/v1/universes/CSI300/instruments?limit=2&cursor={body['page']['next_cursor']}"
    )
    rest = second.json()
    assert len(rest["data"]) == 1
    assert rest["page"]["has_more"] is False


async def test_universe_members_invalid_cursor_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    response = await client.get("/api/v1/universes/CSI300/instruments?cursor=garbage!!")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_universe_without_snapshot_returns_424(
    client: AsyncClient, session: AsyncSession
) -> None:
    """成分从未同步成功 ⇒ 424，而不是返回空列表假装"沪深300是空的"。"""
    await seed_universe(session, AT_0950)
    response = await client.get("/api/v1/universes/CSI300/instruments")

    assert response.status_code == 424
    assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


async def test_universe_members_limit_over_max_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    response = await client.get("/api/v1/universes/CSI300/instruments?limit=101")

    assert response.status_code == 400


# ── 搜索 ─────────────────────────────────────────────────────────────────────
async def test_search_prefers_exact_code_match(client: AsyncClient, session: AsyncSession) -> None:
    """spec §7.1：精确代码匹配优先。"""
    await seed_universe(session, AT_0950)
    for symbol, name in [("600519", "贵州茅台"), ("600518", "康美药业"), ("600510", "黑牡丹")]:
        await seed_instrument(session, AT_0950, symbol=symbol, name=name)
        await seed_membership(session, AT_0950, symbol=symbol)

    body = (await client.get("/api/v1/instruments/search?q=600519")).json()

    assert body["data"][0]["symbol"] == "600519"


async def test_search_by_name(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    body = (await client.get("/api/v1/instruments/search?q=茅台")).json()

    assert [i["symbol"] for i in body["data"]] == [SYMBOL]


async def test_search_excludes_non_members(client: AsyncClient, session: AsyncSession) -> None:
    """只搜索查询日**当前**成分。"""
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(
        session, AT_0950, effective_from=date(2020, 1, 1), effective_to=date(2026, 6, 30)
    )
    # 另有一只在册成分，保证 universe 有快照
    await seed_instrument(session, AT_0950, symbol=OTHER_SYMBOL, name="平安银行", exchange="SZSE")
    await seed_membership(session, AT_0950, symbol=OTHER_SYMBOL)

    body = (await client.get("/api/v1/instruments/search?q=600519")).json()

    assert body["data"] == []


async def test_search_empty_query_returns_400(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    response = await client.get("/api/v1/instruments/search?q=")

    assert response.status_code == 400


# ── 文档 ─────────────────────────────────────────────────────────────────────
async def test_documents_pagination_and_filter(
    client: AsyncClient, session: AsyncSession
) -> None:
    import uuid

    from apps.api.app.models.tables import Document

    await setup_member(session)
    for i in range(3):
        session.add(
            Document(
                id=uuid.uuid4(),
                symbol=SYMBOL,
                document_type="announcement",
                title=f"公告{i}",
                body_text="正文",
                source="cninfo",
                source_url=f"http://www.cninfo.com.cn/{i}",
                published_at=AT_0950 - timedelta(days=i),
                observed_at=AT_0950,
                content_hash=f"{i:064d}",
            )
        )
    session.add(
        Document(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            document_type="news",
            title="新闻",
            body_text="正文",
            source="eastmoney",
            source_url="http://finance.eastmoney.com/x",
            published_at=AT_0950,
            observed_at=AT_0950,
            content_hash="f" * 64,
        )
    )
    await session.flush()

    announcements = (
        await client.get(f"/api/v1/stocks/{SYMBOL}/documents?type=announcement&limit=2")
    ).json()
    assert len(announcements["data"]) == 2
    assert announcements["page"]["has_more"] is True
    assert all(d["document_type"] == "announcement" for d in announcements["data"])

    page2 = (
        await client.get(
            f"/api/v1/stocks/{SYMBOL}/documents"
            f"?type=announcement&limit=2&cursor={announcements['page']['next_cursor']}"
        )
    ).json()
    assert len(page2["data"]) == 1
    assert page2["page"]["has_more"] is False


async def test_documents_unknown_symbol_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    response = await client.get("/api/v1/stocks/999999/documents")
    assert response.status_code == 404


async def test_refresh_analyses_returns_202_job(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    response = await client.post(f"/api/v1/stocks/{SYMBOL}/analyses/refresh")

    assert response.status_code == 202
    job = response.json()["data"]
    assert job["job_type"] == "analysis_refresh"
    assert job["status"] == "queued"
