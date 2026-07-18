"""训练前数据质量审计；不满足硬门槛时拒绝进入训练。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import CSI300_CODE, Timeframe
from apps.api.app.models.tables import Bar, UniverseMembership

MIN_DAILY_SESSIONS = 250
EXPECTED_CSI300_MEMBERS = 300


@dataclass(frozen=True, slots=True)
class TrainingDataAudit:
    as_of: date
    daily_rows: int
    daily_symbols: int
    daily_start: datetime | None
    daily_end: datetime | None
    minimum_daily_sessions: int
    insufficient_symbols: tuple[str, ...]
    invalid_ohlcv_rows: int
    adjustments: dict[str, int]
    current_members: int
    membership_periods: int
    membership_start: date | None
    minute_rows: int
    minute_symbols: int
    minute_start: datetime | None
    minute_end: datetime | None
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_json(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "ready": self.ready,
            "daily": {
                "rows": self.daily_rows,
                "symbols": self.daily_symbols,
                "start": self.daily_start.isoformat() if self.daily_start else None,
                "end": self.daily_end.isoformat() if self.daily_end else None,
                "minimum_sessions": self.minimum_daily_sessions,
                "insufficient_symbols": list(self.insufficient_symbols),
                "invalid_ohlcv_rows": self.invalid_ohlcv_rows,
                "adjustments": self.adjustments,
            },
            "membership": {
                "current_members": self.current_members,
                "periods": self.membership_periods,
                "start": self.membership_start.isoformat() if self.membership_start else None,
            },
            "minute": {
                "rows": self.minute_rows,
                "symbols": self.minute_symbols,
                "start": self.minute_start.isoformat() if self.minute_start else None,
                "end": self.minute_end.isoformat() if self.minute_end else None,
            },
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


async def audit_training_data(session: AsyncSession, *, as_of: date) -> TrainingDataAudit:
    daily = (
        await session.execute(
            select(
                func.count(),
                func.count(distinct(Bar.symbol)),
                func.min(Bar.bar_time),
                func.max(Bar.bar_time),
            ).where(Bar.timeframe == Timeframe.DAY1.value)
        )
    ).one()
    coverage_rows = (
        await session.execute(
            select(Bar.symbol, func.count(distinct(func.date(Bar.bar_time))))
            .where(Bar.timeframe == Timeframe.DAY1.value)
            .group_by(Bar.symbol)
        )
    ).all()
    coverage = {str(symbol): int(count) for symbol, count in coverage_rows}
    minimum_sessions = min(coverage.values(), default=0)
    insufficient = tuple(
        sorted(symbol for symbol, count in coverage.items() if count < MIN_DAILY_SESSIONS)
    )
    invalid_ohlcv = int(
        (
            await session.execute(
                select(func.count()).select_from(Bar).where(
                    Bar.timeframe == Timeframe.DAY1.value,
                    or_(
                        Bar.open <= 0,
                        Bar.high <= 0,
                        Bar.low <= 0,
                        Bar.close <= 0,
                        Bar.volume < 0,
                        Bar.high < Bar.low,
                        Bar.high < Bar.open,
                        Bar.high < Bar.close,
                        Bar.low > Bar.open,
                        Bar.low > Bar.close,
                    ),
                )
            )
        ).scalar_one()
        or 0
    )
    adjustment_rows = (
        await session.execute(
            select(Bar.adjustment, func.count())
            .where(Bar.timeframe == Timeframe.DAY1.value)
            .group_by(Bar.adjustment)
        )
    ).all()
    adjustments = {str(name): int(count) for name, count in adjustment_rows}

    current_members = int(
        (
            await session.execute(
                select(func.count(distinct(UniverseMembership.symbol))).where(
                    UniverseMembership.universe_code == CSI300_CODE,
                    UniverseMembership.effective_from <= as_of,
                    or_(
                        UniverseMembership.effective_to.is_(None),
                        UniverseMembership.effective_to >= as_of,
                    ),
                )
            )
        ).scalar_one()
        or 0
    )
    membership = (
        await session.execute(
            select(func.count(), func.min(UniverseMembership.effective_from)).where(
                UniverseMembership.universe_code == CSI300_CODE
            )
        )
    ).one()
    periods = int(membership[0] or 0)
    membership_start = membership[1]
    minute = (
        await session.execute(
            select(
                func.count(),
                func.count(distinct(Bar.symbol)),
                func.min(Bar.bar_time),
                func.max(Bar.bar_time),
            ).where(Bar.timeframe == Timeframe.MIN5.value)
        )
    ).one()

    blockers: list[str] = []
    warnings: list[str] = []
    if int(daily[0]) == 0:
        blockers.append("日线为空")
    if insufficient:
        warnings.append(
            f"{len(insufficient)} 只股票日线少于 {MIN_DAILY_SESSIONS} 个交易日；"
            "训练自动跳过无法形成完整特征/标签的样本"
        )
    if invalid_ohlcv:
        blockers.append(f"存在 {invalid_ohlcv} 条非法 OHLCV")
    if len(adjustments) != 1:
        blockers.append(f"日线复权口径不唯一：{sorted(adjustments)}")
    if current_members != EXPECTED_CSI300_MEMBERS:
        blockers.append(
            f"当前沪深300成分数量应为 {EXPECTED_CSI300_MEMBERS}，实际 {current_members}"
        )
    if periods == 0:
        blockers.append("缺少沪深300历史成分有效期")
    elif daily[2] is not None and membership_start is not None:
        daily_start = daily[2].date()
        if membership_start > daily_start:
            blockers.append(
                "沪深300历史成分覆盖不足："
                f"成分始于 {membership_start.isoformat()}，"
                f"日线始于 {daily_start.isoformat()}"
            )
    if int(minute[0]) == 0:
        warnings.append("5分钟线为空：日频模型仍可训练，盘中模型保持不可用")
    elif minute[2] is not None and minute[3] is not None:
        minute_days = (minute[3].date() - minute[2].date()).days + 1
        if minute_days < 90:
            warnings.append(f"5分钟线仅覆盖约 {minute_days} 天，不启用盘中模型训练")

    return TrainingDataAudit(
        as_of=as_of,
        daily_rows=int(daily[0]),
        daily_symbols=int(daily[1]),
        daily_start=daily[2],
        daily_end=daily[3],
        minimum_daily_sessions=minimum_sessions,
        insufficient_symbols=insufficient,
        invalid_ohlcv_rows=invalid_ohlcv,
        adjustments=adjustments,
        current_members=current_members,
        membership_periods=periods,
        membership_start=membership_start,
        minute_rows=int(minute[0]),
        minute_symbols=int(minute[1]),
        minute_start=minute[2],
        minute_end=minute[3],
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )
