"""结构化日志与可观测性指标（spec §14.3 / §14.4）。

铁律：
- 每个请求生成 ``request_id``（后台作业用 ``job_id``），并随每条日志输出；
- 日志**不得**包含完整 API 密钥、用户机器路径或原始提示中的敏感数据 —— 由
  ``RedactionFilter`` 在写出前统一脱敏，而不是指望每个调用点自觉；
- 记录数据源成功率、延迟、旧数据数量、预测生成数量与结算积压（§14.4）。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

# ── 请求上下文 ────────────────────────────────────────────────────────────────
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    return request_id_var.get()


def set_request_id(value: str) -> None:
    request_id_var.set(value)


# ── 脱敏 ─────────────────────────────────────────────────────────────────────
# 1) 常见 API key 形态（sk-xxx / Bearer xxx / 长随机串）
# 2) 用户机器路径（/home/<user>/... 、/Users/<user>/...、C:\Users\<user>\...）
# 3) 显式 key=value 形式的密钥字段
_REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), _REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), f"Bearer {_REDACTED}"),
    (
        # 前缀 [A-Za-z_]* 是必需的：``agent_api_key`` 里 "api" 前面是下划线（词字符），
        # 所以 \b 根本不会匹配 —— 用 \b 的话这条规则对真实配置名完全失效。
        re.compile(
            r"(?i)([A-Za-z_]*(?:api[_-]?key|apikey|token|secret|password|authorization))"
            r"\s*[=:]\s*[\"']?([^\s\"',}]+)"
        ),
        r"\1=" + _REDACTED,
    ),
    (re.compile(r"(?i)/(?:home|users)/[^/\s\"']+"), "/<home>"),
    (re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s\"']+"), r"<home>"),
)


def redact(text: str) -> str:
    """把密钥与用户机器路径从任意文本中抹掉（spec §14.3 / §14.4）。"""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactionFilter(logging.Filter):
    """在日志落盘前统一脱敏：消息、参数与 extra 字段全覆盖。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_value(a) for a in record.args)
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_LOGRECORD_KEYS:
                continue
            record.__dict__[key] = _redact_value(value)
        return True


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


_RESERVED_LOGRECORD_KEYS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "thread", "threadName", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志，便于本地 grep 与后续接入日志系统。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = get_request_id()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOGRECORD_KEYS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            # 只记录异常类型与消息；堆栈留给本地 DEBUG，不进入响应体（spec §14.3）
            exc_type, exc_value, _ = record.exc_info
            payload["exc_type"] = getattr(exc_type, "__name__", str(exc_type))
            payload["exc_message"] = redact(str(exc_value))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """进程启动时调用一次。"""
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn 自带的 access 日志与我们的结构化中间件重复，交给中间件统一输出
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ── 可观测性指标（spec §14.4）────────────────────────────────────────────────
@dataclass
class _SourceStat:
    success: int = 0
    failure: int = 0
    latency_ms: list[float] = field(default_factory=list)


class Metrics:
    """进程内指标登记簿。

    MVP 不引入 Prometheus；这里保留可读快照，供 ``/settings/data-sources`` 页面与
    集成测试断言使用（spec §13.1：数据源、最后成功时间和模型连接状态）。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._sources: dict[str, _SourceStat] = defaultdict(_SourceStat)
        self._counters: dict[str, int] = defaultdict(int)
        self._request_latency_ms: list[float] = []

    # 数据源成功率与延迟
    def record_data_source(self, source: str, *, ok: bool, latency_ms: float) -> None:
        with self._lock:
            stat = self._sources[source]
            if ok:
                stat.success += 1
            else:
                stat.failure += 1
            stat.latency_ms.append(latency_ms)

    # 旧数据数量：每次向客户端返回 stale 行情就 +1
    def record_stale_quote(self, symbol: str) -> None:
        self.increment("stale_quotes_served")

    def record_request(self, *, path: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self._request_latency_ms.append(latency_ms)
            self._counters[f"http_status_{status // 100}xx"] += 1

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            sources = {
                name: {
                    "success": s.success,
                    "failure": s.failure,
                    "success_rate": (
                        s.success / (s.success + s.failure)
                        if (s.success + s.failure)
                        else None  # 一次都没调过 ⇒ 成功率无定义，不写 0
                    ),
                    "p95_latency_ms": _percentile(s.latency_ms, 95),
                }
                for name, s in self._sources.items()
            }
            return {
                "counters": dict(self._counters),
                "data_sources": sources,
                "request_p95_latency_ms": _percentile(self._request_latency_ms, 95),
            }

    def reset(self) -> None:
        with self._lock:
            self._sources.clear()
            self._counters.clear()
            self._request_latency_ms.clear()


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[index]


METRICS = Metrics()
