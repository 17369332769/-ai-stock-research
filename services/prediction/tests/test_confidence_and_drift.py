"""置信度（spec §9.5）与 PSI 漂移（spec §9.3.1）。"""

from __future__ import annotations

import pytest

from apps.api.app.core.enums import ConfidenceLabel
from services.prediction.evaluation.drift import (
    build_psi_reference,
    compute_drift,
    compute_psi,
)
from services.prediction.features.config import FeatureSetConfig
from services.prediction.inference.confidence import (
    PSI_HIGH_MAX,
    PSI_MEDIUM_MAX,
    ConfidenceInputs,
    decide_confidence,
)


def _inputs(**overrides) -> ConfidenceInputs:  # type: ignore[no-untyped-def]
    base = {
        "better_than_baseline": True,
        "validation_predictions": 300,
        "required_validation_predictions": 120,
        "calibration_acceptable": True,
        "key_feature_psi": {"ret_5": 0.05, "vol_20": 0.03},
        "degraded": False,
        "degradation_reasons": (),
    }
    base.update(overrides)
    return ConfidenceInputs(**base)  # type: ignore[arg-type]


# ── high ────────────────────────────────────────────────────────────────────


def test_high_requires_all_conditions() -> None:
    """high：优于基准 + 验证样本 >= 2× 门槛 + 校准合格 + 所有关键特征 PSI <= 0.10。"""
    decision = decide_confidence(_inputs())
    assert decision.label is ConfidenceLabel.HIGH


def test_high_downgrades_to_medium_when_samples_below_double() -> None:
    decision = decide_confidence(_inputs(validation_predictions=200))  # < 2×120
    assert decision.label is ConfidenceLabel.MEDIUM
    assert any("2×120" in reason for reason in decision.reasons)


def test_high_downgrades_to_medium_when_psi_above_010() -> None:
    decision = decide_confidence(_inputs(key_feature_psi={"ret_5": 0.15}))
    assert decision.label is ConfidenceLabel.MEDIUM
    assert PSI_HIGH_MAX == 0.10


def test_unknown_psi_cannot_be_high() -> None:
    """PSI 未知（漂移监控还没跑）→ 无法声称 <= 0.10 → 最多 medium（fail closed）。"""
    decision = decide_confidence(_inputs(key_feature_psi={}))
    assert decision.label is ConfidenceLabel.MEDIUM
    assert any("PSI 未知" in reason for reason in decision.reasons)


# ── low ─────────────────────────────────────────────────────────────────────


def test_not_better_than_baseline_forces_low() -> None:
    """spec §9.4：未优于基准的模型仍可 active，但置信度**只能** low。"""
    decision = decide_confidence(_inputs(better_than_baseline=False))
    assert decision.label is ConfidenceLabel.LOW
    assert any("未优于基准" in reason for reason in decision.reasons)


def test_psi_above_020_forces_low() -> None:
    decision = decide_confidence(_inputs(key_feature_psi={"ret_5": 0.05, "vol_20": 0.25}))
    assert decision.label is ConfidenceLabel.LOW
    assert any("vol_20" in reason for reason in decision.reasons)
    assert PSI_MEDIUM_MAX == 0.20


def test_data_degradation_forces_low() -> None:
    """开盘模型降级 / 基准缺失 → low（spec §9.5「数据降级」）。"""
    decision = decide_confidence(
        _inputs(degraded=True, degradation_reasons=("minute_bars_insufficient",))
    )
    assert decision.label is ConfidenceLabel.LOW
    assert any("minute_bars_insufficient" in reason for reason in decision.reasons)


def test_bad_calibration_forces_low() -> None:
    decision = decide_confidence(_inputs(calibration_acceptable=False))
    assert decision.label is ConfidenceLabel.LOW
    assert any("校准" in reason for reason in decision.reasons)


def test_below_minimum_samples_forces_low() -> None:
    decision = decide_confidence(_inputs(validation_predictions=50))
    assert decision.label is ConfidenceLabel.LOW
    assert any("最低门槛" in reason for reason in decision.reasons)


def test_medium_at_exactly_minimum_threshold() -> None:
    decision = decide_confidence(
        _inputs(validation_predictions=120, key_feature_psi={"ret_5": 0.18})
    )
    assert decision.label is ConfidenceLabel.MEDIUM


