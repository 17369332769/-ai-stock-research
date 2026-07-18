"""用中证指数官方调样公告重建沪深300 point-in-time 成分历史。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.db import session_scope
from apps.api.app.core.enums import CSI300_CODE, Timeframe
from apps.api.app.core.errors import AppError
from apps.api.app.core.runtime import get_clock
from apps.api.app.models.tables import Bar, Instrument, UniverseMembership
from services.market_data.ingest import upsert_bars
from services.market_data.openbb_gateway import create_gateway

EXPECTED_MEMBERS = 300
DEFAULT_CONFIG = Path("config/csi300_adjustments.json")
_SYMBOL = re.compile(r"\d{6}")


@dataclass(frozen=True, slots=True)
class Change:
    out_symbol: str
    out_name: str
    in_symbol: str
    in_name: str


@dataclass(frozen=True, slots=True)
class Adjustment:
    effective_from: date
    announcement_id: int
    source_url: str
    changes: tuple[Change, ...]


@dataclass(frozen=True, slots=True)
class MembershipPeriod:
    symbol: str
    effective_from: date
    effective_to: date | None
    source_url: str


def load_adjustments(path: Path = DEFAULT_CONFIG) -> tuple[list[Adjustment], set[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("universe") != "CSI300":
        raise ValueError("历史调样配置必须声明 universe=CSI300")
    adjustments: list[Adjustment] = []
    for raw in payload["adjustments"]:
        changes = tuple(Change(*values) for values in raw["changes"])
        out_symbols = [item.out_symbol for item in changes]
        in_symbols = [item.in_symbol for item in changes]
        if (
            not changes
            or len(out_symbols) != len(set(out_symbols))
            or len(in_symbols) != len(set(in_symbols))
        ):
            raise ValueError(f"公告 {raw['announcement_id']} 的调样代码为空或重复")
        if any(not _SYMBOL.fullmatch(symbol) for symbol in [*out_symbols, *in_symbols]):
            raise ValueError(f"公告 {raw['announcement_id']} 含非法证券代码")
        adjustments.append(
            Adjustment(
                effective_from=date.fromisoformat(raw["effective_from"]),
                announcement_id=int(raw["announcement_id"]),
                source_url=str(raw["source_url"]),
                changes=changes,
            )
        )
    if [item.effective_from for item in adjustments] != sorted(
        item.effective_from for item in adjustments
    ):
        raise ValueError("调样事件必须按生效日升序排列")
    return adjustments, set(payload.get("inactive_symbols", []))


def reconstruct_periods(
    current_symbols: set[str], adjustments: list[Adjustment]
) -> tuple[list[MembershipPeriod], dict[str, str]]:
    """从当前 300 只逆推每次调样后的快照，再压缩为连续有效期。"""
    if len(current_symbols) != EXPECTED_MEMBERS:
        raise ValueError(f"当前成分应为 {EXPECTED_MEMBERS} 只，实际 {len(current_symbols)}")
    if not adjustments:
        raise ValueError("缺少官方调样事件")

    names: dict[str, str] = {}
    snapshots: dict[date, set[str]] = {}
    snapshot = set(current_symbols)
    for event in reversed(adjustments):
        snapshots[event.effective_from] = set(snapshot)
        entrants = {item.in_symbol for item in event.changes}
        exits = {item.out_symbol for item in event.changes}
        for change in event.changes:
            names[change.out_symbol] = change.out_name
            names[change.in_symbol] = change.in_name
        missing_entrants = entrants - snapshot
        unexpected_exits = exits & snapshot
        if missing_entrants or unexpected_exits:
            raise ValueError(
                f"公告 {event.announcement_id} 无法从当前快照逆推："
                f"缺少调入 {sorted(missing_entrants)}，调出仍在快照 {sorted(unexpected_exits)}"
            )
        snapshot = (snapshot - entrants) | exits
        if len(snapshot) != EXPECTED_MEMBERS:
            raise ValueError(f"公告 {event.announcement_id} 逆推后不是 300 只")

    first = adjustments[0]
    active: dict[str, tuple[date, str]] = dict.fromkeys(
        snapshots[first.effective_from], (first.effective_from, first.source_url)
    )
    periods: list[MembershipPeriod] = []
    previous = snapshots[first.effective_from]
    for event in adjustments[1:]:
        current = snapshots[event.effective_from]
        expected_removed = {item.out_symbol for item in event.changes}
        expected_added = {item.in_symbol for item in event.changes}
        if previous - current != expected_removed or current - previous != expected_added:
            raise ValueError(f"公告 {event.announcement_id} 与相邻快照差分不一致")
        for symbol in sorted(expected_removed):
            started, source_url = active.pop(symbol)
            periods.append(
                MembershipPeriod(symbol, started, event.effective_from - timedelta(days=1), source_url)
            )
        for symbol in sorted(expected_added):
            active[symbol] = (event.effective_from, event.source_url)
        previous = current
    periods.extend(
        MembershipPeriod(symbol, started, None, source_url)
        for symbol, (started, source_url) in active.items()
    )
    periods.sort(key=lambda item: (item.symbol, item.effective_from))
    return periods, names


def _exchange(symbol: str) -> str:
    return "SSE" if symbol.startswith(("5", "6", "9")) else "SZSE"


async def _load_current() -> tuple[set[str], dict[str, str]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(UniverseMembership.symbol, Instrument.name)
                .join(Instrument, Instrument.symbol == UniverseMembership.symbol)
                .where(
                    UniverseMembership.universe_code == CSI300_CODE,
                    UniverseMembership.effective_to.is_(None),
                )
            )
        ).all()
    return {str(symbol) for symbol, _ in rows}, {str(symbol): str(name) for symbol, name in rows}


async def apply_history(config: Path) -> dict[str, Any]:
    adjustments, inactive = load_adjustments(config)
    current, current_names = await _load_current()
    periods, event_names = reconstruct_periods(current, adjustments)
    names = {**event_names, **current_names}
    symbols = {period.symbol for period in periods}
    now = get_clock().now()
    instrument_rows = [
        {
            "symbol": symbol,
            "exchange": _exchange(symbol),
            "name": names.get(symbol, symbol),
            "industry": None,
            "listed_at": None,
            "active": symbol not in inactive,
            "updated_at": now,
        }
        for symbol in sorted(symbols)
    ]
    membership_rows = [
        {
            "universe_code": CSI300_CODE,
            "symbol": period.symbol,
            "effective_from": period.effective_from,
            "effective_to": period.effective_to,
            "source": "csindex",
            "source_url": period.source_url,
            "observed_at": now,
        }
        for period in periods
    ]
    async with session_scope() as session:
        stmt = pg_insert(Instrument).values(instrument_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Instrument.symbol],
            set_={
                "name": stmt.excluded.name,
                "exchange": stmt.excluded.exchange,
                "active": stmt.excluded.active,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await session.execute(stmt)
        await session.execute(
            delete(UniverseMembership).where(UniverseMembership.universe_code == CSI300_CODE)
        )
        await session.execute(pg_insert(UniverseMembership).values(membership_rows))
    return {
        "events": len(adjustments),
        "unique_symbols": len(symbols),
        "membership_periods": len(periods),
        "history_start": adjustments[0].effective_from.isoformat(),
        "current_members": len(current),
    }


async def backfill_missing_daily() -> dict[str, Any]:
    async with session_scope() as session:
        member_symbols = select(distinct(UniverseMembership.symbol)).where(
            UniverseMembership.universe_code == CSI300_CODE
        )
        bar_symbols = select(distinct(Bar.symbol)).where(Bar.timeframe == Timeframe.DAY1.value)
        missing = sorted(
            str(symbol)
            for symbol in (
                await session.execute(member_symbols.where(~UniverseMembership.symbol.in_(bar_symbols)))
            ).scalars()
        )
        first_bar = (
            await session.execute(
                select(func.min(Bar.bar_time)).where(Bar.timeframe == Timeframe.DAY1.value)
            )
        ).scalar_one_or_none()
    if not missing:
        return {"requested": 0, "completed": 0, "empty": [], "failed": {}}
    now = get_clock().now()
    start = first_bar or datetime(now.year - 3, now.month, now.day, tzinfo=SHANGHAI)
    completed = 0
    empty: list[str] = []
    failed: dict[str, str] = {}
    async with create_gateway() as gateway:
        for symbol in missing:
            bars = []
            for attempt in range(1, 4):
                try:
                    bars = await gateway.get_bars(symbol, "1d", start, now)
                    break
                except AppError as exc:
                    if attempt == 3:
                        failed[symbol] = f"{exc.code.value}: {exc.message}"
                    else:
                        await asyncio.sleep(attempt)
            if symbol in failed:
                continue
            if not bars:
                empty.append(symbol)
                continue
            async with session_scope() as session:
                await upsert_bars(session, bars, now)
            completed += 1
    return {
        "requested": len(missing),
        "completed": completed,
        "empty": empty,
        "failed": failed,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    adjustments, _ = load_adjustments(args.config)
    current, _ = await _load_current()
    periods, _ = reconstruct_periods(current, adjustments)
    result: dict[str, Any] = {
        "validated": True,
        "events": len(adjustments),
        "membership_periods": len(periods),
        "current_members": len(current),
    }
    if args.apply:
        result["apply"] = await apply_history(args.config)
    if args.backfill_daily:
        result["daily_backfill"] = await backfill_missing_daily()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="校验并重建沪深300历史成分")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--apply", action="store_true", help="事务性替换 CSI300 成分有效期")
    parser.add_argument("--backfill-daily", action="store_true", help="回补历史成分缺失的日线")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
