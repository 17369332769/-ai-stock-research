"""预测账本仓储。

**只读 + 只追加**：这里刻意不提供任何 update 方法 —— 原始预测创建后不可覆盖，
模型更新必须创建新的模型版本（spec §3.4 / 验收 §15.8）。结算只往
``prediction_outcomes`` 追加行，由 services/prediction 负责。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.core.errors import InvalidArgument
from apps.api.app.core.pagination import Cursor
from apps.api.app.models.tables import ModelVersion, Prediction, PredictionOutcome

PREDICTION_SORT_KEY = "as_of"


def _parse_cursor(cursor: Cursor) -> tuple[datetime, uuid.UUID]:
    try:
        moment = datetime.fromisoformat(cursor.value)
        row_id = uuid.UUID(cursor.id)
    except ValueError as exc:
        raise InvalidArgument("游标字段无效") from exc
    if moment.tzinfo is None:
        raise InvalidArgument("游标时间必须带时区")
    return moment, row_id


async def latest(
    session: AsyncSession,
    symbol: str,
    horizon: PredictionHorizon,
    *,
    as_of_not_before: datetime | None = None,
) -> Prediction | None:
    """最新一条预测。

    ``as_of_not_before`` 用于 today_close：只有**当前交易日**的今日预测才算数，
    否则会把昨天那条已结算的预测当成"今天的预测"展示（spec §9.1）。
    """
    stmt = (
        select(Prediction)
        .where(Prediction.symbol == symbol, Prediction.horizon == horizon.value)
        .order_by(Prediction.as_of.desc(), Prediction.created_at.desc())
        .limit(1)
    )
    if as_of_not_before is not None:
        stmt = stmt.where(Prediction.as_of >= as_of_not_before)

    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def latest_ids_per_horizon(session: AsyncSession, symbol: str) -> list[uuid.UUID]:
    """快照里的 ``latest_predictions``：每个 horizon 最新一条（spec §7.2）。"""
    ids: list[uuid.UUID] = []
    for horizon in PredictionHorizon:
        result = await session.execute(
            select(Prediction.id)
            .where(Prediction.symbol == symbol, Prediction.horizon == horizon.value)
            .order_by(Prediction.as_of.desc())
            .limit(1)
        )
        row_id = result.scalar_one_or_none()
        if row_id is not None:
            ids.append(row_id)
    return ids


async def history(
    session: AsyncSession,
    symbol: str,
    horizon: PredictionHorizon,
    *,
    limit: int,
    cursor: Cursor | None = None,
) -> tuple[list[Prediction], bool]:
    stmt = (
        select(Prediction)
        .where(Prediction.symbol == symbol, Prediction.horizon == horizon.value)
        .order_by(Prediction.as_of.desc(), Prediction.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        moment, row_id = _parse_cursor(cursor)
        stmt = stmt.where(
            tuple_(Prediction.as_of, Prediction.id) < tuple_(literal(moment), literal(row_id))
        )

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def eligible_for_scorecard(
    session: AsyncSession,
    model_key: str,
    *,
    now: datetime,
    window: int | None,
) -> list[tuple[Prediction, PredictionOutcome | None]]:
    """成绩单口径（spec §7.4）。

    **eligible = 目标时间已到（target_at <= now）**；尚未到目标时间的预测不进入分母。
    左连接 outcomes：有 outcome ⇒ settled，没有 ⇒ pending（结算积压）。
    ``window=None`` 表示 all。
    """
    stmt = (
        select(Prediction, PredictionOutcome)
        .join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
        .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(ModelVersion.model_key == model_key, Prediction.target_at <= now)
        .order_by(Prediction.as_of.desc(), Prediction.id.desc())
    )
    if window is not None:
        stmt = stmt.limit(window)

    result = await session.execute(stmt)
    # 左连接：没有 outcome 的行 row[1] 就是 None（pending）
    return [(row[0], row[1]) for row in result.all()]


def build_cursor(row: Prediction) -> Cursor:
    return Cursor(sort=PREDICTION_SORT_KEY, value=row.as_of.isoformat(), id=str(row.id))
