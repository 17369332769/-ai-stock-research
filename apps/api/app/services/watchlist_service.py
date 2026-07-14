"""自选股编排（spec §7.1）。

验收标准直接考的三条：
1. ``POST /watchlist`` **在同一事务中**校验当前 CSI300 成员资格；已调出 ⇒ 409 NOT_CURRENT_UNIVERSE_MEMBER；
2. 重复添加 ⇒ 409 DUPLICATE_WATCHLIST_ITEM；
3. 首次添加 ⇒ 202 + backfill_job（三步固定：daily_bars / minute_bars / documents）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.enums import CSI300_CODE
from apps.api.app.core.errors import (
    DuplicateWatchlistItem,
    InstrumentNotFound,
    InvalidArgument,
    NotCurrentUniverseMember,
)
from apps.api.app.models.tables import Instrument, WatchlistItem
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories import jobs as jobs_repo
from apps.api.app.repositories import quotes as quotes_repo
from apps.api.app.repositories import watchlist as watchlist_repo
from apps.api.app.schemas.jobs import JobDTO
from apps.api.app.schemas.quotes import QuoteDTO
from apps.api.app.schemas.watchlist import WatchlistAddedDTO, WatchlistItemDTO
from apps.api.app.services.freshness import to_quote_dto


class WatchlistIntegrityError(RuntimeError):
    """自选股引用了不存在的证券。数据完整性事故 ⇒ 500，不伪装成用户错误。"""


@dataclass(frozen=True, slots=True)
class AddResult:
    payload: WatchlistAddedDTO
    # 首次添加（回补作业刚入队）⇒ 202；已完成过回补的重新添加 ⇒ 201
    status_code: int


async def list_watchlist(session: AsyncSession, now: datetime) -> list[WatchlistItemDTO]:
    items = await watchlist_repo.list_items(session)
    if not items:
        return []

    symbols = [item.symbol for item in items]
    quotes = await quotes_repo.latest_many(session, symbols)
    members = await instruments_repo.current_member_symbols(
        session, CSI300_CODE, to_shanghai(now).date(), symbols
    )
    rows = await instruments_repo.get_many(session, symbols)

    result: list[WatchlistItemDTO] = []
    for item in items:
        instrument = rows.get(item.symbol)
        if instrument is None:
            # watchlist_items.symbol 有外键，缺行只可能是数据完整性事故。
            # 这是个不带 {symbol} 的列表接口，回 404 INSTRUMENT_NOT_FOUND 会误导调用方
            # （"我又没点名哪只股票"）；按完整性事故 fail closed 成 500。
            raise WatchlistIntegrityError(
                f"自选股 {item.symbol} 没有对应的 instruments 行（外键被破坏）"
            )
        quote_row = quotes.get(item.symbol)
        result.append(
            _to_item_dto(
                item,
                instrument,
                is_current_member=item.symbol in members,
                quote=to_quote_dto(quote_row, now) if quote_row is not None else None,
            )
        )
    return result


async def add_to_watchlist(session: AsyncSession, symbol: str, now: datetime) -> AddResult:
    """全流程在**一个事务**内完成：成员资格校验 → 去重 → 插入 → 登记回补作业。

    事务在路由层 commit；这里任何一步抛错都不会留下半条记录。
    """
    as_of = to_shanghai(now).date()

    instrument = await instruments_repo.get(session, symbol)
    if instrument is None:
        raise InstrumentNotFound(symbol)

    # ① 当前成分资格 —— 与插入同事务（spec §7.1）。已调出 ⇒ 禁止重新添加（spec §3.1）
    if not await instruments_repo.is_current_member(session, symbol, CSI300_CODE, as_of):
        raise NotCurrentUniverseMember(symbol, CSI300_CODE)

    # ② 去重：先查一次给出干净错误，并发时再由 UNIQUE(symbol) 兜底
    if await watchlist_repo.get(session, symbol) is not None:
        raise DuplicateWatchlistItem(symbol)

    display_order = await watchlist_repo.next_display_order(session)
    try:
        item = await watchlist_repo.add(session, symbol, CSI300_CODE, display_order)
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateWatchlistItem(symbol) from exc

    # ③ 回补作业：只登记 queued 行，由 worker 领取（spec §14.1：后台采集不得阻塞 API）
    already_backfilled = await jobs_repo.succeeded_backfill(session, symbol)
    job_dto: JobDTO | None = None
    status_code = 201
    if already_backfilled is None:
        job = await jobs_repo.enqueue_backfill(session, symbol)
        job_dto = JobDTO.from_row(job)
        status_code = 202  # spec §7.1：首次添加返回 202 + 回补任务

    payload = WatchlistAddedDTO(
        watchlist_item=_to_item_dto(item, instrument, is_current_member=True, quote=None),
        backfill_job=job_dto,
    )
    return AddResult(payload=payload, status_code=status_code)


async def remove_from_watchlist(session: AsyncSession, symbol: str) -> None:
    removed = await watchlist_repo.remove(session, symbol)
    if removed == 0:
        raise InstrumentNotFound(symbol)


async def reorder_watchlist(
    session: AsyncSession, symbols: list[str], now: datetime
) -> list[WatchlistItemDTO]:
    """``symbols`` 必须是当前自选股的一个全排列，否则 400 —— 不做"尽力而为"的部分重排。"""
    current = {item.symbol for item in await watchlist_repo.list_items(session)}
    requested = list(symbols)

    if len(set(requested)) != len(requested):
        raise InvalidArgument("symbols 含重复项")
    if set(requested) != current:
        missing = sorted(current - set(requested))
        unknown = sorted(set(requested) - current)
        raise InvalidArgument(
            f"symbols 必须是当前自选股的全排列；缺少={missing or '无'}，多余={unknown or '无'}"
        )

    await watchlist_repo.reorder(session, requested)
    return await list_watchlist(session, now)


def _to_item_dto(
    item: WatchlistItem,
    instrument: Instrument,
    *,
    is_current_member: bool,
    quote: QuoteDTO | None,
) -> WatchlistItemDTO:
    return WatchlistItemDTO(
        symbol=item.symbol,
        name=instrument.name,
        display_order=item.display_order,
        created_at=to_shanghai(item.created_at),
        is_current_universe_member=is_current_member,
        quote=quote,
    )
