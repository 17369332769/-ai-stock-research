"""Point-in-time 可见性判定 —— 本项目的第一死线（spec §4.2 / §9.2 / §16.1）。

三道防线，缺一不可：

1. **SQL 层**：``repository.py`` 的 WHERE 条件只取 cutoff 之前的行。
2. **过滤层**：本模块的 ``visible_*`` 把越界数据显式丢弃（并计数，便于测试断言）。
3. **断言层**：``PitPanel`` 构造时再跑一遍 ``assert_*``，越界即 ``PitViolation``。

``PitViolation`` 是**程序错误**（不是用户错误），所以它不是 ``AppError``：
出现即表示某条数据通路绕过了前两道防线，必须让进程炸掉而不是返回一个"看起来正常"的预测。

关键判定：日线的可见时刻是 **该交易日的收盘（15:00）**，不是 ``bar_time``。
上游可能把日线的 bar_time 写成当日 00:00 —— 那样 09:45 的今日预测就会读到当天的收盘价。
这是最典型、最致命的一类泄漏，必须在类型层面堵死。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.trading_calendar import session_close_at

__all__ = [
    "HasBarTime",
    "HasPublishedAt",
    "PitViolation",
    "assert_no_future_daily_bars",
    "assert_no_future_documents",
    "assert_no_future_minute_bars",
    "daily_bar_visible_at",
    "is_daily_bar_visible",
    "is_document_visible",
    "is_minute_bar_visible",
    "require_aware",
    "visible_daily_bars",
    "visible_documents",
    "visible_minute_bars",
]


class PitViolation(RuntimeError):
    """未来数据进入了 point-in-time 视图。这是 bug，不是可恢复的业务状态。"""


class HasBarTime(Protocol):
    @property
    def bar_time(self) -> datetime: ...


class HasPublishedAt(Protocol):
    @property
    def published_at(self) -> datetime: ...


def require_aware(moment: datetime, label: str) -> datetime:
    """所有 PIT 判定都必须在带时区的时间上做；naive datetime 直接拒绝。"""
    if moment.tzinfo is None:
        raise PitViolation(f"{label} 是 naive datetime，无法做 point-in-time 判定")
    return to_shanghai(moment)


# ── 日线 ────────────────────────────────────────────────────────────────────


def daily_bar_visible_at(bar: HasBarTime) -> datetime:
    """日线真正变得可见的时刻 = 它所属交易日的收盘（15:00 Asia/Shanghai）。"""
    session = require_aware(bar.bar_time, "bar_time").date()
    return session_close_at(session)


def is_daily_bar_visible(bar: HasBarTime, data_cutoff: datetime) -> bool:
    cutoff = require_aware(data_cutoff, "data_cutoff")
    return daily_bar_visible_at(bar) <= cutoff


def visible_daily_bars[BarT: HasBarTime](
    bars: Iterable[BarT], data_cutoff: datetime
) -> tuple[BarT, ...]:
    """只保留在 cutoff 时已经收盘的日线，按时间升序。"""
    cutoff = require_aware(data_cutoff, "data_cutoff")
    kept = [bar for bar in bars if is_daily_bar_visible(bar, cutoff)]
    kept.sort(key=lambda bar: bar.bar_time)
    return tuple(kept)


def assert_no_future_daily_bars(bars: Sequence[HasBarTime], data_cutoff: datetime) -> None:
    cutoff = require_aware(data_cutoff, "data_cutoff")
    for bar in bars:
        visible_at = daily_bar_visible_at(bar)
        if visible_at > cutoff:
            raise PitViolation(
                f"未来日线进入 PIT 视图：bar_time={bar.bar_time.isoformat()} "
                f"收盘于 {visible_at.isoformat()}，晚于 data_cutoff={cutoff.isoformat()}"
            )


# ── 分钟线 ──────────────────────────────────────────────────────────────────


def is_minute_bar_visible(bar: HasBarTime, data_cutoff: datetime) -> bool:
    """bar_time 是分钟 bar 的**结束**时刻，因此 bar_time <= cutoff 即整根 bar 已完成。"""
    cutoff = require_aware(data_cutoff, "data_cutoff")
    return require_aware(bar.bar_time, "bar_time") <= cutoff


def visible_minute_bars[BarT: HasBarTime](
    bars: Iterable[BarT], data_cutoff: datetime
) -> tuple[BarT, ...]:
    cutoff = require_aware(data_cutoff, "data_cutoff")
    kept = [bar for bar in bars if is_minute_bar_visible(bar, cutoff)]
    kept.sort(key=lambda bar: bar.bar_time)
    return tuple(kept)


def assert_no_future_minute_bars(bars: Sequence[HasBarTime], data_cutoff: datetime) -> None:
    cutoff = require_aware(data_cutoff, "data_cutoff")
    for bar in bars:
        bar_time = require_aware(bar.bar_time, "bar_time")
        if bar_time > cutoff:
            raise PitViolation(
                f"未来分钟线进入 PIT 视图：bar_time={bar_time.isoformat()} > "
                f"data_cutoff={cutoff.isoformat()}"
            )


# ── 文档 ────────────────────────────────────────────────────────────────────


def is_document_visible(document: HasPublishedAt, data_cutoff: datetime) -> bool:
    """只允许 published_at <= data_cutoff 的文档（spec §9.2）。

    注意用的是 ``published_at`` 而不是 ``observed_at``：一条昨天发布、今天才被抓到的公告，
    在昨天就已经是公开信息了；反过来，一条明天才发布的公告，即使我们今天"预知"了它，
    也绝不能进入特征。
    """
    cutoff = require_aware(data_cutoff, "data_cutoff")
    return require_aware(document.published_at, "published_at") <= cutoff


def visible_documents[DocT: HasPublishedAt](
    documents: Iterable[DocT], data_cutoff: datetime
) -> tuple[DocT, ...]:
    cutoff = require_aware(data_cutoff, "data_cutoff")
    kept = [doc for doc in documents if is_document_visible(doc, cutoff)]
    kept.sort(key=lambda doc: doc.published_at)
    return tuple(kept)


def assert_no_future_documents(documents: Sequence[HasPublishedAt], data_cutoff: datetime) -> None:
    cutoff = require_aware(data_cutoff, "data_cutoff")
    for doc in documents:
        published_at = require_aware(doc.published_at, "published_at")
        if published_at > cutoff:
            raise PitViolation(
                f"未来文档进入 PIT 视图：published_at={published_at.isoformat()} > "
                f"data_cutoff={cutoff.isoformat()}"
            )
