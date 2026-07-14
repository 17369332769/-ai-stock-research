"""LightGBM 训练（spec §9.3 / §9.3.1 / §9.4）。

管线（每一步的边界都是为了不泄漏）：

    全部交易日
      ├─ dev  = 前 (1 - test_fraction)，再留 embargo
      │    └─ expanding-window walk-forward：每折在 fold.train 上训练，在 fold.validation 上预测
      │         → 汇总出**样本外**验证预测（这就是 spec §9.4 的"验证覆盖"）
      │         → 概率校准器在这批样本外预测上拟合（isotonic / <200 降级 Platt）
      │         → 归一化参数只在 dev 上拟合（analogs 用）
      ├─ embargo（= horizon 的交易日数，避免训练标签看到测试期价格）
      └─ test = 最后一段，**只**用来跟基准比，不参与任何拟合

四个 booster：
    regressor  → expected_return
    classifier → probability_up（**上涨概率只来自方向模型**，绝不由回归值反推）
    q20 / q80  → 区间（LightGBM 原生 quantile objective）

最终模型在整个 dev 上重训，轮数取各折早停轮数的中位数 —— 测试段全程不参与任何选择。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from apps.api.app.core.enums import PredictionHorizon
from services.prediction.evaluation.drift import PsiReference, build_psi_reference
from services.prediction.features.config import FeatureSetConfig
from services.prediction.training.baselines import (
    BaselineParams,
    BaselineSet,
    better_than_baseline,
    evaluate_baselines,
    fit_baseline_params,
)
from services.prediction.training.calibration import (
    CalibrationReport,
    Calibrator,
    fit_calibrator,
)
from services.prediction.training.labels import horizon_embargo_sessions
from services.prediction.training.metrics import MetricSet
from services.prediction.training.model_config import ModelConfig
from services.prediction.training.samples import Sample
from services.prediction.training.splits import (
    DateRange,
    Fold,
    SplitError,
    assert_time_ordered,
    make_walk_forward_folds,
)

__all__ = [
    "Normalizer",
    "ReleaseGate",
    "TrainedModel",
    "train_model",
]

# spec §9.4：验证覆盖不少于 120 个日预测或 60 个周预测
MIN_VALIDATION_PREDICTIONS: dict[str, int] = {
    PredictionHorizon.TODAY_CLOSE.value: 120,
    PredictionHorizon.NEXT_5D.value: 60,
}


@dataclass(frozen=True, slots=True)
class Normalizer:
    """z-score 参数。**只在训练窗口拟合**（spec §9.3）。

    LightGBM 是树模型，不需要归一化 —— 这份参数是给「历史相似行情」用的（spec §10：
    "用训练集均值和标准差标准化"）。放在这里是为了保证它和模型同源、同窗口、同版本。
    """

    names: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]

    def standardize(self, values: Sequence[float | None]) -> list[float | None]:
        out: list[float | None] = []
        for value, mu, sigma in zip(values, self.mean, self.std, strict=True):
            if value is None:
                out.append(None)
            elif sigma == 0:
                out.append(0.0)
            else:
                out.append((value - mu) / sigma)
        return out

    def to_json(self) -> dict[str, Any]:
        return {"names": list(self.names), "mean": list(self.mean), "std": list(self.std)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Normalizer:
        return cls(
            names=tuple(data["names"]),
            mean=tuple(float(x) for x in data["mean"]),
            std=tuple(float(x) for x in data["std"]),
        )


@dataclass(frozen=True, slots=True)
class ReleaseGate:
    """spec §9.4 的发布门槛。``passed=False`` 的候选**不得**成为 active。"""

    leakage_tests_passed: bool
    validation_predictions: int
    required_validation_predictions: int
    metrics_finite: bool
    interval_coverage_visible: bool
    reasons: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.reasons

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "leakage_tests_passed": self.leakage_tests_passed,
            "validation_predictions": self.validation_predictions,
            "required_validation_predictions": self.required_validation_predictions,
            "metrics_finite": self.metrics_finite,
            "interval_coverage_visible": self.interval_coverage_visible,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class TrainedModel:
    model_key: str
    version: str
    target_horizon: str
    feature_set_version: str
    feature_set_sha256: str
    feature_names: tuple[str, ...]
    unavailable_features: tuple[str, ...]

    boosters: dict[str, Any]  # 'regressor' | 'classifier' | 'q20' | 'q80' -> lgb.Booster
    calibrator: Calibrator
    calibration: CalibrationReport
    normalizer: Normalizer
    baseline_params: BaselineParams
    psi_reference: PsiReference

    train_range: DateRange
    validation_range: DateRange
    test_range: DateRange
    embargo_sessions: int
    folds: list[Fold]

    validation_metrics: MetricSet
    test_metrics: MetricSet
    test_baselines: BaselineSet
    better_than_baseline: bool
    release_gate: ReleaseGate

    train_samples: int
    test_samples: int
    best_iterations: dict[str, int] = field(default_factory=dict)

    def metrics_json(self) -> dict[str, Any]:
        """写入 ``model_versions.validation_metrics``（JSONB）。

        推理侧的置信度判定（spec §9.5）就靠这里的 better_than_baseline / 验证样本数 /
        校准是否合格 —— 所以这三样必须随模型版本冻结，不能事后再算。
        """
        return {
            "feature_set_version": self.feature_set_version,
            "feature_set_sha256": self.feature_set_sha256,
            "better_than_baseline": self.better_than_baseline,
            "validation": self.validation_metrics.to_json(),
            "validation_predictions": self.validation_metrics.count,
            "required_validation_predictions": self.release_gate.required_validation_predictions,
            "test": self.test_metrics.to_json(),
            "test_baselines": self.test_baselines.to_json(),
            "baseline_params": self.baseline_params.to_json(),
            "calibration": self.calibration.to_json(),
            "calibration_acceptable": self.calibration.is_acceptable,
            "release_gate": self.release_gate.to_json(),
            "splits": {
                "train": self.train_range.to_json(),
                "validation": self.validation_range.to_json(),
                "test": self.test_range.to_json(),
                "embargo_sessions": self.embargo_sessions,
                "walk_forward_folds": [fold.to_json() for fold in self.folds],
            },
            "sample_counts": {
                "train": self.train_samples,
                "validation": self.validation_metrics.count,
                "test": self.test_samples,
            },
            "unavailable_features": list(self.unavailable_features),
            "best_iterations": self.best_iterations,
        }


# ── 训练 ────────────────────────────────────────────────────────────────────


def _matrix(samples: Sequence[Sample], config: FeatureSetConfig) -> Any:
    import numpy as np

    return np.array(
        [sample.snapshot.to_model_row(config) for sample in samples], dtype="float64"
    )


def _returns(samples: Sequence[Sample]) -> list[float]:
    return [sample.target_return for sample in samples]


def _labels(samples: Sequence[Sample]) -> list[int]:
    return [1 if sample.up else 0 for sample in samples]


def _in_range(samples: Sequence[Sample], rng: DateRange) -> list[Sample]:
    return [sample for sample in samples if rng.contains(sample.session)]


def _train_booster(
    params: dict[str, Any],
    x: Any,
    y: Sequence[float],
    *,
    num_boost_round: int,
    feature_names: Sequence[str],
    valid_x: Any = None,
    valid_y: Sequence[float] | None = None,
    early_stopping_rounds: int | None = None,
) -> tuple[Any, int]:
    import lightgbm as lgb

    train_set = lgb.Dataset(x, label=list(y), feature_name=list(feature_names), free_raw_data=False)
    valid_sets: list[Any] = []
    callbacks: list[Any] = []
    if valid_x is not None and valid_y is not None and len(valid_y) > 0:
        valid_sets.append(
            lgb.Dataset(
                valid_x, label=list(valid_y), feature_name=list(feature_names), reference=train_set
            )
        )
        if early_stopping_rounds:
            callbacks.append(lgb.early_stopping(early_stopping_rounds, verbose=False))
    callbacks.append(lgb.log_evaluation(period=0))
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=num_boost_round,
        valid_sets=valid_sets or None,
        callbacks=callbacks,
    )
    best = int(getattr(booster, "best_iteration", 0) or num_boost_round)
    return booster, best


def _fit_normalizer(
    samples: Sequence[Sample], names: Sequence[str]
) -> Normalizer:
    means: list[float] = []
    stds: list[float] = []
    for name in names:
        values = [
            sample.snapshot.values[name]
            for sample in samples
            if sample.snapshot.values.get(name) is not None
        ]
        clean = [float(v) for v in values if v is not None]
        if len(clean) < 2:
            means.append(0.0)
            stds.append(0.0)  # 全缺失/常数列：标准化后恒为 0，相似度里自然不起作用
            continue
        means.append(statistics.fmean(clean))
        stds.append(statistics.stdev(clean))
    return Normalizer(names=tuple(names), mean=tuple(means), std=tuple(stds))


def train_model(
    *,
    samples: Sequence[Sample],
    sessions: Sequence[date],
    model_config: ModelConfig,
    feature_config: FeatureSetConfig,
    version: str,
    leakage_tests_passed: bool = False,
) -> TrainedModel:
    """训练一个模型版本。

    ``leakage_tests_passed`` 必须由**跑过泄漏测试的流水线**显式传 True。
    默认 False —— 没人证明过就当作没通过，发布门槛直接判负（fail closed，spec §9.4）。
    """
    import numpy as np

    horizon = model_config.target_horizon
    if not samples:
        raise SplitError("没有任何训练样本")

    feature_names = samples[0].snapshot.names
    for sample in samples:
        if sample.snapshot.names != feature_names:
            raise SplitError("样本之间的特征顺序不一致 —— 特征集版本混用了")
        if sample.snapshot.feature_set_sha256 != feature_config.sha256:
            raise SplitError("样本的特征集哈希与当前配置不一致 —— 训练期间配置被改过")

    embargo = horizon_embargo_sessions(horizon)
    ordered = sorted(set(sessions))
    sample_sessions = sorted({sample.session for sample in samples})
    if not sample_sessions:
        raise SplitError("样本没有有效交易日")

    # ── 切 dev / test（test 只用于跟基准比）────────────────────────────
    usable = [day for day in ordered if sample_sessions[0] <= day <= sample_sessions[-1]]
    total = len(usable)
    test_len = max(1, int(total * model_config.test_fraction))
    dev_len = total - test_len - embargo
    if dev_len < model_config.min_train_sessions:
        raise SplitError(
            f"可用交易日 {total} 太少：dev 段只剩 {dev_len} 个，"
            f"少于 min_train_sessions={model_config.min_train_sessions}"
        )
    dev_range = DateRange(usable[0], usable[dev_len - 1])
    test_range = DateRange(usable[dev_len + embargo], usable[-1])
    assert_time_ordered(dev_range, test_range, embargo_sessions=embargo, sessions=usable)

    dev_sessions = usable[:dev_len]
    dev_samples = _in_range(samples, dev_range)
    test_samples = _in_range(samples, test_range)
    if not dev_samples or not test_samples:
        raise SplitError("dev 或 test 段没有样本")

    # ── walk-forward：产出样本外验证预测 ────────────────────────────────
    folds = make_walk_forward_folds(
        dev_sessions,
        embargo_sessions=embargo,
        n_folds=model_config.walk_forward_folds,
        min_train_sessions=model_config.min_train_sessions,
        validation_sessions=model_config.validation_sessions,
    )

    oos_probabilities: list[float] = []
    oos_expected: list[float] = []
    oos_actual: list[float] = []
    oos_lower: list[float] = []
    oos_upper: list[float] = []
    fold_best: list[int] = []

    for fold in folds:
        fold_train = _in_range(dev_samples, fold.train)
        fold_valid = _in_range(dev_samples, fold.validation)
        if len(fold_train) < 50 or not fold_valid:
            continue
        x_train = _matrix(fold_train, feature_config)
        x_valid = _matrix(fold_valid, feature_config)

        classifier, best_cls = _train_booster(
            model_config.classifier_params,
            x_train,
            _labels(fold_train),
            num_boost_round=model_config.num_boost_round,
            feature_names=feature_names,
            valid_x=x_valid,
            valid_y=_labels(fold_valid),
            early_stopping_rounds=model_config.early_stopping_rounds,
        )
        regressor, best_reg = _train_booster(
            model_config.regressor_params,
            x_train,
            _returns(fold_train),
            num_boost_round=model_config.num_boost_round,
            feature_names=feature_names,
            valid_x=x_valid,
            valid_y=_returns(fold_valid),
            early_stopping_rounds=model_config.early_stopping_rounds,
        )
        q_low, _ = _train_booster(
            model_config.quantile_params(model_config.lower_quantile),
            x_train,
            _returns(fold_train),
            num_boost_round=model_config.num_boost_round,
            feature_names=feature_names,
        )
        q_high, _ = _train_booster(
            model_config.quantile_params(model_config.upper_quantile),
            x_train,
            _returns(fold_train),
            num_boost_round=model_config.num_boost_round,
            feature_names=feature_names,
        )
        fold_best.append(max(1, (best_cls + best_reg) // 2))

        oos_probabilities.extend(float(p) for p in classifier.predict(x_valid))
        oos_expected.extend(float(v) for v in regressor.predict(x_valid))
        lower = [float(v) for v in q_low.predict(x_valid)]
        upper = [float(v) for v in q_high.predict(x_valid)]
        oos_lower.extend(min(lo, hi) for lo, hi in zip(lower, upper, strict=True))
        oos_upper.extend(max(lo, hi) for lo, hi in zip(lower, upper, strict=True))
        oos_actual.extend(_returns(fold_valid))

    if not oos_probabilities:
        raise SplitError("walk-forward 没有产出任何样本外验证预测")

    # ── 概率校准：在样本外验证预测上拟合（spec §9.3.1）────────────────
    calibrator, calibration = fit_calibrator(
        oos_probabilities, [value > 0 for value in oos_actual]
    )
    calibrated_oos = calibrator.apply_many(oos_probabilities)
    validation_metrics = MetricSet.compute(
        probabilities=calibrated_oos,
        expected_returns=oos_expected,
        actual_returns=oos_actual,
        lower=oos_lower,
        upper=oos_upper,
    )
    validation_range = DateRange(folds[0].validation.start, folds[-1].validation.end)

    # ── 最终模型：在整个 dev 上重训（test 从未参与任何选择）────────────
    x_dev = _matrix(dev_samples, feature_config)
    rounds = int(statistics.median(fold_best)) if fold_best else model_config.num_boost_round
    rounds = max(1, min(rounds, model_config.num_boost_round))

    classifier, _ = _train_booster(
        model_config.classifier_params,
        x_dev,
        _labels(dev_samples),
        num_boost_round=rounds,
        feature_names=feature_names,
    )
    regressor, _ = _train_booster(
        model_config.regressor_params,
        x_dev,
        _returns(dev_samples),
        num_boost_round=rounds,
        feature_names=feature_names,
    )
    q_low, _ = _train_booster(
        model_config.quantile_params(model_config.lower_quantile),
        x_dev,
        _returns(dev_samples),
        num_boost_round=rounds,
        feature_names=feature_names,
    )
    q_high, _ = _train_booster(
        model_config.quantile_params(model_config.upper_quantile),
        x_dev,
        _returns(dev_samples),
        num_boost_round=rounds,
        feature_names=feature_names,
    )

    # ── 测试段评估 + 基准（基准参数只在 dev 上拟合）────────────────────
    x_test = _matrix(test_samples, feature_config)
    test_probabilities = calibrator.apply_many(
        [float(p) for p in classifier.predict(x_test)]
    )
    test_expected = [float(v) for v in regressor.predict(x_test)]
    raw_low = [float(v) for v in q_low.predict(x_test)]
    raw_high = [float(v) for v in q_high.predict(x_test)]
    test_lower = [min(lo, hi) for lo, hi in zip(raw_low, raw_high, strict=True)]
    test_upper = [max(lo, hi) for lo, hi in zip(raw_low, raw_high, strict=True)]
    test_actual = _returns(test_samples)

    test_metrics = MetricSet.compute(
        probabilities=test_probabilities,
        expected_returns=test_expected,
        actual_returns=test_actual,
        lower=test_lower,
        upper=test_upper,
    )
    baseline_params = fit_baseline_params(_returns(dev_samples))
    benchmark_returns = [sample.benchmark_return for sample in test_samples]
    usable_benchmark = (
        [value for value in benchmark_returns if value is not None]
        if all(value is not None for value in benchmark_returns)
        else None
    )
    test_baselines = evaluate_baselines(
        baseline_params, actual_returns=test_actual, benchmark_returns=usable_benchmark
    )
    beats_baseline = better_than_baseline(
        model_brier=test_metrics.brier_score,
        model_mae=test_metrics.mae,
        baseline_brier=test_baselines.baseline_brier_score,
        baseline_mae=test_baselines.baseline_mae,
    )

    # ── 发布门槛（spec §9.4）────────────────────────────────────────────
    required = MIN_VALIDATION_PREDICTIONS[horizon]
    reasons: list[str] = []
    if not leakage_tests_passed:
        reasons.append("未来数据泄漏测试未通过（或未提供通过证明）")
    if validation_metrics.count < required:
        reasons.append(
            f"验证覆盖 {validation_metrics.count} < 要求的 {required} 个"
        )
    if not validation_metrics.all_finite or not test_metrics.all_finite:
        reasons.append("Brier / MAE / 方向准确率存在非有限值")
    if test_baselines.baseline_brier_score is None or test_baselines.baseline_mae is None:
        reasons.append("基准指标未能计算")
    if test_metrics.interval_coverage is None:
        reasons.append("区间覆盖率不可见")
    gate = ReleaseGate(
        leakage_tests_passed=leakage_tests_passed,
        validation_predictions=validation_metrics.count,
        required_validation_predictions=required,
        metrics_finite=validation_metrics.all_finite and test_metrics.all_finite,
        interval_coverage_visible=test_metrics.interval_coverage is not None,
        reasons=tuple(reasons),
    )

    # 全缺失的列：如实记录，写进模型卡（例如当前数据口径下的 turnover_rate）
    unavailable = tuple(
        name
        for i, name in enumerate(feature_names)
        if bool(np.all(np.isnan(x_dev[:, i])))
    )

    return TrainedModel(
        model_key=model_config.model_key,
        version=version,
        target_horizon=horizon,
        feature_set_version=feature_config.version,
        feature_set_sha256=feature_config.sha256,
        feature_names=feature_names,
        unavailable_features=unavailable,
        boosters={
            "regressor": regressor,
            "classifier": classifier,
            "q20": q_low,
            "q80": q_high,
        },
        calibrator=calibrator,
        calibration=calibration,
        normalizer=_fit_normalizer(dev_samples, feature_names),
        baseline_params=baseline_params,
        # PSI 的参考分布只能来自训练窗口 —— 用 dev 段，绝不碰 test
        psi_reference=build_psi_reference(
            snapshots=[sample.snapshot.values for sample in dev_samples],
            config=feature_config,
        ),
        train_range=dev_range,
        validation_range=validation_range,
        test_range=test_range,
        embargo_sessions=embargo,
        folds=folds,
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
        test_baselines=test_baselines,
        better_than_baseline=beats_baseline,
        release_gate=gate,
        train_samples=len(dev_samples),
        test_samples=len(test_samples),
        best_iterations={"final_rounds": rounds},
    )
