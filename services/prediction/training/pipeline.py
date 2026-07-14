"""训练流水线：导快照 → 回放样本 → 训练 → 落产物 → 注册 candidate。

一条命令走完 spec §9.3 / §9.3.1 的全部步骤。**永远只写 candidate** ——
激活是独立的一步（``registry.activate``），且必须先过发布门槛。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.runtime import get_clock, get_trading_calendar
from apps.api.app.core.trading_calendar import TradingCalendar
from services.prediction.features.config import FeatureSetConfig, load_feature_set
from services.prediction.training.artifacts import ArtifactBundle, write_artifacts
from services.prediction.training.dataset import (
    DatasetManifest,
    dataset_root,
    export_snapshot,
    load_membership_index,
    load_series_from_snapshot,
)
from services.prediction.training.model_config import ModelConfig, load_model_config
from services.prediction.training.qlib_dataset import QlibDatasetLayout, build_qlib_dataset
from services.prediction.training.registry import register_candidate
from services.prediction.training.samples import SampleBuildStats, build_samples
from services.prediction.training.trainer import TrainedModel, train_model

__all__ = ["TrainingRun", "make_version", "run_training"]


def make_version(moment: datetime, sequence: int = 1) -> str:
    """版本号形如 ``2026.07.14.1``（spec §7.4 示例）。"""
    return f"{moment.year}.{moment.month:02d}.{moment.day:02d}.{sequence}"


@dataclass(frozen=True, slots=True)
class TrainingRun:
    model: TrainedModel
    bundle: ArtifactBundle
    dataset: DatasetManifest
    qlib: QlibDatasetLayout | None
    sample_stats: SampleBuildStats
    model_version_id: Any

    def summary(self) -> dict[str, Any]:
        return {
            "model_key": self.model.model_key,
            "version": self.model.version,
            "horizon": self.model.target_horizon,
            "artifact_uri": self.bundle.artifact_uri,
            "better_than_baseline": self.model.better_than_baseline,
            "release_gate_passed": self.model.release_gate.passed,
            "release_gate_reasons": list(self.model.release_gate.reasons),
            "validation_predictions": self.model.validation_metrics.count,
            "samples": self.sample_stats.to_json(),
            "dataset_snapshot_id": self.dataset.snapshot_id,
        }


async def run_training(
    session: AsyncSession,
    *,
    horizon: str,
    snapshot_id: str | None = None,
    version: str | None = None,
    leakage_tests_passed: bool = False,
    build_qlib: bool = True,
    calendar: TradingCalendar | None = None,
    feature_set_version: str | None = None,
) -> TrainingRun:
    now = get_clock().now()
    trading_calendar = calendar or get_trading_calendar()

    from services.prediction.training.registry import model_key_for_horizon

    model_key = model_key_for_horizon(horizon)
    model_config: ModelConfig = load_model_config(model_key)
    if model_config.target_horizon != horizon:
        raise ValueError(f"{model_key} 的 target_horizon 是 {model_config.target_horizon}，不是 {horizon}")

    feature_config: FeatureSetConfig = load_feature_set(
        feature_set_version or model_config.feature_set_version
    )

    snapshot = snapshot_id or f"{now:%Y%m%d}-{horizon}"
    dataset = await export_snapshot(
        session,
        snapshot_id=snapshot,
        end=now,
        created_at=now,
        include_minute=horizon == "today_close",
    )
    directory = dataset_root() / snapshot

    series, benchmarks = load_series_from_snapshot(
        directory, dataset, benchmark_symbols=feature_config.benchmarks
    )
    universe = load_membership_index(directory, dataset)

    samples, stats = build_samples(
        horizon=horizon,
        universe=universe,
        series=series,
        benchmarks=benchmarks,
        calendar=trading_calendar,
        config=feature_config,
    )

    sessions = _sessions_from(series, trading_calendar)
    qlib_layout: QlibDatasetLayout | None = None
    if build_qlib:
        qlib_layout = build_qlib_dataset(
            root=directory / "qlib",
            sessions=sessions,
            series=series,
            universe=universe,
        )

    resolved_version = version or make_version(now)
    model = train_model(
        samples=samples,
        sessions=sessions,
        model_config=model_config,
        feature_config=feature_config,
        version=resolved_version,
        leakage_tests_passed=leakage_tests_passed,
    )

    bundle = write_artifacts(
        model, model_config=model_config, dataset=dataset, created_at=now
    )
    model_version_id = await register_candidate(
        session,
        model=model,
        bundle=bundle,
        train_start=model.train_range.start,
        train_end=model.train_range.end,
    )

    return TrainingRun(
        model=model,
        bundle=bundle,
        dataset=dataset,
        qlib=qlib_layout,
        sample_stats=stats,
        model_version_id=model_version_id,
    )


def _sessions_from(series: dict[str, Any], calendar: TradingCalendar) -> list[date]:
    """数据里出现过、且是真实交易日的日期集合。"""
    days: set[date] = set()
    for item in series.values():
        days.update(item.sessions)
    return sorted(day for day in days if calendar.is_trading_day(day))
