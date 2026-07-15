"""系统状态 DTO（数据源 / 模型 / Agent 连接）。

**契约缺口说明**：spec §7 没有为 `/settings/data-sources` 页定义端点，但
§13.1 把该页列为必须页面，§8 又要求「界面展示具体失败源和最后成功时间」，
§9.3.1 要求 PSI 漂移导致的模型不可用要能看见。三者合起来必须有一个只读状态端点。

因此这里补 `GET /api/v1/system/status`，形状与前端 `SystemStatusDTO` 逐字段对齐。
它**只读**：不触发采集、不改任何状态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.core.enums import PredictionHorizon

DataSourceStatus = Literal["ok", "pending", "degraded", "failed"]
ConnectionStatus = Literal["active", "degraded", "unavailable"]


class DataSourceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Worker Provider 标识：csi300 / akshare / cn_disclosure")
    name: str = Field(description="展示名")
    status: DataSourceStatus
    active_source: str = Field(description="该数据源唯一、明确的实际来源标识")
    last_success_at: datetime | None = Field(
        default=None, description="最后一次成功采集时间；从未成功为 null（spec §8）"
    )
    consecutive_failures: int = 0
    next_run_at: datetime | None = None
    coverage: int = 0
    total: int = 0
    job_count: int = 0
    failing_jobs: list[str] = Field(default_factory=list)
    last_error_code: str | None = None
    last_error_message: str | None = Field(
        default=None, description="已脱敏：worker 侧落盘前抹掉密钥（spec §14.3）"
    )


class ModelConnectionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_key: str
    horizon: PredictionHorizon | None = None
    status: ConnectionStatus
    active_version: str | None = None
    better_than_baseline: bool | None = Field(
        default=None, description="未优于基准时前端必须显示「未优于基准」（spec §9.4）"
    )
    last_prediction_at: datetime | None = None
    reason: str | None = Field(
        default=None, description="降级/不可用原因，例如特征 PSI > 0.30（spec §9.3.1）"
    )


class AgentConnectionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model_name: str | None = None
    status: ConnectionStatus
    last_success_at: datetime | None = None
    reason: str | None = None


class SystemStatusDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[DataSourceDTO]
    models: list[ModelConnectionDTO]
    agent: AgentConnectionDTO | None = None
