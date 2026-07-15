"""模型版本仓储。

``candidate`` 状态**永远不对 API 提供预测**（spec §9.4）—— 因此"有没有可用模型"
一律只看 ``status='active'``；没有 active ⇒ 503 MODEL_UNAVAILABLE。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import ModelStatus
from apps.api.app.models.tables import ModelVersion


async def active_for_horizon(session: AsyncSession, horizon: str) -> ModelVersion | None:
    result = await session.execute(
        select(ModelVersion)
        .where(
            ModelVersion.target_horizon == horizon,
            ModelVersion.status == ModelStatus.ACTIVE.value,
        )
        .order_by(ModelVersion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get(session: AsyncSession, model_version_id: uuid.UUID) -> ModelVersion | None:
    result = await session.execute(select(ModelVersion).where(ModelVersion.id == model_version_id))
    return result.scalar_one_or_none()


async def get_many(
    session: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, ModelVersion]:
    if not ids:
        return {}
    result = await session.execute(select(ModelVersion).where(ModelVersion.id.in_(list(ids))))
    return {row.id: row for row in result.scalars().all()}


async def latest_for_scorecard(session: AsyncSession, model_key: str) -> ModelVersion | None:
    """成绩单明确读取该 ``model_key`` 最近创建版本的验证指标。"""
    result = await session.execute(
        select(ModelVersion)
        .where(ModelVersion.model_key == model_key)
        .order_by(ModelVersion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def key_exists(session: AsyncSession, model_key: str) -> bool:
    result = await session.execute(
        select(func.count()).select_from(
            select(ModelVersion.id).where(ModelVersion.model_key == model_key).subquery()
        )
    )
    return (result.scalar_one() or 0) > 0
