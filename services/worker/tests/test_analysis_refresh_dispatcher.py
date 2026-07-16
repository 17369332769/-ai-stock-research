"""用户主动分析刷新必须只处理目标股票，并写入明确终态。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from services.worker import scheduler
from services.worker.jobs import analysis_jobs


async def test_single_symbol_analysis_refresh_targets_requested_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_refresh(symbol: str, **_kwargs: object) -> int:
        calls.append(symbol)
        return 1

    monkeypatch.setattr(analysis_jobs, "build_chat_client", lambda: None)
    monkeypatch.setattr(analysis_jobs, "_refresh_symbol_analyses", fake_refresh)
    monkeypatch.setattr(
        analysis_jobs,
        "get_trading_calendar",
        lambda: SimpleNamespace(is_trading_day=lambda _day: False),
    )

    await analysis_jobs.run_analysis_refresh(uuid.uuid4(), "000001")

    assert calls == ["000001"]


async def test_analysis_dispatcher_marks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[uuid.UUID, str]] = []
    finished: list[tuple[uuid.UUID, bool, str | None]] = []

    async def fake_run(job_id: uuid.UUID, symbol: str) -> None:
        ran.append((job_id, symbol))

    async def fake_finish(
        job_id: uuid.UUID, *, ok: bool, message: str | None
    ) -> None:
        finished.append((job_id, ok, message))

    job_id = uuid.uuid4()
    monkeypatch.setattr(scheduler, "run_analysis_refresh", fake_run)
    monkeypatch.setattr(scheduler, "_finish_backfill", fake_finish)

    await scheduler._run_analysis_refresh_task(job_id, "600519")

    assert ran == [(job_id, "600519")]
    assert finished == [(job_id, True, None)]


async def test_analysis_dispatcher_marks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    finished: list[tuple[bool, str | None]] = []

    async def fake_run(_job_id: uuid.UUID, _symbol: str) -> None:
        raise RuntimeError("agent unavailable")

    async def fake_finish(
        _job_id: uuid.UUID, *, ok: bool, message: str | None
    ) -> None:
        finished.append((ok, message))

    monkeypatch.setattr(scheduler, "run_analysis_refresh", fake_run)
    monkeypatch.setattr(scheduler, "_finish_backfill", fake_finish)

    await scheduler._run_analysis_refresh_task(uuid.uuid4(), "600519")

    assert finished[0][0] is False
    assert "agent unavailable" in (finished[0][1] or "")
