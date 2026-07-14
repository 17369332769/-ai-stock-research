"""切分、指标、基准、校准（spec §9.3 / §9.3.1 / §9.4）。"""

from __future__ import annotations

from datetime import date

import pytest

from services.prediction.tests.conftest import TEST_SESSIONS
from services.prediction.training.baselines import (
    BaselineParams,
    better_than_baseline,
    evaluate_baselines,
    fit_baseline_params,
)
from services.prediction.training.calibration import (
    ECE_ACCEPTABLE,
    MIN_ISOTONIC_SAMPLES,
    Calibrator,
    fit_calibrator,
)
from services.prediction.training.labels import (
    compute_label,
    direction_up,
    horizon_embargo_sessions,
    target_session_for,
    training_cutoff_for,
)
from services.prediction.training.metrics import (
    MetricSet,
    brier_score,
    direction_accuracy,
    expected_calibration_error,
    interval_coverage,
    mean_absolute_error,
)
from services.prediction.training.splits import SplitError, make_walk_forward_folds

SESSIONS = [d for d in TEST_SESSIONS if date(2023, 1, 3) <= d <= date(2026, 6, 30)]


# ── 标签 ────────────────────────────────────────────────────────────────────


def test_direction_label_zero_is_not_up() -> None:
    """spec §9.1：**大于** 0 才算上涨；恰好 0 记为非上涨。"""
    assert direction_up(0.0001) is True
    assert direction_up(0.0) is False
    assert direction_up(-0.01) is False


def test_embargo_matches_horizon() -> None:
    assert horizon_embargo_sessions("next_5d") == 5
    assert horizon_embargo_sessions("today_close") == 1
    with pytest.raises(ValueError, match="未知 horizon"):
        horizon_embargo_sessions("next_10d")


def test_today_cutoff_is_0945(calendar) -> None:  # type: ignore[no-untyped-def]
    cutoff = training_cutoff_for(date(2026, 7, 14), "today_close")
    assert (cutoff.hour, cutoff.minute) == (9, 45)

    close_cutoff = training_cutoff_for(date(2026, 7, 14), "next_5d")
    assert (close_cutoff.hour, close_cutoff.minute) == (15, 0)


def test_label_uses_trading_calendar(calendar) -> None:  # type: ignore[no-untyped-def]
    label = compute_label(
        session=date(2025, 9, 26),
        horizon="next_5d",
        reference_price=100.0,
        target_price=105.0,
        calendar=calendar,
    )
    assert label.target_session == date(2025, 10, 13)  # 跨国庆
    assert label.target_return == pytest.approx(0.05)
    assert label.up is True


def test_target_session_for_today_is_same_day(calendar) -> None:  # type: ignore[no-untyped-def]
    assert target_session_for(date(2026, 7, 14), "today_close", calendar) == date(2026, 7, 14)


# ── walk-forward ────────────────────────────────────────────────────────────


def test_walk_forward_is_expanding_and_non_overlapping() -> None:
    folds = make_walk_forward_folds(
        SESSIONS, embargo_sessions=5, n_folds=5, min_train_sessions=250, validation_sessions=60
    )
    assert len(folds) >= 2

    for i, fold in enumerate(folds):
        # 训练段总是从最早的交易日开始（expanding window）
        assert fold.train.start == SESSIONS[0]
        # 验证段严格在训练段之后
        assert fold.train.end < fold.validation.start
        if i > 0:
            # 训练窗口只增不减
            assert fold.train.end >= folds[i - 1].train.end
            # 验证窗口向前滚动
            assert fold.validation.start >= folds[i - 1].validation.start


def test_walk_forward_respects_embargo() -> None:
    folds = make_walk_forward_folds(
        SESSIONS, embargo_sessions=5, n_folds=3, min_train_sessions=250, validation_sessions=60
    )
    index = {day: i for i, day in enumerate(SESSIONS)}
    for fold in folds:
        gap = index[fold.validation.start] - index[fold.train.end] - 1
        assert gap >= 5, "训练段与验证段之间必须至少隔 5 个交易日"