# ── PSI ─────────────────────────────────────────────────────────────────────


def _reference(config: FeatureSetConfig, values: list[float]):  # type: ignore[no-untyped-def]
    snapshots = [dict.fromkeys(config.psi.key_features, value) for value in values]
    return build_psi_reference(snapshots=snapshots, config=config)


def test_psi_of_identical_distribution_is_zero(feature_config: FeatureSetConfig) -> None:
    values = [i / 100 for i in range(200)]
    reference = _reference(feature_config, values)
    item = reference.features[0]

    psi = compute_psi(item, list(values))
    assert psi == pytest.approx(0.0, abs=1e-6)


def test_psi_detects_distribution_shift(feature_config: FeatureSetConfig) -> None:
    """整体平移的分布必须被识别为漂移。"""
    train = [i / 100 for i in range(200)]
    reference = _reference(feature_config, train)
    item = reference.features[0]

    shifted = [v + 1.5 for v in train]  # 整体右移，远超训练分布
    psi = compute_psi(item, shifted)

    assert psi > feature_config.psi.block_threshold, f"平移后的 PSI 只有 {psi}"


def test_psi_detects_feature_becoming_all_missing(feature_config: FeatureSetConfig) -> None:
    """一个特征突然全变 NaN 是最该报警的漂移。

    如果只对非缺失值分箱，这种情况会完全看不见 —— 所以必须有 missing 桶。
    """
    train = [i / 100 for i in range(200)]
    reference = _reference(feature_config, train)
    item = reference.features[0]
    assert item.missing_rate == pytest.approx(0.0)

    psi = compute_psi(item, [None] * 50)
    assert psi > feature_config.psi.block_threshold, "全缺失必须触发阻断级漂移"


def test_psi_empty_sample_raises(feature_config: FeatureSetConfig) -> None:
    """没有线上样本 → 抛错。绝不返回 PSI=0 假装"没有漂移"。"""
    reference = _reference(feature_config, [i / 100 for i in range(200)])
    with pytest.raises(ValueError, match="没有任何线上样本"):
        compute_psi(reference.features[0], [])


def test_drift_report_thresholds(feature_config: FeatureSetConfig) -> None:
    train = [i / 100 for i in range(200)]
    reference = _reference(feature_config, train)

    # 同分布 → 不漂移、不阻断
    calm = compute_drift(
        model_key="a_share_5d_lightgbm",
        reference=reference,
        snapshots=[dict.fromkeys(feature_config.psi.key_features, v) for v in train],
        computed_at="2026-07-14T18:00:00+08:00",
        lookback_sessions=20,
    )
    assert calm.drifted is False
    assert calm.blocked is False
    assert calm.max_psi is not None and calm.max_psi < 0.01

    # 剧烈平移 → 漂移且阻断
    broken = compute_drift(
        model_key="a_share_5d_lightgbm",
        reference=reference,
        snapshots=[
            dict.fromkeys(feature_config.psi.key_features, v + 1.5) for v in train
        ],
        computed_at="2026-07-14T18:00:00+08:00",
        lookback_sessions=20,
    )
    assert broken.drifted is True
    assert broken.blocked is True
    assert set(broken.blocking_features()) == set(feature_config.psi.key_features)

    # JSON 往返
    from services.prediction.evaluation.drift import DriftReport

    assert DriftReport.from_json(broken.to_json()).max_psi == pytest.approx(broken.max_psi)


def test_drift_store_roundtrip(tmp_path, feature_config: FeatureSetConfig) -> None:  # type: ignore[no-untyped-def]
    from datetime import date

    from services.prediction.evaluation.drift_store import DriftStore

    reference = _reference(feature_config, [i / 100 for i in range(200)])
    report = compute_drift(
        model_key="a_share_5d_lightgbm",
        reference=reference,
        snapshots=[
            dict.fromkeys(feature_config.psi.key_features, v / 100) for v in range(200)
        ],
        computed_at="2026-07-14T18:00:00+08:00",
        lookback_sessions=20,
    )

    store = DriftStore(tmp_path)
    store.write(report, date(2026, 7, 14))

    assert store.latest("a_share_5d_lightgbm") is not None
    assert store.read("a_share_5d_lightgbm", date(2026, 7, 14)) is not None
    assert store.latest("no_such_model") is None
