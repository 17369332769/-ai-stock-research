"""交易日守卫与数据源禁用：非交易日必须跳过（spec §8 表头"交易日调度"）。"""

from __future__ import annotations

from datetime import datetime, time

from apps.api.app.core.clock import SHANGHAI, FixedClock
from services.worker.runner import HealthRegistry, JobRunner, RetryPolicy
from services.worker.scheduler import JobSpec, WorkerScheduler, at_times, build_schedule
from services.worker.tests.conftest import HOLIDAY, TRADING_DAY, WEEKEND


def make_spec(calls: list[str], *, job_id: str = "quotes", trading_day_only: bool = True) -> JobSpec:
    async def job() -> None:
        calls.append(job_id)

    return JobSpec(
        job_id=job_id,
        title="自选股报价",
        provider="akshare",
        fn=job,
        triggers=at_times([time(9, 45)]),
        retry=RetryPolicy(max_attempts=1),
        trading_day_only=trading_day_only,
    )


def build(specs: list[JobSpec], registry: HealthRegistry, runner: JobRunner, disabled: set[str] | None = None) -> WorkerScheduler:
    return WorkerScheduler(
        specs,
        registry=registry,
        runner=runner,
        disabled=frozenset(disabled or set()),
    )


async def test_runs_on_trading_day(
    registry: HealthRegistry, runner: JobRunner, clock: FixedClock
) -> None:
    calls: list[str] = []
    spec = make_spec(calls)
    worker = build([spec], registry, runner)

    clock.set(datetime.combine(TRADING_DAY, time(9, 45), tzinfo=SHANGHAI))
    await worker.dispatch(spec)

    assert calls == ["quotes"]
    assert registry.get("quotes").total_runs == 1


async def test_skips_weekend(registry: HealthRegistry, runner: JobRunner, clock: FixedClock) -> None:
    calls: list[str] = []
    spec = make_spec(calls)
    worker = build([spec], registry, runner)

    clock.set(datetime.combine(WEEKEND, time(9, 45), tzinfo=SHANGHAI))
    await worker.dispatch(spec)

    assert calls == []
    health = registry.get("quotes")
    assert health.last_skip_reason == "non_trading_day"
    assert health.total_runs == 0
    assert health.total_failures == 0  # 跳过不是失败，不得触发降级


async def test_skips_public_holiday(
    registry: HealthRegistry, runner: JobRunner, clock: FixedClock
) -> None:
    """国庆是工作日周四，但不是交易日 —— 必须靠交易日历判断，不能只看星期。"""
    calls: list[str] = []
    spec = make_spec(calls)
    worker = build([spec], registry, runner)

    assert HOLIDAY.weekday() < 5
    clock.set(datetime.combine(HOLIDAY, time(9, 45), tzinfo=SHANGHAI))
    await worker.dispatch(spec)

    assert calls == []
    assert registry.get("quotes").last_skip_reason == "non_trading_day"


async def test_backfill_dispatcher_runs_on_non_trading_day(
    registry: HealthRegistry, runner: JobRunner, clock: FixedClock
) -> None:
    """用户周末添加自选股，回补必须照样执行（spec §3.1）。"""
    calls: list[str] = []
    spec = make_spec(calls, job_id="backfill_dispatcher", trading_day_only=False)
    worker = build([spec], registry, runner)

    clock.set(datetime.combine(WEEKEND, time(10, 0), tzinfo=SHANGHAI))
    await worker.dispatch(spec)

    assert calls == ["backfill_dispatcher"]


async def test_quote_refresh_dispatcher_runs_on_non_trading_day(
    registry: HealthRegistry, runner: JobRunner, clock: FixedClock
) -> None:
    calls: list[str] = []
    spec = make_spec(calls, job_id="quote_refresh_dispatcher", trading_day_only=False)
    worker = build([spec], registry, runner)

    clock.set(datetime.combine(WEEKEND, time(10, 0), tzinfo=SHANGHAI))
    await worker.dispatch(spec)

    assert calls == ["quote_refresh_dispatcher"]


async def test_analysis_refresh_dispatcher_runs_on_non_trading_day(
    registry: HealthRegistry, runner: JobRunner, clock: FixedClock
) -> None:
    calls: list[str] = []
    spec = make_spec(calls, job_id="analysis_refresh_dispatcher", trading_day_only=False)
    worker = build([spec], registry, runner)

    clock.set(datetime.combine(WEEKEND, time(10, 0), tzinfo=SHANGHAI))
    await worker.dispatch(spec)

    assert calls == ["analysis_refresh_dispatcher"]


async def test_disabled_provider_is_not_scheduled(
    registry: HealthRegistry, runner: JobRunner, clock: FixedClock
) -> None:
    """spec §19.2 数据源回滚：禁用故障 Provider 后作业不再排期，但状态仍可见。"""
    calls: list[str] = []
    spec = make_spec(calls)
    worker = build([spec], registry, runner, disabled={"akshare"})

    assert worker.scheduler.get_job("quotes") is None
    assert registry.get("quotes").status == "disabled"

    await worker.dispatch(spec)  # 即便被手工触发也不执行
    assert calls == []


def test_full_schedule_registers_every_job(registry: HealthRegistry, runner: JobRunner) -> None:
    worker = WorkerScheduler(build_schedule(), registry=registry, runner=runner, disabled=frozenset())

    job_ids = {job.id for job in worker.scheduler.get_jobs()}
    assert job_ids == {spec.job_id for spec in build_schedule()}
    assert len(registry.all()) == len(build_schedule())
