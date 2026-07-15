"""自选股 API（spec §7.1 / 验收 §15.1）。"""

from __future__ import annotations

from datetime import date

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import BACKFILL_STEPS, JobStatus
from apps.api.tests.conftest import (
    AT_0950,
    OTHER_SYMBOL,
    SYMBOL,
    seed_instrument,
    seed_membership,
    seed_universe,
)


async def setup_member(session: AsyncSession, symbol: str = SYMBOL, name: str = "贵州茅台") -> None:
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950, symbol=symbol, name=name)
    await seed_membership(session, AT_0950, symbol=symbol)


async def test_get_watchlist_empty_returns_list_envelope(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    response = await client.get("/api/v1/watchlist")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["page"] == {"next_cursor": None, "has_more": False}
    assert body["request_id"]


async def test_post_watchlist_first_add_returns_202_with_backfill_job(
    client: AsyncClient, session: AsyncSession
) -> None:
    """spec §7.1：首次添加返回 202 + 回补任务（三步固定）。"""
    await setup_member(session)

    response = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["watchlist_item"]["symbol"] == SYMBOL
    assert data["watchlist_item"]["display_order"] == 0

    job = data["backfill_job"]
    assert job["status"] == JobStatus.QUEUED.value
    assert job["completed_steps"] == 0
    assert job["total_steps"] == len(BACKFILL_STEPS) == 3
    assert job["current_step"] == "daily_bars"
    assert job["error_code"] is None


async def test_post_watchlist_duplicate_returns_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    response = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_WATCHLIST_ITEM"


async def test_post_watchlist_removed_from_index_returns_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    """验收 §15.1 / §15.17：已调出沪深300的股票不能（重新）添加。"""
    await seed_universe(session, AT_0950)
    await seed_instrument(session, AT_0950)
    # 有效期在查询日之前就结束了 ⇒ 已调出
    await seed_membership(
        session, AT_0950, effective_from=date(2020, 1, 1), effective_to=date(2026, 6, 30)
    )

    response = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOT_CURRENT_UNIVERSE_MEMBER"


async def test_post_watchlist_unknown_symbol_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    response = await client.post("/api/v1/watchlist", json={"symbol": "999999"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INSTRUMENT_NOT_FOUND"


async def test_post_watchlist_malformed_symbol_returns_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    """入参校验失败必须是 400 INVALID_ARGUMENT —— 422 在本 spec 里专属 INSUFFICIENT_DATA。"""
    await seed_universe(session, AT_0950)
    response = await client.post("/api/v1/watchlist", json={"symbol": "60051"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_watchlist_lists_quote_and_freshness(
    client: AsyncClient, session: AsyncSession
) -> None:
    """前端不得自行算新鲜度 ⇒ 列表必须自带 quote.freshness。"""
    from apps.api.tests.conftest import seed_quote

    await setup_member(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    await seed_quote(session, AT_0950)

    body = (await client.get("/api/v1/watchlist")).json()

    item = body["data"][0]
    assert item["symbol"] == SYMBOL
    assert item["name"] == "贵州茅台"
    assert item["is_current_universe_member"] is True
    assert item["market"]["phase"] == "morning"
    assert item["quote"]["freshness"] == "fresh"


async def test_watchlist_without_quote_is_not_reported_as_provider_failure(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    item = (await client.get("/api/v1/watchlist")).json()["data"][0]

    assert item["quote"] is None
    assert item["market"]["phase"] == "morning"


async def test_watchlist_uses_server_cursor_pagination(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    await setup_member_second(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    await client.post("/api/v1/watchlist", json={"symbol": OTHER_SYMBOL})

    first = (await client.get("/api/v1/watchlist?limit=1")).json()
    assert len(first["data"]) == 1
    assert first["page"]["has_more"] is True
    assert first["page"]["next_cursor"]

    second = (
        await client.get(
            "/api/v1/watchlist",
            params={"limit": 1, "cursor": first["page"]["next_cursor"]},
        )
    ).json()
    assert len(second["data"]) == 1
    assert second["data"][0]["symbol"] != first["data"][0]["symbol"]
    assert second["page"] == {"next_cursor": None, "has_more": False}


async def test_watchlist_searches_all_items_before_pagination(
    client: AsyncClient, session: AsyncSession
) -> None:
    await setup_member(session)
    await setup_member_second(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    await client.post("/api/v1/watchlist", json={"symbol": OTHER_SYMBOL})

    body = (await client.get("/api/v1/watchlist", params={"q": "平安", "limit": 50})).json()

    assert [item["symbol"] for item in body["data"]] == [OTHER_SYMBOL]
    assert body["page"] == {"next_cursor": None, "has_more": False}


async def test_delete_watchlist_returns_204(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})

    response = await client.delete(f"/api/v1/watchlist/{SYMBOL}")

    assert response.status_code == 204
    assert (await client.get("/api/v1/watchlist")).json()["data"] == []


async def test_delete_unknown_watchlist_item_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_universe(session, AT_0950)
    response = await client.delete(f"/api/v1/watchlist/{SYMBOL}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INSTRUMENT_NOT_FOUND"


async def test_patch_watchlist_order(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    await setup_member_second(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    await client.post("/api/v1/watchlist", json={"symbol": OTHER_SYMBOL})

    response = await client.patch(
        "/api/v1/watchlist/order", json={"symbols": [OTHER_SYMBOL, SYMBOL]}
    )

    assert response.status_code == 200
    items = response.json()["data"]
    assert [item["symbol"] for item in items] == [OTHER_SYMBOL, SYMBOL]
    assert [item["display_order"] for item in items] == [0, 1]


async def test_patch_watchlist_order_rejects_partial_permutation(
    client: AsyncClient, session: AsyncSession
) -> None:
    """不做"尽力而为"的部分重排：缺项即 400。"""
    await setup_member(session)
    await setup_member_second(session)
    await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    await client.post("/api/v1/watchlist", json={"symbol": OTHER_SYMBOL})

    response = await client.patch("/api/v1/watchlist/order", json={"symbols": [SYMBOL]})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def setup_member_second(session: AsyncSession) -> None:
    await seed_instrument(session, AT_0950, symbol=OTHER_SYMBOL, name="平安银行", exchange="SZSE")
    await seed_membership(session, AT_0950, symbol=OTHER_SYMBOL)


async def test_get_job_returns_job_dto(client: AsyncClient, session: AsyncSession) -> None:
    await setup_member(session)
    created = await client.post("/api/v1/watchlist", json={"symbol": SYMBOL})
    job_id = created.json()["data"]["backfill_job"]["id"]

    response = await client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    job = response.json()["data"]
    assert job["id"] == job_id
    assert job["status"] in {"queued", "running", "succeeded", "failed"}


async def test_get_unknown_job_returns_404(client: AsyncClient, session: AsyncSession) -> None:
    response = await client.get("/api/v1/jobs/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404


async def test_get_job_with_malformed_id_returns_400(client: AsyncClient) -> None:
    response = await client.get("/api/v1/jobs/not-a-uuid")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
