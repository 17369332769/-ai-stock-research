"""OpenBB 中国法定披露 Provider（巨潮资讯 / 上交所 / 深交所公告原文）。

spec §5.2：法定披露扩展**只返回巨潮、上交所或深交所原文**。因此本 Provider 的 source
取值被 ``constants.ALLOWED_SOURCES`` 白名单锁死；任何其它来源（财经媒体转载、聚合站、
研报）都不得从这里出去 —— 转载稿属于「新闻」，走 akshare_provider。

分层同 akshare_provider：``constants`` / ``transform`` 纯 stdlib 可测；``client`` 触达上游；
``models`` / ``provider`` 装配 OpenBB。entry point 指向 ``openbb_cn_disclosure.provider``。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
