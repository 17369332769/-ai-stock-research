"""AKShare Provider 的常量、错误类型与时区工具。

纯 stdlib：不 import akshare、不 import openbb_core、不 import 应用代码 ——
Provider 必须能作为独立发行包 ``openbb_akshare`` 安装（spec §5.1：扩展不得保存业务状态）。
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

# ── 溯源（spec §4.2：行情必须保存 source / source_url / observed_at）─────────────
#
# 数据实际由东方财富提供，akshare 只是抓取客户端；因此 source 写明"经由 akshare 取得的
# 东方财富数据"，与 spec §7.2 响应样例中的 "eastmoney_via_akshare" 完全一致。
SOURCE_NAME = "eastmoney_via_akshare"

# 上游原文页面（写入 quotes.source_url / bars.source_url / documents.source_url）
EASTMONEY_QUOTE_URL = "https://quote.eastmoney.com/{market}{symbol}.html"
EASTMONEY_SEARCH_URL = "https://so.eastmoney.com/news/s?keyword={symbol}"

# ── 单位口径（写入 docs/data-sources.md，禁止在别处改口径）────────────────────
#
# 成交量：手（1 手 = 100 股）—— spot / daily / 5m 三个接口口径一致。
# 成交额：元。
VOLUME_UNIT = "hand"  # 手
AMOUNT_UNIT = "CNY"

# 复权方式：日线/分钟线固定前复权（qfq）。禁止在同一张表里混用复权口径（spec §5.2）。
DEFAULT_ADJUSTMENT = "qfq"
ALLOWED_ADJUSTMENTS = frozenset({"qfq", "hfq", ""})

# akshare 的分钟周期取值；MVP 只用 5 分钟（spec §6 bars.timeframe CHECK IN ('5m','1d')）。
MINUTE_PERIOD = "5"

# 交易时段（Asia/Shanghai）。分钟线过滤掉 09:30 之前与 15:00 之后的异常行。
SESSION_OPEN_HHMM = (9, 30)
SESSION_CLOSE_HHMM = (15, 0)

# 日线 bar_time 固定落在收盘时刻：日线在收盘前不可知，落 15:00 才是 point-in-time 安全的
# （spec §4.2：训练与回测禁止使用预测时点之后发布的数据）。
DAILY_BAR_CLOSE_HHMM = (15, 0)


class AKShareProviderError(RuntimeError):
    """AKShare Provider 的错误基类。"""


class ProviderConfigError(AKShareProviderError):
    """调用了白名单之外的 akshare 函数，或参数非法。"""


class ProviderDataError(AKShareProviderError):
    """上游返回的数据形态不符合契约：缺列、类型改变、脏值。

    **不吞掉、不猜测、不用默认值填充** —— 直接抛出，让上层进入 stale/unavailable
    （spec §5.2：异常数据直接拒收）。
    """


class ProviderUpstreamError(AKShareProviderError):
    """上游不可用：网络错误、限流、超时、5xx。"""


def market_prefix(symbol: str) -> str:
    """A 股代码 → 东方财富行情页前缀（sh / sz）。"""
    if symbol.startswith(("6", "9")):
        return "sh"
    if symbol.startswith(("0", "2", "3")):
        return "sz"
    raise ProviderDataError(f"无法判定交易所：{symbol!r} 不是沪深 A 股代码")


def quote_url(symbol: str) -> str:
    return EASTMONEY_QUOTE_URL.format(market=market_prefix(symbol), symbol=symbol)


def news_url(symbol: str) -> str:
    return EASTMONEY_SEARCH_URL.format(symbol=symbol)
