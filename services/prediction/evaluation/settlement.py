"""预测结算（spec §3.4 / §7.4，验收 §15.7、§15.8）。

- ``today_close``：当日收盘数据确认后结算。
- ``next_5d``：**第 5 个后续交易日**收盘后结算。目标日在生成预测时就已用交易日历算好写进
  ``target_at`` —— 结算只认它，不会拿自然日再数一遍（节假日必然错）。
- **幂等**：``prediction_outcomes`` 主键是 prediction_id，重复结算 ON CONFLICT DO NOTHING。
- **账本不可覆盖**：这里只 INSERT outcome，绝不 UPDATE predictions。

复权边界：实际收益必须和参考价算在**同一个复权基准**上。
参考价当时的锚点（anchor_session / anchor_close_at_as_of）存在 features_snapshot.reference 里，
结算时用"现在读到的锚点收盘价 / 当时的锚点收盘价"求出缩放因子，把参考价搬到当前基准上再算收益。
期间除权多少次都不影响结果。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai, trading_day_of
from apps.api.app.models.tables import Prediction, PredictionOutcome
from services.prediction.features.repository import load_session_close
from services.prediction.inference.reference_price import ReferencePrice

__all__ = ["SettlementOutcome", "SettlementStats", "settle_due_predictions"]


@dataclass(frozen=True, slots=True)
class SettlementOutcome:
    prediction_id: uuid.UUID
    symbol: str
    horizon: str
    actual_price: float
    actual_return: float
    direction_correct: bool
    absolute_error: float
    settled_at: datetime


@dataclass(slots=True)
class SettlementStats:
    due: int = 0
    settled: int = 0
    already_settled: int = 0
    waiting_for_bar: int = 0
    rejected: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "due": self.due,
            "settled": self.settled,
            "already_settled": self.already_settled,
            "waiting_for_bar": self.waiting_for_bar,
            "rejected": self.rejected,
        }


async def settle_due_predictions(
    session: AsyncSession, *, now: datetime, limit: int = 5000
) -> tuple[list[SettlementOutcome], SettlementStats]:
    """结算所有 ``target_at <= now`` 且尚无 outcome 的预测。"""
    moment = to_shanghai(now)
    stats = SettlementStats()

    stmt = (
        select(Prediction)
        .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(
            Prediction.target_at <= moment,  # 未到目标时间的预测不结算，也不进分母
            PredictionOutcome.prediction_id.is_(None),
        )
        .order_by(Prediction.target_at.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    stats.due = len(rows)

    outcomes: list[SettlementOutcome] = []
    for row in rows:
        outcome = await _settle_one(session, row, moment, stats)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes, stats


async def _settle_one(
    session: AsyncSession, row: Prediction, moment: datetime, stats: SettlementStats
) -> SettlementOutcome | None:
    target_session = trading_day_of(row.target_at)
    target_close = await load_session_close(session, row.symbol, target_session)
    if target_close is None or target_close <= 0:
        # 收盘数据还没确认（日线 15:10 才写）——留到下一轮（次日 08:30 补偿）
        stats.waiting_for_bar += 1
        return None

    reference_on_current_basis = await _reference_on_current_basis(session, row)
    if reference_on_current_basis is None:
        stats.rejected += 1
        return None

    actual_return = target_close / reference_on_current_basis - 1
    expected_return = float(row.expected_return)
    probability_up = float(row.probability_up)

    # 预测方向 = probability_up >= 0.5；实际方向 = 实际收益 > 0（spec §9.1）
    direction_correct = (probability_up >= 0.5) == (actual_return > 0)
    absolute_error = abs(actual_return - expected_return)

    statement = (
        pg_insert(PredictionOutcome)
        .values(
            prediction_id=row.id,
            actual_price=Decimal(str(round(target_close, 4))),
            actual_return=Decimal(str(round(actual_return, 8))),
            direction_correct=direction_correct,
            absolute_error=Decimal(str(round(absolute_error, 8))),
            settled_at=moment,
        )
        .on_conflict_do_nothing(index_elements=["prediction_id"])  # 幂等
        .returning(PredictionOutcome.prediction_id)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    if inserted is None:
        stats.already_settled += 1
        return None

    stats.settled += 1
    return SettlementOutcome(
        prediction_id=row.id,
        symbol=row.symbol,
        horizon=row.horizon,
        actual_price=target_close,
        actual_return=actual_return,
        direction_correct=direction_correct,
        absolute_error=absolute_error,
        settled_at=moment,
    )


async def _reference_on_current_basis(session: AsyncSession, row: Prediction) -> float | None:
    """把当时的参考价搬到**当前**复权基准上。

    没有锚点信息（例如极早期写入的预测）时，退回直接使用 reference_price ——
    但只在参考价本身就是复权收盘价的情况下才成立，所以要求 features_snapshot 里有 reference。
    读不到锚点就拒绝结算（记 rejected），绝不"猜一个基准"算出一个错误的收益率。
    """
    snapshot: dict[str, Any] = dict(row.features_snapshot or {})
    raw_reference = snapshot.get("reference")
    if not isinstance(raw_reference, dict):
        return None

    try:
        reference = ReferencePrice.from_json(raw_reference)
    except (KeyError, ValueError):
        return None

    anchor_close_now = await load_session_close(session, row.symbol, reference.anchor_session)
    if anchor_close_now is None or anchor_close_now <= 0:
        return None
    if reference.anchor_close_at_as_of <= 0:
        return None

    # 除权会把整条 qfq 历史按同一个倍数重标定；这个比值就是那个倍数。
    rescale = anchor_close_now / reference.anchor_close_at_as_of
    result = reference.price_on_as_of_basis * rescale
    return result if result > 0 else None
