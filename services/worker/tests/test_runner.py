"""JobRunner：作业锁、指数退避、连续失败降级、健康快照（spec §8 失败处理、§14.2、§14.4）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from apps.api.app.core.clock import FixedClock
from services.worker.runner import (
    DEGRADE_AFTER_CONSECUTIVE_FAILURES,
    HealthRegistry,
    JobRunner,
    RetryPolicy,
    redact,
)
from services.worker.tests.conftest import FakeSleep


class Boom(RuntimeError):
    """模拟上游数据源故障。"""


async def test_success_records_last_success_time(
    runner: JobRunner, registry: HealthRegistry, clock: FixedClock
) -> None:
    registry.register("quotes", "自选股报价", "akshare")
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)

    result = await runner.run(job_id="quotes", fn=job)

    assert result.ok is True
    assert calls == [1]
    health = registry.get("quotes")
    assert health.status == "healthy"
    assert health.last_success_at == clock.now()
    assert health.consecutive_failures == 0
    assert health.total_runs == 1


async def test_failure_never_raises_and_is_recorded(
    runner: JobRunner, registry: HealthRegistry
) -> None:
    """spec §14.2：数据源故障不得导致进程崩溃。"""
    registry.register("quotes", "自选股报价", "akshare")

    async def job() -> None:
        raise Boom("上游 503")

    result = await runner.run(job_id="quotes", fn=job, retry=RetryPolicy(max_attempts=1))

    assert result.ok is False
    health = registry.get("quotes")
    assert health.total_failures == 1
    assert health.consecutive_failures == 1
    assert health.status == "failing"  # 还没到 3 次，未降级
    assert health.degraded is False
    assert "Boom" in (health.last_error or "")


async def test_exponential_backoff_between_attempts(
    runner: JobRunner, registry: HealthRegistry, fake_sleep: FakeSleep
) -> None:
    """spec §8 自选股报价失败处理：指数退避。"""
    registry.register("quotes", "自选股报价", "akshare")
    attempts: list[int] = []

    async def job() -> None:
        attempts.append(len(attempts) + 1)
        raise Boom("timeout")

    policy = RetryPolicy(max_attempts=4, base_delay_seconds=1.0, factor=2.0, max_delay_seconds=4.0)
    result = await runner.run(job_id="quotes", fn=job, retry=policy)

    assert result.ok is False
    assert len(attempts) == 4
    # 1s → 2s → 4s（第 4 次失败后不再等待），max_delay 封顶
    assert fake_sleep.delays == [1.0, 2.0, 4.0]
    assert registry.get("quotes").consecutive_failures == 1  # 一轮 run = 一次失败记录


async def test_retry_succeeds_on_second_attempt(runner: JobRunner, registry: HealthRegistry) -> None:
    registry.register("quotes", "自选股报价", "akshare")
    attempts: list[int] = []

    async def job() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise Boom("瞬时抖动")

    result = await runner.run(job_id="quotes", fn=job, retry=RetryPolicy(max_attempts=3))

    assert result.ok is True
    assert result.attempts == 2
    assert registry.get("quotes").consecutive_failures == 0


async def test_degraded_after_three_consecutive_failures(
    runner: JobRunner, registry: HealthRegistry, clock: FixedClock
) -> None:
    """spec §8：数据源连续失败 3 次后进入降级状态，界面展示失败源和最后成功时间。"""
    registry.register("quotes", "自选股报价", "akshare")
    ok = True

    async def job() -> None:
        if not ok:
            raise Boom("上游挂了")

    await runner.run(job_id="quotes", fn=job)  # 先成功一次，记住最后成功时间
    last_success = registry.get("quotes").last_success_at
    assert last_success is not None

    ok = False
    for expected in range(1, DEGRADE_AFTER_CONSECUTIVE_FAILURES + 1):
        await runner.run(job_id="quotes", fn=job, retry=RetryPolicy(max_attempts=1))
        assert registry.get("quotes").consecutive_failures == expected

    health = registry.get("quotes")
    assert health.degraded is True
    assert health.status == "degraded"
    # 降级不抹掉历史：最后成功时间必须还在（spec §8 "界面展示…最后成功时间"）
    assert health.last_success_at == last_success

    providers = registry.providers()
    assert providers["akshare"]["degraded"] is True
    assert providers["akshare"]["status"] == "degraded"
    assert providers["akshare"]["failing_jobs"] == ["quotes"]
    assert providers["akshare"]["last_success_at"] == last_success.isoformat()


async def test_success_clears_degraded_state(runner: JobRunner, registry: HealthRegistry) -> None:
    registry.register("quotes", "自选股报价", "akshare")
    failing = True

    async def job() -> None:
        if failing:
            raise Boom("上游挂了")

    for _ in range(DEGRADE_AFTER_CONSECUTIVE_FAILURES):
        await runner.run(job_id="quotes", fn=job, retry=RetryPolicy(max_attempts=1))
    assert registry.get("quotes").degraded is True

    failing = False
    await runner.run(job_id="quotes", fn=job)
    assert registry.get("quotes").degraded is False
    assert registry.get("quotes").status == "healthy"


async def test_job_lock_skips_overlapping_run(runner: JobRunner, registry: HealthRegistry) -> None:
    """作业锁：同一作业不并发；上一轮还在跑时本轮直接跳过，不排队堆积（幂等保护）。"""
    registry.register("bars", "5分钟K线", "akshare")
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)
        started.set()
        await release.wait()

    first = asyncio.create_task(runner.run(job_id="bars", fn=job))
    await started.wait()

    second = await runner.run(job_id="bars", fn=job)  # 第二次触发
    assert second.skipped == "already_running"
    assert second.ok is False

    release.set()
    assert (await first).ok is True
    assert calls == [1]  # 作业体只执行了一次
    assert registry.get("bars").skipped_count == 1
    assert registry.get("bars").total_failures == 0  # 跳过不算失败，不触发降级


async def test_timeout_is_recorded_as_failure(runner: JobRunner, registry: HealthRegistry) -> None:
    registry.register("bars", "5分钟K线", "akshare")

    async def job() -> None:
        await asyncio.sleep(5)

    result = await runner.run(
        job_id="bars", fn=job, retry=RetryPolicy(max_attempts=1), timeout_seconds=0.01
    )

    assert result.ok is False
    assert registry.get("bars").consecutive_failures == 1


async def test_disabled_provider_is_skipped_not_failed(
    runner: JobRunner, registry: HealthRegistry
) -> None:
    """spec §19.2 数据源回滚：禁用故障 Provider，不删历史、不产生失败告警。"""
    registry.register("news", "新闻", "akshare", enabled=False)
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)

    result = await runner.run(job_id="news", fn=job)

    assert result.skipped == "provider_disabled"
    assert calls == []
    assert registry.get("news").status == "disabled"
    assert registry.get("news").total_failures == 0


async def test_health_snapshot_is_written_atomically(
    runner: JobRunner, registry: HealthRegistry, tmp_path: Path
) -> None:
    """健康快照是 worker → API 的唯一只读契约（/settings/data-sources）。"""
    registry.register("quotes", "自选股报价", "akshare")
    registry.register("ann", "公告", "cn_disclosure")

    async def ok_job() -> None:
        return None

    async def bad_job() -> None:
        raise Boom("巨潮 502")

    await runner.run(job_id="quotes", fn=ok_job)
    await runner.run(job_id="ann", fn=bad_job, retry=RetryPolicy(max_attempts=1))

    snapshot_path = tmp_path / "worker_health.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["jobs"]["quotes"]["status"] == "healthy"
    assert payload["jobs"]["quotes"]["last_success_at"] is not None
    assert payload["jobs"]["ann"]["status"] == "failing"
    assert payload["providers"]["cn_disclosure"]["failing_jobs"] == ["ann"]
    assert payload["providers"]["akshare"]["status"] == "healthy"
    assert not list(tmp_path.glob("*.tmp"))  # 临时文件已 rename，读侧看不到半截 JSON


async def test_secrets_never_reach_health_snapshot(
    runner: JobRunner, registry: HealthRegistry
) -> None:
    """spec §14.3 / §14.4：日志与状态文件不得包含完整 API 密钥。"""
    registry.register("quotes", "自选股报价", "akshare")

    async def job() -> None:
        raise Boom("GET https://x/api?api_key=SUPERSECRET123 failed")

    await runner.run(job_id="quotes", fn=job, retry=RetryPolicy(max_attempts=1))

    last_error = registry.get("quotes").last_error or ""
    assert "SUPERSECRET123" not in last_error
    assert "api_key=***" in last_error


def test_redact_covers_common_secret_shapes() -> None:
    assert "abc" not in redact("token=abc")
    assert "abc" not in redact("Authorization: Bearer abc")
    assert "p@ss" not in redact("postgresql://app:p@ss@db:5432/app")


def test_retry_policy_caps_delay() -> None:
    policy = RetryPolicy(max_attempts=10, base_delay_seconds=1.0, factor=3.0, max_delay_seconds=10.0)
    assert [policy.delay_for(i) for i in (1, 2, 3, 4)] == [1.0, 3.0, 9.0, 10.0]
