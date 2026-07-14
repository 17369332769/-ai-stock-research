"""Provider 契约测试的公共夹具。

spec §16.1：``tests/fixtures/providers/`` 保存脱敏后的正常与异常响应；**测试禁止访问公网**。
本 conftest 只从磁盘读夹具，任何测试都不得发起真实网络请求。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from apps.api.app.core.clock import SHANGHAI, FixedClock

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "apps" / "api" / "tests" / "fixtures" / "providers"

# 固定"现在"：2026-07-14（周二）09:50，盘中（spec §16.1 要求用可注入 Clock 固定时间）
NOW = datetime(2026, 7, 14, 9, 50, tzinfo=SHANGHAI)


def load_json(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def load_bytes(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)
