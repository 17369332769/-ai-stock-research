"""预测目标与标签（spec §9.1）。

- ``today_close``：当日收盘价 / **昨日收盘价** - 1。参考价固定为昨收，
  所以同一天 09:45 / 10:00 / 14:45 生成的多个版本预测的是**同一个目标**，可以横向比较概率变化（spec §7.4）。
- ``next_5d``：**第 5 个后续交易日**收盘价 / 预测参考价 - 1。
  "第 5 个后续交易日"只有一个实现：``apps.api.app.core.trading_calendar.nth_trading_day_after``。
  这里绝不自己数自然日 —— 跨节假日、跨年都靠交易日历。
- 方向标签：目标收益率 **> 0** 记为上涨，否则记为非上涨。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.core.trading_calendar import (
    TODAY_PREDICTION_EARLIEST,
    TradingCalendar,
    nth_trading_day_after,
    session_close_at,
)

__all__ = [
    "NEXT_5D_SESSIONS",
    "Label",
    "compute_label",
    "direction_up",
    "horizon_embargo_sessions",
    "target_session_for",
    "target_time_for",
    "training_cutoff_for",
]

# "一周" = 第 5 个后续交易日（spec §9.1）
NEXT_5D_SESSIONS = 5


def direction_up(target_return: float) -> bool:
    """spec §9.1：目标收益率大于 0 记为上涨，否则（含恰好为 0）记为非上涨。"""
    return target_return > 0


def horizon_embargo_sessions(horizon: str) -> int:
    """walk-forward 的禁运期 = 标签向前看的交易日数。

    next_5d 的标签要用到 t+5 的收盘价，所以训练段末尾与验证段开头必须至少隔 5 个交易日，
    否则训练标签已经"看过"验证期的价格。today_close 的标签当天收盘就实现，隔 1 天即可。
    """
    if horizon == PredictionHorizon.NEXT_5D:
        return NEXT_5D_SESSIONS
    if horizon == PredictionHorizon.TODAY_CLOSE:
        return 1
    raise ValueError(f"未知 horizon：{horizon!r}")


def target_session_for(session: date, horizon: str, calendar: TradingCalendar) -> date:
    """目标交易日。next_5d 用交易日历数 5 个交易日，绝不用自然日。"""
    if horizon == PredictionHorizon.TODAY_CLOSE:
        return session
    if horizon == PredictionHorizon.NEXT_5D:
        return nth_trading_day_after(session, NEXT_5D_SESSIONS, calendar)
    raise ValueError(f"未知 horizon：{horizon!r}")


def target_time_for(session: date, horizon: str, calendar: TradingCalendar) -> datetime:
    """``predictions.target_at``：目标交易日的收盘时刻。"""
    return session_close_at(target_session_for(session, horizon, calendar))


def training_cutoff_for(session: date, horizon: str) -> datetime:
    """训练样本回放时的 data_cutoff。

    - today_close：当日 **09:45**（与线上最早生成时刻一致，spec §3.3）。
      此刻当日日线尚未收盘，因此不可见；只有开盘价与已完成的分钟线可见。
    - next_5d：当日 **收盘 15:00**。参考价 = 当日收盘价。
    """
    if horizon == PredictionHorizon.TODAY_CLOSE:
        return datetime.combine(session, TODAY_PREDICTION_EARLIEST, tzinfo=SHANGHAI)
    if horizon == PredictionHorizon.NEXT_5D:
        return session_close_at(session)
    raise ValueError(f"未知 horizon：{horizon!r}")


@dataclass(frozen=True, slots=True)
class Label:
    session: date
    target_session: date
    reference_price: float
    target_price: float
    target_return: float
    up: bool


def compute_label(
    *,
    session: date,
    horizon: str,
    reference_price: float,
    target_price: float,
    calendar: TradingCalendar,
) -> Label:
    if reference_price <= 0:
        raise ValueError(f"参考价非法：{reference_price}")
    target_session = target_session_for(session, horizon, calendar)
    target_return = target_price / reference_price - 1
    return Label(
        session=session,
        target_session=target_session,
        reference_price=reference_price,
        target_price=target_price,
        target_return=target_return,
        up=direction_up(target_return),
    )


# 供测试与文档引用：今日预测最早时刻（09:45）来自交易日历模块，不在这里重新定义
TODAY_CUTOFF_TIME: time = TODAY_PREDICTION_EARLIEST
