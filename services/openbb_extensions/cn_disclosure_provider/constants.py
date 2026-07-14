"""中国法定披露 Provider 的常量与错误类型。纯 stdlib。"""

from __future__ import annotations

from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

# ── 法定披露来源白名单（spec §5.2：只返回巨潮 / 上交所 / 深交所原文）───────────────
SOURCE_CNINFO = "cninfo"  # 巨潮资讯（证监会指定信息披露平台）
SOURCE_SSE = "sse"  # 上海证券交易所
SOURCE_SZSE = "szse"  # 深圳证券交易所
ALLOWED_SOURCES: frozenset[str] = frozenset({SOURCE_CNINFO, SOURCE_SSE, SOURCE_SZSE})

# MVP 实现的唯一来源：巨潮资讯。
# 巨潮同时覆盖沪深两市且是法定披露平台，因此**不需要**在沪/深所之间做静默切换 ——
# spec §5.2 明令禁止静默备用源：巨潮失败即 unavailable，不改口径去打交易所站点。
PRIMARY_SOURCE = SOURCE_CNINFO

CNINFO_BASE = "http://www.cninfo.com.cn"
CNINFO_QUERY_URL = f"{CNINFO_BASE}/new/hisAnnouncement/query"
CNINFO_ORG_LOOKUP_URL = f"{CNINFO_BASE}/new/information/topSearch/query"
CNINFO_STATIC_BASE = "http://static.cninfo.com.cn"
CNINFO_DETAIL_URL = f"{CNINFO_BASE}/new/disclosure/detail"
CNINFO_LIST_REFERER = f"{CNINFO_BASE}/new/commonUrl?url=disclosure/list/notice"

# 上交所 / 深交所官方公告入口 —— **当前未接线**，仅登记在 docs/data-sources.md 备查。
# 接线前不得出现在任何 fallback 路径里。
SSE_ANNOUNCEMENT_URL = "http://www.sse.com.cn/disclosure/listedinfo/announcement/"
SZSE_ANNOUNCEMENT_URL = "https://www.szse.cn/disclosure/listed/notice/index.html"

# 巨潮的板块参数：沪市 sse，深市 szse
COLUMN_SSE = "sse"
COLUMN_SZSE = "szse"

PAGE_SIZE = 30
MAX_PAGES = 20  # 硬上限，防止上游 hasMore 永真导致无限翻页

# 频率限制：巨潮无公开配额，实测对高频访问会 403/限流。这里的节流参数写进 docs。
REQUEST_INTERVAL_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 30.0

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)


class DisclosureProviderError(RuntimeError):
    """法定披露 Provider 错误基类。"""


class ProviderConfigError(DisclosureProviderError):
    """参数非法。"""


class ProviderDataError(DisclosureProviderError):
    """上游数据形态不符合契约：缺字段、类型改变、脏值。"""


class ProviderUpstreamError(DisclosureProviderError):
    """上游不可用：网络错误、限流、超时、非 2xx。"""


def column_for(symbol: str) -> str:
    """A 股代码 → 巨潮板块参数。"""
    if symbol.startswith(("6", "9")):
        return COLUMN_SSE
    if symbol.startswith(("0", "2", "3")):
        return COLUMN_SZSE
    raise ProviderConfigError(f"无法判定交易所：{symbol!r} 不是沪深 A 股代码")
