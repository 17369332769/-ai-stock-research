"""置信度判定（spec §9.5，逐字实现）。

    high   : better_than_baseline=true 且 验证样本 >= 2× 最低门槛 且 校准合格
             且 所有关键特征 PSI <= 0.10
    medium : better_than_baseline=true 且 验证样本 >= 最低门槛 且 校准合格
             且 所有关键特征 PSI <= 0.20
    low    : better_than_baseline=false 或 数据降级 或 任一关键特征 PSI > 0.20

另外（spec §9.4）：低于最低样本门槛的模型**不得成为 active** —— 那是 registry 的发布门槛，
不是这里的降级项。这里只在真的遇到这种模型时兜底返回 low 并给出理由。

判定顺序是"先看能不能 high，再看能不能 medium，否则 low"，
任何一个条件不满足就往下掉 —— 绝不"四舍五入"到更高档。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from apps.api.app.core.enums import ConfidenceLabel

__all__ = ["ConfidenceDecision", "ConfidenceInputs", "decide_confidence"]

PSI_HIGH_MAX = 0.10
PSI_MEDIUM_MAX = 0.20


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    better_than_baseline: bool
    validation_predictions: int
    required_validation_predictions: int
    calibration_acceptable: bool
    key_feature_psi: Mapping[str, float]
    degraded: bool
    degradation_reasons: tuple[str, ...] = ()

    @property
    def max_psi(self) -> float | None:
        if not self.key_feature_psi:
            return None
        return max(self.key_feature_psi.values())


@dataclass(frozen=True, slots=True)
class ConfidenceDecision:
    label: ConfidenceLabel
    reasons: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {"label": self.label.value, "reasons": list(self.reasons)}


def decide_confidence(inputs: ConfidenceInputs) -> ConfidenceDecision:
    reasons: list[str] = []

    # ── 强制 low 的条件（spec §9.5 第三条）────────────────────────────
    if not inputs.better_than_baseline:
        reasons.append("未优于基准（better_than_baseline=false）")
    if inputs.degraded:
        reasons.append(
            "数据降级：" + "、".join(inputs.degradation_reasons)
            if inputs.degradation_reasons
            else "数据降级"
        )
    max_psi = inputs.max_psi
    if max_psi is not None and max_psi > PSI_MEDIUM_MAX:
        drifted = sorted(
            name for name, value in inputs.key_feature_psi.items() if value > PSI_MEDIUM_MAX
        )
        reasons.append(f"关键特征 PSI > {PSI_MEDIUM_MAX}：{drifted}")
    if inputs.validation_predictions < inputs.required_validation_predictions:
        reasons.append(
            f"验证样本 {inputs.validation_predictions} < 最低门槛 "
            f"{inputs.required_validation_predictions}（该模型本不应成为 active）"
        )
    if not inputs.calibration_acceptable:
        reasons.append("概率校准不合格")

    if reasons:
        return ConfidenceDecision(label=ConfidenceLabel.LOW, reasons=tuple(reasons))

    # 到这里：优于基准、无降级、校准合格、样本达标、PSI <= 0.20（或未知）
    #
    # PSI 未知（漂移监控还没产出报告）时**不能**给 high：
    # spec §9.5 的 high 要求"所有关键特征 PSI 不超过 0.10"，没有证据就不能声称满足。
    # 反过来，未知也不构成"漂移了"的证据，所以不压到 low —— 停在 medium。
    psi_known = max_psi is not None
    psi_ok_for_high = psi_known and max_psi is not None and max_psi <= PSI_HIGH_MAX
    doubled = inputs.validation_predictions >= 2 * inputs.required_validation_predictions

    if doubled and psi_ok_for_high:
        return ConfidenceDecision(
            label=ConfidenceLabel.HIGH,
            reasons=(
                f"优于基准；验证样本 {inputs.validation_predictions} >= "
                f"2×{inputs.required_validation_predictions}；校准合格；"
                f"关键特征 PSI <= {PSI_HIGH_MAX}",
            ),
        )

    detail: list[str] = []
    if not doubled:
        detail.append(
            f"验证样本 {inputs.validation_predictions} 未达 2×{inputs.required_validation_predictions}"
        )
    if not psi_known:
        detail.append("关键特征 PSI 未知（漂移监控尚未产出报告），无法确认 <= 0.10")
    elif max_psi is not None and max_psi > PSI_HIGH_MAX:
        detail.append(f"最大关键特征 PSI {max_psi:.4f} > {PSI_HIGH_MAX}")
    return ConfidenceDecision(
        label=ConfidenceLabel.MEDIUM,
        reasons=("优于基准、达最低样本门槛、校准合格", *detail),
    )
