"""数据采集作业（spec §8）。

**函数名是调度器的契约**，调度器（``services/worker/scheduler.py``，非本模块所有）按名 import：

    sync_csi300_universe    交易日 07:30、18:30
    ingest_watchlist_quotes 09:25-11:30、13:00-15:00 每 15 秒
    ingest_minute_bars      09:35-11:30、13:05-15:05 每 60 秒
    ingest_daily_bars       15:10、18:00
    ingest_announcements    交易时段每 5 分钟，其他时段每小时
    ingest_news             交易时段每 10 分钟，其他时段每 2 小时
    run_instrument_backfill 自选股首次添加时触发（三步：daily_bars → minute_bars → documents）

全部作业幂等（落库层按主键 upsert）。健康状态按报价 / K 线 / 新闻能力分别记录，
避免其中一种成功掩盖另一种失败；调度器的 JobHealth 快照供状态页展示（spec §8 / §13.1）。
降级时**不使用缓存冒充新数据** —— 作业照常失败并记账。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import SHANGHAI, Clock
from apps.api.app.core.db import session_scope
from apps.api.app.core.enums import BACKFILL_STEPS, CSI300_CODE, JobStatus
from apps.api.app.core.errors import (
    AppError,
    ErrorCode,
    InstrumentNotFound,
    InvalidArgument,
    ProviderUnavailable,
)
from apps.api.app.core.runtime import get_clock, get_trading_calendar
from apps.api.app.core.trading_calendar import TradingCalendar
from apps.api.app.models.tables import Instrument, Job, WatchlistItem
from services.market_data.ingest import (
    IngestReport,
    sync_universe_members,
    upsert_bars,
    upsert_documents,
    upsert_instruments,
    upsert_quotes,
)
from services.market_data.openbb_gateway import create_gateway

logger = logging.getLogger(__name__)

# ── 数据源标识（与 provider 里的 source 常量一致）───────────────────────────────
SOURCE_CSINDEX = "csindex"
SOURCE_DISCLOSURE = "cninfo"

# 同一发行方的不同接口故障域并不相同。健康键必须按能力拆分，否则 K 线成功会把
# 报价的连续失败清零，产生“报价最后成功”的错误观感。
HEALTH_MARKET_QUOTES = "eastmoney_quote_via_akshare"
HEALTH_MARKET_BARS = "eastmoney_bars_via_akshare"
HEALTH_MARKET_NEWS = "eastmoney_news_via_akshare"

# 连续失败达到该次数即降级（spec §8）
DEGRADED_AFTER_FAILURES = 3

# 采集窗口
DAILY_RECONCILE_DAYS = 10  # 日线每次回看 10 个自然日做对账
MINUTE_LOOKBACK_DAYS = 1  # 分钟线只补当日
ANNOUNCEMENT_LOOKBACK_DAYS = 3
NEWS_LOOKBACK_DAYS = 2

# 回补窗口（spec §9.3：日线至少回补 3 年）
BACKFILL_DAILY_YEARS = 3
BACKFILL_MINUTE_DAYS = 5  # 上游 5 分钟线只保留最近约 5 个交易日
BACKFILL_DOCUMENT_DAYS = 180

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(15, 5)


# ── 数据源健康（spec §8：连续失败 3 次进入降级，展示失败源与最后成功时间）──────────
@dataclass(slots=True)
class SourceHealth:
    source: str
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None

    @property
    def degraded(self) -> bool:
        return self.consecutive_failures >= DEGRADED_AFTER_FAILURES

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "degraded": self.degraded,
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_error": self.last_error,
        }


class SourceHealthRegistry:
    """进程内数据源健康台账。

    ⚠️ 这是 **worker 进程内**的状态：API 进程读不到它（spec §6 的 12 张表里没有数据源健康表）。
    数据源状态页要跨进程展示时，需要另加一张表或让 API 直接查 ``jobs`` —— 这个缺口
    记录在 docs/data-sources.md，没有在代码里用假数据糊过去。
    """

    def __init__(self) -> None:
        self._sources: dict[str, SourceHealth] = {}

    def _get(self, source: str) -> SourceHealth:
        if source not in self._sources:
            self._sources[source] = SourceHealth(source=source)
        return self._sources[source]

    def record_success(self, source: str, now: datetime) -> None:
        health = self._get(source)
        health.consecutive_failures = 0
        health.last_success_at = now
        health.last_error = None

    def record_failure(self, source: str, error: str, now: datetime) -> None:
        health = self._get(source)
        health.consecutive_failures += 1
        health.last_failure_at = now
        health.last_error = error
        if health.degraded:
            logger.error(
                "数据源 %s 已降级：连续失败 %d 次，最后成功时间 %s，最后错误：%s",
                source,
                health.consecutive_failures,
                health.last_success_at.isoformat() if health.last_success_at else "从未成功",
                error,
            )

    def snapshot(self) -> list[SourceHealth]:
        return list(self._sources.values())


_HEALTH = SourceHealthRegistry()


def get_source_health() -> list[dict[str, object]]:
    """数据源状态（供状态页 / 运维查询）。"""
    return [health.as_dict() for health in _HEALTH.snapshot()]


def reset_source_health() -> None:
    """测试清理钩子。"""
    global _HEALTH
    _HEALTH = SourceHealthRegistry()


async def _guarded[T](source: str, clock: Clock, call: Callable[[], Awaitable[T]]) -> T:
    """把一次上游调用记进健康台账。失败原样抛出 —— 不吞异常、不返回缓存。"""
    try:
        result = await call()
    except AppError as exc:
        _HEALTH.record_failure(source, f"{exc.code.value}: {exc.message}", clock.now())
        raise
    except Exception as exc:
        _HEALTH.record_failure(source, f"{type(exc).__name__}: {exc}", clock.now())
        raise
    _HEALTH.record_success(source, clock.now())
    return result


# ── 公共小工具 ──────────────────────────────────────────────────────────────
async def _watchlist_symbols(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(WatchlistItem.symbol).order_by(WatchlistItem.display_order, WatchlistItem.symbol)
    )
    return list(rows.scalars().all())


def _log_report(job: str, report: IngestReport) -> None:
    logger.info(
        "%s：写入 %d 条，重复 %d 条，拒收 %d 条",
        job,
        report.written,
        report.duplicates,
        report.rejected_count,
    )
    for rejection in report.rejected:
        logger.warning("%s 拒收：%s %s —— %s", job, rejection.key, rejection.reason.value, rejection.detail)
    for warning in report.warnings:
        logger.info("%s：%s", job, warning)


def _is_trading_day(now: datetime, calendar: TradingCalendar) -> bool:
    return calendar.is_trading_day(now.date())


# ── 作业 1：沪深300 成分同步 ────────────────────────────────────────────────
async def sync_csi300_universe() -> None:
    """交易日 07:30 / 18:30。

    失败处理（spec §8）：**保留上一快照并标记同步失败，不覆盖历史有效期**。
    因此这里的异常直接向上抛：不写入任何东西 → 库里保持上一份有效期不变。
    空成分在网关层就 fail closed，绝不会走到"把 300 只全部标记为调出"。
    """
    clock = get_clock()
    calendar = get_trading_calendar()
    now = clock.now()
    as_of = now.date()

    async with create_gateway() as gateway:
        instruments = await _guarded(
            SOURCE_CSINDEX, clock, lambda: gateway.list_instruments("CSI300", as_of)
        )
        members = await _guarded(
            SOURCE_CSINDEX, clock, lambda: gateway.get_universe_members("CSI300", as_of)
        )

    async with session_scope() as session:
        report = await upsert_instruments(session, instruments, now)
        report.merge(
            await sync_universe_members(session, members, as_of, now, calendar, CSI300_CODE)
        )
    _log_report("沪深300 成分同步", report)


# ── 作业 2：自选股报价 ──────────────────────────────────────────────────────
async def ingest_watchlist_quotes() -> None:
    """09:25-11:30 / 13:00-15:00 每 15 秒。180 秒后前端标 stale（由 freshness_of 判定）。"""
    clock = get_clock()
    now = clock.now()

    async with session_scope() as session:
        symbols = await _watchlist_symbols(session)
    if not symbols:
        logger.debug("自选股为空，跳过报价采集")
        return

    async with create_gateway() as gateway:
        quotes = await _guarded(
            HEALTH_MARKET_QUOTES, clock, lambda: gateway.get_quotes(symbols, now)
        )

    missing = sorted(set(symbols) - {quote.symbol for quote in quotes})
    async with session_scope() as session:
        report = await upsert_quotes(session, quotes, now)
    if missing:
        # 上游没返回这些标的 —— 不补零、不复制上一条，让它们自然变 stale/unavailable
        report.warnings.append(f"上游未返回报价：{', '.join(missing)}")
    _log_report("自选股报价", report)


# ── 作业 3：5 分钟 K 线 ─────────────────────────────────────────────────────
async def ingest_minute_bars() -> None:
    """09:35-11:30 / 13:05-15:05 每 60 秒，按主键幂等补写。"""
    clock = get_clock()
    calendar = get_trading_calendar()
    now = clock.now()
    if not _is_trading_day(now, calendar):
        logger.debug("%s 非交易日，跳过分钟线采集", now.date())
        return

    async with session_scope() as session:
        symbols = await _watchlist_symbols(session)
    if not symbols:
        return

    first_day = now.date() - timedelta(days=MINUTE_LOOKBACK_DAYS - 1)
    start = datetime.combine(first_day, SESSION_OPEN, tzinfo=SHANGHAI)
    report = IngestReport()
    async with create_gateway() as gateway:
        for symbol in symbols:
            bars = await _guarded(
                HEALTH_MARKET_BARS,
                clock,
                partial(gateway.get_bars, symbol, "5m", start, now),
            )
            if not bars:
                continue
            async with session_scope() as session:
                report.merge(await upsert_bars(session, bars, now))
    _log_report("5 分钟 K 线", report)


# ── 作业 4：日线 ───────────────────────────────────────────────────────────
async def ingest_daily_bars() -> None:
    """15:10 与 18:00 各一次；第二次对账后覆盖同源未确认记录（upsert where source 相同）。"""
    clock = get_clock()
    now = clock.now()

    async with session_scope() as session:
        symbols = await _watchlist_symbols(session)
    if not symbols:
        return

    start = now - timedelta(days=DAILY_RECONCILE_DAYS)
    report = IngestReport()
    async with create_gateway() as gateway:
        for symbol in symbols:
            bars = await _guarded(
                HEALTH_MARKET_BARS,
                clock,
                partial(gateway.get_bars, symbol, "1d", start, now),
            )
            if not bars:
                continue
            async with session_scope() as session:
                report.merge(await upsert_bars(session, bars, now))
    _log_report("日线", report)


# ── 作业 5：公告 ───────────────────────────────────────────────────────────
async def ingest_announcements() -> None:
    """交易时段每 5 分钟，其他时段每小时。按内容哈希去重。"""
    clock = get_clock()
    now = clock.now()

    async with session_scope() as session:
        symbols = await _watchlist_symbols(session)
    if not symbols:
        return

    start = now - timedelta(days=ANNOUNCEMENT_LOOKBACK_DAYS)
    report = IngestReport()
    async with create_gateway() as gateway:
        for symbol in symbols:
            documents = await _guarded(
                SOURCE_DISCLOSURE, clock, partial(gateway.get_announcements, symbol, start, now)
            )
            if not documents:
                continue
            async with session_scope() as session:
                report.merge(await upsert_documents(session, documents, now))
    _log_report("公告", report)


# ── 作业 6：新闻 ───────────────────────────────────────────────────────────
async def ingest_news() -> None:
    """交易时段每 10 分钟，其他时段每 2 小时。按 URL 和内容哈希去重。"""
    clock = get_clock()
    now = clock.now()

    async with session_scope() as session:
        symbols = await _watchlist_symbols(session)
    if not symbols:
        return

    start = now - timedelta(days=NEWS_LOOKBACK_DAYS)
    report = IngestReport()
    async with create_gateway() as gateway:
        for symbol in symbols:
            documents = await _guarded(
                HEALTH_MARKET_NEWS,
                clock,
                partial(gateway.get_news, symbol, start, now),
            )
            if not documents:
                continue
            async with session_scope() as session:
                report.merge(await upsert_documents(session, documents, now))
    _log_report("新闻", report)


# ── 作业 7：单标的回补 ─────────────────────────────────────────────────────
async def _set_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    now: datetime,
    *,
    status: JobStatus | None = None,
    current_step: str | None = None,
    completed_steps: int | None = None,
    warnings: list[dict[str, str]] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        raise InvalidArgument(f"作业 {job_id} 不存在")
    if status is not None:
        job.status = status.value
    if current_step is not None:
        job.current_step = current_step
    if completed_steps is not None:
        job.completed_steps = completed_steps
    if warnings is not None:
        job.warnings = [*list(job.warnings or []), *warnings]
    if error_code is not None:
        job.error_code = error_code
    if error_message is not None:
        job.error_message = error_message
    if started:
        job.started_at = now
    if finished:
        job.finished_at = now
    job.updated_at = now
    await session.flush()


async def run_quote_refresh(job_id: uuid.UUID, symbol: str) -> None:
    """只采集一只股票的最新行情，并把真实结果写回作业表。"""
    clock = get_clock()
    now = clock.now()
    code = symbol.strip()

    try:
        async with session_scope() as session:
            if await session.get(Instrument, code) is None:
                raise InstrumentNotFound(code)

        async with create_gateway() as gateway:
            quotes = await _guarded(
                HEALTH_MARKET_QUOTES,
                clock,
                lambda: gateway.get_quotes([code], now),
            )
        quote = next((item for item in quotes if item.symbol == code), None)
        if quote is None:
            raise ProviderUnavailable(f"{code} 的唯一行情来源未返回报价")

        async with session_scope() as session:
            report = await upsert_quotes(session, [quote], now)
            await _set_job(
                session,
                job_id,
                clock.now(),
                status=JobStatus.SUCCEEDED,
                current_step="fetch_quote",
                completed_steps=1,
                warnings=report.as_warnings(),
                finished=True,
            )
        _log_report(f"手动刷新 {code} 行情", report)
    except AppError as exc:
        async with session_scope() as session:
            await _set_job(
                session,
                job_id,
                clock.now(),
                status=JobStatus.FAILED,
                error_code=exc.code.value,
                error_message=exc.message,
                finished=True,
            )
        raise
    except Exception as exc:
        async with session_scope() as session:
            await _set_job(
                session,
                job_id,
                clock.now(),
                status=JobStatus.FAILED,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
                error_message=f"{type(exc).__name__}: {exc}",
                finished=True,
            )
        raise ProviderUnavailable(f"刷新 {code} 行情失败：{exc}") from exc


async def run_instrument_backfill(job_id: uuid.UUID, symbol: str) -> None:
    """自选股首次添加时的三步回补（spec §7.1）。

    步骤固定为 ``BACKFILL_STEPS`` = daily_bars → minute_bars → documents。

    **分钟数据不可得时只记 warning，不使整项回补失败** —— 上游 5 分钟线只保留最近数个交易日，
    新股/停牌股拿不到分钟数据是常态，不该让整个自选股添加失败。
    日线与文档失败则整项失败（status=failed + error_code），因为预测与研究页依赖它们。

    幂等：落库层全部 upsert；同一 job_id 重跑会重置进度并重新执行三步。
    """
    clock = get_clock()
    now = clock.now()
    code = symbol.strip()

    async with session_scope() as session:
        instrument = await session.get(Instrument, code)
        if instrument is None:
            await _set_job(
                session,
                job_id,
                now,
                status=JobStatus.FAILED,
                error_code=ErrorCode.INSTRUMENT_NOT_FOUND.value,
                error_message=f"证券 {code} 不在 instruments 表中，请先同步沪深300 成分",
                finished=True,
            )
            raise InstrumentNotFound(code)
        await _set_job(
            session,
            job_id,
            now,
            status=JobStatus.RUNNING,
            current_step=BACKFILL_STEPS[0],
            completed_steps=0,
            started=True,
        )

    completed = 0
    warnings: list[dict[str, str]] = []

    try:
        async with create_gateway() as gateway:
            # step 1：日线（3 年）—— 失败即整项失败
            daily_start = now - timedelta(days=365 * BACKFILL_DAILY_YEARS)
            bars = await _guarded(
                HEALTH_MARKET_BARS,
                clock,
                lambda: gateway.get_bars(code, "1d", daily_start, now),
            )
            async with session_scope() as session:
                report = await upsert_bars(session, bars, now)
                warnings.extend(report.as_warnings())
                completed = 1
                await _set_job(
                    session,
                    job_id,
                    clock.now(),
                    completed_steps=completed,
                    current_step=BACKFILL_STEPS[1],
                    warnings=report.as_warnings(),
                )
            _log_report(f"回补 {code} 日线", report)

            # step 2：分钟线 —— 不可得只记 warning（spec §7.1）
            minute_start = now - timedelta(days=BACKFILL_MINUTE_DAYS)
            try:
                minute_bars = await _guarded(
                    HEALTH_MARKET_BARS,
                    clock,
                    lambda: gateway.get_bars(code, "5m", minute_start, now),
                )
            except AppError as exc:
                warning = {
                    "key": f"backfill:{code}:minute_bars",
                    "reason": "minute_bars_unavailable",
                    "detail": f"{exc.code.value}: {exc.message}",
                }
                logger.warning("回补 %s 分钟线不可得（不影响整项回补）：%s", code, exc.message)
                async with session_scope() as session:
                    completed = 2
                    await _set_job(
                        session,
                        job_id,
                        clock.now(),
                        completed_steps=completed,
                        current_step=BACKFILL_STEPS[2],
                        warnings=[warning],
                    )
                warnings.append(warning)
            else:
                async with session_scope() as session:
                    report = await upsert_bars(session, minute_bars, now)
                    completed = 2
                    await _set_job(
                        session,
                        job_id,
                        clock.now(),
                        completed_steps=completed,
                        current_step=BACKFILL_STEPS[2],
                        warnings=report.as_warnings(),
                    )
                if not minute_bars:
                    logger.warning("回补 %s 分钟线返回空（不影响整项回补）", code)
                _log_report(f"回补 {code} 分钟线", report)

            # step 3：文档（公告 + 新闻）—— 失败即整项失败
            doc_start = now - timedelta(days=BACKFILL_DOCUMENT_DAYS)
            announcements = await _guarded(
                SOURCE_DISCLOSURE, clock, lambda: gateway.get_announcements(code, doc_start, now)
            )
            news = await _guarded(
                HEALTH_MARKET_NEWS,
                clock,
                lambda: gateway.get_news(code, doc_start, now),
            )
            async with session_scope() as session:
                report = await upsert_documents(session, [*announcements, *news], now)
                completed = 3
                await _set_job(
                    session,
                    job_id,
                    clock.now(),
                    status=JobStatus.SUCCEEDED,
                    completed_steps=completed,
                    current_step=BACKFILL_STEPS[2],
                    warnings=report.as_warnings(),
                    finished=True,
                )
            _log_report(f"回补 {code} 文档", report)

    except AppError as exc:
        async with session_scope() as session:
            await _set_job(
                session,
                job_id,
                clock.now(),
                status=JobStatus.FAILED,
                completed_steps=completed,
                error_code=exc.code.value,
                error_message=exc.message,
                finished=True,
            )
        logger.error("回补 %s 失败（已完成 %d/%d 步）：%s", code, completed, len(BACKFILL_STEPS), exc.message)
        raise
    except Exception as exc:
        async with session_scope() as session:
            await _set_job(
                session,
                job_id,
                clock.now(),
                status=JobStatus.FAILED,
                completed_steps=completed,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
                error_message=f"{type(exc).__name__}: {exc}",
                finished=True,
            )
        logger.exception("回补 %s 异常终止", code)
        raise ProviderUnavailable(f"回补 {code} 失败：{exc}") from exc


__all__ = [
    "SourceHealth",
    "get_source_health",
    "ingest_announcements",
    "ingest_daily_bars",
    "ingest_minute_bars",
    "ingest_news",
    "ingest_watchlist_quotes",
    "reset_source_health",
    "run_instrument_backfill",
    "run_quote_refresh",
    "sync_csi300_universe",
]
