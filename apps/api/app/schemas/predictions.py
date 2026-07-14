"""PredictionDTO / ScorecardDTO（spec §7.4）。

概率与区间必须同时出现，不允许只显示「看涨/看跌」（spec §13.2）；
每个预测区域都带「仅供研究，不构成投资建议」。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from apps.api.app.core.enums import (
    RESEARCH_ONLY_DISCLAIMER,
    ConfidenceLabel,
    PredictionHorizon,
)
from apps.api.app.schemas.common import BaseDTO

ScorecardWindow = Literal["20", "100", "all"]


class ReturnIntervalDTO(BaseDTO):
    """20%/80% 分位区间（spec §3.3）。"""

    p20: float
    p80: float

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.p20 > self.p80:
            raise ValueError(f"区间下界 {self.p20} 大于上界 {self.p80}")
        return self


class PredictionModelRefDTO(BaseDTO):
    key: str
    version: str
    better_than_baseline: bool = Field(
        description=(
            "当且仅当候选模型的 Brier Score 与 MAE 在同一测试窗口上均严格优于"
            "恒定概率与历史均值基准（spec §9.3.1）。由训练侧写入 model_versions.validation_metrics，"
            "API 只读取，不重算"
        )
    )


class PredictionDTO(BaseDTO):
    id: uuid.UUID
    symbol: str
    horizon: PredictionHorizon
    as_of: datetime
    target_at: datetime = Field(
        description="结算目标时刻。next_5d 严格指向第 5 个后续**交易日**收盘（验收 §15.7）"
    )
    data_cutoff: datetime
    reference_price: float = Field(
        description=(
            "today_close 固定为昨收（同日多次预测目标定义不变）；"
            "next_5d 为各自 as_of 时点最新有效价格（spec §7.4）"
        )
    )
    probability_up: float = Field(ge=0.0, le=1.0)
    expected_return: float
    return_interval: ReturnIntervalDTO
    confidence: ConfidenceLabel
    model: PredictionModelRefDTO
    disclaimer: str = Field(default=RESEARCH_ONLY_DISCLAIMER)

    @model_validator(mode="after")
    def _baseline_caps_confidence(self) -> Self:
        """spec §9.4：未优于基准的模型置信度只能为 low。

        fail closed：不允许出现「未优于基准却标 medium/high」的预测被展示。
        """
        if not self.model.better_than_baseline and self.confidence is not ConfidenceLabel.LOW:
            raise ValueError(
                f"预测 {self.id}：better_than_baseline=false 时置信度只能为 low，"
                f"实际为 {self.confidence}（spec §9.4）"
            )
        return self


class PredictionResponse(PredictionDTO):
    """spec §7.4 的响应是裸对象；§7 要求所有响应带 request_id，故追加该字段。"""

    request_id: str


class ScorecardDTO(BaseDTO):
    """预测成绩单（spec §7.4）。

    ``settled_count + pending_count == eligible_count``；
    尚未到目标时间的预测**不进入分母**。
    """

    model_key: str
    window: int | Literal["all"]
    eligible_count: int = Field(ge=0, description="目标时间已到的预测数")
    settled_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    direction_accuracy: float = Field(ge=0.0, le=1.0)
    mae: float = Field(ge=0.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    baseline_direction_accuracy: float = Field(ge=0.0, le=1.0)
    baseline_mae: float = Field(ge=0.0)
    baseline_brier_score: float = Field(ge=0.0, le=1.0)
    better_than_baseline: bool
    calculated_at: datetime

    @model_validator(mode="after")
    def _counts_add_up(self) -> Self:
        if self.settled_count + self.pending_count != self.eligible_count:
            raise ValueError(
                f"settled_count({self.settled_count}) + pending_count({self.pending_count}) "
                f"!= eligible_count({self.eligible_count})（spec §7.4）"
            )
        return self
