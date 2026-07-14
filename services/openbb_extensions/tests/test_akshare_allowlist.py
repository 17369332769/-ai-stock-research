"""spec §5.2 硬约束：AKShare 扩展只允许调用 4 个 akshare 函数。

这不是文档约定 —— ``client.call_akshare`` 有运行时白名单，越权直接抛错。
本测试同时锁死"白名单集合本身"，任何人往里加函数都会让测试红。
"""

from __future__ import annotations

import pytest

from services.openbb_extensions.akshare_provider.client import (
    ALLOWED_AKSHARE_FUNCTIONS,
    PINNED_AKSHARE_VERSION,
    call_akshare,
)
from services.openbb_extensions.akshare_provider.constants import ProviderConfigError

pytestmark = pytest.mark.contract


def test_allowlist_is_exactly_the_four_spec_functions() -> None:
    assert {
        "stock_zh_a_spot_em",
        "stock_zh_a_hist",
        "stock_zh_a_hist_min_em",
        "stock_news_em",
    } == ALLOWED_AKSHARE_FUNCTIONS


def test_akshare_version_is_pinned() -> None:
    assert PINNED_AKSHARE_VERSION == "1.18.64"


@pytest.mark.parametrize(
    "forbidden",
    [
        "stock_zh_a_daily",  # 另一个行情接口 —— 口径不同，禁止
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
