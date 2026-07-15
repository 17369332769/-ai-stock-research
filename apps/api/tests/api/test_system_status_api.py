"""GET /api/v1/system/status（spec §13.1 数据源页 + §8 降级展示 + §9.3.1 PSI 漂移）。

这个端点最重要的性质不是"能返回数据"，而是**不会谎报健康**：
worker 没起来 / 快照损坏 / 数据源从未跑过 —— 一律 failed，绝不 ok。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from apps.api.app.services import system_status as svc


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把健康快照目录指到 tmp，避免读到真实 worker 状态。"""
    monkeypatch.setenv("WORKER_STATE_DIR", str(tmp_path))
    return tmp_path


def _write_health(state_dir: Path, payload: dict[str, Any]) -> None:
    (state_dir / "worker_health.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


async def test_worker_not_running_reports_failed_not_ok(client: AsyncClient, state_dir: Path) -> None:
    """worker 没起来（快照文件不存在）→ 全部数据源 failed，并说明原因。

    这是本端点的第一红线：绝不能因为"读不到坏消息"就报告一切正常。
    """
    response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    sources = response.json()["data"]["sources"]
    assert sources, "必须列出所有已知数据源，而不是返回空列表"
    assert all(s["status"] == "failed" for s in sources)
    assert all(s["last_error_code"] == "WORKER_UNAVAILABLE" for s in sources)
    assert all(s["last_success_at"] is None for s in sources)
    assert {s["key"]: s["active_source"] for s in sources} == {
        "csi300": "csindex",
        "akshare": "eastmoney_via_akshare",
        "cn_disclosure": "cninfo",
    }
    assert all(isinstance(s["coverage"], int) and isinstance(s["total"], int) for s in sources)


async def test_corrupted_snapshot_reports_failed(client: AsyncClient, state_dir: Path) -> None:
    """快照损坏（半截 JSON）→ failed，不是崩溃、也不是 ok。"""
    (state_dir / "worker_health.json").write_text('{"providers": {', encoding="utf-8")

    response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    sources = response.json()["data"]["sources"]
    assert all(s["status"] == "failed" for s in sources)
    assert all(s["last_error_code"] == "WORKER_HEALTH_UNREADABLE" for s in sources)


async def test_never_run_source_is_pending(client: AsyncClient, state_dir: Path) -> None:
    """worker 刚起、还没到调度时间 → pending，不得显示为正常或失败。"""
    _write_health(
        state_dir,
        {
            "schema_version": 1,
            "providers": {"csi300": {"provider": "csi300", "status": "never_run"}},
            "jobs": {},
        },
    )

    response = await client.get("/api/v1/system/status")

    by_key = {s["key"]: s for s in response.json()["data"]["sources"]}
    assert by_key["csi300"]["status"] == "pending"


async def test_provider_with_success_is_ok_when_only_a_scheduled_job_has_not_run(
    client: AsyncClient, state_dir: Path
) -> None:
    """同一 Provider 已有成功采集时，盘前报价作业未运行不等于数据源失败。"""
    _write_health(
        state_dir,
        {
            "schema_version": 1,
            "providers": {
                "akshare": {
                    "provider": "akshare",
                    "status": "never_run",
                    "last_success_at": "2026-07-15T08:00:00+08:00",
                }
            },
            "jobs": {
                "watchlist_quotes": {
                    "provider": "akshare",
                    "consecutive_failures": 0,
                }
            },
        },
    )

    by_key = {
        source["key"]: source
        for source in (await client.get("/api/v1/system/status")).json()["data"]["sources"]
    }

    assert by_key["akshare"]["status"] == "ok"
    assert by_key["akshare"]["last_error_code"] is None


async def test_degraded_source_surfaces_last_success_and_error(
    client: AsyncClient, state_dir: Path
) -> None:
    """降级源必须同时给出「最后成功时间」和「具体失败原因」（spec §8）。"""
    _write_health(
        state_dir,
        {
            "schema_version": 1,
            "providers": {
                "akshare": {
                    "provider": "akshare",
                    "status": "degraded",
                    "last_success_at": "2026-07-14T09:30:00+08:00",
                }
            },
            "jobs": {
                "ingest_watchlist_quotes": {
                    "provider": "akshare",
                    "job_id": "ingest_watchlist_quotes",
                    "title": "自选股报价",
                    "status": "degraded",
                    "consecutive_failures": 5,
                    "last_error": "PROVIDER_UNAVAILABLE: 上游 429",
                    "next_run_at": "2026-07-14T09:31:00+08:00",
                }
            },
        },
    )

    response = await client.get("/api/v1/system/status")

    by_key = {s["key"]: s for s in response.json()["data"]["sources"]}
    source = by_key["akshare"]
    assert source["status"] == "degraded"
    assert source["last_success_at"].startswith("2026-07-14T09:30:00")
    assert source["consecutive_failures"] == 5
    assert "429" in source["last_error_message"]
    assert source["active_source"] == "eastmoney_via_akshare"
    assert source["failing_jobs"] == ["自选股报价"]
    assert source["next_run_at"].startswith("2026-07-14T09:31:00")


async def test_healthy_source_is_ok(client: AsyncClient, state_dir: Path) -> None:
    _write_health(
        state_dir,
        {
            "schema_version": 1,
            "providers": {
                "cn_disclosure": {
                    "provider": "cn_disclosure",
                    "status": "healthy",
                    "last_success_at": "2026-07-14T10:00:00+08:00",
                }
            },
            "jobs": {
                "ingest_announcements": {
                    "provider": "cn_disclosure",
                    "consecutive_failures": 0,
                }
            },
        },
    )

    response = await client.get("/api/v1/system/status")

    by_key = {s["key"]: s for s in response.json()["data"]["sources"]}
    assert by_key["cn_disclosure"]["status"] == "ok"
    assert by_key["cn_disclosure"]["consecutive_failures"] == 0


async def test_agent_unconfigured_is_unavailable_not_error(
    client: AsyncClient, state_dir: Path
) -> None:
    """Agent 未配置是**允许的降级**（分析走模板摘要），不是故障，也不能让整页失败。"""
    response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    agent = response.json()["data"]["agent"]
    assert agent["status"] == "unavailable"
    assert "模板摘要" in agent["reason"]


async def test_no_active_model_returns_empty_models(client: AsyncClient, state_dir: Path) -> None:
    """没有 active 模型版本 → models 为空列表，前端据此显示「模型不可用」。"""
    response = await client.get("/api/v1/system/status")

    assert response.json()["data"]["models"] == []


@pytest.mark.parametrize(
    ("psi", "expected_status"),
    [
        (0.05, "active"),
        (0.25, "degraded"),  # > 0.20 标记漂移（spec §9.3.1）
        (0.35, "unavailable"),  # > 0.30 停止生成新预测
    ],
)
def test_psi_drift_maps_to_model_status(psi: float, expected_status: str) -> None:
    """PSI 阈值直接决定模型连接状态（spec §9.3.1）。"""
    status, reason = svc._model_status({"max_feature_psi": psi})

    assert status == expected_status
    if expected_status != "active":
        assert reason is not None and f"{psi:.2f}" in reason
