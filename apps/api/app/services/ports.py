"""API 层对外部模块的**端口**（Protocol）。

apps/api 只做「权限边界、输入校验、DTO 和业务编排」，**不得包含训练算法或采集解析器**
（spec §5.1）。凡是需要量化算法的能力（历史相似行情），都调用 ``services/prediction``，
由 FastAPI 依赖注入具体实现：

- 生产实现：``services.prediction.analogs.finder.find_analogs``；
- 测试：注入确定性 stub（spec §16.1 允许端到端测试使用固定模型 Stub），
  但 stub 必须返回**真实的** ``AnalogResult`` 类型，不另造一套影子数据结构。

端口签名与真实实现逐字对齐；缺件时不降级、不返回假数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from services.prediction.analogs.finder import AnalogResult


class AnalogFinder(Protocol):
    """``services.prediction.analogs.finder.find_analogs`` 的调用契约。

    只使用 ``as_of`` 当时可见的 point-in-time 特征，不读取目标期数据（spec §10）。
    """

    async def __call__(
        self,
        db: AsyncSession,
        *,
        symbol: str,
        horizon: str,
        as_of: datetime,
        limit: int,
    ) -> AnalogResult: ...
