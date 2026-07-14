"""日志脱敏（spec §14.3 / §14.4）。

日志**不得**包含完整 API 密钥、用户机器路径或原始提示中的敏感数据。
"""

from __future__ import annotations

import pytest

from apps.api.app.core.logging import METRICS, redact
from apps.api.app.core.settings import Settings


def test_redacts_openai_style_key() -> None:
    out = redact("calling provider with sk-abcdef0123456789ABCDEF")
    assert "sk-abcdef0123456789ABCDEF" not in out
    assert "[REDACTED]" in out


def test_redacts_bearer_token() -> None:
    out = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out


def test_redacts_api_key_assignment() -> None:
    out = redact('agent_api_key="super-secret-value-123"')
    assert "super-secret-value-123" not in out


def test_redacts_user_home_path() -> None:
    """不得泄漏用户机器路径。"""
    out = redact("traceback at /home/alice/projects/ai-stock/app.py")
    assert "/home/alice" not in out
    assert "/<home>" in out


def test_redacts_macos_home_path() -> None:
    out = redact("/Users/bob/Library/x")
    assert "/Users/bob" not in out


def test_redacts_windows_home_path() -> None:
    out = redact(r"C:\Users\carol\AppData\key.txt")
    assert "carol" not in out


def test_plain_text_untouched() -> None:
    assert redact("600519 快照返回 200") == "600519 快照返回 200"


def test_settings_repr_hides_secret() -> None:
    """Settings 的 repr 绝不能把密钥打出来。"""
    settings = Settings(agent_api_key="secret-key-value", agent_base_url="", agent_model="")
    assert "secret-key-value" not in repr(settings)


def test_metrics_counts_stale_quotes() -> None:
    METRICS.reset()
    METRICS.record_stale_quote("600519")
    METRICS.record_stale_quote("000001")
    assert METRICS.snapshot()["counters"]["stale_quotes_served"] == 2
    METRICS.reset()


def test_metrics_data_source_success_rate() -> None:
    METRICS.reset()
    METRICS.record_data_source("akshare", ok=True, latency_ms=10.0)
    METRICS.record_data_source("akshare", ok=True, latency_ms=20.0)
    METRICS.record_data_source("akshare", ok=False, latency_ms=30.0)
    stats = METRICS.snapshot()["data_sources"]["akshare"]
    assert stats["success"] == 2
    assert stats["failure"] == 1
    assert stats["success_rate"] == pytest.approx(2 / 3)
    METRICS.reset()


def test_metrics_records_request_latency() -> None:
    METRICS.reset()
    METRICS.record_request(path="/api/v1/watchlist", status=200, latency_ms=12.5)
    snapshot = METRICS.snapshot()
    assert snapshot["counters"]["http_status_2xx"] == 1
    assert snapshot["request_p95_latency_ms"] == pytest.approx(12.5)
    METRICS.reset()
