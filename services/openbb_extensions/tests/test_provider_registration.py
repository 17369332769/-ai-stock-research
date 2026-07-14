"""三个自定义 Provider 的 OpenBB 装配冒烟测试。

⚠️ 需要 ``openbb-core`` 才能跑：未安装时 **skip**（不是 pass）。
这样"没验证"就是"没验证"，不会被一个恒绿的空测试掩盖 —— 见 docs/data-sources.md「未验证事项」。
"""

from __future__ import annotations

import pytest

pytest.importorskip("openbb_core", reason="未安装 openbb-core：Provider 注册未经验证")

pytestmark = pytest.mark.contract


def test_akshare_provider_exposes_expected_fetchers() -> None:
    from services.openbb_extensions.akshare_provider.provider import akshare_provider

    assert akshare_provider.name == "akshare"
    assert set(akshare_provider.fetcher_dict) == {
        "EquityQuote",
        "EquityHistorical",
        "CompanyNews",
    }


def test_cn_disclosure_provider_exposes_expected_fetchers() -> None:
    from services.openbb_extensions.cn_disclosure_provider.provider import cn_disclosure_provider

    assert cn_disclosure_provider.name == "cn_disclosure"
    assert set(cn_disclosure_provider.fetcher_dict) == {"CompanyNews"}


def test_csi300_provider_exposes_expected_fetchers() -> None:
    from services.openbb_extensions.csi300_provider.provider import csi300_provider

    assert csi300_provider.name == "csi300"
    assert set(csi300_provider.fetcher_dict) == {"IndexConstituents"}


def test_providers_require_no_credentials() -> None:
    """三个源都是免费公开数据 —— 没有 API key，也不该有。"""
    from services.openbb_extensions.akshare_provider.provider import akshare_provider
    from services.openbb_extensions.cn_disclosure_provider.provider import cn_disclosure_provider
    from services.openbb_extensions.csi300_provider.provider import csi300_provider

    for provider in (akshare_provider, cn_disclosure_provider, csi300_provider):
        assert not provider.credentials
