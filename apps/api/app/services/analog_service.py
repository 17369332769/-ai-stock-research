"""历史相似行情编排（spec §7.5 / §10）。

距离计算与 point-in-time 特征由 ``services/prediction/analogs`` 负责；
API 只做入参校验、样本量门槛和 DTO 映射 —— 不得包含量化算法（spec §5.1）。

样本门槛：**有效候选少于 30 个时关闭该功能并说明样本不足** ⇒ 422 INSUFFICIENT_DATA。
这里用的是 ``candidates_valid``（真正可比的候选数），不是返回条数 —— 返回 10 条
不代表池子够大。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.core.errors import InstrumentNotFound, InsufficientData
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.schemas.analogs import MIN_ANALOG_CANDIDATES, AnalogDTO
from apps.api.app.services.ports import AnalogFinder


async def get_analogs(
    session: AsyncSession,
    finder: AnalogFinder,
    symbol: str,
    horizon: PredictionHorizon,
    *,
    limit: int,
    now: datetime,
) -> list[AnalogDTO]:
    if await instruments_repo.get(session, symbol) is None:
        raise InstrumentNotFound(symbol)

    result = await finder(
        session, symbol=symbol, horizon=horizon.value, as_of=now, limit=limit
    )

    if result.candidates_valid < MIN_ANALOG_CANDIDATES:
        raise InsufficientData(
            f"{symbol} 的历史相似行情有效候选仅 {result.candidates_valid} 个，"
            f"少于 {MIN_ANALOG_CANDIDATES} 个，该功能已关闭（spec §10）"
        )

    return [
        AnalogDTO(
            symbol=result.symbol,
            horizon=PredictionHorizon(result.horizon),
            as_of_date=analog.session,
            distance=analog.distance,
            features=analog.features,
            feature_set_version=result.feature_set_version,
            forward_return_1d=analog.forward_return_1d,
            forward_return_5d=analog.forward_return_5d,
        )
        for analog in result.analogs
    ]