def test_walk_forward_rejects_short_history() -> None:
    with pytest.raises(SplitError, match="不足以做"):
        make_walk_forward_folds(
            SESSIONS[:100],
            embargo_sessions=5,
            n_folds=5,
            min_train_sessions=250,
            validation_sessions=60,
        )


# ── 指标 ────────────────────────────────────────────────────────────────────


def test_metrics_on_empty_sample_are_none_not_zero() -> None:
    """0 个样本 → 指标是 None。返回 0 会让"什么都没做"看起来完美。"""
    assert brier_score([], []) is None
    assert direction_accuracy([], []) is None
    assert mean_absolute_error([], []) is None
    assert interval_coverage([], [], []) is None

    metrics = MetricSet.compute(
        probabilities=[], expected_returns=[], actual_returns=[], lower=[], upper=[]
    )
    assert metrics.count == 0
    assert metrics.brier_score is None
    assert metrics.all_finite is False  # 发布门槛必须因此判负


def test_brier_and_direction_accuracy() -> None:
    probabilities = [0.9, 0.2, 0.6, 0.4]
    outcomes = [True, False, False, True]

    expected_brier = (0.01 + 0.04 + 0.36 + 0.36) / 4
    assert brier_score(probabilities, outcomes) == pytest.approx(expected_brier)
    # 方向：p>=0.5 → 上涨。命中 [T, T, F, F] → 2/4
    assert direction_accuracy(probabilities, outcomes) == pytest.approx(0.5)


def test_interval_coverage() -> None:
    lower = [-0.05, -0.02, 0.0]
    upper = [0.05, 0.02, 0.10]
    actual = [0.01, 0.05, 0.03]  # 第 2 个落在区间外
    assert interval_coverage(lower, upper, actual) == pytest.approx(2 / 3)


def test_perfect_calibration_has_zero_ece() -> None:
    probabilities = [0.0] * 50 + [1.0] * 50
    outcomes = [False] * 50 + [True] * 50
    assert expected_calibration_error(probabilities, outcomes) == pytest.approx(0.0)


# ── 基准与发布判定 ──────────────────────────────────────────────────────────


def test_baseline_params_fit_on_train_window() -> None:
    returns = [0.01, -0.02, 0.03, -0.01, 0.0]  # 2 涨 3 不涨（0 不算涨）
    params = fit_baseline_params(returns)
    assert params.constant_probability == pytest.approx(0.4)
    assert params.historical_mean_return == pytest.approx(0.002)
    assert params.fitted_on_samples == 5


def test_better_than_baseline_requires_both_metrics_strictly_better() -> None:
    """spec §9.3.1：**当且仅当** Brier 和 MAE **均严格**优于基准。"""
    # 两个都更好 → True
    assert better_than_baseline(
        model_brier=0.20, model_mae=0.015, baseline_brier=0.25, baseline_mae=0.018
    )
    # 只有 Brier 更好 → False
    assert not better_than_baseline(
        model_brier=0.20, model_mae=0.020, baseline_brier=0.25, baseline_mae=0.018
    )
    # 只有 MAE 更好 → False
    assert not better_than_baseline(
        model_brier=0.30, model_mae=0.015, baseline_brier=0.25, baseline_mae=0.018
    )
    # 打平 → False（必须**严格**优于）
    assert not better_than_baseline(
        model_brier=0.25, model_mae=0.018, baseline_brier=0.25, baseline_mae=0.018
    )
    # 指标缺失 → False（fail closed）
    assert not better_than_baseline(
        model_brier=None, model_mae=0.015, baseline_brier=0.25, baseline_mae=0.018
    )
    assert not better_than_baseline(
        model_brier=float("nan"), model_mae=0.015, baseline_brier=0.25, baseline_mae=0.018
    )


