"""Qlib 数据契约（spec §9.3.1）：PostgreSQL → Parquet 快照 → Qlib dataset。

流程与不可协商的点：

1. 日线/分钟线先从 PostgreSQL 导出成按 ``instrument, datetime`` **排序**的 Parquet；
2. 每份快照都写 **manifest**：行数、最小/最大时间、**SHA-256**。
   训练产物记录 manifest，因此"这个模型是用哪份数据训的"是可复算的，不是口头承诺。
3. Qlib 的 ``instruments`` 文件由 ``universe_memberships`` 的**真实** effective_from/effective_to 生成。
   **禁止用当前 300 只回填历史** —— 那会造成幸存者偏差，让回测结果凭空变好。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.enums import CSI300_CODE, Timeframe
from apps.api.app.models.tables import Bar, Document, UniverseMembership
from services.prediction.features.panel import DailyBar, DocumentRef, MinuteBar
from services.prediction.training.samples import InstrumentSeries, MembershipIndex

__all__ = [
    "DatasetManifest",
    "SnapshotManifest",
    "dataset_root",
    "export_snapshot",
    "load_membership_index",
    "load_series_from_snapshot",
    "membership_periods",
]

BAR_COLUMNS: tuple[str, ...] = (
    "instrument",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adjustment",
)
DOCUMENT_COLUMNS: tuple[str, ...] = ("instrument", "datetime", "document_type")
MEMBERSHIP_COLUMNS: tuple[str, ...] = ("instrument", "effective_from", "effective_to")


def dataset_root() -> Path:
    """快照根目录。容器里挂到别处时用 PREDICTION_DATASET_ROOT 覆盖。"""
    override = os.environ.get("PREDICTION_DATASET_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "artifacts" / "datasets"


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """一份 Parquet 快照的可复算指纹。"""

    name: str
    path: str
    rows: int
    columns: tuple[str, ...]
    min_datetime: str | None
    max_datetime: str | None
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "rows": self.rows,
            "columns": list(self.columns),
            "min_datetime": self.min_datetime,
            "max_datetime": self.max_datetime,
            "sha256": self.sha256,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SnapshotManifest:
        return cls(
            name=data["name"],
            path=data["path"],
            rows=int(data["rows"]),
            columns=tuple(data["columns"]),
            min_datetime=data.get("min_datetime"),
            max_datetime=data.get("max_datetime"),
            sha256=data["sha256"],
        )


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    snapshot_id: str
    created_at: str
    universe_code: str
    snapshots: tuple[SnapshotManifest, ...]

    @property
    def directory(self) -> Path:
        return dataset_root() / self.snapshot_id

    def snapshot(self, name: str) -> SnapshotManifest:
        for item in self.snapshots:
            if item.name == name:
                return item
        raise KeyError(f"快照 {name!r} 不在 manifest 中")

    def to_json(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "universe_code": self.universe_code,
            "snapshots": [item.to_json() for item in self.snapshots],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DatasetManifest:
        return cls(
            snapshot_id=data["snapshot_id"],
            created_at=data["created_at"],
            universe_code=data["universe_code"],
            snapshots=tuple(SnapshotManifest.from_json(item) for item in data["snapshots"]),
        )

    def write(self, directory: Path) -> Path:
        path = directory / "manifest.json"
        path.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    @classmethod
    def read(cls, directory: Path) -> DatasetManifest:
        return cls.from_json(json.loads((directory / "manifest.json").read_text(encoding="utf-8")))

    def verify(self, directory: Path) -> None:
        """逐份核对 SHA-256。数据被动过就直接炸，绝不"继续训练"。"""
        for item in self.snapshots:
            path = directory / item.path
            if not path.is_file():
                raise FileNotFoundError(f"快照缺失：{path}")
            digest = _sha256_file(path)
            if digest != item.sha256:
                raise ValueError(
                    f"快照 {item.name} 的 SHA-256 不匹配：manifest={item.sha256} 实际={digest}"
                )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pandas() -> Any:
    """pandas 的唯一入口。

    - **懒加载**：pandas 只在导出/读回 Parquet 时才需要；推理与 PIT 路径不该为它付启动代价。
    - **单一 type: ignore**：pandas 没有内联类型标注，pandas-stubs 也不在依赖清单里
      （pyproject 的 mypy overrides 同样没列 pandas）。忽略只写这一处，
      而不是散落在每个 import 点上 —— 后者会随代码顺序变化时好时坏。
    """
    import pandas as pd

    return pd


# ── 导出 ────────────────────────────────────────────────────────────────────


async def _fetch_bars(
    session: AsyncSession, timeframe: str, end: datetime, symbols: Sequence[str] | None
) -> list[dict[str, Any]]:
    stmt = select(Bar).where(Bar.timeframe == timeframe, Bar.bar_time <= end)
    if symbols is not None:
        stmt = stmt.where(Bar.symbol.in_(symbols))
    stmt = stmt.order_by(Bar.symbol.asc(), Bar.bar_time.asc())  # instrument, datetime 排序
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "instrument": row.symbol,
            "datetime": row.bar_time,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
            "amount": None if row.amount is None else float(row.amount),
            "adjustment": row.adjustment,
        }
        for row in rows
    ]


async def _fetch_documents(session: AsyncSession, end: datetime) -> list[dict[str, Any]]:
    stmt = (
        select(Document.symbol, Document.published_at, Document.document_type)
        .where(Document.published_at <= end, Document.symbol.is_not(None))
        .order_by(Document.symbol.asc(), Document.published_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {"instrument": row[0], "datetime": row[1], "document_type": row[2]} for row in rows
    ]


async def membership_periods(
    session: AsyncSession, universe_code: str = CSI300_CODE
) -> list[dict[str, Any]]:
    """成分股的**真实**历史有效期。effective_to 为空 = 至今仍是成分股。"""
    stmt = (
        select(
            UniverseMembership.symbol,
            UniverseMembership.effective_from,
            UniverseMembership.effective_to,
        )
        .where(UniverseMembership.universe_code == universe_code)
        .order_by(UniverseMembership.symbol.asc(), UniverseMembership.effective_from.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {"instrument": row[0], "effective_from": row[1], "effective_to": row[2]} for row in rows
    ]


def _write_parquet(records: list[dict[str, Any]], columns: Sequence[str], path: Path) -> None:
    pd = _pandas()
    frame = pd.DataFrame.from_records(records, columns=list(columns))
    path.parent.mkdir(parents=True, exist_ok=True)
    # 固定 engine 与压缩，保证同样的数据得到同样的字节 → SHA-256 才有意义
    frame.to_parquet(path, engine="pyarrow", compression="snappy", index=False)


def _manifest_for(
    name: str, records: list[dict[str, Any]], columns: Sequence[str], path: Path, root: Path
) -> SnapshotManifest:
    times = [
        record["datetime"] for record in records if record.get("datetime") is not None
    ]
    return SnapshotManifest(
        name=name,
        path=str(path.relative_to(root)),
        rows=len(records),
        columns=tuple(columns),
        min_datetime=min(times).isoformat() if times else None,
        max_datetime=max(times).isoformat() if times else None,
        sha256=_sha256_file(path),
    )


async def export_snapshot(
    session: AsyncSession,
    *,
    snapshot_id: str,
    end: datetime,
    created_at: datetime,
    universe_code: str = CSI300_CODE,
    symbols: Sequence[str] | None = None,
    include_minute: bool = True,
) -> DatasetManifest:
    """把训练所需的全部数据导成 Parquet 快照 + manifest。

    ``end`` 是快照的时间上界（含）—— 快照本身也是 point-in-time 的：
    用 2026-01-01 的快照训出来的模型，绝不可能见过 2026-01-02 的行情。
    """
    root = dataset_root()
    directory = root / snapshot_id
    directory.mkdir(parents=True, exist_ok=True)

    snapshots: list[SnapshotManifest] = []

    daily = await _fetch_bars(session, Timeframe.DAY1.value, end, symbols)
    daily_path = directory / "bars_1d.parquet"
    _write_parquet(daily, BAR_COLUMNS, daily_path)
    snapshots.append(_manifest_for("bars_1d", daily, BAR_COLUMNS, daily_path, directory))

    if include_minute:
        minute = await _fetch_bars(session, Timeframe.MIN5.value, end, symbols)
        minute_path = directory / "bars_5m.parquet"
        _write_parquet(minute, BAR_COLUMNS, minute_path)
        snapshots.append(_manifest_for("bars_5m", minute, BAR_COLUMNS, minute_path, directory))

    documents = await _fetch_documents(session, end)
    documents_path = directory / "documents.parquet"
    _write_parquet(documents, DOCUMENT_COLUMNS, documents_path)
    snapshots.append(
        _manifest_for("documents", documents, DOCUMENT_COLUMNS, documents_path, directory)
    )

    memberships = await membership_periods(session, universe_code)
    memberships_path = directory / "universe_memberships.parquet"
    _write_parquet(memberships, MEMBERSHIP_COLUMNS, memberships_path)
    snapshots.append(
        SnapshotManifest(
            name="universe_memberships",
            path=str(memberships_path.relative_to(directory)),
            rows=len(memberships),
            columns=MEMBERSHIP_COLUMNS,
            min_datetime=min(
                (record["effective_from"].isoformat() for record in memberships), default=None
            ),
            max_datetime=max(
                (
                    (record["effective_to"] or record["effective_from"]).isoformat()
                    for record in memberships
                ),
                default=None,
            ),
            sha256=_sha256_file(memberships_path),
        )
    )

    manifest = DatasetManifest(
        snapshot_id=snapshot_id,
        created_at=created_at.isoformat(),
        universe_code=universe_code,
        snapshots=tuple(snapshots),
    )
    manifest.write(directory)
    return manifest


# ── 读回（训练侧不再碰数据库）──────────────────────────────────────────────


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    pd = _pandas()
    frame = pd.read_parquet(path, engine="pyarrow")
    records: list[dict[str, Any]] = frame.to_dict(orient="records")
    return records


def _as_shanghai(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else value.to_pydatetime()
    if moment.tzinfo is None:
        raise ValueError(f"快照里出现 naive datetime：{moment!r}（所有时间必须带时区）")
    return moment.astimezone(SHANGHAI)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def load_series_from_snapshot(
    directory: Path, manifest: DatasetManifest, *, benchmark_symbols: dict[str, str]
) -> tuple[dict[str, InstrumentSeries], dict[str, InstrumentSeries]]:
    """从快照读回按标的组织的序列。返回（标的序列, 基准序列）。"""
    manifest.verify(directory)

    daily_rows = _read_parquet(directory / manifest.snapshot("bars_1d").path)
    minute_rows: list[dict[str, Any]] = []
    try:
        minute_manifest = manifest.snapshot("bars_5m")
    except KeyError:
        minute_manifest = None
    if minute_manifest is not None:
        minute_rows = _read_parquet(directory / minute_manifest.path)
    document_rows = _read_parquet(directory / manifest.snapshot("documents").path)

    daily_by_symbol: dict[str, list[DailyBar]] = {}
    adjustments: dict[str, set[str]] = {}
    for row in daily_rows:
        symbol = str(row["instrument"])
        daily_by_symbol.setdefault(symbol, []).append(
            DailyBar(
                bar_time=_as_shanghai(row["datetime"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=None if row["amount"] is None else float(row["amount"]),
            )
        )
        adjustments.setdefault(symbol, set()).add(str(row["adjustment"]))

    minute_by_symbol: dict[str, dict[date, list[MinuteBar]]] = {}
    for row in minute_rows:
        symbol = str(row["instrument"])
        bar = MinuteBar(
            bar_time=_as_shanghai(row["datetime"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            amount=None if row["amount"] is None else float(row["amount"]),
        )
        minute_by_symbol.setdefault(symbol, {}).setdefault(bar.session, []).append(bar)

    documents_by_symbol: dict[str, list[DocumentRef]] = {}
    for row in document_rows:
        symbol = str(row["instrument"])
        documents_by_symbol.setdefault(symbol, []).append(
            DocumentRef(
                published_at=_as_shanghai(row["datetime"]),
                document_type=str(row["document_type"]),
            )
        )

    benchmark_lookup = {value: key for key, value in benchmark_symbols.items()}
    instruments: dict[str, InstrumentSeries] = {}
    benchmarks: dict[str, InstrumentSeries] = {}
    for symbol, bars in daily_by_symbol.items():
        found = adjustments.get(symbol, {"qfq"})
        if len(found) > 1:
            raise ValueError(f"{symbol} 的日线出现多种复权基准 {sorted(found)}，拒绝进入训练")
        series = InstrumentSeries(
            symbol=symbol,
            daily=bars,
            minute_by_session=minute_by_symbol.get(symbol, {}),
            documents=documents_by_symbol.get(symbol, []),
            adjustment=next(iter(found)),
        )
        if symbol in benchmark_lookup:
            benchmarks[benchmark_lookup[symbol]] = series
        else:
            instruments[symbol] = series
    return instruments, benchmarks


def load_membership_index(directory: Path, manifest: DatasetManifest) -> MembershipIndex:
    rows = _read_parquet(
        directory / manifest.snapshot("universe_memberships").path.split("/", 1)[-1]
    )
    periods: dict[str, list[tuple[date, date | None]]] = {}
    for row in rows:
        symbol = str(row["instrument"])
        end_value = row["effective_to"]
        end = None if end_value is None or _is_nat(end_value) else _as_date(end_value)
        periods.setdefault(symbol, []).append((_as_date(row["effective_from"]), end))
    return MembershipIndex(periods={k: tuple(v) for k, v in periods.items()})


def _is_nat(value: Any) -> bool:
    """pandas 把空日期读成 NaT；它既不是 None 也不是 date，只能靠"自反不等"识别。"""
    return bool(value != value)
