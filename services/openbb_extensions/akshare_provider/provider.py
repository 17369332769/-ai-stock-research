"""OpenBB Provider 注册对象。

entry point（见同目录 pyproject.toml）::

    [project.entry-points."openbb_provider_extension"]
    akshare = "openbb_akshare.provider:akshare_provider"

安装后 OpenBB 会把它挂到标准路由上：

    GET /api/v1/equity/price/quote?provider=akshare&symbol=600519
    GET /api/v1/equity/price/historical?provider=akshare&symbol=600519&interval=1d
    GET /api/v1/news/company?provider=akshare&symbol=600519

⚠️ 本机 .venv 尚未装上 openbb==4.7.2，**entry point 注册未经实机验证**；
详见 docs/data-sources.md「未验证事项」。
"""

from __future__ import annotations

from openbb_core.provider.abstract.provider import Provider

from .models.company_news import AKShareCompanyNewsFetcher
from .models.equity_historical import AKShareEquityHistoricalFetcher
from .models.equity_quote import AKShareEquityQuoteFetcher

akshare_provider = Provider(
    name="akshare",
    website="https://akshare.akfamily.xyz",
    description=(
        "A 股行情/K 线/新闻，数据源为东方财富，经 akshare==1.18.64 抓取。"
        "免费公开数据，仅限个人研究，不得重新分发；不保证交易所级实时性。"
    ),
    credentials=None,  # 免费源，无需凭证
    fetcher_dict={
        "EquityQuote": AKShareEquityQuoteFetcher,
        "EquityHistorical": AKShareEquityHistoricalFetcher,
        "CompanyNews": AKShareCompanyNewsFetcher,
    },
    repr_name="AKShare (EastMoney)",
)
