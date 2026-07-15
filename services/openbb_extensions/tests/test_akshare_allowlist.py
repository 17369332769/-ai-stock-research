"""AKShare 扩展只允许调用已审计的生产函数。

这不是文档约定 —— ``client.call_akshare`` 有运行时白名单，越权直接抛错。
本测试同时锁死"白名单集合本身"，任何人往里加函数都会让测试红。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from services.openbb_extensions.akshare_provider import client
from services.openbb_extensions.akshare_provider.client import (
    ALLOWED_AKSHARE_FUNCTIONS,
    PINNED_AKSHARE_VERSION,
    call_akshare,
)
from services.openbb_extensions.akshare_provider.constants import (
    ProviderConfigError,
    ProviderUpstreamError,
)

pytestmark = pytest.mark.contract


def test_allowlist_is_exactly_the_audited_functions() -> None:
    assert {
        "stock_zh_a_spot_em",
        "stock_zh_a_hist",
        "stock_zh_a_hist_min_em",
        "stock_news_em",
    } == ALLOWED_AKSHARE_FUNCTIONS


def test_akshare_version_is_pinned() -> None:
    assert PINNED_AKSHARE_VERSION == "1.18.64"


@pytest.mark.asyncio
async def test_spot_snapshot_is_reused_without_sharing_mutable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_call(function_name: str, /, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        assert function_name == "stock_zh_a_spot_em"
        return [{"代码": "000002", "最新价": 9.88}]

    client.reset_spot_cache()
    monkeypatch.setattr(client, "acall_akshare", fake_call)

    first = await client.fetch_spot()
    first[0]["代码"] = "mutated"
    second = await client.fetch_spot()

    assert calls == 1
    assert second == [{"代码": "000002", "最新价": 9.88}]


@pytest.mark.asyncio
async def test_concurrent_spot_requests_share_one_full_market_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_call(function_name: str, /, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [{"代码": "000002"}]

    client.reset_spot_cache()
    monkeypatch.setattr(client, "acall_akshare", fake_call)
    tasks = [asyncio.create_task(client.fetch_spot()) for _ in range(4)]
    await started.wait()
    release.set()

    assert await asyncio.gather(*tasks) == [[{"代码": "000002"}]] * 4
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_spot_fetch_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def flaky_call(function_name: str, /, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderUpstreamError("temporary failure")
        return [{"代码": "000002"}]

    client.reset_spot_cache()
    monkeypatch.setattr(client, "acall_akshare", flaky_call)

    with pytest.raises(ProviderUpstreamError, match="temporary failure"):
        await client.fetch_spot()
    assert await client.fetch_spot() == [{"代码": "000002"}]
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_spot_failure_is_shared_but_next_call_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    release = asyncio.Event()

    async def flaky_call(function_name: str, /, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            await release.wait()
            raise ProviderUpstreamError("shared failure")
        return [{"代码": "000002"}]

    client.reset_spot_cache()
    monkeypatch.setattr(client, "acall_akshare", flaky_call)
    tasks = [asyncio.create_task(client.fetch_spot()) for _ in range(4)]
    await asyncio.sleep(0)
    release.set()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, ProviderUpstreamError) for result in results)
    assert calls == 1

    assert await client.fetch_spot() == [{"代码": "000002"}]
    assert calls == 2


@pytest.mark.parametrize(
    "forbidden",
    [
        "index_stock_cons_csindex",  # 成分必须走中证官方，不走 akshare
        "stock_individual_info_em",
        "stock_zh_index_daily",
    ],
)
def test_calling_non_allowlisted_function_fails_closed(forbidden: str) -> None:
    with pytest.raises(ProviderConfigError, match="白名单"):
        call_akshare(forbidden)


def test_error_message_lists_the_allowed_functions() -> None:
    with pytest.raises(ProviderConfigError) as exc:
        call_akshare("stock_zh_a_daily")
    message = str(exc.value)
    for allowed in ALLOWED_AKSHARE_FUNCTIONS:
        assert allowed in message


@pytest.mark.asyncio
async def test_daily_upstream_failure_does_not_call_another_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fail(function_name: str, /, **kwargs: object) -> list[dict[str, object]]:
        calls.append(function_name)
        raise ProviderUpstreamError("daily unavailable")

    monkeypatch.setattr(client, "acall_akshare", fail)
    with pytest.raises(ProviderUpstreamError, match="daily unavailable"):
        await client.fetch_daily("000002", date(2026, 7, 1), date(2026, 7, 14))

    assert calls == ["stock_zh_a_hist"]


@pytest.mark.asyncio
async def test_minute_upstream_failure_does_not_call_another_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fail(function_name: str, /, **kwargs: object) -> list[dict[str, object]]:
        calls.append(function_name)
        raise ProviderUpstreamError("minute unavailable")

    monkeypatch.setattr(client, "acall_akshare", fail)
    shanghai = ZoneInfo("Asia/Shanghai")
    with pytest.raises(ProviderUpstreamError, match="minute unavailable"):
        await client.fetch_minute(
            "000002",
            datetime(2026, 7, 14, 9, 30, tzinfo=shanghai),
            datetime(2026, 7, 14, 15, 0, tzinfo=shanghai),
        )

    assert calls == ["stock_zh_a_hist_min_em"]
