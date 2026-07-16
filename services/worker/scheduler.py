"""APScheduler 调度器（独立 worker 进程，不引入 Redis/Celery —— spec §4.1）。

本模块逐行实现 spec §8 的调度表。全部时间按 Asia/Shanghai；交易日历以交易所日历为准，
**非交易日一律跳过**（spec §8 表头"交易日调度"）。

spec §8 调度表 → 本文件 SCHEDULE：

    作业          交易日调度                              → job_id
    ────────────────────────────────────────────────────────────────────────────
    沪深300成分同步  每日 07:30、18:30                      → csi300_universe_sync
    自选股报价      09:25-11:30、13:00-15:00 每 15 秒       → watchlist_quotes
    5 分钟K线       09:35-11:30、13:05-15:05 每 60 秒       → minute_bars
    日线           15:10、18:00 各一次                     → daily_bars
    公告           交易时段每 5 分钟，其他时段每小时         → announcements
    新闻           交易时段每 10 分钟，其他时段每 2 小时     → news
    今日预测        09:45 起每 15 分钟，最后一次 14:45       → today_predictions
    一周预测        09:45、11:30、15:20                    → next5d_predictions
    预测结算        15:20，及次日 08:30 补偿                → settle_predictions

另有三个 spec §8 表里没有给出时刻、但功能章节明确要求"每日/持续"运行的作业，时刻为工程推断，
已在下面各自注明：feature_drift（§9.3.1 每日 PSI）、detect_anomalies（§12）、
refresh_analyses（§11）。以及 backfill_dispatcher —— API 只把回补任务写进 jobs 表（202 排队），
真正的执行必须在 worker 进程，否则违反 §14.1"后台采集不得阻塞 API 进程"。

失败处理见 runner.py：指数退避、作业锁、连续失败 3 次降级、最后成功时间（spec §8 末段）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any, Final

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.db import dispose_engine, session_scope
from apps.api.app.core.runtime import get_clock, get_trading_calendar
from services.worker.jobs.analysis_jobs import detect_anomalies, refresh_analyses, run_analysis_refresh
from services.worker.jobs.market_data_jobs import (
    ingest_announcements,
    ingest_daily_bars,
    ingest_minute_bars,
    ingest_news,
    ingest_watchlist_quotes,
    run_instrument_backfill,
    run_quote_refresh,
    sync_csi300_universe,
)
from services.worker.jobs.prediction_jobs import (
    compute_feature_drift,
    generate_next5d_predictions,
    generate_today_predictions,
    settle_predictions,
)
from services.worker.runner import (
    HEALTH_FILENAME,
    HealthRegistry,
    JobFn,
    JobRunner,
    RetryPolicy,
    disabled_providers,
    redact,
    state_dir,
)

logger = logging.getLogger("worker.scheduler")

TIMEZONE: Final[str] = "Asia/Shanghai"

# ── 数据源标识（spec §8 末段：界面展示"具体失败源"；spec §19.2：可禁用故障 Provider）────────
PROVIDER_CSI300: Final[str] = "csi300"
PROVIDER_AKSHARE: Final[str] = "akshare"
PROVIDER_DISCLOSURE: Final[str] = "cn_disclosure"
PROVIDER_MODEL: Final[str] = "model"
PROVIDER_INTERNAL: Final[str] = "internal"

# ── 时间窗口（spec §8）────────────────────────────────────────────────────────────────────
Window = tuple[time, time]

QUOTE_WINDOWS: Final[tuple[Window, ...]] = ((time(9, 25), time(11, 30)), (time(13, 0), time(15, 0)))
MINUTE_BAR_WINDOWS: Final[tuple[Window, ...]] = ((time(9, 35), time(11, 30)), (time(13, 5), time(15, 5)))
# "交易时段" = 连续竞价 09:30-11:30 / 13:00-15:00（与 trading_calendar.MarketPhase 一致）
SESSION_WINDOWS: Final[tuple[Window, ...]] = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
TODAY_PREDICTION_WINDOWS: Final[tuple[Window, ...]] = ((time(9, 45), time(14, 45)),)

MAX_CONCURRENT_BACKFILLS: Final[int] = 2
BACKFILL_POLL_SECONDS: Final[int] = 5
MAX_CONCURRENT_QUOTE_REFRESHES: Final[int] = 4
QUOTE_REFRESH_POLL_SECONDS: Final[int] = 1
MAX_CONCURRENT_ANALYSIS_REFRESHES: Final[int] = 2
ANALYSIS_REFRESH_POLL_SECONDS: Final[int] = 2
HEARTBEAT_SECONDS: Final[int] = 60


# ── 触发器构造 ───────────────────────────────────────────────────────────────────────────
def _cron(*, hour: str, minute: str, second: str) -> Any:
    return CronTrigger(hour=hour, minute=minute, second=second, timezone=TIMEZONE)


def at_times(times: Sequence[time]) -> tuple[Any, ...]:
    """在给定的若干时刻各触发一次（如日线 15:10 / 18:00）。"""
    return tuple(_cron(hour=str(t.hour), minute=str(t.minute), second=str(t.second)) for t in times)


def every(windows: Sequence[Window], step: timedelta) -> tuple[Any, ...]:
    """窗口内按固定步长触发；**两端点都包含**。

    spec §8 的 "09:25-11:30 每15秒" 语义 = 首次 09:25:00、末次 11:30:00。cron 的 ``a-b/step``
    是半开语义，所以这里把窗口拆成 [start, end) 的逐小时 cron + 一个恰好落在 end 的 cron。

    步长必须能整除小时（15s/60s/5min/10min/15min 均满足），否则跨小时会相位漂移 —— 直接报错，
    不允许静默生成错误的时刻表。
    """
    step_seconds = int(step.total_seconds())
    if step_seconds <= 0:
        raise ValueError("步长必须为正")
    if step_seconds < 60:
        if 60 % step_seconds:
            raise ValueError(f"秒级步长必须整除 60：{step_seconds}")
        # 显式列出取值而不是写 "*/15"：cron 的 range/step 表达式在跨小时时会重新对齐相位，
        # 而且 APScheduler 会拒绝 step 大于区间跨度的写法（例如 "45-59/15"）。
        second_field = ",".join(str(s) for s in range(0, 60, step_seconds))
        minute_step = 1
    else:
        if step_seconds % 60 or 60 % (step_seconds // 60):
            raise ValueError(f"分钟级步长必须整除小时：{step_seconds}")
        second_field = "0"
        minute_step = step_seconds // 60

    triggers: list[Any] = []
    for start, end in windows:
        if start.second or end.second:
            raise ValueError("窗口端点必须落在整分钟上")
        if start >= end:
            raise ValueError(f"窗口非法：{start}-{end}")
        if minute_step > 1 and start.minute % minute_step:
            raise ValueError(f"窗口起点 {start} 未对齐到 {minute_step} 分钟栅格")

        for hour in range(start.hour, end.hour + 1):
            low = start.minute if hour == start.hour else 0
            high = 59 if hour < end.hour else end.minute - 1
            if high < low:
                continue  # 该小时内 [start, end) 没有分钟（如 13:00-15:00 的 15 点）
            minutes = range(low, high + 1, minute_step)
            minute_field = ",".join(str(m) for m in minutes)
            triggers.append(_cron(hour=str(hour), minute=minute_field, second=second_field))

        # 闭区间右端点：11:30:00 / 15:00:00 / 14:45:00 …
        triggers.append(_cron(hour=str(end.hour), minute=str(end.minute), second="0"))

    return tuple(triggers)


def in_windows(moment: time, windows: Sequence[Window]) -> bool:
    return any(start <= moment <= end for start, end in windows)


def every_hours_outside(windows: Sequence[Window], hours: int) -> tuple[Any, ...]:
    """"其他时段每 N 小时"：整点触发，但排除已被窗口覆盖的整点，避免与窗口内触发重复。

    spec §8 公告 = 交易时段每 5 分钟 + 其他时段每小时；新闻 = 交易时段每 10 分钟 + 其他时段每 2 小时。
    """
    if hours < 1 or 24 % hours:
        raise ValueError("小时步长必须整除 24")
    selected = [h for h in range(0, 24, hours) if not in_windows(time(h, 0), windows)]
    if not selected:
        return ()
    return (_cron(hour=",".join(str(h) for h in selected), minute="0", second="0"),)


def fire_times(triggers: Sequence[Any], day: date) -> list[datetime]:
    """枚举某一天内的全部触发时刻（去重排序）。调度表的单元测试与运维自检都用它。"""
    start = datetime.combine(day, time(0, 0), tzinfo=SHANGHAI)
    end = start + timedelta(days=1)
    moments: set[datetime] = set()
    for trigger in triggers:
        cursor = start
        while True:
            nxt = trigger.get_next_fire_time(None, cursor)
            if nxt is None or nxt >= end:
                break
            moments.add(nxt.astimezone(SHANGHAI))
            cursor = nxt + timedelta(seconds=1)  # 最小步长 15s，+1s 不会漏掉相邻触发
    return sorted(moments)


# ── 作业定义 ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class JobSpec:
    job_id: str
    title: str
    provider: str
    fn: JobFn
    triggers: tuple[Any, ...]
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: float = 300.0
    misfire_grace_seconds: int = 300
    # spec §8 表头即"交易日调度"：默认非交易日不跑。回补分发是唯一例外（用户周末也能加自选股）。
    trading_day_only: bool = True

    def trigger(self) -> Any:
        return self.triggers[0] if len(self.triggers) == 1 else OrTrigger(list(self.triggers))


def build_schedule() -> tuple[JobSpec, ...]:
    """spec §8 调度表的唯一实现。"""
    return (
        # 沪深300成分同步 | 每日 07:30、18:30 | 失败：保留上一快照并标记同步失败（作业内实现）
        JobSpec(
            job_id="csi300_universe_sync",
            title="沪深300成分同步",
            provider=PROVIDER_CSI300,
            fn=sync_csi300_universe,
            triggers=at_times([time(7, 30), time(18, 30)]),
            retry=RetryPolicy(max_attempts=3, base_delay_seconds=10.0, max_delay_seconds=60.0),
            timeout_seconds=300.0,
            misfire_grace_seconds=3600,
        ),
        # 自选股报价 | 09:25-11:30、13:00-15:00 每 15 秒 | 失败：指数退避；180 秒后标记 stale
        # （stale 由读侧按 quotes.observed_at + QUOTE_STALE_SECONDS 判定，不在这里改写数据）
        JobSpec(
            job_id="watchlist_quotes",
            title="自选股报价",
            provider=PROVIDER_AKSHARE,
            fn=ingest_watchlist_quotes,
            triggers=every(QUOTE_WINDOWS, timedelta(seconds=15)),
            retry=RetryPolicy(max_attempts=3, base_delay_seconds=1.0, factor=2.0, max_delay_seconds=8.0),
            timeout_seconds=12.0,
            misfire_grace_seconds=10,  # 迟到超过 10 秒的 tick 直接丢弃，不补跑旧行情
        ),
        # 5 分钟K线 | 09:35-11:30、13:05-15:05 每 60 秒 | 失败：按主键幂等补写（作业内 upsert）
        JobSpec(
            job_id="minute_bars",
            title="5分钟K线",
            provider=PROVIDER_AKSHARE,
            fn=ingest_minute_bars,
            triggers=every(MINUTE_BAR_WINDOWS, timedelta(seconds=60)),
            retry=RetryPolicy(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=15.0),
            timeout_seconds=45.0,
            misfire_grace_seconds=30,
        ),
        # 日线 | 15:10、18:00 各一次 | 失败：对账后覆盖同源未确认记录（作业内实现）
        JobSpec(
            job_id="daily_bars",
            title="日线",
            provider=PROVIDER_AKSHARE,
            fn=ingest_daily_bars,
            triggers=at_times([time(15, 10), time(18, 0)]),
            retry=RetryPolicy(max_attempts=3, base_delay_seconds=30.0, max_delay_seconds=120.0),
            timeout_seconds=900.0,
            misfire_grace_seconds=3600,
        ),
        # 公告 | 交易时段每 5 分钟，其他时段每小时 | 失败：按内容哈希去重（作业内实现）
        JobSpec(
            job_id="announcements",
            title="公告",
            provider=PROVIDER_DISCLOSURE,
            fn=ingest_announcements,
            triggers=every(SESSION_WINDOWS, timedelta(minutes=5))
            + every_hours_outside(SESSION_WINDOWS, hours=1),
            retry=RetryPolicy(max_attempts=3, base_delay_seconds=5.0, max_delay_seconds=60.0),
            timeout_seconds=120.0,
            misfire_grace_seconds=120,
        ),
        # 新闻 | 交易时段每 10 分钟，其他时段每 2 小时 | 失败：按 URL 和内容哈希去重（作业内实现）
        JobSpec(
            job_id="news",
            title="新闻",
            provider=PROVIDER_AKSHARE,
            fn=ingest_news,
            triggers=every(SESSION_WINDOWS, timedelta(minutes=10))
            + every_hours_outside(SESSION_WINDOWS, hours=2),
            retry=RetryPolicy(max_attempts=3, base_delay_seconds=5.0, max_delay_seconds=60.0),
            timeout_seconds=120.0,
            misfire_grace_seconds=240,
        ),
        # 今日预测 | 09:45 起每 15 分钟，最后一次 14:45 | 失败：模型不可用则显示 unavailable
        JobSpec(
            job_id="today_predictions",
            title="今日预测",
            provider=PROVIDER_MODEL,
            fn=generate_today_predictions,
            triggers=every(TODAY_PREDICTION_WINDOWS, timedelta(minutes=15)),
            retry=RetryPolicy(max_attempts=2, base_delay_seconds=5.0, max_delay_seconds=30.0),
            timeout_seconds=240.0,
            misfire_grace_seconds=300,
        ),
        # 一周预测 | 09:45、11:30、15:20 | 失败：保留全部版本，不覆盖（账本 append-only）
        JobSpec(
            job_id="next5d_predictions",
            title="一周预测",
            provider=PROVIDER_MODEL,
            fn=generate_next5d_predictions,
            triggers=at_times([time(9, 45), time(11, 30), time(15, 20)]),
            retry=RetryPolicy(max_attempts=2, base_delay_seconds=5.0, max_delay_seconds=30.0),
            timeout_seconds=240.0,
            misfire_grace_seconds=600,
        ),
        # 预测结算 | 15:20 及次日 08:30 补偿 | 失败：幂等；交易日顺延
        # （"交易日顺延" = 非交易日跳过，08:30 的补偿自然落到下一个交易日）
        JobSpec(
            job_id="settle_predictions",
            title="预测结算",
            provider=PROVIDER_MODEL,
            fn=settle_predictions,
            triggers=at_times([time(8, 30), time(15, 20)]),
            retry=RetryPolicy(max_attempts=3, base_delay_seconds=15.0, max_delay_seconds=60.0),
            timeout_seconds=600.0,
            misfire_grace_seconds=3600,
        ),
        # ── 以下 4 个作业 spec §8 表未给出时刻，时刻为工程推断，功能要求见各自章节 ──────────
        # 特征漂移 PSI（spec §9.3.1："每日计算特征PSI"）：日线 18:00 落库后跑。
        JobSpec(
            job_id="feature_drift",
            title="特征漂移检测",
            provider=PROVIDER_MODEL,
            fn=compute_feature_drift,
            triggers=at_times([time(18, 30)]),
            retry=RetryPolicy(max_attempts=2, base_delay_seconds=30.0, max_delay_seconds=60.0),
            timeout_seconds=900.0,
            misfire_grace_seconds=3600,
        ),
        # 异动检测（spec §12）：依赖 5 分钟K线，交易时段每 5 分钟，紧跟分钟线窗口。
        JobSpec(
            job_id="detect_anomalies",
            title="异动检测",
            provider=PROVIDER_INTERNAL,
            fn=detect_anomalies,
            triggers=every(((time(9, 40), time(11, 30)), (time(13, 5), time(15, 5))), timedelta(minutes=5)),
            retry=RetryPolicy(max_attempts=2, base_delay_seconds=5.0, max_delay_seconds=20.0),
            timeout_seconds=120.0,
            misfire_grace_seconds=120,
        ),
        # 证据 Agent 分析刷新（spec §11）：交易时段每 30 分钟 + 收盘后 15:30 补一次。
        JobSpec(
            job_id="refresh_analyses",
            title="分析刷新",
            provider=PROVIDER_INTERNAL,
            fn=refresh_analyses,
            triggers=every(((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))), timedelta(minutes=30))
            + at_times([time(15, 30)]),
            retry=RetryPolicy(max_attempts=2, base_delay_seconds=10.0, max_delay_seconds=30.0),
            timeout_seconds=600.0,
            misfire_grace_seconds=600,
        ),
        # 回补任务分发（spec §3.1 / §7.1 / §14.1）：API 只写 jobs 行（queued）并返回 202，
        # 执行必须在 worker，否则回补会阻塞 API 进程。非交易日也要跑：用户周末也能加自选股。
        JobSpec(
            job_id="backfill_dispatcher",
            title="回补任务分发",
            provider=PROVIDER_INTERNAL,
            fn=dispatch_pending_backfills,
            triggers=(IntervalTrigger(seconds=BACKFILL_POLL_SECONDS, timezone=TIMEZONE),),
            retry=RetryPolicy(max_attempts=1),
            timeout_seconds=30.0,
            misfire_grace_seconds=30,
            trading_day_only=False,
        ),
        JobSpec(
            job_id="quote_refresh_dispatcher",
            title="单股最新行情任务分发",
            provider=PROVIDER_INTERNAL,
            fn=dispatch_pending_quote_refreshes,
            triggers=(IntervalTrigger(seconds=QUOTE_REFRESH_POLL_SECONDS, timezone=TIMEZONE),),
            retry=RetryPolicy(max_attempts=1),
            timeout_seconds=30.0,
            misfire_grace_seconds=30,
            trading_day_only=False,
        ),
        JobSpec(
            job_id="analysis_refresh_dispatcher",
            title="单股分析刷新任务分发",
            provider=PROVIDER_INTERNAL,
            fn=dispatch_pending_analysis_refreshes,
            triggers=(
                IntervalTrigger(seconds=ANALYSIS_REFRESH_POLL_SECONDS, timezone=TIMEZONE),
            ),
            retry=RetryPolicy(max_attempts=1),
            timeout_seconds=30.0,
            misfire_grace_seconds=30,
            trading_day_only=False,
        ),
    )


# ── 回补任务分发（jobs 表即作业锁 + 运行记录）─────────────────────────────────────────────
_backfill_semaphore: Final[asyncio.Semaphore] = asyncio.Semaphore(MAX_CONCURRENT_BACKFILLS)
_backfill_tasks: set[asyncio.Task[None]] = set()
_quote_refresh_semaphore: Final[asyncio.Semaphore] = asyncio.Semaphore(
    MAX_CONCURRENT_QUOTE_REFRESHES
)
_quote_refresh_tasks: set[asyncio.Task[None]] = set()
_analysis_refresh_semaphore: Final[asyncio.Semaphore] = asyncio.Semaphore(
    MAX_CONCURRENT_ANALYSIS_REFRESHES
)
_analysis_refresh_tasks: set[asyncio.Task[None]] = set()

_CLAIM_SQL = text(
    """
    UPDATE jobs
       SET status = 'running', started_at = :now, updated_at = :now
     WHERE id IN (
        SELECT id FROM jobs
         WHERE job_type = :job_type AND status = 'queued'
         ORDER BY created_at
         FOR UPDATE SKIP LOCKED
         LIMIT :limit
     )
    RETURNING id, symbol
    """
)

_REQUEUE_ORPHANS_SQL = text(
    """
    UPDATE jobs
       SET status = 'queued', started_at = NULL, updated_at = :now
     WHERE job_type IN ('instrument_backfill', 'quote_refresh', 'analysis_refresh')
       AND status = 'running'
    RETURNING id
    """
)

# 只在作业自己没有写终态时兜底（作业内部可能已写 succeeded/failed）。
_FINISH_OK_SQL = text(
    """
    UPDATE jobs
       SET status = 'succeeded', finished_at = :now, updated_at = :now
     WHERE id = :id AND status = 'running'
    """
)

_FINISH_FAIL_SQL = text(
    """
    UPDATE jobs
       SET status = 'failed', error_code = :code, error_message = :message,
           finished_at = :now, updated_at = :now
     WHERE id = :id AND status = 'running'
    """
)


async def requeue_orphan_backfills() -> int:
    """worker 重启后，把中断的回补与单股行情任务重新排队。"""
    async with session_scope() as session:
        result = await session.execute(_REQUEUE_ORPHANS_SQL, {"now": get_clock().now()})
        rows = result.fetchall()
    if rows:
        logger.warning("重启后回收 %d 个中断任务，已重新排队", len(rows))
    return len(rows)


async def dispatch_pending_backfills() -> None:
    """认领 queued 的回补任务并在 worker 内执行（并发上限 MAX_CONCURRENT_BACKFILLS）。"""
    free = MAX_CONCURRENT_BACKFILLS - len(_backfill_tasks)
    if free <= 0:
        return

    async with session_scope() as session:
        result = await session.execute(
            _CLAIM_SQL,
            {
                "now": get_clock().now(),
                "limit": free,
                "job_type": "instrument_backfill",
            },
        )
        claimed = [(row[0], row[1]) for row in result.fetchall()]

    for job_id, symbol in claimed:
        task = asyncio.create_task(_run_backfill(job_id, symbol), name=f"backfill:{symbol}")
        _backfill_tasks.add(task)
        task.add_done_callback(_backfill_tasks.discard)


async def dispatch_pending_quote_refreshes() -> None:
    """认领单股行情任务；每个任务只请求自己的 symbol。"""
    free = MAX_CONCURRENT_QUOTE_REFRESHES - len(_quote_refresh_tasks)
    if free <= 0:
        return

    async with session_scope() as session:
        result = await session.execute(
            _CLAIM_SQL,
            {
                "now": get_clock().now(),
                "limit": free,
                "job_type": "quote_refresh",
            },
        )
        claimed = [(row[0], row[1]) for row in result.fetchall()]

    for job_id, symbol in claimed:
        task = asyncio.create_task(
            _run_quote_refresh_task(job_id, symbol),
            name=f"quote-refresh:{symbol}",
        )
        _quote_refresh_tasks.add(task)
        task.add_done_callback(_quote_refresh_tasks.discard)


async def dispatch_pending_analysis_refreshes() -> None:
    """认领用户发起的单股分析任务，非交易日也给出明确终态。"""
    free = MAX_CONCURRENT_ANALYSIS_REFRESHES - len(_analysis_refresh_tasks)
    if free <= 0:
        return

    async with session_scope() as session:
        result = await session.execute(
            _CLAIM_SQL,
            {
                "now": get_clock().now(),
                "limit": free,
                "job_type": "analysis_refresh",
            },
        )
        claimed = [(row[0], row[1]) for row in result.fetchall()]

    for job_id, symbol in claimed:
        task = asyncio.create_task(
            _run_analysis_refresh_task(job_id, symbol),
            name=f"analysis-refresh:{symbol}",
        )
        _analysis_refresh_tasks.add(task)
        task.add_done_callback(_analysis_refresh_tasks.discard)


async def _run_analysis_refresh_task(job_id: uuid.UUID, symbol: str | None) -> None:
    if not symbol:
        await _finish_backfill(job_id, ok=False, message="分析刷新任务缺少 symbol")
        return
    async with _analysis_refresh_semaphore:
        logger.info("开始单股分析刷新 symbol=%s job_id=%s", symbol, job_id)
        try:
            await run_analysis_refresh(job_id, symbol)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = redact(f"{type(exc).__name__}: {exc}")
            logger.error("单股分析刷新失败 symbol=%s job_id=%s：%s", symbol, job_id, message)
            await _finish_backfill(job_id, ok=False, message=message)
            return
        await _finish_backfill(job_id, ok=True, message=None)
        logger.info("单股分析刷新完成 symbol=%s job_id=%s", symbol, job_id)


async def _run_quote_refresh_task(job_id: uuid.UUID, symbol: str | None) -> None:
    if not symbol:
        await _finish_backfill(job_id, ok=False, message="行情刷新任务缺少 symbol")
        return
    async with _quote_refresh_semaphore:
        logger.info("开始手动刷新行情 symbol=%s job_id=%s", symbol, job_id)
        try:
            await run_quote_refresh(job_id, symbol)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = redact(f"{type(exc).__name__}: {exc}")
            logger.error("手动行情刷新失败 symbol=%s job_id=%s：%s", symbol, job_id, message)
            await _finish_backfill(job_id, ok=False, message=message)
            return
        await _finish_backfill(job_id, ok=True, message=None)
        logger.info("手动行情刷新完成 symbol=%s job_id=%s", symbol, job_id)


async def _run_backfill(job_id: uuid.UUID, symbol: str | None) -> None:
    if not symbol:
        await _finish_backfill(job_id, ok=False, message="回补任务缺少 symbol")
        return
    async with _backfill_semaphore:
        logger.info("开始回补 symbol=%s job_id=%s", symbol, job_id)
        try:
            await run_instrument_backfill(job_id, symbol)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 单只股票回补失败不得杀进程（spec §14.2）
            message = redact(f"{type(exc).__name__}: {exc}")
            logger.error("回补失败 symbol=%s job_id=%s：%s", symbol, job_id, message)
            await _finish_backfill(job_id, ok=False, message=message)
            return
        await _finish_backfill(job_id, ok=True, message=None)
        logger.info("回补完成 symbol=%s job_id=%s", symbol, job_id)


async def _finish_backfill(job_id: uuid.UUID, *, ok: bool, message: str | None) -> None:
    """兜底写终态。作业自身若已写终态（status != running），这里不覆盖。"""
    try:
        async with session_scope() as session:
            if ok:
                await session.execute(_FINISH_OK_SQL, {"id": job_id, "now": get_clock().now()})
            else:
                await session.execute(
                    _FINISH_FAIL_SQL,
                    {
                        "id": job_id,
                        "now": get_clock().now(),
                        "code": "BACKFILL_FAILED",
                        "message": (message or "")[:2000],
                    },
                )
    except Exception as exc:  # 写终态失败也不得杀进程
        logger.error("写回补终态失败 job_id=%s：%s", job_id, redact(str(exc)))


# ── 调度器 ───────────────────────────────────────────────────────────────────────────────
class WorkerScheduler:
    """把 SCHEDULE 装进 APScheduler，并在每次触发前做交易日/禁用源守卫。"""

    def __init__(
        self,
        specs: Sequence[JobSpec] | None = None,
        *,
        registry: HealthRegistry | None = None,
        runner: JobRunner | None = None,
        disabled: frozenset[str] | None = None,
    ) -> None:
        self.specs: tuple[JobSpec, ...] = tuple(specs) if specs is not None else build_schedule()
        self.disabled: frozenset[str] = disabled if disabled is not None else disabled_providers()
        self.registry = registry or HealthRegistry(state_dir() / HEALTH_FILENAME)
        self.runner = runner or JobRunner(registry=self.registry)
        self.scheduler: Any = AsyncIOScheduler(
            timezone=TIMEZONE,
            job_defaults={"coalesce": True, "max_instances": 1},
        )

        for spec in self.specs:
            enabled = spec.provider not in self.disabled
            self.registry.register(spec.job_id, spec.title, spec.provider, enabled=enabled)
            if not enabled:
                logger.warning("数据源 %s 已被禁用，跳过作业 %s", spec.provider, spec.job_id)
                continue
            self.scheduler.add_job(
                partial(self.dispatch, spec),
                trigger=spec.trigger(),
                id=spec.job_id,
                name=spec.title,
                misfire_grace_time=spec.misfire_grace_seconds,
                replace_existing=True,
            )

    async def dispatch(self, spec: JobSpec) -> None:
        """单次触发：先做非交易日守卫，再交给 JobRunner（重试/降级/记账都在那里）。"""
        now = get_clock().now()
        if spec.trading_day_only and not get_trading_calendar().is_trading_day(now.date()):
            self.registry.record_skip(spec.job_id, "non_trading_day")
            logger.debug("非交易日跳过作业 %s（%s）", spec.job_id, now.date())
            return
        await self.runner.run(
            job_id=spec.job_id,
            fn=spec.fn,
            retry=spec.retry,
            timeout_seconds=spec.timeout_seconds,
        )

    def refresh_next_runs(self) -> None:
        for spec in self.specs:
            job = self.scheduler.get_job(spec.job_id)
            self.registry.set_next_run(spec.job_id, getattr(job, "next_run_time", None) if job else None)

    async def heartbeat(self) -> None:
        """定期刷新健康快照（含 next_run_at），供 API 的 /settings/data-sources 与容器健康检查使用。"""
        self.refresh_next_runs()
        self.registry.persist()

    def start(self) -> None:
        self.registry.mark_started(get_clock().now())
        self.scheduler.add_job(
            self.heartbeat,
            trigger=IntervalTrigger(seconds=HEARTBEAT_SECONDS, timezone=TIMEZONE),
            id="__heartbeat__",
            name="健康快照心跳",
            next_run_time=get_clock().now(),  # 启动即写一次，避免 API 读到空文件
            replace_existing=True,
        )
        self.scheduler.start()
        self.refresh_next_runs()
        self.registry.persist()
        logger.info(
            "调度器启动：%d 个作业（禁用数据源：%s）",
            len(self.scheduler.get_jobs()) - 1,
            ",".join(sorted(self.disabled)) or "无",
        )

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        if _backfill_tasks:
            logger.info("等待 %d 个回补任务收尾…", len(_backfill_tasks))
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(30):
                    await asyncio.gather(*list(_backfill_tasks), return_exceptions=True)
        if _quote_refresh_tasks:
            logger.info("等待 %d 个单股行情任务收尾…", len(_quote_refresh_tasks))
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(30):
                    await asyncio.gather(*list(_quote_refresh_tasks), return_exceptions=True)
        if _analysis_refresh_tasks:
            logger.info("等待 %d 个单股分析任务收尾…", len(_analysis_refresh_tasks))
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(30):
                    await asyncio.gather(*list(_analysis_refresh_tasks), return_exceptions=True)
        self.registry.persist()
        await dispose_engine()
        logger.info("调度器已停止")


# ── 进程入口 ─────────────────────────────────────────────────────────────────────────────
def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)


async def main() -> None:
    _configure_logging()
    worker = WorkerScheduler()

    # 全新安装时数据库没有成分股；若只等 07:30/18:30，首页可能长时间显示空列表。
    # 启动时仅在当前成分为空的情况下做一次初始化同步，失败不阻断调度器启动。
    try:
        async with session_scope() as session:
            current_members = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM universe_memberships "
                    "WHERE universe_code = 'CSI300' AND effective_to IS NULL"
                )
            )
        if not current_members:
            logger.info("当前沪深300成分为空，执行首次同步")
            await sync_csi300_universe()
    except Exception as exc:
        logger.error("首次沪深300成分同步失败（继续启动）：%s", redact(str(exc)))

    # 沪深300由 universe_memberships 自动形成研究池；watchlist_items 只保留额外自选。

    # 崩溃恢复：把中断的回补任务放回队列。数据库暂时不可用时不要拖垮 worker 启动。
    try:
        await requeue_orphan_backfills()
    except Exception as exc:  # 数据库暂时不可用不得阻断 worker 启动
        logger.error("回收中断回补任务失败（继续启动）：%s", redact(str(exc)))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    worker.start()
    await stop.wait()
    await worker.shutdown()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
