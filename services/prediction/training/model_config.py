"""模型配置（``config/models/*.yaml``）。

和特征集一样：配置文件的 **sha256** 随模型产物一起冻结，因此"这个模型是用哪套超参训的"
是可复算的。用同一个受限 YAML 解析器，不引 pyyaml。

**embargo 刻意不在这里配** —— 它由 horizon 决定（today_close=1、next_5d=5），
写死在 ``training/labels.py``。禁运期是防泄漏的结构性约束，不是可以调小的旋钮。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from apps.api.app.core.enums import PredictionHorizon
from services.prediction.features._yamlmini import parse_yaml_subset

__all__ = ["ModelConfig", "ModelConfigError", "load_model_config", "model_config_root"]


class ModelConfigError(ValueError):
    """模型配置非法。"""


def model_config_root() -> Path:
    override = os.environ.get("PREDICTION_CONFIG_ROOT")
    if override:
        return Path(override) / "models"
    return Path(__file__).resolve().parents[3] / "config" / "models"


@dataclass(frozen=True, slots=True)
class ModelConfig:
    model_key: str
    target_horizon: str
    feature_set_version: str
    sha256: str
    source_path: Path

    test_fraction: float
    min_train_sessions: int
    walk_forward_folds: int
    validation_sessions: int

    lower_quantile: float
    upper_quantile: float

    num_boost_round: int
    early_stopping_rounds: int

    common_params: dict[str, Any]
    _regressor: dict[str, Any]
    _classifier: dict[str, Any]

    @property
    def regressor_params(self) -> dict[str, Any]:
        return {**self.common_params, **self._regressor}

    @property
    def classifier_params(self) -> dict[str, Any]:
        return {**self.common_params, **self._classifier}

    def quantile_params(self, alpha: float) -> dict[str, Any]:
        return {**self.common_params, "objective": "quantile", "alpha": alpha, "metric": "quantile"}

    def to_json(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "target_horizon": self.target_horizon,
            "feature_set_version": self.feature_set_version,
            "sha256": self.sha256,
            "test_fraction": self.test_fraction,
            "min_train_sessions": self.min_train_sessions,
            "walk_forward_folds": self.walk_forward_folds,
            "validation_sessions": self.validation_sessions,
            "lower_quantile": self.lower_quantile,
            "upper_quantile": self.upper_quantile,
            "num_boost_round": self.num_boost_round,
            "early_stopping_rounds": self.early_stopping_rounds,
            "regressor_params": self.regressor_params,
            "classifier_params": self.classifier_params,
        }


@lru_cache(maxsize=8)
def load_model_config(model_key: str) -> ModelConfig:
    path = model_config_root() / f"{model_key}.yaml"
    if not path.is_file():
        raise ModelConfigError(f"模型配置不存在：{path}")
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    doc = parse_yaml_subset(text)
    if not isinstance(doc, dict):
        raise ModelConfigError(f"{path}：顶层必须是映射")

    declared_key = _require(doc, "model_key", str, path)
    if declared_key != model_key:
        raise ModelConfigError(f"{path}：文件名 {model_key!r} 与内部 model_key {declared_key!r} 不一致")

    horizon = _require(doc, "target_horizon", str, path)
    valid = {item.value for item in PredictionHorizon}
    if horizon not in valid:
        raise ModelConfigError(f"{path}：target_horizon={horizon!r} 非法（只允许 {sorted(valid)}）")

    lower = float(_require(doc, "lower_quantile", (int, float), path))
    upper = float(_require(doc, "upper_quantile", (int, float), path))
    if not 0 < lower < upper < 1:
        raise ModelConfigError(f"{path}：分位必须满足 0 < lower < upper < 1")

    test_fraction = float(_require(doc, "test_fraction", (int, float), path))
    if not 0 < test_fraction < 0.5:
        raise ModelConfigError(f"{path}：test_fraction 必须在 (0, 0.5) 之间")

    return ModelConfig(
        model_key=declared_key,
        target_horizon=horizon,
        feature_set_version=_require(doc, "feature_set_version", str, path),
        sha256=digest,
        source_path=path,
        test_fraction=test_fraction,
        min_train_sessions=_require(doc, "min_train_sessions", int, path),
        walk_forward_folds=_require(doc, "walk_forward_folds", int, path),
        validation_sessions=_require(doc, "validation_sessions", int, path),
        lower_quantile=lower,
        upper_quantile=upper,
        num_boost_round=_require(doc, "num_boost_round", int, path),
        early_stopping_rounds=_require(doc, "early_stopping_rounds", int, path),
        common_params=dict(_require(doc, "common_params", dict, path)),
        _regressor=dict(_require(doc, "regressor_params", dict, path)),
        _classifier=dict(_require(doc, "classifier_params", dict, path)),
    )


def _require(mapping: dict[str, Any], key: str, kind: type | tuple[type, ...], path: Path) -> Any:
    if key not in mapping:
        raise ModelConfigError(f"{path}：缺少必填字段 {key!r}")
    value = mapping[key]
    if kind is int and isinstance(value, bool):
        raise ModelConfigError(f"{path}：字段 {key!r} 期望 int，实际是 bool")
    if not isinstance(value, kind):
        expected = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
        raise ModelConfigError(f"{path}：字段 {key!r} 期望 {expected}，实际 {type(value).__name__}")
    return value
