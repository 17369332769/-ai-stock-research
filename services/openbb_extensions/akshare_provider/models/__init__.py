"""AKShare Provider 的 OpenBB Fetcher 集合。

本包 import ``openbb_core``；纯映射逻辑在上一层的 ``transform.py``（不 import openbb_core），
以便无 OpenBB 环境也能跑确定性契约测试（spec §16.1）。
"""

from __future__ import annotations

from .company_news import AKShareCompanyNewsFetcher
from .equity_historical import AKShareEquityHistoricalFetcher
from .equity_quote import AKShareEquityQuoteFetcher

__all__ = [
    "AKShareCompanyNewsFetcher",
    "AKShareEquityHistoricalFetcher",
    "AKShareEquityQuoteFetcher",
]
