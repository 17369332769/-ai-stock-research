"""端到端（不碰数据库）：样本回放 → LightGBM 训练 → 落产物 → 读回 → 推理。

这条测试的意义在于：它是唯一一处**真正跑过** LightGBM、校准器、产物读写与预测的地方。
其余测试都在验证边界与不变量；这里验证"整条管线真的能跑通，而且结果自洽"。

数据是合成的、确定性的（没有随机种子问题），因此断言的是**结构性质**
（概率在 [0,1]、p20 <= p80、产物只读、哈希对得上），而不是"准确率必须多少" ——
用合成数据去断言模型好坏是自欺。
"""

from __future__ import annotations

import json
import math
import stat
from datetime import date, datetime
from pathlib import Path

import pytest

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.trading_calendar import StaticTradingCalendar
from services.prediction.evaluation.drift import compute_drift
from services.prediction.features.config import FeatureSetConfig
from services.prediction.features.panel import BENCH_CSI300, BENCH_SSE, DailyBar
from services.prediction.tests.conftest import TEST_SESSIONS, daily_bar
from services.prediction.training.artifacts import (
    artifact_uri_for,
    resolve_artifact_dir,
    write_artifacts,
)
from services.prediction.training.dataset import DatasetManifest, SnapshotManifest
from services.prediction.training.model_config import ModelConfig
from services.prediction.training.samples import InstrumentSeries, MembershipIndex, build_samples
from services.prediction.training.trainer import train_model

pytest.importorskip("lightgbm")
pytest.importorskip("sklearn")
pytest.importorskip("numpy")


# ── 合成数据：确定性、有信号、有噪声 ────────────────────────────────────────


def _series(symbol: str, sessions: list[date], seed: int) -> list[DailyBar]:
    """一条有轻微均值回复信号的确定性价格序列（不用 random，保证可复算）。"""
    bars: list[DailyBar] = []
    price = 100.0 + seed
    for i, day in enumerate(sessions):
        # 确定性的"伪随机"：正弦叠加，不同 seed 相位不同
        shock = math.sin((i + seed * 7) * 0.7) * 0.9 + math.sin((i + seed) * 0.13) * 0.5
        price = max(5.0, price * (1 + shock / 100))
        volume = 1_000_000 * (1 + 0.3 * math.sin((i + seed) * 0.31))
        bars.append(daily_bar(day, round(price, 2), volume=round(volume, 1)))
    return bars


TrainingInputs = tuple[
    list[date],
    dict[str, InstrumentSeries],
    dict[str, InstrumentSeries],
    MembershipIndex,
]


@pytest.fixture
def training_inputs(feature_config: FeatureSetConfig) -> TrainingInputs:
    sessions = [d for d in TEST_SESSIONS if d <= date(2026, 6, 30)]
    symbols = ["600519", "000001", "600036", "601318"]
    series = {
        symbol: InstrumentSeries(symbol=symbol, daily=_series(symbol, sessions, i + 1))
        for i, symbol in enumerate(symbols)
    }
    benchmarks = {
        BENCH_CSI300: InstrumentSeries(symbol="000300", daily=_series("000300", sessions, 11)),
        BENCH_SSE: InstrumentSeries(symbol="000001i", daily=_series("000001i", sessions, 13)),
    }
    universe = MembershipIndex(
        periods={symbol: ((date(2022, 1, 1), None),) for symbol in symbols}
    )
    return sessions, series, benchmarks, universe


def _model_config(horizon: str, model_key: str) -> ModelConfig:
    """轻量训练配置：只为跑通管线，不为跑出好模型。"""
    return ModelConfig(
        model_key=model_key,
        target_horizon=horizon,
        feature_set_version="v1",
        sha256="0" * 64,
        source_path=Path("/dev/null"),
        test_fraction=0.20,
        min_train_sessions=250,
        walk_forward_folds=3,
        validation_sessions=60,
        lower_quantile=0.20,
        upper_quantile=0.80,
        num_boost_round=30,
        early_stopping_rounds=10,
        common_params={
            "learning_rate": 0.1,
            "num_leaves": 7,
            "min_data_in_leaf": 20,
            "verbosity": -1,
            "num_threads": 2,
            "seed": 7,
            "deterministic": True,
        },
        _regressor={"objective": "regression", "metric": "l1"},
        _classifier={"objective": "binary", "metric": "binary_logloss"},
    )


