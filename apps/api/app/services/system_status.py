"""系统状态编排：把三个来源缝成一个只读视图。

1. **数据源健康** —— 来自 worker 写的健康快照文件（``$WORKER_STATE_DIR/worker_health.json``，
   容器内 ``/state`` 只读挂载给 API）。spec §6 的 12 张表里没有数据源健康表，而 worker 是
   独立进程，进程内台账 API 读不到 —— 所以用这个跨进程只读文件，**不是**用假数据糊。
   文件缺失（worker 没起来）时诚实返回 ``failed`` + 原因，绝不谎报 ok。
2. **模型连接** —— 来自 ``model_versions`` 表的 active 版本 + 其 ``validation_metrics``。
3. **Agent 连接** —— 来自配置；未配置即 ``unavailable``（这是允许的降级，不是故障）。

只读：不触发采集、不写库、不改任何状态。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import CSI300_CODE, ModelStatus, PredictionHorizon
from apps.api.app.core.settings import get_settings
from apps.api.app.models.tables import Bar, Document, ModelVersion, Prediction, UniverseMembership
from apps.api.app.schemas.system import (
    AgentConnectionDTO,
    ConnectionStatus,
    DataSourceDTO,
    DataSourceStatus,
    ModelConnectionDTO,
    SystemStatusDTO,
)
from services.worker.runner import HEALTH_FILENAME, state_dir

# 数据源展示名。key 直接使用 worker 注册的 provider 标识，不维护第二套别名。
SOURCE_NAMES: dict[str, str] = {
    "csi300": "中证指数（沪深300 成分）",
    "akshare": "AKShare（行情与新闻）",
    "cn_disclosure": "巨潮资讯 / 交易所（法定公告）",
}

ACTIVE_SOURCES: dict[str, str] = {
    "csi300": "csindex",
    "akshare": "eastmoney_via_akshare",
    "cn_disclosure": "cninfo",
}

# worker 的作业状态 → 对外数据源状态。
# worker 刚启动、作业尚未到调度时间时显示 pending；这不是采集失败。
_WORKER_STATUS_MAP: dict[str, DataSourceStatus] = {
    "healthy": "ok",
    "degraded": "degraded",  # 连续失败已达阈值（spec §8：连续 3 次进入降级）
    "failing": "degraded",  # 有失败但还没到降级阈值
    "disabled": "failed",  # 被 DISABLED_PROVIDERS 关掉（spec §19.2 数据源回滚）
    "never_run": "pending",
}


def _data_source_status(worker_status: str, last_success_at: datetime | None) -> DataSourceStatus:
    """把 Provider 聚合状态转为页面状态。

    一个 Provider 可承载多个作业。盘前报价作业尚未首次运行时，聚合状态会是
    ``never_run``；如果同一 Provider 已有其他成功采集记录，它本身已被证明可用，
    不应被页面标成失败。
    """
    if worker_status == "never_run" and last_success_at is not None:
        return "ok"
    return _WORKER_STATUS_MAP.get(worker_status, "failed")

# 特征漂移阈值（spec §9.3.1）：> 0.30 停止生成新预测并返回 MODEL_UNAVAILABLE。
PSI_HALT_THRESHOLD = 0.30
PSI_DRIFT_THRESHOLD = 0.20


def _health_snapshot_path() -> Path:
    return state_dir() / HEALTH_FILENAME


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_data_sources() -> list[DataSourceDTO]:
    """读 worker 健康快照。文件不存在/损坏 → 全部标 failed 并说明原因，不谎报 ok。"""
    path = _health_snapshot_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [
            DataSourceDTO(
                key=key,
                name=name,
                status="failed",
                active_source=ACTIVE_SOURCES[key],
                last_success_at=None,
                consecutive_failures=0,
                last_error_code="WORKER_UNAVAILABLE",
                last_error_message=f"worker 健康快照不存在（{path}）：采集进程可能未启动",
            )
            for key, name in SOURCE_NAMES.items()
        ]
    except (OSError, ValueError) as exc:
        return [
            DataSourceDTO(
                key=key,
                name=name,
                status="failed",
                active_source=ACTIVE_SOURCES[key],
                last_success_at=None,
                consecutive_failures=0,
                last_error_code="WORKER_HEALTH_UNREADABLE",
                last_error_message=f"worker 健康快照不可读：{type(exc).__name__}",
            )
            for key, name in SOURCE_NAMES.items()
        ]

    providers: dict[str, Any] = raw.get("providers") or {}
    jobs: dict[str, Any] = raw.get("jobs") or {}

    # 每个数据源的连续失败次数取其名下作业的最大值（最坏情况即该源的处境）。
    failures_by_provider: dict[str, int] = {}
    errors_by_provider: dict[str, str] = {}
    jobs_by_provider: dict[str, list[dict[str, Any]]] = {}
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        provider = str(job.get("provider", ""))
        jobs_by_provider.setdefault(provider, []).append(job)
        count = int(job.get("consecutive_failures") or 0)
        if count > failures_by_provider.get(provider, -1):
            failures_by_provider[provider] = count
            if job.get("last_error"):
                errors_by_provider[provider] = str(job["last_error"])

    out: list[DataSourceDTO] = []
    for key, name in SOURCE_NAMES.items():
        provider_jobs = jobs_by_provider.get(key, [])
        next_runs = [
            moment
            for moment in (_parse_dt(job.get("next_run_at")) for job in provider_jobs)
            if moment is not None
        ]
        failing_jobs = [
            str(job.get("title") or job.get("job_id") or "未知作业")
            for job in provider_jobs
            if str(job.get("status")) in {"failing", "degraded", "disabled"}
        ]
        entry = providers.get(key)
        if not isinstance(entry, dict):
            out.append(
                DataSourceDTO(
                    key=key,
                    name=name,
                    status="failed",
                    active_source=ACTIVE_SOURCES[key],
                    next_run_at=min(next_runs) if next_runs else None,
                    job_count=len(provider_jobs),
                    failing_jobs=failing_jobs,
                    last_error_code="NO_JOB_REGISTERED",
                    last_error_message="worker 未注册该数据源的采集作业",
                )
            )
            continue
        worker_status = str(entry.get("status", "never_run"))
        last_success_at = _parse_dt(entry.get("last_success_at"))
        out.append(
            DataSourceDTO(
                key=key,
                name=name,
                status=_data_source_status(worker_status, last_success_at),
                active_source=ACTIVE_SOURCES[key],
                last_success_at=last_success_at,
                consecutive_failures=failures_by_provider.get(key, 0),
                next_run_at=min(next_runs) if next_runs else None,
                job_count=len(provider_jobs),
                failing_jobs=failing_jobs,
                last_error_code=None,
                last_error_message=errors_by_provider.get(key),
            )
        )
    return out


async def _source_coverage(session: AsyncSession, as_of: datetime) -> dict[str, tuple[int, int]]:
    active_members = select(UniverseMembership.symbol).where(
        UniverseMembership.universe_code == CSI300_CODE,
        UniverseMembership.effective_from <= as_of.date(),
        or_(
            UniverseMembership.effective_to.is_(None),
            UniverseMembership.effective_to >= as_of.date(),
        ),
    )
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(active_members.subquery())
            )
        ).scalar_one()
        or 0
    )
    market_coverage = int(
        (
            await session.execute(
                select(func.count(distinct(Bar.symbol))).where(
                    Bar.timeframe == "1d",
                    Bar.symbol.in_(active_members),
                )
            )
        ).scalar_one()
        or 0
    )
    disclosure_coverage = int(
        (
            await session.execute(
                select(func.count(distinct(Document.symbol))).where(
                    Document.document_type == "announcement",
                    Document.symbol.in_(active_members),
                )
            )
        ).scalar_one()
        or 0
    )
    return {
        "csi300": (total, total),
        "akshare": (market_coverage, total),
        "cn_disclosure": (disclosure_coverage, total),
    }


def _model_status(metrics: dict[str, Any]) -> tuple[ConnectionStatus, str | None]:
    """按 PSI 漂移判定模型连接状态（spec §9.3.1）。"""
    psi = metrics.get("max_feature_psi")
    if isinstance(psi, int | float):
        if psi > PSI_HALT_THRESHOLD:
            return "unavailable", (
                f"关键特征 PSI {psi:.2f} 超过 {PSI_HALT_THRESHOLD}，已停止生成新预测"
            )
        if psi > PSI_DRIFT_THRESHOLD:
            return "degraded", f"关键特征 PSI {psi:.2f} 超过 {PSI_DRIFT_THRESHOLD}，已标记漂移"
    return "active", None


async def read_models(session: AsyncSession) -> list[ModelConnectionDTO]:
    """现役模型。没有 active 版本时返回空列表 —— 前端据此显示「模型不可用」。"""
    rows = (
        await session.execute(
            select(ModelVersion)
            .where(ModelVersion.status == ModelStatus.ACTIVE.value)
            .order_by(ModelVersion.model_key, ModelVersion.created_at.desc())
        )
    ).scalars()

    out: list[ModelConnectionDTO] = []
    for model in rows:
        metrics = model.validation_metrics or {}
        status, reason = _model_status(metrics)
        better = metrics.get("better_than_baseline")
        last_at = (
            await session.execute(
                select(Prediction.as_of)
                .where(Prediction.model_version_id == model.id)
                .order_by(Prediction.as_of.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        out.append(
            ModelConnectionDTO(
                model_key=model.model_key,
                horizon=PredictionHorizon(model.target_horizon),
                status=status,
                active_version=model.version,
                better_than_baseline=better if isinstance(better, bool) else None,
                last_prediction_at=last_at,
                reason=reason,
            )
        )
    return out


def read_agent() -> AgentConnectionDTO:
    """Agent 连接。未配置是**允许的降级**（分析走模板摘要），不是故障。"""
    settings = get_settings()
    if not settings.agent_enabled:
        return AgentConnectionDTO(
            provider=None,
            model_name=None,
            status="unavailable",
            reason="未配置 AGENT_BASE_URL / AGENT_MODEL：分析降级为模板摘要，方向恒为 unknown",
        )
    return AgentConnectionDTO(
        provider=settings.agent_base_url,
        model_name=settings.agent_model,
        status="active",
        reason=None,
    )


async def get_system_status(session: AsyncSession, now: datetime) -> SystemStatusDTO:
    sources = read_data_sources()
    coverage = await _source_coverage(session, now)
    sources = [
        source.model_copy(
            update={"coverage": coverage[source.key][0], "total": coverage[source.key][1]}
        )
        for source in sources
    ]
    return SystemStatusDTO(
        sources=sources,
        models=await read_models(session),
        agent=read_agent(),
    )
