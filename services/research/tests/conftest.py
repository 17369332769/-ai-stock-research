"""测试夹具。

原则（spec §16.1）：

* 固定时钟 + 固定交易日历，不依赖运行机器当前日期；
* **不访问公网**：LLM 一律用脚本化假 client；
* **不需要数据库**：证据展开用假会话（返回真实 ORM 对象），行情用内存假仓库。

假 client 只用于驱动执行路径与断言约束，**绝不用来冒充真实分析结果**：
所有断言检查的是"约束是否生效"（Schema、证据、降级、注入防护），不是"模型说得对不对"。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.trading_calendar import StaticTradingCalendar
from apps.api.app.models.tables import Document
from services.research.agents.client import ChatCompletion, ToolCall
from services.research.agents.repository import (
    BarPoint,
    DocumentSnapshot,
    ModelPredictionSnapshot,
    QuoteSnapshot,
)

AS_OF = datetime(2026, 7, 14, 10, 5, tzinfo=SHANGHAI)  # 交易日盘中
SYMBOL = "600519"


# ── 时间/日历 ───────────────────────────────────────────────────────────────


def trading_days(end: date, count: int) -> list[date]:
    """构造连续 ``count`` 个"交易日"（跳过周末），以 ``end`` 结尾。"""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


@pytest.fixture
def calendar() -> StaticTradingCalendar:
    return StaticTradingCalendar(trading_days(date(2026, 12, 31), 400))


# ── K 线构造 ────────────────────────────────────────────────────────────────


def bar(
    day: date,
    slot: time,
    *,
    close: float,
    open_: float | None = None,
    volume: float = 1000.0,
    symbol: str = SYMBOL,
    timeframe: str = "5m",
) -> BarPoint:
    opened = open_ if open_ is not None else close
    return BarPoint(
        symbol=symbol,
        timeframe=timeframe,
        bar_time=datetime.combine(day, slot, tzinfo=SHANGHAI),
        open=Decimal(str(opened)),
        high=Decimal(str(max(opened, close))),
        low=Decimal(str(min(opened, close))),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
    )


def daily_bar(day: date, *, open_: float, close: float, symbol: str = SYMBOL) -> BarPoint:
    return bar(day, time(15, 0), close=close, open_=open_, symbol=symbol, timeframe="1d")


# ── 假仓库（内存，PIT 由调用方保证）────────────────────────────────────────


@dataclass
class FakeRepository:
    """``ResearchReadRepository`` 的内存实现。只做过滤，不做任何外部访问。"""

    quotes: dict[str, list[QuoteSnapshot]] = field(default_factory=dict)
    bars: dict[tuple[str, str], list[BarPoint]] = field(default_factory=dict)
    documents: list[DocumentSnapshot] = field(default_factory=list)
    predictions: dict[tuple[str, str], list[ModelPredictionSnapshot]] = field(default_factory=dict)

    async def get_quote_snapshot(self, symbol: str, as_of: datetime) -> QuoteSnapshot | None:
        visible = [q for q in self.quotes.get(symbol, []) if q.observed_at <= as_of]
        return max(visible, key=lambda q: q.observed_at) if visible else None

    async def get_recent_bars(
        self, symbol: str, timeframe: str, limit: int, as_of: datetime
    ) -> list[BarPoint]:
        visible = [b for b in self.bars.get((symbol, timeframe), []) if b.bar_time <= as_of]
        visible.sort(key=lambda b: b.bar_time)
        return visible[-limit:]

    async def get_bars_in_range(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, as_of: datetime
    ) -> list[BarPoint]:
        upper = min(end, as_of)
        visible = [
            b
            for b in self.bars.get((symbol, timeframe), [])
            if start <= b.bar_time <= upper and b.bar_time <= as_of
        ]
        return sorted(visible, key=lambda b: b.bar_time)

    async def get_documents(
        self, symbol: str, start: datetime, end: datetime, as_of: datetime, limit: int = 20
    ) -> list[DocumentSnapshot]:
        upper = min(end, as_of)
        visible = [
            d
            for d in self.documents
            if d.symbol == symbol
            and start <= d.published_at <= upper
            and d.published_at <= as_of
            and d.observed_at <= as_of
        ]
        visible.sort(key=lambda d: d.published_at, reverse=True)
        return visible[:limit]

    async def get_documents_observed_after(
        self,
        symbol: str,
        observed_after: datetime | None,
        as_of: datetime,
        limit: int = 50,
        after_id: uuid.UUID | None = None,
    ) -> list[DocumentSnapshot]:
        def _after_cursor(doc: DocumentSnapshot) -> bool:
            if observed_after is None:
                return True
            if doc.observed_at > observed_after:
                return True
            return after_id is not None and doc.observed_at == observed_after and doc.id > after_id

        visible = [
            d
            for d in self.documents
            if d.symbol == symbol
            and d.observed_at <= as_of
            and d.published_at <= as_of
            and _after_cursor(d)
        ]
        visible.sort(key=lambda d: (d.observed_at, d.id))
        return visible[:limit]

    async def get_model_prediction(
        self, symbol: str, horizon: str, as_of: datetime
    ) -> ModelPredictionSnapshot | None:
        visible = [p for p in self.predictions.get((symbol, horizon), []) if p.as_of <= as_of]
        return max(visible, key=lambda p: p.as_of) if visible else None


# ── 假会话（证据展开只 select documents，够用）───────────────────────────────


class FakeResult:
    def __init__(self, rows: Sequence[Any]) -> None:
        self._rows = list(rows)

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """只支持 ``execute(select(Document)...)``：返回构造时给定的文档行。"""

    def __init__(self, documents: Sequence[Document] = ()) -> None:
        self.documents = list(documents)
        self.added: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        """假会话忽略语句细节：证据展开只按 id 取文档，命中与否由 ``build_evidence`` 判定。"""
        return FakeResult(self.documents)

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def make_document(
    *,
    document_id: uuid.UUID | None = None,
    symbol: str = SYMBOL,
    title: str = "关于签署重大合同的公告",
    body_text: str | None = "公司于 2026 年 7 月 14 日与甲方签署了重大合同，合同金额 12 亿元。",
    source: str = "cninfo",
    source_url: str = "https://example.invalid/announcement/1",
    published_at: datetime | None = None,
    observed_at: datetime | None = None,
    document_type: str = "announcement",
) -> Document:
    """真实 ORM 对象（不入库），用于证据展开与校验测试。"""
    published = published_at or (AS_OF - timedelta(hours=2))
    return Document(
        id=document_id or uuid.uuid4(),
        symbol=symbol,
        document_type=document_type,
        title=title,
        body_text=body_text,
        source=source,
        source_url=source_url,
        published_at=published,
        observed_at=observed_at or published,
        content_hash="0" * 64,
    )


def make_document_snapshot(document: Document) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=document.id,
        symbol=document.symbol,
        document_type=document.document_type,
        title=document.title,
        body_text=document.body_text,
        source=document.source,
        source_url=document.source_url,
        published_at=document.published_at,
        observed_at=document.observed_at,
    )


# ── 脚本化假 LLM（绝不访问公网）─────────────────────────────────────────────


class ScriptedChatClient:
    """按脚本返回补全；脚本项是 ``ChatCompletion`` 或要抛出的异常。"""

    def __init__(self, script: Sequence[ChatCompletion | Exception], model: str = "test-model") -> None:
        self._script = list(script)
        self._model = model
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatCompletion:
        self.calls.append(([dict(m) for m in messages], list(tools)))
        if not self._script:
            raise AssertionError("脚本已耗尽：被调用的次数超出预期")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text_completion(payload: str) -> ChatCompletion:
    return ChatCompletion(content=payload)


def tool_completion(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
    )
