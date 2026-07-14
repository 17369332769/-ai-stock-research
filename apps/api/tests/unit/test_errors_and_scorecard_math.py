"""错误码 ↔ HTTP 状态映射，以及成绩单的口径函数（spec §7 / §7.4 / §9.4）。"""

from __future__ import annotations

import pytest

from apps.api.app.core.errors import (
    ERROR_STATUS,
    AppError,
    DuplicateWatchlistItem,
    ErrorCode,
    InstrumentNotFound,
    InsufficientData,
    InvalidArgument,
    ModelUnavailable,
    NotCurrentUniverseMember,
    ProviderUnavailable,
)
from apps.api.app.services.scorecard_service import (
    ALLOWED_WINDOWS,
    _actual_up_label,
    _read_baselines,
    parse_window,
)


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidArgument("x"), 400, ErrorCode.INVALID_ARGUMENT),
        (InstrumentNotFound("600519"), 404, ErrorCode.INSTRUMENT_NOT_FOUND),
        (NotCurrentUniverseMember("600519"), 409, ErrorCode.NOT_CURRENT_UNIVERSE_MEMBER),
        (DuplicateWatchlistItem("600519"), 409, ErrorCode.DUPLICATE_WATCHLIST_ITEM),
        (InsufficientData("x"), 422, ErrorCode.INSUFFICIENT_DATA),
        (ProviderUnavailable("x"), 424, ErrorCode.PROVIDER_UNAVAILABLE),
        (ModelUnavailable("x"), 503, ErrorCode.MODEL_UNAVAILABLE),
    ],
)
def test_error_status_mapping(error: AppError, status: int, code: ErrorCode) -> None:
    """HTTP 状态由 AppError.status_code 唯一决定（不在路由里硬编码）。"""
    assert error.status_code == status
    assert error.code is code


def test_error_table_covers_exactly_the_spec_codes() -> None:
    assert set(ERROR_STATUS) == set(ErrorCode)
    assert sorted(ERROR_STATUS.values()) == [400, 404, 409, 409, 422, 424, 503]


# ── 成绩单口径 ───────────────────────────────────────────────────────────────
def test_up_label_strictly_greater_than_zero() -> None:
    """spec §9.1：目标收益率**大于 0** 记为上涨，否则记为非上涨（0 算非上涨）。"""
    assert _actual_up_label(0.001) == 1.0
    assert _actual_up_label(0.0) == 0.0
    assert _actual_up_label(-0.001) == 0.0


def test_window_all_means_no_limit() -> None:
    window = parse_window("all")
    assert window.size is None
    assert window.label == "all"


@pytest.mark.parametrize("raw", ["20", "100"])
def test_window_numeric(raw: str) -> None:
    window = parse_window(raw)
    assert window.size == int(raw)
    assert window.label == int(raw)


def test_window_rejects_other_values() -> None:
    with pytest.raises(InvalidArgument):
        parse_window("50")


def test_allowed_windows_match_spec() -> None:
    assert ALLOWED_WINDOWS == ("20", "100", "all")


def test_baselines_missing_key_fails_closed() -> None:
    """基准指标缺失 ⇒ 模型没走过 §9.4 发布门槛 ⇒ 503，而不是默认 0。"""
    with pytest.raises(ModelUnavailable):
        _read_baselines({"baseline_mae": 0.019}, "k", "v")


def test_baselines_reject_bool_as_number() -> None:
    with pytest.raises(ModelUnavailable):
        _read_baselines(
            {
                "baseline_direction_accuracy": True,
                "baseline_mae": 0.019,
                "baseline_brier_score": 0.25,
            },
            "k",
            "v",
        )


def test_baselines_reject_nan() -> None:
    with pytest.raises(ModelUnavailable):
        _read_baselines(
            {
                "baseline_direction_accuracy": float("nan"),
                "baseline_mae": 0.019,
                "baseline_brier_score": 0.25,
            },
            "k",
            "v",
        )


def test_baselines_reject_inf() -> None:
    with pytest.raises(ModelUnavailable):
        _read_baselines(
            {
                "baseline_direction_accuracy": 0.52,
                "baseline_mae": float("inf"),
                "baseline_brier_score": 0.25,
            },
            "k",
            "v",
        )


def test_baselines_happy_path() -> None:
    values = _read_baselines(
        {
            "baseline_direction_accuracy": 0.52,
            "baseline_mae": 0.019,
            "baseline_brier_score": 0.25,
        },
        "k",
        "v",
    )
    assert values["baseline_mae"] == pytest.approx(0.019)
