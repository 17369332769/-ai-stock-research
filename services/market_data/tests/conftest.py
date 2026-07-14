"""OpenBB 网关契约测试的公共夹具。

spec §16.1：测试禁止访问公网。这里用 respx mock 掉 httpx，用 FixedClock 固定时间。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from apps.api.app.core.clock import SHANGHAI, FixedClock
from services.market_data.openbb_gateway import OpenBBHttpGateway

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "apps" / "api" / "tests" / "fixtures" / "providers"

BASE_URL = "http://openbb.test:6900"
TIMEOUT = 30.0

# 2026-07-14（周二）09:50 —— 盘中（spec §16.1 固定时间点之一）
NOW = datetime(2026, 7, 14, 9, 50, tzinfo=SHANGHAI)


def load(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
async def gateway(clock: FixedClock) -> AsyncIterator[OpenBBHttpGateway]:
    instance = OpenBBHttpGateway(base_url=BASE_URL, timeout_seconds=TIMEOUT, clock=clock)
    try:
        yield instance
    finally:
        await instance.aclose()
