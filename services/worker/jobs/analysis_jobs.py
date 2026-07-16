"""分析与异动检测作业（spec §8 调度表 / §12 / §14.2 幂等）。

两个入口，函数名固定：

* ``refresh_analyses()``  —— 为自选股里**新到的文档**生成解读分析（``analyses.analysis_type='document'``）。
* ``detect_anomalies()``  —— 跑四条确定性异动规则；触发即生成异动分析（``'anomaly'``），
  先确定性量价事实，再检索事件证据。

幂等（spec §14.2）：

* 文档分析用"**采集时间水位线**"推进：水位线 = 该证券已有 document 分析的 ``max(data_cutoff)``；
  每轮只处理 ``observed_at > 水位线`` 的文档，一个证券一个事务。中途崩溃 → 该证券整批回滚 →
  下轮重跑，既不重复也不遗漏（晚到的旧公告靠 ``observed_at`` 而非 ``published_at`` 被捕获）。
* 异动分析按"**规则标签 + 交易日**"去重：同一交易日、同一规则不重复建分析；
  当日新触发的规则才建新分析。

可靠性：单只证券失败只影响它自己（独立事务 + 捕获异常），不崩整个作业（spec §14.2）。
Agent 未配置或不可达时全部降级为模板摘要（direction=unknown + 固定文案），不阻断其余功能。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.db import session_scope
from apps.api.app.core.enums import AnalysisType
from apps.api.app.core.runtime import get_clock, get_trading_calendar
from apps.api.app.core.trading_calendar import TradingCalendar
from apps.api.app.models.tables import Analysis
from services.research.agents.analyst import analyze_anomaly, analyze_document, draft_to_json
from services.research.agents.client import ChatClient, build_chat_client
from services.research.agents.repository import SqlResearchReadRepository
from services.research.anomaly import (
    AnomalyEvent,
    detect_for_symbol,
    session_bounds,
    uncovered_signals,
)
from services.worker.jobs.tracking_scope import tracking_symbols

logger = logging.getLogger(__name__)

# 文档分析的最大回溯窗口：首次为某证券建分析时，只看这个窗口内的文档，避免开机即灌满历史
DOCUMENT_BOOTSTRAP_LOOKBACK = timedelta(days=2)
# 单证券单轮的安全阀（不是常规限流）。触到它说明上游数据异常：
# 此时水位线会推进到本轮 as_of，剩余文档将不再被分析 —— 因此必须报 ERROR，而不是静默丢弃。
MAX_DOCUMENTS_PER_SYMBOL = 200
DOCUMENT_BATCH_SIZE = 50


async def refresh_analyses() -> None:
    """为自选股新到的公告/新闻生成解读分析。Agent 不可用时写模板摘要，不报错。"""
    clock = get_clock()
    as_of = clock.now()
    client = build_chat_client()

    symbols = await _watchlist_symbols()
    if not symbols:
        logger.info("自选股为空，refresh_analyses 无事可做")
        return

    logger.info("refresh_analyses 开始 symbols=%d as_of=%s", len(symbols), as_of.isoformat())
    created = 0
    for symbol in symbols:
        try:
            created += await _refresh_symbol_analyses(symbol, as_of=as_of, client=client)
        except Exception:  # 单只证券失败不得影响其余（spec §14.2）
            logger.exception("refresh_analyses 处理 %s 失败", symbol)
    logger.info("refresh_analyses 完成 created=%d", created)


async def run_analysis_refresh(_job_id: uuid.UUID, symbol: str) -> None:
    """执行用户主动发起的单股分析刷新；异常向上抛出，由 dispatcher 写入失败终态。"""
    as_of = get_clock().now()
    client = build_chat_client()
    created = await _refresh_symbol_analyses(symbol, as_of=as_of, client=client)
    calendar = get_trading_calendar()
    anomaly_created = False
    if calendar.is_trading_day(to_shanghai(as_of).date()):
        anomaly_created = await _detect_symbol_anomaly(
            symbol,
            as_of=as_of,
            calendar=calendar,
            client=client,
        )
    logger.info(
        "单股分析刷新完成 symbol=%s document_analyses=%d anomaly=%s",
        symbol,
        created,
        anomaly_created,
    )


async def detect_anomalies() -> None:
    """跑四条确定性异动规则（无 LLM），触发则生成异动分析（先事实、后证据）。"""
    clock = get_clock()
    as_of = clock.now()
    calendar = get_trading_calendar()
    client = build_chat_client()

    if not calendar.is_trading_day(to_shanghai(as_of).date()):
        logger.info("非交易日，detect_anomalies 跳过 as_of=%s", as_of.isoformat())
        return

    symbols = await _watchlist_symbols()
    if not symbols:
        logger.info("自选股为空，detect_anomalies 无事可做")
        return

    logger.info("detect_anomalies 开始 symbols=%d as_of=%s", len(symbols), as_of.isoformat())
    created = 0
    for symbol in symbols:
        try:
            if await _detect_symbol_anomaly(symbol, as_of=as_of, calendar=calendar, client=client):
                created += 1
        except Exception:  # 单只证券失败不得影响其余
            logger.exception("detect_anomalies 处理 %s 失败", symbol)
    logger.info("detect_anomalies 完成 created=%d", created)


# ── 内部实现 ────────────────────────────────────────────────────────────────


async def _watchlist_symbols() -> list[str]:
    async with session_scope() as session:
        return await tracking_symbols(session, to_shanghai(get_clock().now()).date())


async def _document_watermark(session: AsyncSession, symbol: str) -> datetime | None:
    """该证券已有 document 分析的最大 ``data_cutoff``：新文档只从这个时间之后取。"""
    stmt = (
        select(Analysis.data_cutoff)
        .where(Analysis.symbol == symbol, Analysis.analysis_type == AnalysisType.DOCUMENT.value)
        .order_by(Analysis.data_cutoff.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _refresh_symbol_analyses(symbol: str, *, as_of: datetime, client: ChatClient | None) -> int:
    """一个证券 = 一个事务。整批成功才提交，避免"提交了一半、水位线却已推进"。

    翻页用 ``(observed_at, id)`` keyset：同一次采集写入的多篇公告 ``observed_at`` 相同，
    只按时间翻页会在批次边界上漏掉同刻文档。
    """
    created = 0
    async with session_scope() as session:
        repository = SqlResearchReadRepository(session)
        watermark = await _document_watermark(session, symbol)
        if watermark is None:
            # 首次分析：只回溯有限窗口，不把历史公告全部灌进来
            watermark = as_of - DOCUMENT_BOOTSTRAP_LOOKBACK

        cursor_time: datetime | None = watermark
        cursor_id: uuid.UUID | None = None
        exhausted = False
        while created < MAX_DOCUMENTS_PER_SYMBOL:
            documents = await repository.get_documents_observed_after(
                symbol, cursor_time, as_of, limit=DOCUMENT_BATCH_SIZE, after_id=cursor_id
            )
            if not documents:
                exhausted = True
                break
            for document in documents:
                draft = await analyze_document(
                    session,
                    repository=repository,
                    client=client,
                    symbol=symbol,
                    document=document,
                    as_of=as_of,
                )
                session.add(draft.to_orm())
                created += 1
                logger.info("生成文档分析 %s", draft_to_json(draft))
                cursor_time, cursor_id = document.observed_at, document.id
                if created >= MAX_DOCUMENTS_PER_SYMBOL:
                    break
            if len(documents) < DOCUMENT_BATCH_SIZE:
                exhausted = True
                break

        if not exhausted:
            leftover = await repository.get_documents_observed_after(
                symbol, cursor_time, as_of, limit=1, after_id=cursor_id
            )
            if leftover:
                # 水位线即将推进到 as_of，剩余文档不会再被捡起 —— 这是数据异常，必须可见
                logger.error(
                    "%s 单轮文档分析触及安全阀 %d，仍有文档未分析（请检查采集数据）",
                    symbol,
                    MAX_DOCUMENTS_PER_SYMBOL,
                )
    return created


async def _existing_anomaly_summaries(
    session: AsyncSession, symbol: str, as_of: datetime
) -> list[str]:
    """当日已生成的异动分析摘要（用于按规则标签去重）。"""
    day_start, day_end = session_bounds(to_shanghai(as_of).date())
    stmt = select(Analysis.summary).where(
        Analysis.symbol == symbol,
        Analysis.analysis_type == AnalysisType.ANOMALY.value,
        Analysis.data_cutoff >= day_start,
        Analysis.data_cutoff < day_end,
    )
    return list((await session.execute(stmt)).scalars().all())


async def _detect_symbol_anomaly(
    symbol: str,
    *,
    as_of: datetime,
    calendar: TradingCalendar,
    client: ChatClient | None,
) -> bool:
    async with session_scope() as session:
        repository = SqlResearchReadRepository(session)
        event = await detect_for_symbol(repository, symbol=symbol, as_of=as_of, calendar=calendar)
        if event is None:
            return False

        existing = await _existing_anomaly_summaries(session, symbol, as_of)
        fresh_signals = uncovered_signals(event, existing)
        if not fresh_signals:
            logger.info("%s 当日异动规则已记录，跳过（幂等）", symbol)
            return False

        # 只就"当日新触发的规则"建分析，避免同一规则一天多条
        event = AnomalyEvent(
            symbol=event.symbol,
            as_of=event.as_of,
            trading_day=event.trading_day,
            signals=fresh_signals,
            skipped=event.skipped,
        )
        draft = await analyze_anomaly(
            session,
            repository=repository,
            client=client,
            event=event,
            as_of=as_of,
        )
        session.add(draft.to_orm())
        logger.info("生成异动分析 %s rules=%s", draft_to_json(draft), [r.value for r in event.rules])
        return True
