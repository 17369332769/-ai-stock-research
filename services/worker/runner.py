"""作业运行器：作业锁、指数退避重试、连续失败降级与运行记录。

对应 spec：
- §8 失败处理：指数退避；数据源连续失败 3 次后进入降级状态，界面展示具体失败源和最后成功时间。
- §14.2 可靠性：所有采集和结算作业幂等；数据源故障不得导致进程崩溃或已有历史数据丢失。
- §14.4 可观测性：每个后台作业生成 job_id；记录数据源成功率与延迟；日志不得包含完整 API 密钥。

本模块只负责"怎么跑"（锁 / 重试 / 记账 / 健康快照），不含任何业务逻辑（spec §5.1 模块边界）。
业务逻辑在 services/worker/jobs/ 下，由 scheduler 注入。

健康快照文件（默认 ``$WORKER_STATE_DIR/worker_health.json``，容器内 ``/state``）是 worker 与 API
之间唯一的只读契约：worker 读写，API 只读挂载，用于 /settings/data-sources 页展示"失败源 + 最后
成功时间"（spec §8 / §13.1）。快照原子写入（临时文件 + rename），读侧永远看到完整 JSON。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from apps.api.app.core.runtime import get_clock

logger = logging.getLogger("worker.runner")

# 作业签名：无参协程；参数化作业（如回补）由调用方用 partial 绑定后传入。
JobFn = Callable[[], Awaitable[None]]
SleepFn = Callable[[float], Awaitable[None]]

# spec §8：数据源连续失败 3 次后进入降级状态。
DEGRADE_AFTER_CONSECUTIVE_FAILURES: Final[int] = 3

# 降级后不再按原始高频调度持续轰击上游；冷却结束后允许一次探测调用，成功即恢复。
DEGRADED_COOLDOWN_SECONDS: Final[int] = 300

# 健康快照文件名（API 侧按此名只读读取）。
HEALTH_FILENAME: Final[str] = "worker_health.json"

DEFAULT_STATE_DIR: Final[str] = "/state"


def state_dir() -> Path:
    """worker 状态目录。容器由配置指定 /state；本机开发默认使用 data/worker-state。"""
    raw = os.getenv("WORKER_STATE_DIR", DEFAULT_STATE_DIR)
    path = Path(raw)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path("data/worker-state")  # 本机开发默认目录（data/ 已在 .gitignore）
        path.mkdir(parents=True, exist_ok=True)
    return path


def disabled_providers() -> frozenset[str]:
    """被禁用的数据源（spec §19.2 数据源回滚：禁用故障 Provider，保留历史，不删数据）。

    通过环境变量 ``DISABLED_PROVIDERS=akshare,cn_disclosure`` 注入；被禁用的作业不再调度，
    健康快照里标记为 disabled 并保留 last_success_at，界面据此显示"数据可能过期"。
    """
    raw = os.getenv("DISABLED_PROVIDERS", "")
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


# ── 密钥脱敏（spec §14.3 / §14.4：日志与快照不得包含完整 API 密钥）────────────────────────
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # key=value / key: value；值可以带 "Bearer " 前缀（否则只会抹掉 "Bearer"，把真 token 留在后面）
    re.compile(
        r"(?i)\b(api[_-]?key|apikey|token|password|passwd|secret|authorization)\b\s*[=:]\s*"
        r"(?:bearer\s+)?\S+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)://[^/\s:@]+:[^/\s@]+@"),  # URL 里的 user:password@
)


def redact(text: str) -> str:
    """抹掉可能出现在异常信息 / URL 中的密钥后再落日志或快照。"""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: _mask(m.group(0)), out)
    return out


def _mask(fragment: str) -> str:
    if "://" in fragment:
        return "://***:***@"
    head, sep, _ = fragment.partition("=")
    if not sep:
        head, sep, _ = fragment.partition(":")
    if sep:
        return f"{head}{sep}***"
    return "***"


# ── 重试策略 ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RetryPolicy:
    """指数退避（spec §8 自选股报价："指数退避"）。

    不加抖动：本项目是单实例个人部署，没有惊群问题，确定性退避更容易测试（spec §16.1）。
    """

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    factor: float = 2.0
    max_delay_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        if self.base_delay_seconds < 0 or self.factor < 1:
            raise ValueError("退避参数非法")

    def delay_for(self, attempt: int) -> float:
        """第 attempt 次失败后的等待秒数（attempt 从 1 开始）。"""
        if attempt < 1:
            raise ValueError("attempt 从 1 开始")
        delay = self.base_delay_seconds * (self.factor ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


NO_RETRY: Final[RetryPolicy] = RetryPolicy(max_attempts=1)


# ── 健康登记簿 ───────────────────────────────────────────────────────────────────────────
@dataclass
class JobHealth:
    """单个作业的运行记录。degraded / last_success_at 直接喂给 /settings/data-sources。"""

    job_id: str
    title: str
    provider: str
    enabled: bool = True
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    total_runs: int = 0
    total_failures: int = 0
    last_duration_ms: int | None = None
    last_run_id: str | None = None
    last_skip_reason: str | None = None
    skipped_count: int = 0
    next_run_at: datetime | None = None

    @property
    def degraded(self) -> bool:
        """spec §8：连续失败 3 次即降级。"""
        return self.enabled and self.consecutive_failures >= DEGRADE_AFTER_CONSECUTIVE_FAILURES

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.degraded:
            return "degraded"
        if self.consecutive_failures > 0:
            return "failing"
        if self.total_runs == 0:
            return "never_run"
        return "healthy"

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "provider": self.provider,
            "status": self.status,
            "enabled": self.enabled,
            "degraded": self.degraded,
            "last_success_at": _iso(self.last_success_at),
            "last_failure_at": _iso(self.last_failure_at),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "total_runs": self.total_runs,
            "total_failures": self.total_failures,
            "last_duration_ms": self.last_duration_ms,
            "last_run_id": self.last_run_id,
            "last_skip_reason": self.last_skip_reason,
            "skipped_count": self.skipped_count,
            "next_run_at": _iso(self.next_run_at),
        }


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


# 状态严重程度：聚合到 provider 时取最差的一个。
_SEVERITY: Final[dict[str, int]] = {
    "healthy": 0,
    "never_run": 1,
    "failing": 2,
    "degraded": 3,
    "disabled": 4,
}


class HealthRegistry:
    """全部作业的运行记录 + 健康快照持久化。"""

    def __init__(self, state_path: Path | None = None) -> None:
        self._jobs: dict[str, JobHealth] = {}
        self._state_path = state_path
        self._started_at: datetime | None = None

    # ── 注册 ────────────────────────────────────────────────────────────────
    def register(self, job_id: str, title: str, provider: str, *, enabled: bool = True) -> JobHealth:
        health = JobHealth(job_id=job_id, title=title, provider=provider, enabled=enabled)
        self._jobs[job_id] = health
        return health

    def get(self, job_id: str) -> JobHealth:
        return self._jobs[job_id]

    def all(self) -> list[JobHealth]:
        return list(self._jobs.values())

    def mark_started(self, moment: datetime) -> None:
        self._started_at = moment

    # ── 记账 ────────────────────────────────────────────────────────────────
    def record_success(self, job_id: str, *, run_id: str, duration_ms: int, at: datetime) -> None:
        health = self._jobs[job_id]
        health.total_runs += 1
        health.consecutive_failures = 0
        health.last_success_at = at
        health.last_duration_ms = duration_ms
        health.last_run_id = run_id
        health.last_error = None

    def record_failure(self, job_id: str, *, run_id: str, error: str, at: datetime) -> None:
        health = self._jobs[job_id]
        health.total_runs += 1
        health.total_failures += 1
        health.consecutive_failures += 1
        health.last_failure_at = at
        health.last_error = redact(error)[:500]
        health.last_run_id = run_id

    def record_skip(self, job_id: str, reason: str) -> None:
        """跳过不是失败：非交易日、上一轮仍在跑、Provider 被禁用，都不得触发降级。"""
        health = self._jobs[job_id]
        health.skipped_count += 1
        health.last_skip_reason = reason

    def set_next_run(self, job_id: str, moment: datetime | None) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].next_run_at = moment

    # ── 快照 ────────────────────────────────────────────────────────────────
    def providers(self) -> dict[str, dict[str, Any]]:
        """按数据源聚合：spec §8 要求界面展示"具体失败源"和"最后成功时间"。"""
        out: dict[str, dict[str, Any]] = {}
        for health in self._jobs.values():
            entry = out.setdefault(
                health.provider,
                {
                    "provider": health.provider,
                    "status": "healthy",
                    "degraded": False,
                    "last_success_at": None,
                    "failing_jobs": [],
                },
            )
            if _SEVERITY[health.status] > _SEVERITY[str(entry["status"])]:
                entry["status"] = health.status
            if health.degraded:
                entry["degraded"] = True
            previous = entry["last_success_at"]
            current = _iso(health.last_success_at)
            if current is not None and (previous is None or current > str(previous)):
                entry["last_success_at"] = current
            if health.status in ("degraded", "failing"):
                jobs = entry["failing_jobs"]
                assert isinstance(jobs, list)
                jobs.append(health.job_id)
        return out

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": get_clock().now().isoformat(),
            "started_at": _iso(self._started_at),
            "degraded": any(h.degraded for h in self._jobs.values()),
            "jobs": {job_id: health.as_dict() for job_id, health in self._jobs.items()},
            "providers": self.providers(),
        }

    def persist(self) -> None:
        """原子写入健康快照。写失败不得影响作业本身（只记日志）。"""
        if self._state_path is None:
            return
        payload = json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
        tmp = self._state_path.with_suffix(".json.tmp")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._state_path)  # POSIX rename：读侧永远看到完整 JSON
        except OSError as exc:
            logger.warning("健康快照写入失败（不影响作业执行）：%s", redact(str(exc)))


# ── 运行器 ───────────────────────────────────────────────────────────────────────────────
@dataclass
class RunResult:
    job_id: str
    run_id: str
    ok: bool
    attempts: int
    duration_ms: int
    skipped: str | None = None
    error: str | None = None


@dataclass
class JobRunner:
    """执行单个作业：作业锁 → 超时 → 指数退避重试 → 记账 → 持久化。

    ``run()`` 永不向外抛异常（CancelledError 除外，用于优雅关停）：
    spec §14.2 "数据源故障不得导致进程崩溃"。
    """

    registry: HealthRegistry
    sleep: SleepFn = asyncio.sleep  # 测试注入假 sleep，退避不占真实时间
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)

    async def run(
        self,
        *,
        job_id: str,
        fn: JobFn,
        retry: RetryPolicy = NO_RETRY,
        timeout_seconds: float = 300.0,
    ) -> RunResult:
        health = self.registry.get(job_id)
        run_id = str(uuid.uuid4())

        if not health.enabled:
            self.registry.record_skip(job_id, "provider_disabled")
            return RunResult(job_id, run_id, ok=False, attempts=0, duration_ms=0, skipped="provider_disabled")

        now = get_clock().now()
        if (
            health.degraded
            and health.last_failure_at is not None
            and now < health.last_failure_at + timedelta(seconds=DEGRADED_COOLDOWN_SECONDS)
        ):
            self.registry.record_skip(job_id, "degraded_cooldown")
            self.registry.persist()
            logger.warning(
                "作业 %s 处于降级冷却期，跳过本轮 tick（%ds 后探测）",
                job_id,
                DEGRADED_COOLDOWN_SECONDS,
            )
            return RunResult(
                job_id,
                run_id,
                ok=False,
                attempts=0,
                duration_ms=0,
                skipped="degraded_cooldown",
            )

        # 作业锁：同一作业不并发运行（spec §5.1 "作业锁"）。上一轮还在跑（例如报价正在退避重试）
        # 时直接跳过本轮，而不是排队堆积。
        lock = self._locks.setdefault(job_id, asyncio.Lock())
        if lock.locked():
            self.registry.record_skip(job_id, "already_running")
            logger.warning("作业 %s 上一轮仍在运行，跳过本轮 tick", job_id)
            return RunResult(job_id, run_id, ok=False, attempts=0, duration_ms=0, skipped="already_running")

        async with lock:
            started = get_clock().now()
            last_error: BaseException | None = None
            attempts = 0

            for attempt in range(1, retry.max_attempts + 1):
                attempts = attempt
                try:
                    async with asyncio.timeout(timeout_seconds):
                        await fn()
                except asyncio.CancelledError:
                    raise  # 关停信号必须能穿透
                except Exception as exc:  # 采集失败必须被吞掉，不能杀进程（spec §14.2）
                    last_error = exc
                    logger.warning(
                        "作业 %s 第 %d/%d 次尝试失败 run_id=%s：%s",
                        job_id,
                        attempt,
                        retry.max_attempts,
                        run_id,
                        redact(f"{type(exc).__name__}: {exc}"),
                    )
                    if attempt < retry.max_attempts:
                        await self.sleep(retry.delay_for(attempt))  # 指数退避
                        continue
                else:
                    finished = get_clock().now()
                    duration_ms = int((finished - started).total_seconds() * 1000)
                    self.registry.record_success(
                        job_id, run_id=run_id, duration_ms=duration_ms, at=finished
                    )
                    self.registry.persist()
                    logger.info(
                        "作业 %s 成功 run_id=%s attempts=%d duration_ms=%d",
                        job_id,
                        run_id,
                        attempt,
                        duration_ms,
                    )
                    return RunResult(job_id, run_id, ok=True, attempts=attempt, duration_ms=duration_ms)

            finished = get_clock().now()
            duration_ms = int((finished - started).total_seconds() * 1000)
            error_text = redact(f"{type(last_error).__name__}: {last_error}")
            self.registry.record_failure(job_id, run_id=run_id, error=error_text, at=finished)
            self.registry.persist()
            health_after = self.registry.get(job_id)
            log = logger.error if health_after.degraded else logger.warning
            log(
                "作业 %s 失败 run_id=%s attempts=%d 连续失败=%d degraded=%s：%s",
                job_id,
                run_id,
                attempts,
                health_after.consecutive_failures,
                health_after.degraded,
                error_text,
            )
            return RunResult(
                job_id,
                run_id,
                ok=False,
                attempts=attempts,
                duration_ms=duration_ms,
                error=error_text,
            )


__all__ = [
    "DEGRADED_COOLDOWN_SECONDS",
    "DEGRADE_AFTER_CONSECUTIVE_FAILURES",
    "HEALTH_FILENAME",
    "NO_RETRY",
    "HealthRegistry",
    "JobFn",
    "JobHealth",
    "JobRunner",
    "RetryPolicy",
    "RunResult",
    "disabled_providers",
    "redact",
    "state_dir",
]
