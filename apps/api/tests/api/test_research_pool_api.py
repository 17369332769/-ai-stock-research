"""002：自动研究池与额外自选 API。"""

from __future__ import annotations

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


async def test_csi300_member_is_automatic_without_watchlist_row(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950)

    response = await client.get("/api/v1/research-pool", params={"scope": "csi300"})

    assert response.status_code == 200
    item = response.json()["data"][0]
    assert item["symbol"] == SYMBOL
    assert item["pool_source"] == "csi300"
    assert item["can_remove"] is False
    assert item["industry"] is None
    assert item["has_anomaly"] is False
    assert item["document_count"] == 0
    assert item["prediction_count"] == 0
    assert item["analysis_status"] == "waiting"


async def test_extra_watchlist_only_accepts_non_member(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950)
    await seed_instrument(
        session, AT_0950, symbol=OTHER_SYMBOL, name="平安银行", exchange="SZSE"
    )

    current = await client.post("/api/v1/extra-watchlist", json={"symbol": SYMBOL})
    assert current.status_code == 400
    assert "已自动包含" in current.json()["error"]["message"]

    extra = await client.post("/api/v1/extra-watchlist", json={"symbol": OTHER_SYMBOL})
    assert extra.status_code == 202
    assert extra.json()["data"]["watchlist_item"]["pool_source"] == "extra"

    merged = await client.get("/api/v1/research-pool", params={"scope": "all"})
    rows = merged.json()["data"]
    assert [row["symbol"] for row in rows] == [SYMBOL, OTHER_SYMBOL]
    assert len({row["symbol"] for row in rows}) == 2
    extra_row = next(row for row in rows if row["symbol"] == OTHER_SYMBOL)
    assert extra_row["backfill_job"]["status"] == "queued"


async def test_research_pool_exposes_persisted_analysis_job(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950)
    queued = await client.post(f"/api/v1/stocks/{SYMBOL}/analyses/refresh")
    assert queued.status_code == 202

    response = await client.get("/api/v1/research-pool", params={"scope": "csi300"})

    item = response.json()["data"][0]
    assert item["analysis_status"] == "queued"
    assert item["analysis_job"]["id"] == queued.json()["data"]["id"]


async def test_latest_quotes_endpoint_reads_research_scope(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    await seed_membership(session, AT_0950)
    await seed_quote(session, AT_0950)

    response = await client.get("/api/v1/quotes/latest", params={"scope": "csi300"})

    assert response.status_code == 200
    quote = response.json()["data"][0]
    assert quote["symbol"] == SYMBOL
    assert quote["age_status"] == "latest"
    assert quote["data_age_seconds"] == 0


async def test_unknown_but_valid_a_share_code_can_be_added_directly(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)

    response = await client.post("/api/v1/extra-watchlist", json={"symbol": "300750"})

    assert response.status_code == 202
    item = response.json()["data"]["watchlist_item"]
    assert item["symbol"] == "300750"
    assert item["name"] == "300750（名称待同步）"
