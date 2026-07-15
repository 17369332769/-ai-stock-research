"""单股行情刷新只触达当前 symbol，并把成功/失败写回作业状态。"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.app.core.errors import ProviderUnavailable
from apps.api.app.models.tables import Instrument, Job
from services.market_data.ingest import IngestReport
from services.worker.jobs import market_data_jobs


class FakeSession:
    def __init__(self) -> None:
        self.job = SimpleNamespace(
            status="running",
            completed_steps=0,
            current_step="fetch_quote",
            warnings=[],
            error_code=None,
            error_message=None,
            started_at=None,
            finished_at=None,
            updated_at=None,
        )

    async def get(self, model: type[Any], key: Any) -> Any:
        if model is Instrument:
            return SimpleNamespace(symbol=str(key))
        if model is Job:
            return self.job
        return None

    async def flush(self) -> None:
        return None


class FakeGateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    async def __aenter__(self) -> FakeGateway:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get_quotes(self, symbols: list[str], _now: object) -> list[Any]:
        self.calls.append(symbols)
        if self.fail:
            raise ProviderUnavailable("唯一行情来源不可用")
        return [SimpleNamespace(symbol=symbols[0])]


def install_fakes(monkeypatch: pytest.MonkeyPatch, gateway: FakeGateway) -> FakeSession:
    market_data_jobs.reset_source_health()
    session = FakeSession()

    @asynccontextmanager
    async def fake_session_scope() -> Any:
        yield session

    async def fake_upsert(_session: object, _quotes: object, _now: object) -> IngestReport:
        return IngestReport(written=1)

    monkeypatch.setattr(market_data_jobs, "session_scope", fake_session_scope)
    monkeypatch.setattr(market_data_jobs, "create_gateway", lambda: gateway)
    monkeypatch.setattr(market_data_jobs, "upsert_quotes", fake_upsert)
    return session


async def test_quote_refresh_requests_only_current_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = FakeGateway()
    session = install_fakes(monkeypatch, gateway)

    await market_data_jobs.run_quote_refresh(uuid.uuid4(), "000002")

    assert gateway.calls == [["000002"]]
    assert session.job.status == "succeeded"
    assert session.job.completed_steps == 1


async def test_quote_refresh_records_upstream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = FakeGateway(fail=True)
    session = install_fakes(monkeypatch, gateway)

    with pytest.raises(ProviderUnavailable):
        await market_data_jobs.run_quote_refresh(uuid.uuid4(), "000002")

    assert gateway.calls == [["000002"]]
    assert session.job.status == "failed"
    assert session.job.error_code == "PROVIDER_UNAVAILABLE"
    health = market_data_jobs.get_source_health()
    assert [item["source"] for item in health] == ["eastmoney_quote_via_akshare"]
    assert health[0]["consecutive_failures"] == 1
