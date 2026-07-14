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


async def test_corrupted_snapshot_reports_failed(client: AsyncClient, state_dir: Path) -> None:
    """快照损坏（半截 JSON）→ failed，不是崩溃、也不是 ok。"""
    (state_dir / "worker_health.json").write_text('{"providers": {', encoding="utf-8")

    response = await client.get("/api/v1/system/status")

    assert response.status_code == 200
    sources = response.json()["data"]["sources"]
    assert all(s["status"] == "failed" for s in sources)
    assert all(s["last_error_code"] == "WORKER_HEALTH_UNREADABLE" for s in sources)


async def test_never_run_source_is_not_ok(client: AsyncClient, state_dir: Path) -> None:
    """worker 刚起、还没跑过采集 → failed（"尚未采集"），不能显示"正常"。"""
    _write_health(
        state_dir,
        {
            "schema_version": 1,
            "providers": {"csindex": {"provider": "csindex", "status": "never_run"}},
            "jobs": {},
        },
    )

    response = await client.get("/api/v1/system/status")

    by_key = {s["key"]: s for s in response.json()["data"]["sources"]}
    assert by_key["csindex"]["status"] == "failed"


async def test_degraded_source_surfaces_last_success_and_error(
    client: AsyncClient, state_dir: Path
) -> None:
    """降级源必须同时给出「最后成功时间」和「具体失败原因」（spec §8）。"""
    _write_health(
        state_dir,
        {
            "schema_version": 1,
            "providers": {
                "eastmoney_via_akshare": {
                    "provider": "eastmoney_via_akshare",
                    "status": "degraded",
                    "last_success_at": "2026-07-14T09:30:00+08:00",
                }
            },
            "jobs": {
                "ingest_watchlist_quotes": {
                    "provider": "eastmoney_via_akshare",
                    "consecutive_failures": 5,
                    "last_error": "PROVIDER_UNAVAILABLE: 上游 429",
                }
            },
        },
    )

    response = await client.get("/api/v1/system/status")

    by_key = {s["key"]: s for s in response.json()["data"]["sources"]}
    source = by_key["eastmoney_via_akshare"]
    assert source["status"] == "degraded"
    assert source["last_success_at"].startswith("2026-07-14T09:30:00")
    assert source["consecutive_failures"] == 5
    assert "429" in source["last_error_message"]


async def test_healthy_source_is_ok(client: AsyncClient, state_dir: Path) -> None:
    _write_health(
        state_dir,
        {
            "schema_version": 1,
            "providers": {
                "cninfo": {
                    "provider": "cninfo",
                    "status": "healthy",
                    "last_success_at": "2026-07-14T10:00:00+08:00",
                }
            },
            "jobs": {"ingest_announcements": {"provider": "cninfo", "consecutive_failures": 0}},
        },
    )

    response = await client.get("/api/v1/system/status")

    by_key = {s["key"]: s for s in response.json()["data"]["sources"]}
    assert by_key["cninfo"]["status"] == "ok"
    assert by_key["cninfo"]["consecutive_failures"] == 0


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
