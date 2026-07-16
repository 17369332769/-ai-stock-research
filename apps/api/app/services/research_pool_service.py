"""沪深300自动研究池与额外自选的统一读模型。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.enums import CSI300_CODE
from apps.api.app.core.errors import (
    DuplicateWatchlistItem,
    InstrumentNotFound,
    InvalidArgument,
    ProviderUnavailable,
)
from apps.api.app.models.tables import Analysis, Document, Instrument, Job, Prediction, WatchlistItem
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories import jobs as jobs_repo
from apps.api.app.repositories import quotes as quotes_repo
from apps.api.app.repositories import watchlist as watchlist_repo
from apps.api.app.schemas.jobs import JobDTO
from apps.api.app.schemas.quotes import QuoteDTO
from apps.api.app.schemas.watchlist import WatchlistAddedDTO, WatchlistItemDTO
from apps.api.app.services.freshness import to_quote_dto
from apps.api.app.services.market_state import current_market
from apps.api.app.services.watchlist_service import AddResult

ResearchScope = Literal["csi300", "extra", "all"]

MODEL_SCOPE_WARNING = "该股票不属于模型主要训练股票池，预测可靠性可能较低。"


async def list_research_pool(
    session: AsyncSession,
    now: datetime,
    *,
    scope: ResearchScope,
    query: str | None = None,
) -> list[WatchlistItemDTO]:
    as_of = to_shanghai(now).date()
    automatic: list[Instrument] = []
    extras: list[WatchlistItem] = []
    if scope in ("csi300", "all"):
        if not await instruments_repo.universe_has_snapshot(session, CSI300_CODE, as_of):
            raise ProviderUnavailable(f"{CSI300_CODE} 在 {as_of} 尚无可用成分快照")
        automatic, _ = await instruments_repo.list_members(
            session, CSI300_CODE, as_of, limit=500, cursor=None
        )
    if scope in ("extra", "all"):
        extras = await watchlist_repo.list_items(session)

    # CSI300 来源优先。即使旧客户端误把成分股写进 watchlist，也不会在“全部关注”重复展示。
    automatic_symbols = {row.symbol for row in automatic}
    extra_current_symbols = await instruments_repo.current_member_symbols(
        session,
        CSI300_CODE,
        as_of,
        [item.symbol for item in extras],
    )
    extra_symbols = [
        item.symbol
        for item in extras
        if item.symbol not in automatic_symbols and item.symbol not in extra_current_symbols
    ]
    symbols = [row.symbol for row in automatic] + extra_symbols
    instruments = await instruments_repo.get_many(session, symbols)
    quotes = await quotes_repo.latest_many(session, symbols)
    analysis_rows = (
        await session.execute(
            select(
                Analysis.symbol,
                Analysis.created_at,
                Analysis.analysis_type,
                Analysis.confidence,
                Analysis.data_cutoff,
            ).where(Analysis.symbol.in_(symbols))
        )
    ).all()
    analysis_at: dict[str, datetime] = {}
    anomaly_strength: dict[str, float] = {}
    document_analysis_cutoff: dict[str, datetime] = {}
    anomaly_since = now - timedelta(days=1)
    for symbol, created_at, analysis_type, confidence, data_cutoff in analysis_rows:
        key = str(symbol)
        if key not in analysis_at or created_at > analysis_at[key]:
            analysis_at[key] = created_at
        if analysis_type == "document" and (
            key not in document_analysis_cutoff or data_cutoff > document_analysis_cutoff[key]
        ):
            document_analysis_cutoff[key] = data_cutoff
        if analysis_type == "anomaly" and created_at >= anomaly_since:
            value = float(confidence) if confidence is not None else 0.0
            anomaly_strength[key] = max(anomaly_strength.get(key, 0.0), value)

    document_rows = (
        await session.execute(
            select(Document.symbol, Document.published_at, Document.observed_at).where(
                Document.symbol.in_(symbols),
                Document.published_at >= now - timedelta(days=7),
            )
        )
    ).all()
    document_count: dict[str, int] = {}
    latest_document_at: dict[str, datetime] = {}
    latest_document_observed_at: dict[str, datetime] = {}
    for symbol, published_at, observed_at in document_rows:
        if symbol is None:
            continue
        key = str(symbol)
        document_count[key] = document_count.get(key, 0) + 1
        if key not in latest_document_at or published_at > latest_document_at[key]:
            latest_document_at[key] = published_at
        if key not in latest_document_observed_at or observed_at > latest_document_observed_at[key]:
            latest_document_observed_at[key] = observed_at

    prediction_rows = (
        await session.execute(select(Prediction.symbol).where(Prediction.symbol.in_(symbols)))
    ).scalars().all()
    prediction_count: dict[str, int] = {}
    for symbol in prediction_rows:
        key = str(symbol)
        prediction_count[key] = prediction_count.get(key, 0) + 1

    job_rows = (
        await session.execute(
            select(Job)
            .where(
                Job.symbol.in_(symbols),
                Job.job_type.in_(("instrument_backfill", "analysis_refresh")),
            )
            .order_by(Job.updated_at.desc())
        )
    ).scalars().all()
    backfill_jobs: dict[str, JobDTO] = {}
    analysis_jobs: dict[str, Job] = {}
    for job in job_rows:
        if job.symbol is None:
            continue
        if (
            job.job_type == "instrument_backfill"
            and job.symbol not in backfill_jobs
            and job.status in ("queued", "running", "failed")
        ):
            backfill_jobs[job.symbol] = JobDTO.from_row(job)
        if job.job_type == "analysis_refresh" and job.symbol not in analysis_jobs:
            analysis_jobs[job.symbol] = job

    def analysis_status(symbol: str) -> Literal[
        "waiting", "queued", "analyzing", "analyzed", "failed"
    ]:
        job = analysis_jobs.get(symbol)
        if job is not None:
            if job.status == "queued":
                return "queued"
            if job.status == "running":
                return "analyzing"
            if job.status == "failed":
                return "failed"
        latest_document = latest_document_observed_at.get(symbol)
        document_cutoff = document_analysis_cutoff.get(symbol)
        if latest_document is not None and (
            document_cutoff is None or latest_document > document_cutoff
        ):
            return "waiting"
        return "analyzed" if symbol in analysis_at else "waiting"

    market = current_market(now)

    normalized = (query or "").strip().lower()
    result: list[WatchlistItemDTO] = []
    for order, row in enumerate(automatic):
        if normalized and normalized not in row.symbol.lower() and normalized not in row.name.lower():
            continue
        quote = quotes.get(row.symbol)
        result.append(
            WatchlistItemDTO(
                symbol=row.symbol,
                name=row.name,
                industry=row.industry,
                display_order=order,
                created_at=None,
                pool_source="csi300",
                can_remove=False,
                analysis_status=analysis_status(row.symbol),
                analysis_updated_at=analysis_at.get(row.symbol),
                has_anomaly=row.symbol in anomaly_strength,
                anomaly_strength=anomaly_strength.get(row.symbol),
                has_documents=document_count.get(row.symbol, 0) > 0,
                document_count=document_count.get(row.symbol, 0),
                latest_document_at=latest_document_at.get(row.symbol),
                has_prediction=prediction_count.get(row.symbol, 0) > 0,
                prediction_count=prediction_count.get(row.symbol, 0),
                is_current_universe_member=True,
                quote=to_quote_dto(quote, now) if quote is not None else None,
                market=market,
                backfill_job=backfill_jobs.get(row.symbol),
                analysis_job=(
                    JobDTO.from_row(analysis_jobs[row.symbol])
                    if row.symbol in analysis_jobs
                    and analysis_jobs[row.symbol].status in ("queued", "running", "failed")
                    else None
                ),
            )
        )

    offset = len(automatic)
    for item in extras:
        if item.symbol in automatic_symbols or item.symbol in extra_current_symbols:
            continue
        instrument = instruments.get(item.symbol)
        if instrument is None:
            continue
        if normalized and normalized not in item.symbol.lower() and normalized not in instrument.name.lower():
            continue
        quote = quotes.get(item.symbol)
        result.append(
            WatchlistItemDTO(
                symbol=item.symbol,
                name=instrument.name,
                industry=instrument.industry,
                display_order=offset + item.display_order,
                created_at=to_shanghai(item.created_at),
                pool_source="extra",
                can_remove=True,
                model_scope_warning=MODEL_SCOPE_WARNING,
                analysis_status=analysis_status(item.symbol),
                analysis_updated_at=analysis_at.get(item.symbol),
                has_anomaly=item.symbol in anomaly_strength,
                anomaly_strength=anomaly_strength.get(item.symbol),
                has_documents=document_count.get(item.symbol, 0) > 0,
                document_count=document_count.get(item.symbol, 0),
                latest_document_at=latest_document_at.get(item.symbol),
                has_prediction=prediction_count.get(item.symbol, 0) > 0,
                prediction_count=prediction_count.get(item.symbol, 0),
                is_current_universe_member=False,
                quote=to_quote_dto(quote, now) if quote is not None else None,
                market=market,
                backfill_job=backfill_jobs.get(item.symbol),
                analysis_job=(
                    JobDTO.from_row(analysis_jobs[item.symbol])
                    if item.symbol in analysis_jobs
                    and analysis_jobs[item.symbol].status in ("queued", "running", "failed")
                    else None
                ),
            )
        )
    return result


async def add_extra_watchlist(session: AsyncSession, symbol: str, now: datetime) -> AddResult:
    instrument = await instruments_repo.get(session, symbol)
    if instrument is None:
        if symbol.startswith(("6", "9")):
            exchange = "SSE"
        elif symbol.startswith(("0", "2", "3")):
            exchange = "SZSE"
        else:
            raise InstrumentNotFound(symbol)
        instrument = Instrument(
            symbol=symbol,
            exchange=exchange,
            name=f"{symbol}（名称待同步）",
            industry=None,
            listed_at=None,
            active=True,
            updated_at=now,
        )
        session.add(instrument)
        await session.flush()
    as_of = to_shanghai(now).date()
    if await instruments_repo.is_current_member(session, symbol, CSI300_CODE, as_of):
        raise InvalidArgument(f"{symbol} 已自动包含在沪深300研究池，无需重复加入自选")
    if await watchlist_repo.get(session, symbol) is not None:
        raise DuplicateWatchlistItem(symbol)

    try:
        item = await watchlist_repo.add(
            session, symbol, CSI300_CODE, await watchlist_repo.next_display_order(session)
        )
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateWatchlistItem(symbol) from exc

    succeeded = await jobs_repo.succeeded_backfill(session, symbol)
    job_dto: JobDTO | None = None
    status_code = 201
    if succeeded is None:
        job_dto = JobDTO.from_row(await jobs_repo.enqueue_backfill(session, symbol))
        status_code = 202

    payload = WatchlistAddedDTO(
        watchlist_item=WatchlistItemDTO(
            symbol=item.symbol,
            name=instrument.name,
            display_order=item.display_order,
            created_at=to_shanghai(item.created_at),
            pool_source="extra",
            can_remove=True,
            model_scope_warning=MODEL_SCOPE_WARNING,
            is_current_universe_member=False,
            quote=None,
            market=current_market(now),
        ),
        backfill_job=job_dto,
    )
    return AddResult(payload=payload, status_code=status_code)


async def latest_quotes_for_scope(
    session: AsyncSession, now: datetime, *, scope: ResearchScope
) -> list[QuoteDTO]:
    as_of = to_shanghai(now).date()
    symbols: list[str] = []
    automatic_symbols: set[str] = set()
    if scope in ("csi300", "all"):
        if not await instruments_repo.universe_has_snapshot(session, CSI300_CODE, as_of):
            raise ProviderUnavailable(f"{CSI300_CODE} 在 {as_of} 尚无可用成分快照")
        automatic, _ = await instruments_repo.list_members(
            session, CSI300_CODE, as_of, limit=500, cursor=None
        )
        symbols.extend(row.symbol for row in automatic)
        automatic_symbols = {row.symbol for row in automatic}
    if scope in ("extra", "all"):
        extras = await watchlist_repo.list_items(session)
        extra_current = await instruments_repo.current_member_symbols(
            session, CSI300_CODE, as_of, [item.symbol for item in extras]
        )
        symbols.extend(
            item.symbol
            for item in extras
            if item.symbol not in automatic_symbols and item.symbol not in extra_current
        )
    quotes = await quotes_repo.latest_many(session, symbols)
    return [to_quote_dto(quotes[symbol], now) for symbol in symbols if symbol in quotes]