def test_evaluate_baselines_includes_csi300_direction() -> None:
    params = BaselineParams(
        constant_probability=0.55, historical_mean_return=0.001, fitted_on_samples=100
    )
    actual = [0.02, -0.01, 0.03]
    market = [0.01, 0.01, 0.02]  # 大盘方向：涨 涨 涨；实际：涨 跌 涨 → 2/3

    baselines = evaluate_baselines(params, actual_returns=actual, benchmark_returns=market)
    assert baselines.count == 3
    assert baselines.baseline_csi300_direction_accuracy == pytest.approx(2 / 3)
    # 恒定概率 0.55 >= 0.5 → 永远猜涨 → 命中 2/3
    assert baselines.baseline_direction_accuracy == pytest.approx(2 / 3)
    assert baselines.baseline_brier_score is not None
    assert baselines.baseline_mae is not None


# ── 校准 ────────────────────────────────────────────────────────────────────


def test_calibrator_json_roundtrip() -> None:
    original = Calibrator(
        method="isotonic", thresholds_x=(0.1, 0.5, 0.9), thresholds_y=(0.0, 0.4, 1.0)
    )
    restored = Calibrator.from_json(original.to_json())
    assert restored == original
    for probability in (0.0, 0.3, 0.5, 0.7, 1.0):
        assert restored.apply(probability) == pytest.approx(original.apply(probability))


def test_calibrator_clamps_to_unit_interval() -> None:
    calibrator = Calibrator(method="isotonic", thresholds_x=(0.2, 0.8), thresholds_y=(0.0, 1.0))
    assert calibrator.apply(-5.0) == 0.0
    assert calibrator.apply(5.0) == 1.0
    assert 0.0 <= calibrator.apply(0.5) <= 1.0


def test_platt_degradation_below_200_validation_samples() -> None:
    """spec §9.3.1：验证样本 < 200 → 降级 Platt，并记录降级原因。"""
    pytest.importorskip("sklearn")

    n = 100
    probabilities = [i / n for i in range(n)]
    outcomes = [i / n > 0.5 for i in range(n)]

    calibrator, report = fit_calibrator(probabilities, outcomes)

    assert n < MIN_ISOTONIC_SAMPLES
    assert calibrator.method == "platt"
    assert report.degraded is True
    assert report.degraded_reason is not None
    assert "Platt" in report.degraded_reason
    assert report.validation_samples == n


def test_isotonic_used_with_enough_samples() -> None:
    pytest.importorskip("sklearn")

    n = 400
    probabilities = [(i % 100) / 100 for i in range(n)]
    outcomes = [(i % 100) / 100 > 0.5 for i in range(n)]

    calibrator, report = fit_calibrator(probabilities, outcomes)

    assert calibrator.method == "isotonic"
    assert report.degraded is False
    assert report.degraded_reason is None
    assert report.brier_after is not None and report.brier_before is not None
    assert report.brier_after <= report.brier_before


def test_calibration_acceptance_criterion() -> None:
    pytest.importorskip("sklearn")

    n = 400
    # 一个系统性高估的模型：概率都偏高
    probabilities = [0.9 for _ in range(n)]
    outcomes = [i % 2 == 0 for i in range(n)]  # 真实频率 50%

    calibrator, report = fit_calibrator(probabilities, outcomes)
    calibrated = calibrator.apply_many(probabilities)

    # 校准后应把 0.9 拉回 ~0.5
    assert calibrated[0] == pytest.approx(0.5, abs=0.05)
    assert report.ece_before is not None and report.ece_after is not None
    assert report.ece_after < report.ece_before
    assert report.is_acceptable
    assert report.to_json()["criterion"].startswith(f"ECE <= {ECE_ACCEPTABLE}")


def test_empty_validation_set_fails_closed() -> None:
    """验证集为空 → 抛错。绝不"就用未校准概率算了"。"""
    with pytest.raises(ValueError, match="验证集为空"):
        fit_calibrator([], [])


def test_single_class_validation_degrades_to_identity() -> None:
    """验证集只有一个方向 → 无法校准，保持恒等并标记降级（而不是拟合噪声）。"""
    calibrator, report = fit_calibrator([0.6, 0.7, 0.8], [True, True, True])
    assert calibrator.method == "identity"
    assert report.degraded is True
    assert calibrator.apply(0.7) == pytest.approx(0.7)
