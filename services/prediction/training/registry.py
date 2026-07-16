"""模型注册表：``model_versions`` 表的写入与查询（spec §6 / §9.4）。

两条铁律：
1. **candidate 永远不对 API 提供预测**（spec §9.4）。``active_model`` 只查 status='active'。
2. 未通过发布门槛的候选**不得**被激活 —— ``activate`` 会拒绝，而不是"警告后继续"。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import ModelStatus
from apps.api.app.core.errors import ModelUnavailable
from apps.api.app.models.tables import ModelVersion
from services.prediction.training.artifacts import ArtifactBundle
from services.prediction.training.trainer import TrainedModel

__all__ = [
    "ActiveModel",
    "activate",
    "active_model",
    "model_key_for_horizon",
    "register_candidate",
    "retire",
]

# 模型 key 与 horizon 的固定映射（spec §7.4 的示例用的就是这两个 key）
MODEL_KEYS: dict[str, str] = {
    "today_close": "a_share_today_lightgbm",
    "next_5d": "a_share_5d_lightgbm",
}


def model_key_for_horizon(horizon: str) -> str:
    try:
        return MODEL_KEYS[horizon]
    except KeyError as exc:
        raise ValueError(f"未知 horizon：{horizon!r}") from exc


@dataclass(frozen=True, slots=True)
class ActiveModel:
    """推理侧需要的一切。``validation_metrics`` 里冻结着置信度判定的输入。"""

    id: uuid.UUID
    model_key: str
    version: str
    target_horizon: str
    artifact_uri: str
    feature_schema: dict[str, Any]
    validation_metrics: dict[str, Any]

    @property
    def feature_set_version(self) -> str:
        return str(self.feature_schema.get("feature_set_version", ""))

    @property
    def feature_set_sha256(self) -> str:
        return str(self.feature_schema.get("feature_set_sha256", ""))

    @property
    def better_than_baseline(self) -> bool:
        return bool(self.validation_metrics.get("better_than_baseline", False))

    @property
    def validation_predictions(self) -> int:
        return int(self.validation_metrics.get("validation_predictions", 0))

    @property
    def required_validation_predictions(self) -> int:
        return int(self.validation_metrics.get("required_validation_predictions", 0))

    @property
    def calibration_acceptable(self) -> bool:
        return bool(self.validation_metrics.get("calibration_acceptable", False))


async def register_candidate(
    session: AsyncSession,
    *,
    model: TrainedModel,
    bundle: ArtifactBundle,
    train_start: date,
    train_end: date,
) -> uuid.UUID:
    """写入一个 **candidate**。永远不直接写 active —— 激活是独立、显式的一步。"""
    row = ModelVersion(
        id=uuid.uuid4(),
        model_key=model.model_key,
        version=model.version,
        target_horizon=model.target_horizon,
        feature_schema={
            "feature_set_version": model.feature_set_version,
            "feature_set_sha256": model.feature_set_sha256,
            "names": list(model.feature_names),
            "unavailable_features": list(model.unavailable_features),
        },
        train_start=train_start,
        train_end=train_end,
        validation_metrics=model.metrics_json(),
        artifact_uri=bundle.artifact_uri,
        status=ModelStatus.CANDIDATE.value,
    )
    session.add(row)
    await session.flush()
    return row.id


async def activate(
    session: AsyncSession, *, model_key: str, version: str, now: datetime
) -> uuid.UUID:
    """把 candidate 升为 active，并把同 key 的旧 active 退役。

    发布门槛不过 → 拒绝激活（spec §9.4：泄漏测试失败 / 验证覆盖不足 / 指标非有限
    的候选**不得**成为 active）。002 进一步要求候选必须优于基准后才能激活。
    """
    stmt = select(ModelVersion).where(
        ModelVersion.model_key == model_key, ModelVersion.version == version
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ModelUnavailable(f"模型版本不存在：{model_key}/{version}")

    gate = row.validation_metrics.get("release_gate", {})
    if not gate.get("passed", False):
        reasons = gate.get("reasons") or ["未记录发布门槛结果"]
        raise ModelUnavailable(
            f"{model_key}/{version} 未通过发布门槛，拒绝激活：{reasons}"
        )
    if not bool(row.validation_metrics.get("better_than_baseline", False)):
        raise ModelUnavailable(f"{model_key}/{version} 未优于基准模型，拒绝激活")

    await session.execute(
        update(ModelVersion)
        .where(
            ModelVersion.model_key == model_key,
            ModelVersion.status == ModelStatus.ACTIVE.value,
            ModelVersion.id != row.id,
        )
        .values(status=ModelStatus.RETIRED.value)
    )
    row.status = ModelStatus.ACTIVE.value
    await session.flush()
    return row.id


async def retire(session: AsyncSession, *, model_key: str, version: str) -> None:
    await session.execute(
        update(ModelVersion)
        .where(ModelVersion.model_key == model_key, ModelVersion.version == version)
        .values(status=ModelStatus.RETIRED.value)
    )


async def active_model(session: AsyncSession, *, horizon: str) -> ActiveModel:
    """取该 horizon 当前 active 的模型。没有 → ``ModelUnavailable``（绝不退回 candidate）。"""
    model_key = model_key_for_horizon(horizon)
    stmt = (
        select(ModelVersion)
        .where(
            ModelVersion.model_key == model_key,
            ModelVersion.target_horizon == horizon,
            ModelVersion.status == ModelStatus.ACTIVE.value,  # candidate 永不服务 API
        )
        .order_by(ModelVersion.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ModelUnavailable(f"{horizon} 没有 active 模型版本，无法生成预测")
    return ActiveModel(
        id=row.id,
        model_key=row.model_key,
        version=row.version,
        target_horizon=row.target_horizon,
        artifact_uri=row.artifact_uri,
        feature_schema=dict(row.feature_schema),
        validation_metrics=dict(row.validation_metrics),
    )
