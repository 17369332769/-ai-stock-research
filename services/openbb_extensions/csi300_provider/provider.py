"""OpenBB Provider 注册对象（沪深300 成分）。

entry point::

    [project.entry-points."openbb_provider_extension"]
    csi300 = "openbb_csi300.provider:csi300_provider"

REST 路由：``GET /api/v1/index/constituents?provider=csi300&symbol=000300&as_of=2026-07-14``

⚠️ entry point 注册未经实机验证（本机 .venv 未装 openbb）。见 docs/data-sources.md。
"""

from __future__ import annotations

from openbb_core.provider.abstract.provider import Provider

from .models.index_constituents import Csi300IndexConstituentsFetcher

csi300_provider = Provider(
    name="csi300",
    website="https://www.csindex.com.cn",
    description=(
        "沪深300 指数成分，权威来源为中证指数有限公司官方成分文件与指数调整公告。"
        "当前成分用于选股；历史成分快照用于无幸存者偏差的训练取样。"
        "公开数据仅限个人研究，不得重新分发。"
    ),
    credentials=None,
    fetcher_dict={
        "IndexConstituents": Csi300IndexConstituentsFetcher,
    },
    repr_name="China Securities Index (CSI 300)",
)
