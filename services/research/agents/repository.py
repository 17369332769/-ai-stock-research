"""Agent 的**只读**产品数据库访问层（spec §14.3：Agent 获取的数据只限产品数据库）。

铁律：

* Agent **不**直接访问外部数据源。外部数据必须先经 ``OpenBBGateway`` 规范化落库，
  Agent 只读已落库的数据（spec §4.2）。
* 本层不读本地任意文件、不发外网请求、不写库（只有 ``select``）。
* **每个方法都必须带 ``as_of``，且只返回 ``as_of`` 之前可见的数据**（point-in-time）。
  可见性按两条时间轴同时约束：
    - 行情/K线：``observed_at <= as_of``，K线另加 ``bar_time <= as_of``（未收完的K线不可见）；
    - 文档：``published_at <= as_of`` **且** ``observed_at <= as_of``（发布了但我们还没采到，同样不可见）；
    - 预测：``predictions.as_of <= as_of``，且不取 candidate 模型（spec §9.4）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.models.tables import Bar, Document, ModelVersion, Prediction, Quote

# 单次工具调用的返回上限，防止把整库塞进上下文
MAX_BARS = 240
MAX_DOCUMENTS = 20
# 作业分页的单批上限（不是 LLM 工具，不受上下文限制）
MAX_JOB_DOCUMENTS = 50
# 单篇文档进入 prompt 的正文上限（引文仍以数据库原文为准，截断不影响引文可验证性）
MAX_DOCUMENT_CHARS = 3000


def require_aware(moment: datetime, field: str) -> datetime:
    """所有 datetime 必须带时区（spec §8）。"""
    if moment.tzinfo is None:
        raise ValueError(f"{field} 必须是带时区的 datetime")
    return to_shanghai(moment)


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    symbol: str
    price: Decimal
    previous_close: Decimal
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    volume_ratio: Decimal | None
    source: str
    source_url: str | None
    observed_at: datetime

    @property
    def change_percent(self) -> float | None:
        if self.previous_close == 0:
            return None
        return float(self.price) / float(self.previous_close) - 1.0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": float(self.price),
            "previous_close": float(self.previous_close),
            "change_percent": self.change_percent,
            "open": None if self.open is None else float(self.open),
            "high": None if self.high is None else float(self.high),
            "low": None if self.low is None else float(self.low),
            "volume": None if self.volume is None else float(self.volume),
            "amount": None if self.amount is None else float(self.amount),
            "volume_ratio": None if self.volume_ratio is None else float(self.volume_ratio),
            "source": self.source,
            "source_url": self.source_url,
            "observed_at": self.observed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BarPoint:
    """一根 K 线（5m 或 1d）。异动检测与 Agent 工具共用同一形态。"""

    symbol: str
    timeframe: str
    bar_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "bar_time": self.bar_time.isoformat(),
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": float(self.volume),
            "amount": None if self.amount is None else float(self.amount),
        }


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    """Agent 可见的文档。``body_text`` 是**不可信外部内容**（spec §14.3），渲染进 prompt 前必须隔离。"""

    id: uuid.UUID
    symbol: str | None
    document_type: str
    title: str
    body_text: str | None
    source: str
    source_url: str
    published_at: datetime
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ModelPredictionSnapshot:
    """量化模型的预测。Agent **只能引用，不得修改**这些数值（spec §11.3 / §4.2）。"""

    symbol: str
    horizon: str
    as_of: datetime
    reference_price: Decimal
    probability_up: Decimal
    expected_return: Decimal
    lower_return: Decimal
    upper_return: Decimal
    confidence_label: str
    data_cutoff: datetime
    model_key: str
    model_version: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "as_of": self.as_of.isoformat(),
            "reference_price": float(self.reference_price),
            "probability_up": float(self.probability_up),
            "expected_return": float(self.expected_return),
            "return_interval": {"p20": float(self.lower_return), "p80": float(self.upper_return)},
            "confidence": self.confidence_label,
            "data_cutoff": self.data_cutoff.isoformat(),
            "model": {"key": self.model_key, "version": self.model_version},
            "note": "以上数值来自量化模型，禁止修改或重新估计（spec §4.2）",
        }


class ResearchReadRepository(Protocol):
    """Agent 工具与异动检测唯一的数据入口。实现只允许读产品数据库。"""

    async def get_quote_snapshot(self, symbol: str, as_of: datetime) -> QuoteSnapshot | None: ...

    async def get_recent_bars(
        self, symbol: str, timeframe: str, limit: int, as_of: datetime
    ) -> list[BarPoint]: ...

    async def get_bars_in_range(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, as_of: datetime
    ) -> list[BarPoint]: ...

    async def get_documents(
        self, symbol: str, start: datetime, end: datetime, as_of: datetime, limit: int = MAX_DOCUMENTS
    ) -> list[DocumentSnapshot]: ...

    async def get_documents_observed_after(
        self,
        symbol: str,
        observed_after: datetime | None,
        as_of: datetime,
        limit: int = MAX_JOB_DOCUMENTS,
        after_id: uuid.UUID | None = None,
    ) -> list[DocumentSnapshot]: ...

    async def get_model_prediction(
        self, symbol: str, horizon: str, as_of: datetime
    ) -> ModelPredictionSnapshot | None: ...


class SqlResearchReadRepository:
    """``ResearchReadRepository`` 的 SQLAlchemy 实现。构造时注入会话 → 与调用方同一事务快照。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_quote_snapshot(self, symbol: str, as_of: datetime) -> QuoteSnapshot | None:
        cutoff = require_aware(as_of, "as_of")
        stmt = (
            select(Quote)
            .where(Quote.symbol == symbol, Quote.observed_at <= cutoff)
            .order_by(Quote.observed_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            return None
        return QuoteSnapshot(
            symbol=row.symbol,
            price=row.price,
            previous_close=row.previous_close,
            open=row.open,
            high=row.high,
            low=row.low,
            volume=row.volume,
            amount=row.amount,
            volume_ratio=row.volume_ratio,
            source=row.source,
            source_url=row.source_url,
            observed_at=row.observed_at,
        )

    def _bar_stmt(self, symbol: str, timeframe: str, cutoff: datetime) -> Select[tuple[Bar]]:
        # PIT 双闸：K线时间不得晚于 as_of（未来K线），采集时间也不得晚于 as_of（当时还没采到）
        return select(Bar).where(
            Bar.symbol == symbol,
            Bar.timeframe == timeframe,
            Bar.bar_time <= cutoff,
            Bar.observed_at <= cutoff,
        )

    async def get_recent_bars(
        self, symbol: str, timeframe: str, limit: int, as_of: datetime
    ) -> list[BarPoint]:
        cutoff = require_aware(as_of, "as_of")
        capped = max(1, min(int(limit), MAX_BARS))
        stmt = self._bar_stmt(symbol, timeframe, cutoff).order_by(Bar.bar_time.desc()).limit(capped)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_bar_point(row) for row in reversed(rows)]  # 返回升序

    async def get_bars_in_range(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, as_of: datetime
    ) -> list[BarPoint]:
        cutoff = require_aware(as_of, "as_of")
        lower = require_aware(start, "start")
        upper = min(require_aware(end, "end"), cutoff)  # end 永远不得越过 as_of
        stmt = (
            self._bar_stmt(symbol, timeframe, cutoff)
            .where(Bar.bar_time >= lower, Bar.bar_time <= upper)
            .order_by(Bar.bar_time.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_bar_point(row) for row in rows]

    async def get_documents(
        self, symbol: str, start: datetime, end: datetime, as_of: datetime, limit: int = MAX_DOCUMENTS
    ) -> list[DocumentSnapshot]:
        cutoff = require_aware(as_of, "as_of")
        lower = require_aware(start, "start")
        upper = min(require_aware(end, "end"), cutoff)  # 不允许检索 as_of 之后的文档
        capped = max(1, min(int(limit), MAX_DOCUMENTS))
        stmt = (
            select(Document)
            .where(
                Document.symbol == symbol,
                Document.published_at >= lower,
                Document.published_at <= upper,
                Document.published_at <= cutoff,
                Document.observed_at <= cutoff,
            )
            .order_by(Document.published_at.desc())
            .limit(capped)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            DocumentSnapshot(
                id=row.id,
                symbol=row.symbol,
                document_type=row.document_type,
                title=row.title,
                body_text=row.body_text,
                source=row.source,
                source_url=row.source_url,
                published_at=row.published_at,
                observed_at=row.observed_at,
            )
            for row in rows
        ]

    async def get_documents_observed_after(
        self,
        symbol: str,
        observed_after: datetime | None,
        as_of: datetime,
        limit: int = MAX_JOB_DOCUMENTS,
        after_id: uuid.UUID | None = None,
    ) -> list[DocumentSnapshot]:
        """按**采集时间**升序取"上次分析之后新到的"文档。作业用（不是 Agent 工具）。

        * 用 ``observed_at`` 而不是 ``published_at`` 作为推进轴：晚到的旧公告也不会被漏掉。
        * ``(observed_at, id)`` keyset 分页：同一批采集写入的多篇文档 ``observed_at`` 完全相同，
          若只用 ``observed_at >`` 翻页，批次边界上的同刻文档会被永久跳过。
        """
        cutoff = require_aware(as_of, "as_of")
        capped = max(1, min(int(limit), MAX_JOB_DOCUMENTS))
        stmt = select(Document).where(
            Document.symbol == symbol,
            Document.observed_at <= cutoff,
            Document.published_at <= cutoff,  # PIT：未来发布的文档不可见
        )
        if observed_after is not None:
            lower = require_aware(observed_after, "observed_after")
            if after_id is None:
                stmt = stmt.where(Document.observed_at > lower)
            else:
                stmt = stmt.where(
                    or_(
                        Document.observed_at > lower,
                        and_(Document.observed_at == lower, Document.id > after_id),
                    )
                )
        stmt = stmt.order_by(Document.observed_at.asc(), Document.id.asc()).limit(capped)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            DocumentSnapshot(
                id=row.id,
                symbol=row.symbol,
                document_type=row.document_type,
                title=row.title,
                body_text=row.body_text,
                source=row.source,
                source_url=row.source_url,
                published_at=row.published_at,
                observed_at=row.observed_at,
            )
            for row in rows
        ]

    async def get_model_prediction(
        self, symbol: str, horizon: str, as_of: datetime
    ) -> ModelPredictionSnapshot | None:
        cutoff = require_aware(as_of, "as_of")
        stmt = (
            select(Prediction, ModelVersion)
            .join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
            .where(
                Prediction.symbol == symbol,
                Prediction.horizon == horizon,
                Prediction.as_of <= cutoff,
                # candidate 模型永远不对外提供预测（spec §9.4）
                ModelVersion.status != "candidate",
            )
            .order_by(Prediction.as_of.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        prediction, model_version = row[0], row[1]
        return ModelPredictionSnapshot(
            symbol=prediction.symbol,
            horizon=prediction.horizon,
            as_of=prediction.as_of,
            reference_price=prediction.reference_price,
            probability_up=prediction.probability_up,
            expected_return=prediction.expected_return,
            lower_return=prediction.lower_return,
            upper_return=prediction.upper_return,
            confidence_label=prediction.confidence_label,
            data_cutoff=prediction.data_cutoff,
            model_key=model_version.model_key,
            model_version=model_version.version,
        )


def _to_bar_point(row: Bar) -> BarPoint:
    return BarPoint(
        symbol=row.symbol,
        timeframe=row.timeframe,
        bar_time=row.bar_time,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        amount=row.amount,
    )
