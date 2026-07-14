"""加载模型产物（只读）。

**特征集哈希校验**是这里最重要的一件事：
产物里记着训练时的 ``feature_set_sha256``；如果有人原地改了 ``config/features/v1.yaml``，
哈希对不上 → 直接 ``ModelUnavailable``。这就是"特征定义变了必须升版本"的机器强制 ——
而不是靠一句注释请求大家自觉。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from apps.api.app.core.errors import ModelUnavailable
from services.prediction.evaluation.drift import PsiReference
from services.prediction.features.config import FeatureSetConfig, load_feature_set
from services.prediction.training.artifacts import BOOSTER_FILES, resolve_artifact_dir
from services.prediction.training.calibration import Calibrator
from services.prediction.training.trainer import Normalizer

__all__ = ["LoadedModel", "clear_model_cache", "load_model_bundle"]


@dataclass(frozen=True, slots=True)
class LoadedModel:
    model_key: str
    version: str
    horizon: str
    directory: Path
    feature_names: tuple[str, ...]
    feature_set_version: str
    feature_set_sha256: str
    boosters: dict[str, Any]
    calibrator: Calibrator
    normalizer: Normalizer
    psi_reference: PsiReference
    metrics: dict[str, Any]

    def predict_one(self, row: list[float]) -> tuple[float, float, float, float]:
        """返回 (probability_up, expected_return, p20, p80)。

        **上涨概率只来自方向分类模型 + 校准器**（spec §9.3.1），绝不由回归值反推。
        """
        import numpy as np

        matrix = np.array([row], dtype="float64")
        raw_probability = float(self.boosters["classifier"].predict(matrix)[0])
        probability = self.calibrator.apply(raw_probability)
        expected = float(self.boosters["regressor"].predict(matrix)[0])
        low = float(self.boosters["q20"].predict(matrix)[0])
        high = float(self.boosters["q80"].predict(matrix)[0])
        # 分位模型互相独立，偶尔会交叉；排序而不是硬套，保证 p20 <= p80
        return probability, expected, min(low, high), max(low, high)


@lru_cache(maxsize=8)
def load_model_bundle(
    artifact_uri: str, model_key: str, version: str, horizon: str
) -> LoadedModel:
    import lightgbm as lgb

    directory = resolve_artifact_dir(artifact_uri)
    if not directory.is_dir():
        raise ModelUnavailable(f"模型产物目录不存在：{directory}（artifact_uri={artifact_uri}）")

    schema = _read_json(directory / "feature_schema.json")
    feature_set_version = str(schema["feature_set_version"])
    recorded_sha = str(schema["feature_set_sha256"])

    config: FeatureSetConfig = load_feature_set(feature_set_version)
    if config.sha256 != recorded_sha:
        raise ModelUnavailable(
            f"特征集 {feature_set_version} 的内容已变更"
            f"（训练时 sha256={recorded_sha[:16]}…，当前={config.sha256[:16]}…）。"
            f"模型 {model_key}/{version} 与当前特征定义不一致，拒绝服务 —— "
            f"改特征必须升版本号（spec §9.3.1）"
        )

    boosters: dict[str, Any] = {}
    for name, filename in BOOSTER_FILES.items():
        path = directory / filename
        if not path.is_file():
            raise ModelUnavailable(f"模型文件缺失：{path}")
        boosters[name] = lgb.Booster(model_file=str(path))

    return LoadedModel(
        model_key=model_key,
        version=version,
        horizon=horizon,
        directory=directory,
        feature_names=tuple(schema["names"]),
        feature_set_version=feature_set_version,
        feature_set_sha256=recorded_sha,
        boosters=boosters,
        calibrator=Calibrator.from_json(_read_json(directory / "calibrator.json")),
        normalizer=Normalizer.from_json(_read_json(directory / "normalizer.json")),
        psi_reference=PsiReference.from_json(_read_json(directory / "psi_reference.json")),
        metrics=_read_json(directory / "metrics.json"),
    )


def clear_model_cache() -> None:
    load_model_bundle.cache_clear()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ModelUnavailable(f"模型产物缺失：{path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
