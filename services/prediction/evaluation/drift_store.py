"""漂移报告的存放处。

spec §6 的表结构里没有漂移表，而漂移是**运行时状态**（每天变），
产物目录又是只读的 —— 所以放在一个独立的可写目录，按 (model_key, 交易日) 组织：

    {root}/{model_key}/{session}.json
    {root}/{model_key}/latest.json

推理侧读 ``latest``：PSI > 0.30 → 停止生成新预测（spec §9.3.1）。
读不到报告时**不阻断**（没有证据说明漂移了），但置信度无法升到 high（没有证据说明没漂移）。
两边都是 fail-closed 的方向。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from services.prediction.evaluation.drift import DriftReport

__all__ = ["DriftStore", "get_drift_store", "reset_drift_store", "set_drift_store"]


class DriftStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _directory(self, model_key: str) -> Path:
        return self._root / model_key

    def write(self, report: DriftReport, session: date) -> Path:
        directory = self._directory(report.model_key)
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report.to_json(), ensure_ascii=False, indent=2, sort_keys=True)
        path = directory / f"{session.isoformat()}.json"
        path.write_text(payload, encoding="utf-8")
        (directory / "latest.json").write_text(payload, encoding="utf-8")
        return path

    def latest(self, model_key: str) -> DriftReport | None:
        path = self._directory(model_key) / "latest.json"
        if not path.is_file():
            return None
        return DriftReport.from_json(json.loads(path.read_text(encoding="utf-8")))

    def read(self, model_key: str, session: date) -> DriftReport | None:
        path = self._directory(model_key) / f"{session.isoformat()}.json"
        if not path.is_file():
            return None
        return DriftReport.from_json(json.loads(path.read_text(encoding="utf-8")))


def _default_root() -> Path:
    override = os.environ.get("PREDICTION_DRIFT_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "artifacts" / "drift"


_store: DriftStore | None = None


def get_drift_store() -> DriftStore:
    global _store
    if _store is None:
        _store = DriftStore(_default_root())
    return _store


def set_drift_store(store: DriftStore) -> None:
    """测试注入（与 runtime.set_clock 同一套路数）。"""
    global _store
    _store = store


def reset_drift_store() -> None:
    global _store
    _store = None
