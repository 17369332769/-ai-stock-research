"""规范化记录 → PostgreSQL 的**幂等**落库。

三条铁律（spec §4.2 / §8）：

1. **溯源必写**：行情/K 线写 ``source`` + ``source_url`` + ``observed_at``；文档另写 ``published_at``。
   记录契约（``services/market_data/contracts.py``）已把它们设为必填，这里只做落库。
2. **幂等**：全部按主键 upsert（quotes: symbol+observed_at；bars: symbol+timeframe+bar_time；
   documents: content_hash UNIQUE）。同一作业重跑不会产生重复行。
3. **脏数据拒收并记录在案**：逐条 ``validate_*``，被拒记录进 ``IngestReport.rejected``，
   由作业写入 ``jobs.warnings`` 并打日志。**整批全脏 → 抛 ``ProviderUnavailable``**，
   不允许"写了 0 行但报告成功"。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import CSI300_BENCHMARK_SYMBOL, CSI300_CODE, DocumentType
from apps.api.app.core.errors import ProviderUnavailable
from apps.api.app.core.trading_calendar import TradingCalendar
from apps.api.app.models.tables import (
    Bar,
    Document,
    Instrument,
    LatestQuote,
    Quote,
    Universe,
    UniverseMembership,
)
from services.market_data.contracts import (
    BarRecord,
    DocumentRecord,
    InstrumentRecord,
    QuoteRecord,
    UniverseMemberRecord,
)
from services.market_data.normalization.dedup import dedup_documents
from services.market_data.normalization.symbols import exchange_of
from services.market_data.normalization.validators import (
    Rejection,
    validate_bar,
    validate_document,
    validate_quote,
)

logger = logging.getLogger(__name__)

CSI300_UNIVERSE_NAME = "沪深300"


@dataclass(slots=True)
class IngestReport:
    """一次入库的结果。``rejected`` 非空即代表上游有脏数据 —— 必须让人看见。"""

    written: int = 0
    duplicates: int = 0
    rejected: list[Rejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def merge(self, other: IngestReport) -> IngestReport:
        self.written += other.written
        self.duplicates += other.duplicates
        self.rejected.extend(other.rejected)
        self.warnings.extend(other.warnings)
        return self

    def as_warnings(self) -> list[dict[str, str]]:
        """→ ``jobs.warnings``（jsonb 数组）。"""
        items = [rejection.as_dict() for rejection in self.rejected]
        items.extend({"key": "-", "reason": "warning", "detail": text} for text in self.warnings)
        return items


def _guard_all_rejected(kind: str, total: int, kept: int, rejected: list[Rejection]) -> None:
    """整批都被拒 → 上游数据不可用，别装成"成功写了 0 行"。"""
    if total > 0 and kept == 0:
        detail = "; ".join(f"{r.key} {r.reason.value}: {r.detail}" for r in rejected[:5])
        raise ProviderUnavailable(f"{kind}：上游 {total} 条记录全部未通过数据质量校验（{detail}）")


# ── instruments ─────────────────────────────────────────────────────────────
async def upsert_instruments(
    session: AsyncSession, instruments: Sequence[InstrumentRecord], now: datetime
) -> IngestReport:
    report = IngestReport()
    if not instruments:
        return report

    rows = [
        {
            "symbol": item.symbol,
            "exchange": item.exchange,
            "name": item.name,
            "industry": item.industry,
            "listed_at": item.listed_at,
            "active": True,
            "updated_at": now,
        }
        for item in instruments
    ]
    stmt = pg_insert(Instrument).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Instrument.symbol],
        set_={
            "name": stmt.excluded.name,
            "exchange": stmt.excluded.exchange,
            # industry / listed_at 上游可能为 NULL：不要用 NULL 覆盖已有值
            "industry": stmt.excluded.industry,
            "active": stmt.excluded.active,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    report.written = len(rows)
    return report


# ── universes / universe_memberships ────────────────────────────────────────
async def sync_universe_members(
    session: AsyncSession,
    members: Sequence[UniverseMemberRecord],
    as_of: date,
    now: datetime,
    calendar: TradingCalendar,
    universe_code: str = CSI300_CODE,
) -> IngestReport:
    """成分快照 → ``universe_memberships`` 的有效期差分。

    - 新成员（当前没有未闭合区间）→ 插入 ``[effective_from, NULL)``。
    - 已调出（库里未闭合但不在本次快照里）→ 把 ``effective_to`` 闭到 ``as_of`` 的**上一个交易日**。
    - 已闭合的历史区间**永不改写**（spec §8：不覆盖历史有效期）。

    空快照在网关层就已经 fail closed（``ProviderUnavailable``），到不了这里；
    这层再加一道断言，防止有人绕过网关直接调。
    """
    report = IngestReport()
    if not members:
        raise ProviderUnavailable(f"{universe_code} 成分快照为空：拒绝据此关闭全部历史有效期")

    snapshot = {member.symbol: member for member in members}
    sample = next(iter(snapshot.values()))

    # universes 快照行
    universe_stmt = pg_insert(Universe).values(
        [
            {
                "code": universe_code,
                "name": CSI300_UNIVERSE_NAME,
                "benchmark_symbol": CSI300_BENCHMARK_SYMBOL,
                "source": sample.source,
                "source_url": sample.source_url,
                "snapshot_at": sample.observed_at,
            }
        ]
    )
    universe_stmt = universe_stmt.on_conflict_do_update(
        index_elements=[Universe.code],
        set_={
            "source": universe_stmt.excluded.source,
            "source_url": universe_stmt.excluded.source_url,
            "snapshot_at": universe_stmt.excluded.snapshot_at,
        },
    )
    await session.execute(universe_stmt)

    # 库内当前未闭合的成员
    open_rows = (
        await session.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe_code == universe_code,
                UniverseMembership.effective_to.is_(None),
            )
        )
    ).scalars().all()
    open_symbols = {row.symbol for row in open_rows}

    # 1) 调入：快照里有、库里没有未闭合区间
    added = [symbol for symbol in snapshot if symbol not in open_symbols]
    if added:
        rows = [
            {
                "universe_code": universe_code,
                "symbol": symbol,
                "effective_from": snapshot[symbol].effective_from,
                "effective_to": None,
                "source": snapshot[symbol].source,
                "source_url": snapshot[symbol].source_url,
                "observed_at": snapshot[symbol].observed_at,
            }
            for symbol in added
        ]
        member_stmt = pg_insert(UniverseMembership).values(rows)
        # 同一 (universe, symbol, effective_from) 重跑 → 幂等
        member_stmt = member_stmt.on_conflict_do_nothing(
            index_elements=[
                UniverseMembership.universe_code,
                UniverseMembership.symbol,
                UniverseMembership.effective_from,
            ]
        )
        await session.execute(member_stmt)
        report.written += len(rows)

    # 2) 调出：库里未闭合、快照里没有 → 闭到上一个交易日
    removed = [row for row in open_rows if row.symbol not in snapshot]
    if removed:
        try:
            effective_to = calendar.previous_trading_day(as_of)
        except LookupError:
            effective_to = as_of
        for row in removed:
            # effective_to >= effective_from 是 CHECK 约束；同日调入又调出时闭到当天
            row.effective_to = max(effective_to, row.effective_from)
            report.warnings.append(f"{row.symbol} 已调出 {universe_code}，有效期闭合至 {row.effective_to}")
        await session.flush()

    report.warnings.append(
        f"{universe_code} 成分同步：快照 {len(snapshot)} 只，调入 {len(added)}，调出 {len(removed)}"
    )
    return report


def instruments_from_members(
    members: Sequence[UniverseMemberRecord], names: dict[str, str]
) -> list[InstrumentRecord]:
    """成分记录 + 名称表 → InstrumentRecord（industry/listed_at 上游不提供，留空不编造）。"""
    return [
        InstrumentRecord(
            symbol=member.symbol,
            name=names.get(member.symbol, member.symbol),
            exchange=exchange_of(member.symbol),
        )
        for member in members
    ]


# ── quotes ──────────────────────────────────────────────────────────────────
async def upsert_quotes(
    session: AsyncSession, quotes: Sequence[QuoteRecord], now: datetime
) -> IngestReport:
    report = IngestReport()
    if not quotes:
        return report

    rows: list[dict[str, Any]] = []
    for quote in quotes:
        issues = validate_quote(quote, now)
        if issues:
            report.rejected.extend(issues)
            continue
        rows.append(
            {
                "symbol": quote.symbol,
                "observed_at": quote.observed_at,
                "price": quote.price,
                "previous_close": quote.previous_close,
                "open": quote.open,
                "high": quote.high,
                "low": quote.low,
                "volume": quote.volume,
                "amount": quote.amount,
                "volume_ratio": quote.volume_ratio,
                "turnover_rate": quote.turnover_rate,
                "source": quote.source,
                "source_url": quote.source_url,
                "raw_payload": _jsonable(quote.raw_payload),
            }
        )

    _guard_all_rejected("行情入库", len(quotes), len(rows), report.rejected)
    if not rows:
        return report

    stmt = pg_insert(Quote).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Quote.symbol, Quote.observed_at],
        set_={
            "price": stmt.excluded.price,
            "previous_close": stmt.excluded.previous_close,
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "volume": stmt.excluded.volume,
            "amount": stmt.excluded.amount,
            "volume_ratio": stmt.excluded.volume_ratio,
            "turnover_rate": stmt.excluded.turnover_rate,
            "source": stmt.excluded.source,
            "source_url": stmt.excluded.source_url,
            "raw_payload": stmt.excluded.raw_payload,
        },
    )
    await session.execute(stmt)

    latest_rows = [
        {
            "symbol": quote.symbol,
            "price": quote.price,
            "previous_close": quote.previous_close,
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "volume": quote.volume,
            "amount": quote.amount,
            "volume_ratio": quote.volume_ratio,
            "turnover_rate": quote.turnover_rate,
            "bid1": quote.bid1,
            "ask1": quote.ask1,
            "market_time": quote.market_time,
            "fetched_at": now,
            "source": quote.source,
            "source_url": quote.source_url,
            "raw_payload": _jsonable(quote.raw_payload),
        }
        for quote in quotes
        if not validate_quote(quote, now)
    ]
    latest_stmt = pg_insert(LatestQuote).values(latest_rows)
    latest_stmt = latest_stmt.on_conflict_do_update(
        index_elements=[LatestQuote.symbol],
        set_={
            "price": latest_stmt.excluded.price,
            "previous_close": latest_stmt.excluded.previous_close,
            "open": latest_stmt.excluded.open,
            "high": latest_stmt.excluded.high,
            "low": latest_stmt.excluded.low,
            "volume": latest_stmt.excluded.volume,
            "amount": latest_stmt.excluded.amount,
            "volume_ratio": latest_stmt.excluded.volume_ratio,
            "turnover_rate": latest_stmt.excluded.turnover_rate,
            "bid1": latest_stmt.excluded.bid1,
            "ask1": latest_stmt.excluded.ask1,
            "market_time": latest_stmt.excluded.market_time,
            "fetched_at": latest_stmt.excluded.fetched_at,
            "source": latest_stmt.excluded.source,
            "source_url": latest_stmt.excluded.source_url,
            "raw_payload": latest_stmt.excluded.raw_payload,
        },
        where=latest_stmt.excluded.fetched_at >= LatestQuote.fetched_at,
    )
    await session.execute(latest_stmt)
    report.written = len(rows)
    return report


# ── bars ────────────────────────────────────────────────────────────────────
async def upsert_bars(session: AsyncSession, bars: Sequence[BarRecord], now: datetime) -> IngestReport:
    """K 线幂等补写。

    日线在 15:10 / 18:00 各跑一次：第二次会用 ``ON CONFLICT DO UPDATE`` 覆盖同源的未确认记录
    （spec §8「对账后覆盖同源未确认记录」）。5 分钟线按主键幂等补写。
    """
    report = IngestReport()
    if not bars:
        return report

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, datetime]] = set()
    for bar in bars:
        issues = validate_bar(bar, now)
        if issues:
            report.rejected.extend(issues)
            continue
        key = (bar.symbol, bar.timeframe, bar.bar_time)
        if key in seen:  # 同一批里主键重复会让 ON CONFLICT 直接报错，先去重
            report.duplicates += 1
            continue
        seen.add(key)
        rows.append(
            {
                "symbol": bar.symbol,
                "timeframe": bar.timeframe,
                "bar_time": bar.bar_time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
                "adjustment": bar.adjustment,
                "source": bar.source,
                "source_url": bar.source_url,
                "observed_at": bar.observed_at,
            }
        )

    _guard_all_rejected("K 线入库", len(bars), len(rows), report.rejected)
    if not rows:
        return report

    stmt = pg_insert(Bar).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Bar.symbol, Bar.timeframe, Bar.bar_time],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "amount": stmt.excluded.amount,
            "adjustment": stmt.excluded.adjustment,
            "source": stmt.excluded.source,
            "source_url": stmt.excluded.source_url,
            "observed_at": stmt.excluded.observed_at,
        },
        # 只覆盖同源记录：不同源的口径不混（spec §5.2）
        where=Bar.source == stmt.excluded.source,
    )
    await session.execute(stmt)
    report.written = len(rows)
    return report


# ── documents ───────────────────────────────────────────────────────────────
async def upsert_documents(
    session: AsyncSession, documents: Sequence[DocumentRecord], now: datetime
) -> IngestReport:
    """公告/新闻入库。

    去重三层：
    1. 批内 ``content_hash``（公告+新闻）与 ``source_url``（新闻）。
    2. 库内新闻 URL 预查询（DB 没有 source_url 唯一约束，必须显式查）。
    3. ``ON CONFLICT (content_hash) DO NOTHING`` —— 最终幂等由 UNIQUE 约束兜底。
    """
    report = IngestReport()
    if not documents:
        return report

    valid: list[DocumentRecord] = []
    for document in documents:
        issues = validate_document(document, now)
        if issues:
            report.rejected.extend(issues)
            continue
        valid.append(document)

    _guard_all_rejected("文档入库", len(documents), len(valid), report.rejected)
    if not valid:
        return report

    kept, dropped = dedup_documents(valid)
    report.duplicates += len(dropped)
    report.rejected.extend(dropped)

    # 库内新闻 URL 去重（spec §8：新闻按 URL 和内容哈希去重）
    news_urls = [
        document.source_url
        for _, document in kept
        if document.document_type == DocumentType.NEWS.value
    ]
    existing_urls: set[str] = set()
    if news_urls:
        existing_urls = set(
            (
                await session.execute(
                    select(Document.source_url).where(
                        Document.document_type == DocumentType.NEWS.value,
                        Document.source_url.in_(news_urls),
                    )
                )
            )
            .scalars()
            .all()
        )

    rows: list[dict[str, Any]] = []
    for digest, document in kept:
        if (
            document.document_type == DocumentType.NEWS.value
            and document.source_url in existing_urls
        ):
            report.duplicates += 1
            continue
        rows.append(
            {
                "id": uuid.uuid4(),
                "symbol": document.symbol,
                "document_type": document.document_type,
                "title": document.title,
                "body_text": document.body_text,
                "source": document.source,
                "source_url": document.source_url,
                "published_at": document.published_at,
                "observed_at": document.observed_at,
                "content_hash": digest,
            }
        )

    if not rows:
        return report

    stmt = pg_insert(Document).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=[Document.content_hash])
    result = await session.execute(stmt.returning(Document.id))
    inserted = len(result.scalars().all())
    report.written = inserted
    report.duplicates += len(rows) - inserted
    return report


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    """``raw_payload`` 里可能有 datetime/Decimal —— JSONB 只吃基础类型。"""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, datetime | date):
            out[key] = value.isoformat()
        elif isinstance(value, int | float | str | bool) or value is None:
            out[key] = value
        else:
            out[key] = str(value)
    return out
