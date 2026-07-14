"""Parquet 快照 → Qlib dataset（spec §9.3.1）。

Qlib 的本地二进制 provider 布局：

    {qlib_dir}/
      calendars/day.txt                每行一个交易日
      instruments/{universe}.txt       每行 "SYMBOL\\tSTART\\tEND"，同一标的可有多段
      features/{symbol}/{field}.day.bin

``.bin`` 的格式：float32 小端；**第一个元素是该标的首个数据点在 calendar 中的下标**，
其后是逐日的值。这是 Qlib ``dump_bin`` 的既定格式（pyqlib 的 wheel 不带 scripts/，
所以这里自己实现，格式与之保持一致）。

``instruments`` 文件由 ``universe_memberships`` 的真实有效期生成 ——
一只 2023 年才调入沪深300 的股票，2020 年的行不会出现在成分名单里，
因此 Qlib 取样时不会把它算成 2020 年的成分股（幸存者偏差的根源，spec §9.3 明令禁止）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from services.prediction.training.samples import InstrumentSeries, MembershipIndex

__all__ = ["QlibDatasetLayout", "build_qlib_dataset"]

QLIB_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True, slots=True)
class QlibDatasetLayout:
    root: Path
    calendar_sessions: int
    instruments_file: str
    instrument_count: int
    field_files: int

    def to_json(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "calendar_sessions": self.calendar_sessions,
            "instruments_file": self.instruments_file,
            "instrument_count": self.instrument_count,
            "field_files": self.field_files,
        }


def build_qlib_dataset(
    *,
    root: Path,
    sessions: Sequence[date],
    series: Mapping[str, InstrumentSeries],
    universe: MembershipIndex,
    universe_name: str = "csi300",
) -> QlibDatasetLayout:
    """写出 Qlib 本地 provider 目录。``sessions`` 必须是交易日历（升序、去重）。"""
    import numpy as np

    calendar = sorted(set(sessions))
    if not calendar:
        raise ValueError("交易日历为空，无法构建 Qlib dataset")
    index_of = {day: i for i, day in enumerate(calendar)}

    root.mkdir(parents=True, exist_ok=True)
    (root / "calendars").mkdir(exist_ok=True)
    (root / "instruments").mkdir(exist_ok=True)
    (root / "features").mkdir(exist_ok=True)

    (root / "calendars" / "day.txt").write_text(
        "\n".join(day.isoformat() for day in calendar) + "\n", encoding="utf-8"
    )

    # instruments：真实有效期，一只股票可以有多段（调出后又调入）
    lines: list[str] = []
    instrument_count = 0
    for symbol in sorted(universe.periods):
        wrote = False
        for start, end in universe.periods[symbol]:
            effective_end = end if end is not None else calendar[-1]
            if effective_end < calendar[0] or start > calendar[-1]:
                continue  # 有效期完全落在数据范围之外
            clipped_start = max(start, calendar[0])
            clipped_end = min(effective_end, calendar[-1])
            if clipped_start > clipped_end:
                continue
            lines.append(f"{symbol}\t{clipped_start.isoformat()}\t{clipped_end.isoformat()}")
            wrote = True
        if wrote:
            instrument_count += 1
    instruments_file = f"{universe_name}.txt"
    (root / "instruments" / instruments_file).write_text("\n".join(lines) + "\n", encoding="utf-8")

    # all.txt：数据里出现过的全部标的（Qlib 的默认 universe）
    all_lines = [
        f"{symbol}\t{item.daily[0].session.isoformat()}\t{item.daily[-1].session.isoformat()}"
        for symbol, item in sorted(series.items())
        if item.daily
    ]
    (root / "instruments" / "all.txt").write_text("\n".join(all_lines) + "\n", encoding="utf-8")

    # features：每个字段一个 .bin
    field_files = 0
    for symbol, item in sorted(series.items()):
        if not item.daily:
            continue
        bars = [bar for bar in item.daily if bar.session in index_of]
        if not bars:
            continue
        start_index = index_of[bars[0].session]
        # 用交易日历对齐；停牌日留 NaN，绝不前向填充成"当天有成交"
        span = index_of[bars[-1].session] - start_index + 1
        directory = root / "features" / symbol.lower()
        directory.mkdir(parents=True, exist_ok=True)
        for field in QLIB_FIELDS:
            values = np.full(span, np.nan, dtype="<f4")
            for bar in bars:
                values[index_of[bar.session] - start_index] = getattr(bar, field)
            payload = np.hstack([np.array([start_index], dtype="<f4"), values]).astype("<f4")
            payload.tofile(str(directory / f"{field}.day.bin"))
            field_files += 1

    return QlibDatasetLayout(
        root=root,
        calendar_sessions=len(calendar),
        instruments_file=instruments_file,
        instrument_count=instrument_count,
        field_files=field_files,
    )
