"""OpenBB AKShare Provider（A 股行情 / 日线 / 5 分钟线 / 新闻）。

**这是全项目唯一允许 import akshare 的包之一**（spec §4.2 / §5.1）。业务代码一律经
``services/market_data/openbb_gateway.py`` 走 OpenBB 内部 REST，不得直接调用 akshare。

spec §5.2 硬约束：AKShare 扩展固定使用 ``akshare==1.18.64``，**只允许调用 4 个函数**::

    stock_zh_a_spot_em      # 沪深京 A 股实时行情快照（东方财富）
    stock_zh_a_hist         # 日线（前复权）
    stock_zh_a_hist_min_em  # 分钟线（5 分钟）
    stock_news_em           # 个股新闻

该白名单在 ``client.ALLOWED_AKSHARE_FUNCTIONS`` 中以运行时断言强制，越权调用直接抛错。

模块分层（有意为之，见 docs/data-sources.md）：

- ``constants`` / ``transform``：纯 stdlib，**不 import openbb_core、不 import akshare**，
  因此上游字段映射可以被完全确定性地契约测试（spec §16.1：测试禁止访问公网）。
- ``client``：唯一触达 akshare 的地方。
- ``models`` / ``provider``：OpenBB ``Fetcher`` / ``Provider`` 装配，import ``openbb_core``。

本 ``__init__`` **刻意不 import openbb_core**，以便在未安装 OpenBB 的环境下也能加载
``transform``/``client`` 做契约测试；Provider 对象在 ``provider.py``，
entry point 指向 ``openbb_akshare.provider:akshare_provider``。
"""

from __future__ import annotations

__all__ = ["__version__"]

# 与应用版本同步（spec §5.2：自定义扩展版本与应用版本同步）
__version__ = "0.1.0"
