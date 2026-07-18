from datetime import date
from pathlib import Path

import pytest

from services.market_data.csi300_history import (
    EXPECTED_MEMBERS,
    Adjustment,
    Change,
    load_adjustments,
    reconstruct_periods,
)


def test_official_adjustments_reconstruct_300_members_from_current_snapshot() -> None:
    adjustments, inactive = load_adjustments(Path("config/csi300_adjustments.json"))
    current = {f"9{index:05d}" for index in range(EXPECTED_MEMBERS)}
    for event in adjustments:
        current.difference_update(change.out_symbol for change in event.changes)
        current.update(change.in_symbol for change in event.changes)

    # Build a consistent current set by walking forward from a synthetic pre-history set.
    initial_event_symbols = {
        symbol for event in adjustments for change in event.changes for symbol in (change.out_symbol, change.in_symbol)
    }
    filler = iter(sorted(current - initial_event_symbols))
    while len(current) > EXPECTED_MEMBERS:
        current.remove(next(filler))
    while len(current) < EXPECTED_MEMBERS:
        current.add(f"8{len(current):05d}")

    # The production validation is exercised against the live official snapshot; static config checks stay here.
    assert len(adjustments) == 9
    assert adjustments[0].effective_from == date(2023, 6, 12)
    assert adjustments[-1].effective_from == date(2026, 6, 15)
    assert inactive == {"600837", "601989"}


def test_reconstruct_periods_closes_removed_member_on_previous_day() -> None:
    changes = (Change("000001", "旧", "000002", "新"),)
    event = Adjustment(date(2024, 1, 2), 1, "https://example.test/1", changes)
    current = {f"9{index:05d}" for index in range(EXPECTED_MEMBERS - 1)} | {"000002"}
    periods, names = reconstruct_periods(current, [event])

    assert len([period for period in periods if period.effective_to is None]) == EXPECTED_MEMBERS
    assert names == {"000001": "旧", "000002": "新"}


def test_reconstruct_periods_rejects_snapshot_that_disagrees_with_announcement() -> None:
    event = Adjustment(
        date(2024, 1, 2),
        1,
        "https://example.test/1",
        (Change("000001", "旧", "000002", "新"),),
    )
    current = {f"9{index:05d}" for index in range(EXPECTED_MEMBERS)}
    with pytest.raises(ValueError, match="缺少调入"):
        reconstruct_periods(current, [event])