# ── 端到端 ──────────────────────────────────────────────────────────────────


def test_full_pipeline_next_5d(
    training_inputs: TrainingInputs,
    feature_config: FeatureSetConfig,
    calendar: StaticTradingCalendar,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, series, benchmarks, universe = training_inputs

    # 1) 样本回放
    samples, stats = build_samples(
        horizon="next_5d",
        universe=universe,
        series=series,
        benchmarks=benchmarks,
        calendar=calendar,
        config=feature_config,
    )
    assert stats.built == len(samples) > 1000
    assert stats.skipped_not_member == 0
    # 每个样本的特征集哈希都必须是当前 v1 的哈希
    assert {s.snapshot.feature_set_sha256 for s in samples} == {feature_config.sha256}

    # 2) 训练
    model_config = _model_config("next_5d", "a_share_5d_lightgbm")
    model = train_model(
        samples=samples,
        sessions=sessions,
        model_config=model_config,
        feature_config=feature_config,
        version="2026.07.14.1",
        leakage_tests_passed=True,  # 本仓的泄漏测试确实跑过了
    )

    # 切分：train / validation / test 严格时间有序且不重叠
    assert model.train_range.end < model.test_range.start
    assert model.embargo_sessions == 5
    assert len(model.folds) >= 2
    for fold in model.folds:
        assert fold.train.end < fold.validation.start

    # 指标可算且有限
    assert model.validation_metrics.count > 0
    assert model.test_metrics.all_finite
    assert model.test_metrics.brier_score is not None
    assert 0.0 <= model.test_metrics.brier_score <= 1.0
    assert model.test_metrics.interval_coverage is not None

    # 基准都算出来了
    assert model.test_baselines.baseline_brier_score is not None
    assert model.test_baselines.baseline_mae is not None
    assert isinstance(model.better_than_baseline, bool)

    # 当前数据口径下 turnover_rate 恒缺失 → 被如实标为 unavailable
    assert "turnover_rate" in model.unavailable_features

    # PSI 参考分布来自训练窗口
    assert {f.name for f in model.psi_reference.features} == set(feature_config.psi.key_features)

    # 3) 落产物（只读）
    monkeypatch.setenv("PREDICTION_ARTIFACT_ROOT", str(tmp_path))
    dataset = DatasetManifest(
        snapshot_id="test-snapshot",
        created_at="2026-07-14T18:00:00+08:00",
        universe_code="CSI300",
        snapshots=(
            SnapshotManifest(
                name="bars_1d",
                path="test-snapshot/bars_1d.parquet",
                rows=len(samples),
                columns=("instrument", "datetime", "close"),
                min_datetime="2023-01-03T15:00:00+08:00",
                max_datetime="2026-06-30T15:00:00+08:00",
                sha256="a" * 64,
            ),
        ),
    )
    bundle = write_artifacts(
        model,
        model_config=model_config,
        dataset=dataset,
        created_at=datetime(2026, 7, 14, 18, 0, tzinfo=SHANGHAI),
    )

    assert bundle.artifact_uri == artifact_uri_for("a_share_5d_lightgbm", "2026.07.14.1")
    assert bundle.artifact_uri == "file:///models/a_share_5d_lightgbm/2026.07.14.1"

    directory = bundle.directory
    for filename in (
        "model_regressor.txt",
        "model_classifier.txt",
        "model_q20.txt",
        "model_q80.txt",
        "calibrator.json",
        "normalizer.json",
        "psi_reference.json",
        "feature_schema.json",
        "data_manifest.json",
        "metrics.json",
        "provenance.json",
        "model_card.md",
    ):
        path = directory / filename
        assert path.is_file(), f"产物缺失：{filename}"
        # 只读：产物不可变，改模型只能出新版本
        mode = stat.S_IMODE(path.stat().st_mode)
        assert not (mode & stat.S_IWUSR), f"{filename} 应为只读"

    # 产物里记了可复现所需的一切
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["code_commit_sha"]
    assert provenance["dependency_lock_hashes"]
    assert provenance["feature_config_sha256"] == feature_config.sha256
    assert provenance["data_range"]["test"]["start"]

    schema = json.loads((directory / "feature_schema.json").read_text(encoding="utf-8"))
    assert schema["feature_set_sha256"] == feature_config.sha256
    assert schema["names"] == list(model.feature_names)

    card = (directory / "model_card.md").read_text(encoding="utf-8")
    assert "better_than_baseline" in card
    assert "发布门槛" in card
    assert "仅供研究，不构成投资建议" in card

    # 4) 读回并推理
    from services.prediction.inference.loader import clear_model_cache, load_model_bundle

    clear_model_cache()
    try:
        loaded = load_model_bundle(
            bundle.artifact_uri, "a_share_5d_lightgbm", "2026.07.14.1", "next_5d"
        )
        assert loaded.feature_names == model.feature_names
        assert loaded.feature_set_sha256 == feature_config.sha256
        assert resolve_artifact_dir(bundle.artifact_uri) == directory

        row = samples[-1].snapshot.to_model_row(feature_config)
        probability, expected, p20, p80 = loaded.predict_one(row)

        assert 0.0 <= probability <= 1.0, "上涨概率必须是合法概率"
        assert math.isfinite(expected)
        assert p20 <= p80, "p20 必须不大于 p80"
        assert math.isfinite(p20) and math.isfinite(p80)

        # 概率来自方向模型 + 校准器，而不是回归值 —— 换一行特征，概率应随分类器变化
        other = samples[0].snapshot.to_model_row(feature_config)
        other_probability, _, _, _ = loaded.predict_one(other)
        assert 0.0 <= other_probability <= 1.0
    finally:
        clear_model_cache()

    # 5) 漂移：用测试段样本当"线上分布"，PSI 应该很小（同一数据源）
    report = compute_drift(
        model_key="a_share_5d_lightgbm",
        reference=model.psi_reference,
        snapshots=[
            s.snapshot.values for s in samples if model.test_range.contains(s.session)
        ],
        computed_at="2026-07-14T18:00:00+08:00",
        lookback_sessions=20,
    )
    assert report.max_psi is not None
    assert not report.blocked, f"同源数据不该触发阻断级漂移：{report.feature_psi}"


def test_release_gate_fails_without_leakage_tests(
    training_inputs: TrainingInputs,
    feature_config: FeatureSetConfig,
    calendar: StaticTradingCalendar,
) -> None:
    """``leakage_tests_passed`` 默认 False → 发布门槛直接判负（fail closed，spec §9.4）。"""
    sessions, series, benchmarks, universe = training_inputs
    samples, _ = build_samples(
        horizon="next_5d",
        universe=universe,
        series=series,
        benchmarks=benchmarks,
        calendar=calendar,
        config=feature_config,
    )
    model = train_model(
        samples=samples,
        sessions=sessions,
        model_config=_model_config("next_5d", "a_share_5d_lightgbm"),
        feature_config=feature_config,
        version="2026.07.14.2",
        # leakage_tests_passed 不传 → 默认 False
    )

    assert model.release_gate.passed is False
    assert any("泄漏测试" in reason for reason in model.release_gate.reasons)


def test_today_model_trains_with_open_gap_only(
    training_inputs: TrainingInputs,
    feature_config: FeatureSetConfig,
    calendar: StaticTradingCalendar,
) -> None:
    """今日模型在没有分钟线的历史上训练：退化为开盘模型，但仍能跑通。"""
    sessions, series, benchmarks, universe = training_inputs

    samples, stats = build_samples(
        horizon="today_close",
        universe=universe,
        series=series,
        benchmarks=benchmarks,
        calendar=calendar,
        config=feature_config,
    )
    assert stats.built > 1000

    sample = samples[-1]
    # 没有分钟线 → 退化，且 requires=minute_bars 的特征全缺
    assert sample.snapshot.degraded
    assert any(d.reason == "minute_bars_insufficient" for d in sample.snapshot.degradations)
    assert sample.snapshot.values["ret_since_0945"] is None
    # 但开盘缺口在（当日开盘价 09:30 就公开了）
    assert sample.snapshot.values["open_gap"] is not None

    model = train_model(
        samples=samples,
        sessions=sessions,
        model_config=_model_config("today_close", "a_share_today_lightgbm"),
        feature_config=feature_config,
        version="2026.07.14.1",
        leakage_tests_passed=True,
    )
    assert model.embargo_sessions == 1  # today_close 的禁运期
    assert model.release_gate.required_validation_predictions == 120  # 日预测门槛
    assert model.test_metrics.all_finite
    # 分钟线特征在这批数据里恒缺失 → 如实标注
    for name in ("ret_since_0945", "morning_range", "morning_volume_share"):
        assert name in model.unavailable_features
