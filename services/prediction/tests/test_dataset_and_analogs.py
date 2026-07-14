"""Qlib dataset 契约、Parquet 快照清单、相似度数学（spec §9.3.1 / §10）。

这些都是不碰数据库就能验证的部分。真正需要 PostgreSQL 的（repository / service /
settlement / scorecard 的 SQL 路径）属于集成测试层（spec §16），不在这里。
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest

from services.prediction.analogs.finder import (
    MIN_VALID_CANDIDATES,
    _distance,
    _forward_return,
    _percentile,
)
from services.prediction.features.panel import DailyBar
from services.prediction.tests.conftest import TEST_SESSIONS, daily_bar, price_series
from services.prediction.training.dataset import (
    BAR_COLUMNS,
    DatasetManifest,
    SnapshotManifest,
    _read_parquet,
    _sha256_file,
    _write_parquet,
)
from services.prediction.training.qlib_dataset import QLIB_FIELDS, build_qlib_dataset
from services.prediction.training.samples import InstrumentSeries, MembershipIndex

pytest.importorskip("numpy")
pytest.importorskip("pandas")


SESSIONS = [d for d in TEST_SESSIONS if date(2025, 1, 1) <= d <= date(2025, 12, 31)]


# ── Qlib dataset ────────────────────────────────────────────────────────────


def test_qlib_dataset_layout(tmp_path: Path) -> None:
    series = {
        "600519": InstrumentSeries(symbol="600519", daily=price_series(SESSIONS)),
        "000001": InstrumentSeries(
            symbol="000001", daily=price_series(SESSIONS, start_price=20.0)
        ),
    }
    universe = MembershipIndex(
        periods={
            "600519": ((date(2020, 1, 1), None),),
            "000001": ((date(2020, 1, 1), None),),
        }
    )

    layout = build_qlib_dataset(
        root=tmp_path / "qlib", sessions=SESSIONS, series=series, universe=universe
    )

    assert layout.calendar_sessions == len(SESSIONS)
    assert layout.instrument_count == 2
    assert layout.field_files == 2 * len(QLIB_FIELDS)

    calendar_lines = (tmp_path / "qlib" / "calendars" / "day.txt").read_text().strip().split("\n")
    assert calendar_lines[0] == SESSIONS[0].isoformat()
    assert calendar_lines[-1] == SESSIONS[-1].isoformat()

    for symbol in ("600519", "000001"):
        for field in QLIB_FIELDS:
            assert (tmp_path / "qlib" / "features" / symbol.lower() / f"{field}.day.bin").is_file()


def test_qlib_bin_format_starts_with_calendar_index(tmp_path: Path) -> None:
    """``.bin`` 的第一个 float32 是该标的首个数据点在 calendar 中的**下标**（Qlib dump_bin 格式）。"""
    import numpy as np

    # 这只股票从第 10 个交易日才开始有数据
    late_sessions = SESSIONS[10:]
    series = {"600519": InstrumentSeries(symbol="600519", daily=price_series(late_sessions))}
    universe = MembershipIndex(periods={"600519": ((date(2020, 1, 1), None),)})

    build_qlib_dataset(
        root=tmp_path / "qlib", sessions=SESSIONS, series=series, universe=universe
    )

    payload = np.fromfile(
        str(tmp_path / "qlib" / "features" / "600519" / "close.day.bin"), dtype="<f4"
    )
    assert payload[0] == pytest.approx(10.0), "第一个元素必须是 calendar 起始下标"
    assert len(payload) == 1 + len(late_sessions)
    assert payload[1] == pytest.approx(series["600519"].daily[0].close, rel=1e-5)


def test_qlib_instruments_use_real_membership_periods(tmp_path: Path) -> None:
    """**幸存者偏差的核心防线**（spec §9.3）。

    一只 2025-07 才调入沪深300 的股票，在 instruments 文件里的有效期必须从 2025-07 开始 ——
    绝不能写成"从有数据的第一天起就是成分股"。否则 Qlib 取样时会把它算成 2025-01 的成分股，
    而那正是"用当前 300 只回填历史"的表现形式。
    """
    joined = date(2025, 7, 1)
    left = date(2025, 10, 31)

    series = {
        "600519": InstrumentSeries(symbol="600519", daily=price_series(SESSIONS)),
        "000002": InstrumentSeries(symbol="000002", daily=price_series(SESSIONS)),
    }
    universe = MembershipIndex(
        periods={
            "600519": ((date(2020, 1, 1), None),),  # 一直是成分股
            "000002": ((joined, left),),  # 只有 2025-07 到 2025-10 是成分股
        }
    )

    build_qlib_dataset(
        root=tmp_path / "qlib", sessions=SESSIONS, series=series, universe=universe
    )

    lines = (tmp_path / "qlib" / "instruments" / "csi300.txt").read_text().strip().split("\n")
    entries = {line.split("\t")[0]: line.split("\t")[1:] for line in lines}

    assert entries["000002"] == [joined.isoformat(), left.isoformat()]
    # 一直是成分股的那只，有效期被裁到数据范围
    assert entries["600519"][0] == SESSIONS[0].isoformat()
    assert entries["600519"][1] == SESSIONS[-1].isoformat()

    # all.txt 是"数据里出现过的全部标的"，与成分名单不是一回事
    all_lines = (tmp_path / "qlib" / "instruments" / "all.txt").read_text().strip().split("\n")
    assert len(all_lines) == 2


def test_qlib_multiple_membership_periods(tmp_path: Path) -> None:
    """调出后又调入 → instruments 里是**两行**，中间那段不算成分股。"""
    series = {"600519": InstrumentSeries(symbol="600519", daily=price_series(SESSIONS))}
    universe = MembershipIndex(
        periods={
            "600519": (
                (date(2025, 1, 6), date(2025, 4, 30)),
                (date(2025, 9, 1), None),
            )
        }
    )
    build_qlib_dataset(
        root=tmp_path / "qlib", sessions=SESSIONS, series=series, universe=universe
    )
    lines = (tmp_path / "qlib" / "instruments" / "csi300.txt").read_text().strip().split("\n")
    assert len(lines) == 2
    assert lines[0].split("\t")[1:] == ["2025-01-06", "2025-04-30"]
    assert lines[1].split("\t")[1] == "2025-09-01"


# ── Parquet 快照与清单 ──────────────────────────────────────────────────────


def test_parquet_roundtrip_and_manifest_sha(tmp_path: Path) -> None:
    from apps.api.app.core.clock import SHANGHAI

    records = [
        {
            "instrument": "600519",
            "datetime": __import__("datetime").datetime(2025, 1, 6, 15, 0, tzinfo=SHANGHAI),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
            "amount": 100500.0,
            "adjustment": "qfq",
        }
    ]
    path = tmp_path / "bars_1d.parquet"
    _write_parquet(records, BAR_COLUMNS, path)

    back = _read_parquet(path)
    assert len(back) == 1
    assert back[0]["instrument"] == "600519"
    assert back[0]["close"] == pytest.approx(100.5)

    # 同样的数据 → 同样的字节 → SHA-256 才有意义
    path2 = tmp_path / "bars_1d_again.parquet"
    _write_parquet(records, BAR_COLUMNS, path2)
    assert _sha256_file(path) == _sha256_file(path2)


def test_manifest_verify_detects_tampering(tmp_path: Path) -> None:
    """数据被动过 → SHA-256 对不上 → 直接炸，绝不"继续训练"。"""
    records = [{"instrument": "600519", "datetime": None, "document_type": "news"}]
    path = tmp_path / "documents.parquet"
    _write_parquet(records, ("instrument", "datetime", "document_type"), path)

    manifest = DatasetManifest(
        snapshot_id="snap",
        created_at="2026-07-14T18:00:00+08:00",
        universe_code="CSI300",
        snapshots=(
            SnapshotManifest(
                name="documents",
                path="documents.parquet",
                rows=1,
                columns=("instrument", "datetime", "document_type"),
                min_datetime=None,
                max_datetime=None,
                sha256=_sha256_file(path),
            ),
        ),
    )
    manifest.verify(tmp_path)  # 未被篡改 → 通过

    # 篡改后必须被发现
    _write_parquet(
        [*records, {"instrument": "000001", "datetime": None, "document_type": "news"}],
        ("instrument", "datetime", "document_type"),
        path,
    )
    with pytest.raises(ValueError, match="SHA-256 不匹配"):
        manifest.verify(tmp_path)

    # JSON 往返
    assert DatasetManifest.from_json(manifest.to_json()).snapshot("documents").rows == 1


# ── 相似度数学（spec §10）──────────────────────────────────────────────────


def test_distance_skips_missing_dimensions() -> None:
    """缺失的维度直接跳过，**不当 0**。

    把缺失当 0 等于宣称"这个特征恰好等于训练均值"（标准化后 0 就是均值），
    从而人为拉近距离 —— 这是相似度里最常见的一种自欺。
    """
    current = [1.0, None, 2.0]
    candidate = [1.0, 999.0, 2.0]

    distance, used = _distance(current, candidate)
    assert used == 2
    assert distance == pytest.approx(0.0), "只在两边都有值的维度上比，应该完全相同"

    # 全部缺失 → 距离无穷大（不可比），而不是 0（完美匹配）
    far, none_used = _distance([None, None], [1.0, 2.0])
    assert none_used == 0
    assert math.isinf(far)


def test_distance_is_normalized_by_feature_count() -> None:
    d1, used1 = _distance([0.0, 0.0], [3.0, 4.0])
    assert used1 == 2
    assert d1 == pytest.approx(math.sqrt((9 + 16) / 2))


def test_percentile() -> None:
    ordered = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert _percentile(ordered, 0.0) == pytest.approx(0.0)
    assert _percentile(ordered, 0.5) == pytest.approx(2.0)
    assert _percentile(ordered, 1.0) == pytest.approx(4.0)
    assert _percentile(ordered, 0.25) == pytest.approx(1.0)
    assert _percentile([7.0], 0.5) == pytest.approx(7.0)


def test_forward_return_is_none_beyond_history() -> None:
    """后续收益尚未实现 → None。绝不"用最后一天的价格凑一个"。"""
    bars: list[DailyBar] = [daily_bar(day, 100.0 + i) for i, day in enumerate(SESSIONS[:10])]

    assert _forward_return(bars, 0, 5) == pytest.approx(105 / 100 - 1)
    assert _forward_return(bars, 8, 5) is None  # 越界
    assert _forward_return(bars, 9, 1) is None


def test_min_valid_candidates_is_30() -> None:
    """spec §10：有效候选 < 30 → 关闭功能。"""
    assert MIN_VALID_CANDIDATES == 30
