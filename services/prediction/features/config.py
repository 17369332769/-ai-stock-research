"""特征集契约的加载与校验（spec §9.3.1）。

``config/features/{version}.yaml`` 是特征定义的唯一真相。本模块：

1. 解析并强类型化该文件；
2. 计算它的 **sha256** —— 模型产物记录这个哈希，推理时不一致直接 fail closed。
   这就是 spec "任何字段、窗口或缺失值策略变化都必须升级版本" 的机器强制：
   原地改 v1.yaml 会让所有用 v1 训练出来的模型立刻拒绝服务，逼你新建 v2。
3. 校验 yaml 里登记的特征与代码里实现的特征**双向**一一对应（见 ``validate_against_registry``）。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

from services.prediction.features._yamlmini import parse_yaml_subset

__all__ = [
    "FeatureSetConfig",
    "FeatureSetError",
    "FeatureSpec",
    "MissingPolicy",
    "Scope",
    "feature_config_root",
    "load_feature_set",
]

Scope = Literal["base", "today"]
MissingPolicy = Literal["nan", "zero"]

_VALID_SCOPES: tuple[str, ...] = ("base", "today")
_VALID_MISSING: tuple[str, ...] = ("nan", "zero")


class FeatureSetError(ValueError):
    """特征集配置非法。绝不降级为默认值 —— 配置错就必须炸。"""


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    group: str
    scope: Scope
    window: int
    missing: MissingPolicy
    description: str
    requires: str | None = None
    required: bool = True


@dataclass(frozen=True, slots=True)
class TimestampConventions:
    """可见性约定 —— PIT 的定义本身。"""

    daily_bar_visible_at: str
    daily_session_close: time
    minute_bar_timestamp: str
    minute_bar_minutes: int
    document_visible_at: str


@dataclass(frozen=True, slots=True)
class HistoryRequirements:
    min_completed_sessions: int
    next_5d_min_sessions: int
    today_close_min_sessions: int
    today_close_min_minute_bars: int


@dataclass(frozen=True, slots=True)
class EventConfig:
    document_lookback_days: int


@dataclass(frozen=True, slots=True)
class PsiConfig:
    key_features: tuple[str, ...]
    bins: int
    lookback_sessions: int
    drift_threshold: float
    block_threshold: float


@dataclass(frozen=True, slots=True)
class TodayDegradation:
    mode: str
    drop_requires: str
    reason: str
    force_confidence: str


@dataclass(frozen=True, slots=True)
class FeatureSetConfig:
    version: str
    sha256: str
    source_path: Path
    timestamps: TimestampConventions
    history: HistoryRequirements
    benchmarks: dict[str, str]
    event: EventConfig
    psi: PsiConfig
    features: tuple[FeatureSpec, ...]
    today_degradation: TodayDegradation

    # ── 查询 ────────────────────────────────────────────────────────────
    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.features)

    def spec(self, name: str) -> FeatureSpec:
        for item in self.features:
            if item.name == name:
                return item
        raise FeatureSetError(f"特征 {name!r} 不在特征集 {self.version} 中")

    def names_for_horizon(self, horizon: str) -> tuple[str, ...]:
        """今日模型用 base + today；一周模型只用 base（spec §9.2「今日模型专用」）。"""
        if horizon == "today_close":
            allowed: tuple[str, ...] = ("base", "today")
        elif horizon == "next_5d":
            allowed = ("base",)
        else:
            raise FeatureSetError(f"未知 horizon：{horizon!r}")
        return tuple(spec.name for spec in self.features if spec.scope in allowed)

    def min_sessions_for_horizon(self, horizon: str) -> int:
        if horizon == "today_close":
            return self.history.today_close_min_sessions
        if horizon == "next_5d":
            return self.history.next_5d_min_sessions
        raise FeatureSetError(f"未知 horizon：{horizon!r}")

    def validate_against_registry(self, implemented: Iterable[str]) -> None:
        """yaml 与代码必须双向一一对应，任何一侧漂移都立刻报错。"""
        declared = set(self.names)
        coded = set(implemented)
        missing_impl = sorted(declared - coded)
        missing_decl = sorted(coded - declared)
        problems: list[str] = []
        if missing_impl:
            problems.append(f"yaml 声明但代码未实现：{missing_impl}")
        if missing_decl:
            problems.append(f"代码实现但 yaml 未声明：{missing_decl}")
        if problems:
            raise FeatureSetError(
                f"特征集 {self.version} 与实现不一致（禁止静默漂移）：" + "；".join(problems)
            )


# ── 加载 ────────────────────────────────────────────────────────────────────


def feature_config_root() -> Path:
    """``config/features`` 目录。测试可用 PREDICTION_CONFIG_ROOT 覆盖。"""
    override = os.environ.get("PREDICTION_CONFIG_ROOT")
    if override:
        return Path(override) / "features"
    # services/prediction/features/config.py -> 仓库根
    return Path(__file__).resolve().parents[3] / "config" / "features"


@lru_cache(maxsize=8)
def load_feature_set(version: str) -> FeatureSetConfig:
    path = feature_config_root() / f"{version}.yaml"
    if not path.is_file():
        raise FeatureSetError(f"特征集配置不存在：{path}")
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document = parse_yaml_subset(text)
    if not isinstance(document, dict):
        raise FeatureSetError(f"{path}：顶层必须是映射")
    return _build_config(document, version=version, digest=digest, path=path)


def _build_config(doc: dict[str, Any], *, version: str, digest: str, path: Path) -> FeatureSetConfig:
    declared_version = _require(doc, "version", str, path)
    if declared_version != version:
        raise FeatureSetError(f"{path}：文件名版本 {version!r} 与内部 version {declared_version!r} 不一致")

    ts_raw = _require(doc, "timestamp_conventions", dict, path)
    timestamps = TimestampConventions(
        daily_bar_visible_at=_require(ts_raw, "daily_bar_visible_at", str, path),
        daily_session_close=_parse_time(_require(ts_raw, "daily_session_close", str, path), path),
        minute_bar_timestamp=_require(ts_raw, "minute_bar_timestamp", str, path),
        minute_bar_minutes=_require(ts_raw, "minute_bar_minutes", int, path),
        document_visible_at=_require(ts_raw, "document_visible_at", str, path),
    )
    if timestamps.daily_bar_visible_at != "session_close":
        raise FeatureSetError(f"{path}：daily_bar_visible_at 只支持 session_close（PIT 不可协商）")
    if timestamps.minute_bar_timestamp != "bar_end":
        raise FeatureSetError(f"{path}：minute_bar_timestamp 只支持 bar_end")
    if timestamps.document_visible_at != "published_at":
        raise FeatureSetError(f"{path}：document_visible_at 只支持 published_at")

    hist_raw = _require(doc, "history_requirements", dict, path)
    history = HistoryRequirements(
        min_completed_sessions=_require(hist_raw, "min_completed_sessions", int, path),
        next_5d_min_sessions=_require(hist_raw, "next_5d_min_sessions", int, path),
        today_close_min_sessions=_require(hist_raw, "today_close_min_sessions", int, path),
        today_close_min_minute_bars=_require(hist_raw, "today_close_min_minute_bars", int, path),
    )

    bench_raw = _require(doc, "benchmarks", dict, path)
    benchmarks = {str(k): str(v) for k, v in bench_raw.items()}
    for required_key in ("csi300", "sse"):
        if required_key not in benchmarks:
            raise FeatureSetError(f"{path}：benchmarks 缺少 {required_key}")

    event_raw = _require(doc, "event", dict, path)
    event = EventConfig(
        document_lookback_days=_require(event_raw, "document_lookback_days", int, path)
    )
    if event.document_lookback_days < 5:
        raise FeatureSetError(f"{path}：event.document_lookback_days 至少要覆盖 5 天窗口")

    psi_raw = _require(doc, "psi", dict, path)
    psi = PsiConfig(
        key_features=tuple(str(x) for x in _require(psi_raw, "key_features", list, path)),
        bins=_require(psi_raw, "bins", int, path),
        lookback_sessions=_require(psi_raw, "lookback_sessions", int, path),
        drift_threshold=float(_require(psi_raw, "drift_threshold", (int, float), path)),
        block_threshold=float(_require(psi_raw, "block_threshold", (int, float), path)),
    )
    if not 0 < psi.drift_threshold < psi.block_threshold:
        raise FeatureSetError(f"{path}：psi 阈值必须满足 0 < drift < block")

    features = tuple(_build_spec(item, path) for item in _require(doc, "features", list, path))
    if not features:
        raise FeatureSetError(f"{path}：features 不能为空")
    seen: set[str] = set()
    for spec in features:
        if spec.name in seen:
            raise FeatureSetError(f"{path}：重复特征 {spec.name!r}")
        seen.add(spec.name)
    for key_feature in psi.key_features:
        if key_feature not in seen:
            raise FeatureSetError(f"{path}：psi.key_features 引用了不存在的特征 {key_feature!r}")

    deg_raw = _require(doc, "degradation", dict, path)
    today_raw = _require(deg_raw, "today_close", dict, path)
    rule_raw = _require(today_raw, "on_insufficient_minute_bars", dict, path)
    degradation = TodayDegradation(
        mode=_require(rule_raw, "mode", str, path),
        drop_requires=_require(rule_raw, "drop_requires", str, path),
        reason=_require(rule_raw, "reason", str, path),
        force_confidence=_require(rule_raw, "force_confidence", str, path),
    )

    return FeatureSetConfig(
        version=declared_version,
        sha256=digest,
        source_path=path,
        timestamps=timestamps,
        history=history,
        benchmarks=benchmarks,
        event=event,
        psi=psi,
        features=features,
        today_degradation=degradation,
    )


def _build_spec(item: Any, path: Path) -> FeatureSpec:
    if not isinstance(item, dict):
        raise FeatureSetError(f"{path}：features 的元素必须是映射，实际 {type(item).__name__}")
    name: str = _require(item, "name", str, path)
    scope: str = _require(item, "scope", str, path)
    if scope not in _VALID_SCOPES:
        raise FeatureSetError(f"{path}：特征 {name!r} 的 scope={scope!r} 非法（只允许 {_VALID_SCOPES}）")
    missing: str = _require(item, "missing", str, path)
    if missing not in _VALID_MISSING:
        raise FeatureSetError(f"{path}：特征 {name!r} 的 missing={missing!r} 非法（只允许 {_VALID_MISSING}）")
    requires = item.get("requires")
    if requires is not None and not isinstance(requires, str):
        raise FeatureSetError(f"{path}：特征 {name!r} 的 requires 必须是字符串")
    required = item.get("required", True)
    if not isinstance(required, bool):
        raise FeatureSetError(f"{path}：特征 {name!r} 的 required 必须是布尔值")
    return FeatureSpec(
        name=name,
        group=_require(item, "group", str, path),
        scope=cast(Scope, scope),  # 上面已按白名单校验过
        window=_require(item, "window", int, path),
        missing=cast(MissingPolicy, missing),
        description=_require(item, "description", str, path),
        requires=requires,
        required=required,
    )


def _parse_time(text: str, path: Path) -> time:
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise FeatureSetError(f"{path}：时间格式必须是 HH:MM，实际 {text!r}")
    return time(int(parts[0]), int(parts[1]))


def _require(mapping: dict[str, Any], key: str, kind: type | tuple[type, ...], path: Path) -> Any:
    if key not in mapping:
        raise FeatureSetError(f"{path}：缺少必填字段 {key!r}")
    value = mapping[key]
    # bool 是 int 的子类；int 字段不接受 bool
    if kind is int and isinstance(value, bool):
        raise FeatureSetError(f"{path}：字段 {key!r} 期望 int，实际是 bool")
    if not isinstance(value, kind):
        expected = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
        raise FeatureSetError(f"{path}：字段 {key!r} 期望 {expected}，实际 {type(value).__name__}")
    return value
