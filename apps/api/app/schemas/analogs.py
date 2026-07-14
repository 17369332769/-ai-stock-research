"""AnalogDTO（spec §7.5 / §10）。

响应必须返回：相似日期、距离分数、当时可见特征、后续实际收益、用于计算距离的特征版本。
只使用当时可见特征（point-in-time），不读取目标期数据；相似案例**不得**被描述为因果关系。
有效候选少于 30 个时关闭该功能并说明样本不足（⇒ 422 INSUFFICIENT_DATA）。
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.schemas.common import BaseDTO

# spec §10：有效候选少于 30 个时关闭该功能
MIN_ANALOG_CANDIDATES = 30


class AnalogDTO(BaseDTO):
    symbol: str
    horizon: PredictionHorizon
    as_of_date: date = Field(description="历史相似日期")
    distance: float = Field(ge=0.0, description="距离分数，越小越相似")
    features: dict[str, float | None] = Field(
        description="当时可见的 point-in-time 特征；某个特征当时算不出来就是 null，不补 0"
    )
    feature_set_version: str = Field(description="用于计算距离的特征版本（config/features/{version}.yaml）")
    forward_return_1d: float | None = Field(
        description="其后 1 个交易日的真实收益；历史数据不足以结算时为 null（不编造 0）"
    )
    forward_return_5d: float | None = Field(
        description="其后 5 个交易日的真实累计收益；不足以结算时为 null"
    )
