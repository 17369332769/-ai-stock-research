"""预测相关的调度作业（spec §8）。

    今日预测    09:45 起每 15 分钟，最后一次 14:45   模型不可用则显示 unavailable
    一周预测    09:45、11:30、15:20                  保留全部版本，不覆盖
    预测结算    15:20 及次日 08:30 补偿              幂等；交易日顺延
    特征漂移    每日一次                             PSI > 0.30 → 停止生成新预测

作业只做**编排**：不含特征公式、不含模型算法（模块边界 spec §5.1）。

四条铁律在这里体现为：
- 时间一律来自 ``get_clock()``，绝不 ``datetime.now()``；
- 单只股票失败**不拖垮整批**（记录后继续），但失败原因必须留痕，不静默吞掉；
- 模型不可用 / 数据不足 → 该股票**不产出预测**，绝不写一个默认值；
- 非交易日 / 未到 09:45 → 直接空跑返回。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import SHANGHAI, trading_day_of
from apps.api.app.core.db import session_scope
from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.core.errors import AppError, InsufficientData, ModelUnavailable
from apps.api.app.core.runtime import get_clock, get_trading_calendar
from apps.api.app.core.trading_calendar import TradingCalendar, today_prediction_allowed
from apps.api.app.models.tables import ModelVersion, Prediction
from services.prediction.evaluation.drift import compute_drift
from services.prediction.evaluation.drift_store import get_drift_store
from services.prediction.evaluation.settlement import settle_due_predictions
from services.prediction.features.config import load_feature_set
from services.prediction.features.repository import universe_members_at
from services.prediction.inference.loader import LoadedModel, load_model_bundle
from services.prediction.inference.service import NotUniverseMember, generate_prediction
from services.prediction.training.registry import active_model
from services.worker.jobs.tracking_scope import tracking_symbols

__all__ = [
    "compute_feature_drift",
    "generate_next5d_predictions",
    "generate_today_predictions",
    "settle_predictions",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _BatchResult:
    created: int = 0
    skipped: int = 0
    unavailable: int = 0
    failed: int = 0
    reasons: list[str] = field(default_factory=list)


async def _target_symbols(session: AsyncSession, day: date) -> list[str]:
    """自选股 ∩ 当日有效沪深300成分股。

    调出成分股的标的不再产生新预测（spec §3.1 / §9.3），但它的历史预测仍会被结算。
    """
    return await tracking_symbols(session, day)


async def _run_batch(horizon: str) -> _BatchResult:
    clock = get_clock()
    calendar = get_trading_calendar()
    now = clock.now()
    day = trading_day_of(now)
    result = _BatchResult()

    if not calendar.is_trading_day(day):
        logger.info("prediction.skip horizon=%s reason=not_trading_day day=%s", horizon, day)
        return result

    if horizon == PredictionHorizon.TODAY_CLOSE and not today_prediction_allowed(now, calendar):
        # 09:45 之前不生成今日预测（spec §3.3）
        logger.info("prediction.skip horizon=%s reason=before_0945 now=%s", horizon, now)
        return result

    async with session_scope() as session:
        symbols = await _target_symbols(session, day)
        if not symbols:
            return result
        members = set(await universe_members_at(session, day))

        for symbol in symbols:
            try:
                outcome = await generate_prediction(
                    session,
                    symbol=symbol,
                    horizon=horizon,
                    as_of=now,
                    calendar=calendar,
                    allow_out_of_universe=symbol not in members,
                )
            except NotUniverseMember as exc:
                result.skipped += 1
                logger.info("prediction.skip symbol=%s reason=%s", symbol, exc.message)
            except ModelUnavailable as exc:
                # 模型不可用（无 active 版本 / 漂移超阈值 / 特征集变更）：
                # 该股票本轮不产出预测，界面显示 unavailable。绝不写假概率。
                result.unavailable += 1
                result.reasons.append(f"{symbol}: {exc.message}")
                logger.warning("prediction.unavailable symbol=%s reason=%s", symbol, exc.message)
            except InsufficientData as exc:
                result.skipped += 1
                logger.info("prediction.insufficient symbol=%s reason=%s", symbol, exc.message)
            except AppError as exc:
                result.failed += 1
                result.reasons.append(f"{symbol}: {exc.message}")
                logger.exception("prediction.failed symbol=%s", symbol)
            except Exception:
                result.failed += 1
                result.reasons.append(f"{symbol}: 未预期的异常")
                logger.exception("prediction.error symbol=%s", symbol)
            else:
                if outcome.created:
                    result.created += 1
                else:
                    result.skipped += 1  # 幂等命中：同一 as_of 已有同一模型版本的预测

    logger.info(
        "prediction.batch horizon=%s created=%d skipped=%d unavailable=%d failed=%d",
        horizon,
        result.created,
        result.skipped,
        result.unavailable,
        result.failed,
    )
    return result


async def generate_today_predictions() -> None:
    """今日预测：09:45 起每 15 分钟，最后一次 14:45（spec §8）。"""
    await _run_batch(PredictionHorizon.TODAY_CLOSE.value)


async def generate_next5d_predictions() -> None:
    """一周预测：09:45、11:30、15:20（spec §8）。保留全部版本，不覆盖。"""
    await _run_batch(PredictionHorizon.NEXT_5D.value)


async def settle_predictions() -> None:
    """预测结算：15:20 及次日 08:30 补偿（spec §8）。幂等；交易日顺延。

    结算不看今天是不是交易日 —— 补偿作业本来就可能在非交易日跑，
    到期的预测（target_at <= now）就该结算。目标日是生成时用交易日历算好的。
    """
    now = get_clock().now()
    async with session_scope() as session:
        outcomes, stats = await settle_due_predictions(session, now=now)
    logger.info(
        "settlement.done due=%d settled=%d already=%d waiting=%d rejected=%d",
        stats.due,
        stats.settled,
        stats.already_settled,
        stats.waiting_for_bar,
        stats.rejected,
    )
    _ = outcomes


async def compute_feature_drift() -> None:
    """每日计算特征 PSI（spec §9.3.1）。

    线上分布取自**最近 20 个交易日实际生成的预测**的 features_snapshot ——
    也就是模型真正吃进去的那批特征，而不是重新算一遍的近似值。
    参考分布来自训练窗口（产物里的 psi_reference.json）。

    PSI > 0.20 → 标记漂移（置信度压到 low）；> 0.30 → 停止生成新预测。
    没有线上样本时**不写报告**（没有证据 ≠ 没有漂移，也 ≠ 漂移了）。
    """
    clock = get_clock()
    calendar = get_trading_calendar()
    now = clock.now()
    day = trading_day_of(now)
    store = get_drift_store()

    async with session_scope() as session:
        for horizon in (PredictionHorizon.TODAY_CLOSE, PredictionHorizon.NEXT_5D):
            try:
                model = await active_model(session, horizon=horizon.value)
            except ModelUnavailable:
                logger.info("drift.skip horizon=%s reason=no_active_model", horizon.value)
                continue

            loaded = load_model_bundle(
                model.artifact_uri, model.model_key, model.version, model.target_horizon
            )
            sessions_back = _lookback_sessions(loaded)
            since = _n_sessions_ago(day, sessions_back, calendar)

            snapshots = await _recent_feature_values(session, model.model_key, since)
            if not snapshots:
                logger.info(
                    "drift.skip horizon=%s reason=no_recent_predictions since=%s",
                    horizon.value,
                    since,
                )
                continue

            report = compute_drift(
                model_key=model.model_key,
                reference=loaded.psi_reference,  # 参考分布与阈值随模型版本冻结
                snapshots=snapshots,
                computed_at=now.isoformat(),
                lookback_sessions=sessions_back,
            )
            store.write(report, day)
            logger.info(
                "drift.computed model=%s max_psi=%s drifted=%s blocked=%s samples=%d",
                model.model_key,
                report.max_psi,
                report.drifted,
                report.blocked,
                report.samples,
            )


def _lookback_sessions(loaded: LoadedModel) -> int:
    return load_feature_set(loaded.feature_set_version).psi.lookback_sessions


def _n_sessions_ago(day: date, n: int, calendar: TradingCalendar) -> date:
    try:
        return calendar.previous_trading_day(day, n)
    except LookupError:
        # 日历覆盖不足：退回自然日估算，宁可多取几天样本也不要漏算漂移
        return day - timedelta(days=n * 2)


async def _recent_feature_values(
    session: AsyncSession, model_key: str, since: date
) -> list[dict[str, float | None]]:
    """最近若干交易日内、该模型**实际吃进去的**特征值。"""
    stmt = (
        select(Prediction.features_snapshot)
        .join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
        .where(ModelVersion.model_key == model_key, Prediction.as_of >= _start_of(since))
        .order_by(Prediction.as_of.desc())
        .limit(20000)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict[str, float | None]] = []
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        values = payload.get("values")
        if not isinstance(values, dict):
            continue
        out.append(
            {
                str(key): (float(value) if isinstance(value, int | float) else None)
                for key, value in values.items()
            }
        )
    return out


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=SHANGHAI)
