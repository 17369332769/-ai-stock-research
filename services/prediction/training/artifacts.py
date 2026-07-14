"""模型产物（spec §9.3.1）。

    artifacts/models/{model_key}/{version}/
      model_regressor.txt      LightGBM 文本格式（不是 pickle：跨版本可读、可 diff、可审计）
      model_classifier.txt
      model_q20.txt
      model_q80.txt
      calibrator.json          校准器（isotonic 断点 / Platt 系数）——推理侧不需要 sklearn
      normalizer.json          训练窗口的均值/标准差（历史相似行情用，spec §10）
      feature_schema.json      特征 Schema + 特征集 sha256
      data_manifest.json       数据快照清单（行数 / 时间范围 / SHA-256）
      metrics.json             指标 + 基准 + 发布门槛
      provenance.json          代码 commit SHA、依赖锁文件哈希、数据范围
      model_card.md            模型卡

目录与文件写完即 **只读**（0o444 / 0o555）：产物不可变，改模型只能出新版本（spec §3.4）。
``artifact_uri`` 固定为 ``file:///models/{model_key}/{version}``（容器里只读挂载）。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.api.app.core.enums import RESEARCH_ONLY_DISCLAIMER
from apps.api.app.core.settings import get_settings
from services.prediction.training.dataset import DatasetManifest
from services.prediction.training.model_config import ModelConfig
from services.prediction.training.trainer import TrainedModel

__all__ = [
    "ArtifactBundle",
    "artifact_local_root",
    "artifact_uri_for",
    "resolve_artifact_dir",
    "write_artifacts",
]

BOOSTER_FILES: dict[str, str] = {
    "regressor": "model_regressor.txt",
    "classifier": "model_classifier.txt",
    "q20": "model_q20.txt",
    "q80": "model_q80.txt",
}


def artifact_local_root() -> Path:
    """产物在**本机**的根目录。

    容器里把 ``artifacts/models`` 只读挂到 ``/models``，所以 ``artifact_uri`` 里写的是
    ``file:///models/...``；本机跑训练/测试时用仓库内的 ``artifacts/models``。
    ``PREDICTION_ARTIFACT_ROOT`` 可覆盖（测试用 tmp_path）。
    """
    override = os.environ.get("PREDICTION_ARTIFACT_ROOT")
    if override:
        return Path(override)
    mounted = Path(get_settings().model_artifact_root)
    if mounted.is_dir():
        return mounted
    return Path(__file__).resolve().parents[3] / "artifacts" / "models"


def artifact_uri_for(model_key: str, version: str) -> str:
    """spec §9.3.1 明文规定的形式。注意是挂载点 ``/models``，不是本机路径。"""
    root = get_settings().model_artifact_root.rstrip("/")
    return f"file://{root}/{model_key}/{version}"


def resolve_artifact_dir(artifact_uri: str) -> Path:
    """把 ``artifact_uri`` 映射回本机目录。

    只取 URI 末尾的 ``{model_key}/{version}`` 再拼到本机根目录 ——
    这样同一条 URI 在容器（/models）和本机（artifacts/models）都能解析。
    """
    if not artifact_uri.startswith("file://"):
        raise ValueError(f"artifact_uri 必须是 file:// 形式：{artifact_uri!r}")
    parts = [part for part in artifact_uri[len("file://") :].split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"artifact_uri 缺少 model_key/version：{artifact_uri!r}")
    model_key, version = parts[-2], parts[-1]
    return artifact_local_root() / model_key / version


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    directory: Path
    artifact_uri: str
    files: tuple[str, ...]


def _git_sha() -> str:
    """代码 commit SHA（spec §9.3：每个模型版本必须记录）。"""
    configured = get_settings().git_sha
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _lockfile_hashes() -> dict[str, str]:
    """依赖锁文件哈希（spec §9.3）。锁文件缺失时如实记为 missing，不假装有。"""
    root = Path(__file__).resolve().parents[3]
    out: dict[str, str] = {}
    for name in ("pyproject.toml", "requirements.lock", "uv.lock", "poetry.lock"):
        path = root / name
        if path.is_file():
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif name == "pyproject.toml":
            out[name] = "missing"
    return out


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _make_read_only(directory: Path) -> None:
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    directory.chmod(
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )


def write_artifacts(
    model: TrainedModel,
    *,
    model_config: ModelConfig,
    dataset: DatasetManifest,
    created_at: datetime,
    overwrite: bool = False,
) -> ArtifactBundle:
    """落盘一个模型版本，然后把目录置为只读。"""
    directory = artifact_local_root() / model.model_key / model.version
    if directory.exists() and not overwrite:
        raise FileExistsError(
            f"模型版本已存在：{directory}。产物不可变 —— 改模型请出新版本（spec §3.4）"
        )
    if directory.exists() and overwrite:
        directory.chmod(stat.S_IRWXU)
        for path in directory.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    directory.mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    for name, filename in BOOSTER_FILES.items():
        booster = model.boosters[name]
        booster.save_model(str(directory / filename))
        written.append(filename)

    _write_json(directory / "calibrator.json", model.calibrator.to_json())
    written.append("calibrator.json")

    _write_json(directory / "normalizer.json", model.normalizer.to_json())
    written.append("normalizer.json")

    # PSI 的参考分布（训练窗口）——线上算漂移时不必回头读训练数据
    _write_json(directory / "psi_reference.json", model.psi_reference.to_json())
    written.append("psi_reference.json")

    feature_schema = {
        "feature_set_version": model.feature_set_version,
        "feature_set_sha256": model.feature_set_sha256,
        "horizon": model.target_horizon,
        "names": list(model.feature_names),
        "unavailable_features": list(model.unavailable_features),
        "missing_policy_note": "缺失按 config/features/{version}.yaml 的 missing 策略落位（nan / zero）",
    }
    _write_json(directory / "feature_schema.json", feature_schema)
    written.append("feature_schema.json")

    _write_json(directory / "data_manifest.json", dataset.to_json())
    written.append("data_manifest.json")

    _write_json(directory / "metrics.json", model.metrics_json())
    written.append("metrics.json")

    provenance = {
        "code_commit_sha": _git_sha(),
        "dependency_lock_hashes": _lockfile_hashes(),
        "feature_config_sha256": model.feature_set_sha256,
        "model_config_sha256": model_config.sha256,
        "model_config": model_config.to_json(),
        "data_range": {
            "train": model.train_range.to_json(),
            "validation": model.validation_range.to_json(),
            "test": model.test_range.to_json(),
        },
        "dataset_snapshot_id": dataset.snapshot_id,
        "created_at": created_at.isoformat(),
    }
    _write_json(directory / "provenance.json", provenance)
    written.append("provenance.json")

    card = render_model_card(model, model_config=model_config, dataset=dataset, created_at=created_at)
    (directory / "model_card.md").write_text(card, encoding="utf-8")
    written.append("model_card.md")

    _make_read_only(directory)

    return ArtifactBundle(
        directory=directory,
        artifact_uri=artifact_uri_for(model.model_key, model.version),
        files=tuple(written),
    )


def render_model_card(
    model: TrainedModel,
    *,
    model_config: ModelConfig,
    dataset: DatasetManifest,
    created_at: datetime,
) -> str:
    """随版本落盘的模型卡。docs/model-card.md 是模板与版本历史，这里是**这一版**的实况。"""
    gate = model.release_gate
    metrics = model.test_metrics
    baselines = model.test_baselines
    calibration = model.calibration

    def fmt(value: float | None, digits: int = 4) -> str:
        return "不可用" if value is None else f"{value:.{digits}f}"

    lines = [
        f"# 模型卡：{model.model_key} / {model.version}",
        "",
        f"- 生成时间：{created_at.isoformat()}",
        f"- 预测目标（horizon）：`{model.target_horizon}`",
        f"- 特征集：`{model.feature_set_version}`（sha256 `{model.feature_set_sha256[:16]}…`）",
        f"- 数据快照：`{dataset.snapshot_id}`",
        "",
        "## 目标",
        "",
        (
            "- `today_close`：当日收盘价 / 昨日收盘价 - 1（参考价固定为昨收）。"
            if model.target_horizon == "today_close"
            else "- `next_5d`：第 5 个后续交易日收盘价 / 预测参考价 - 1（交易日历计数，节假日顺延）。"
        ),
        "- 方向标签：目标收益率 > 0 记为上涨，否则记为非上涨。",
        "- **上涨概率只来自方向分类模型 + 校准器，不来自回归值，也不来自任何大语言模型。**",
        "",
        "## 数据",
        "",
        f"- 训练段：{model.train_range.start} → {model.train_range.end}（{model.train_samples} 个样本）",
        f"- 验证段（walk-forward 样本外）：{model.validation_range.start} → "
        f"{model.validation_range.end}（{model.validation_metrics.count} 个预测）",
        f"- 测试段：{model.test_range.start} → {model.test_range.end}（{model.test_samples} 个样本）",
        f"- 禁运期（embargo）：{model.embargo_sessions} 个交易日",
        "- 成分股按每个交易日**当时有效**的沪深300成员取样（禁止用当前 300 只回填历史）。",
        "",
        "### 数据快照",
        "",
        "| 快照 | 行数 | 最早 | 最晚 | SHA-256 |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for item in dataset.snapshots:
        lines.append(
            f"| {item.name} | {item.rows} | {item.min_datetime or '-'} | "
            f"{item.max_datetime or '-'} | `{item.sha256[:16]}…` |"
        )

    lines += [
        "",
        "## 指标（测试段，模型从未在此拟合过任何参数）",
        "",
        "| 指标 | 模型 | 基准 |",
        "| --- | ---: | ---: |",
        f"| Brier Score | {fmt(metrics.brier_score)} | "
        f"{fmt(baselines.baseline_brier_score)}（恒定概率 {baselines.constant_probability:.4f}） |",
        f"| MAE | {fmt(metrics.mae)} | "
        f"{fmt(baselines.baseline_mae)}（历史均值 {baselines.historical_mean_return:.6f}） |",
        f"| 方向准确率 | {fmt(metrics.direction_accuracy)} | "
        f"{fmt(baselines.baseline_direction_accuracy)} |",
        f"| 区间覆盖率（p20–p80，名义 60%） | {fmt(metrics.interval_coverage)} | - |",
        "",
        f"- 沪深300 方向参照准确率：{fmt(baselines.baseline_csi300_direction_accuracy)}"
        "（同期**已实现**大盘方向；事前不可得，因此**不进**发布门槛，只作参照）",
        "",
        f"### better_than_baseline = **{str(model.better_than_baseline).lower()}**",
        "",
        "判定：Brier **且** MAE 在同一测试窗口上**均严格**优于恒定概率与历史均值基准（spec §9.3.1）。",
        (
            "本版**未**优于基准 —— 仍可作为 active 研究模型，但置信度只能为 `low`，"
            "前端必须标记「未优于基准」（spec §9.4）。"
            if not model.better_than_baseline
            else "本版优于基准；置信度可按 PSI 与样本数升到 medium / high（spec §9.5）。"
        ),
        "",
        "## 概率校准",
        "",
        f"- 方法：`{calibration.method}`"
        + (f"（**已降级**：{calibration.degraded_reason}）" if calibration.degraded else ""),
        f"- 验证样本：{calibration.validation_samples}",
        f"- Brier（校准前 → 后）：{fmt(calibration.brier_before)} → {fmt(calibration.brier_after)}",
        f"- ECE（校准前 → 后）：{fmt(calibration.ece_before)} → {fmt(calibration.ece_after)}",
        f"- 校准合格：**{str(calibration.is_acceptable).lower()}**"
        f"（判据：ECE ≤ 0.10 且校准后 Brier 不劣于校准前）",
        "",
        "## 发布门槛（spec §9.4）",
        "",
        f"- 通过：**{str(gate.passed).lower()}**",
        f"- 泄漏测试通过：{str(gate.leakage_tests_passed).lower()}",
        f"- 验证覆盖：{gate.validation_predictions} / 要求 {gate.required_validation_predictions}",
        f"- 指标均为有限数值：{str(gate.metrics_finite).lower()}",
        f"- 区间覆盖率可见：{str(gate.interval_coverage_visible).lower()}",
    ]
    if gate.reasons:
        lines.append("- 未通过原因：")
        lines += [f"  - {reason}" for reason in gate.reasons]

    lines += [
        "",
        "## 限制",
        "",
        f"- **{RESEARCH_ONLY_DISCLAIMER}**。本模型是研究工具，不保证任何收益率。",
        "- 未优于基准时仍可提供预测，但必须显示「未优于基准」且置信度为 `low`。",
    ]
    if model.unavailable_features:
        lines.append(
            f"- 本数据口径下**恒为缺失**的特征：{list(model.unavailable_features)}。"
            "它们在训练中不携带任何信息（详见特征集 yaml 中各自的说明）。"
        )
    if model.target_horizon == "next_5d":
        lines.append(
            "- **训练/线上参考价锚点不一致**：训练样本以交易日收盘（15:00）为 cutoff，"
            "参考价 = 当日收盘价；线上 09:45 / 11:30 生成的 next_5d 预测参考价是"
            "「as_of 时点最新有效价」（spec §7.4 明文要求）。"
            "因此盘中版本的预期收益是「收盘到收盘的 5 日收益」被套用在一个盘中锚点上，"
            "两者相差一个当日盘中波动。15:20 的版本没有这个问题。"
        )
    lines += [
        "- PSI 漂移：关键特征最近 20 日 PSI > 0.20 时置信度压到 `low`；> 0.30 时**停止生成新预测**"
        "并返回 `MODEL_UNAVAILABLE`（spec §9.3.1）。",
        "",
        "## 复现",
        "",
        f"- 代码 commit：`{_git_sha()}`",
        f"- 特征配置 sha256：`{model.feature_set_sha256}`",
        f"- 模型配置 sha256：`{model_config.sha256}`",
        f"- 数据快照：`{dataset.snapshot_id}`（各文件 SHA-256 见上表 / data_manifest.json）",
        "",
    ]
    return "\n".join(lines)
