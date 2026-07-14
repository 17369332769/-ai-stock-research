"""CSI300 Provider 契约测试（中证指数官方成分）。

重点是**幸存者偏差防线**：历史快照缺失时必须报错，绝不用当前成分冒充历史成分（spec §9.3）。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from apps.api.app.core.clock import SHANGHAI
from services.openbb_extensions.csi300_provider.client import (
    fetch_current,
    get_current_constituents,
    get_snapshot_constituents,
    list_snapshots,
)
from services.openbb_extensions.csi300_provider.constants import (
    CSINDEX_CONS_URLS,
    ProviderDataError,
    ProviderUpstreamError,
    SnapshotNotFound,
)
from services.openbb_extensions.csi300_provider.transform import (
    detect_and_parse_rows,
    exchange_of,
    parse_constituents,
)
from services.openbb_extensions.tests.conftest import load_bytes

pytestmark = pytest.mark.contract

NOW = datetime(2026, 7, 14, 7, 30, tzinfo=SHANGHAI)
AS_OF = date(2026, 7, 14)


# ── 解析：三种投递格式 ──────────────────────────────────────────────────────
def test_parse_tab_separated_gbk_file() -> None:
    constituents, snapshot_date = parse_constituents(load_bytes("csindex_cons_tsv.txt"))

    assert len(constituents) == 300
    assert snapshot_date == date(2026, 6, 15)
    head = {item["symbol"]: item for item in constituents[:4]}
    assert head["600519"]["name"] == "贵州茅台"
    assert head["600519"]["exchange"] == "SSE"
    assert head["000001"]["exchange"] == "SZSE"
    assert head["300750"]["exchange"] == "SZSE"


def test_parse_html_table_file() -> None:
    """中证历史上也投递过 HTML 表格 —— 换格式不能静默失败。"""
    constituents, snapshot_date = parse_constituents(load_bytes("csindex_cons_table.html"))
    assert len(constituents) == 300
    assert snapshot_date == date(2026, 6, 15)


def test_parse_empty_payload_fails_closed() -> None:
    with pytest.raises(ProviderDataError, match="为空"):
        parse_constituents(b"")


def test_truncated_file_is_rejected() -> None:
    """半截文件（只有 10 只）—— 若接受，成分同步会把 290 只误判为「已调出」。"""
    with pytest.raises(ProviderDataError, match="低于下限"):
        parse_constituents(load_bytes("csindex_cons_truncated.txt"))


def test_missing_code_column_fails_closed() -> None:
    with pytest.raises(ProviderDataError, match="代码列"):
        parse_constituents(load_bytes("csindex_cons_missing_column.txt"))


def test_exchange_column_conflict_fails_closed() -> None:
    """代码前缀说沪市、交易所列说深市 —— 自相矛盾必须炸，不能二选一猜。"""
    with pytest.raises(ProviderDataError, match="自相矛盾"):
        parse_constituents(load_bytes("csindex_cons_exchange_conflict.txt"))


def test_unparseable_binary_fails_closed() -> None:
    with pytest.raises(ProviderDataError):
        detect_and_parse_rows(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00")  # 伪 .xls 头


@pytest.mark.parametrize(
    ("symbol", "expected"), [("600519", "SSE"), ("601318", "SSE"), ("000001", "SZSE"), ("300750", "SZSE")]
)
def test_exchange_inference(symbol: str, expected: str) -> None:
    assert exchange_of(symbol) == expected


# ── HTTP（respx，不联网）────────────────────────────────────────────────────
@respx.mock
async def test_fetch_current_uses_official_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CSI300_SNAPSHOT_DIR", str(tmp_path))
    respx.get(CSINDEX_CONS_URLS[0]).mock(
        return_value=httpx.Response(200, content=load_bytes("csindex_cons_tsv.txt"))
    )
    records = await get_current_constituents(AS_OF, observed_at=NOW)

    assert len(records) == 300
    first = records[0]
    assert first["source"] == "csindex"
    assert first["source_url"] == CSINDEX_CONS_URLS[0]
    assert first["observed_at"] == NOW
    assert first["as_of"] == AS_OF
    assert first["snapshot_date"] == date(2026, 6, 15)  # 成分表官方生效日 ≠ as_of


@respx.mock
async def test_fetch_current_falls_back_to_official_mirror() -> None:
    """两个 URL 是同一发行方的主站与 OSS 镜像（口径一致），不是"另一个数据源"。"""
    respx.get(CSINDEX_CONS_URLS[0]).mock(return_value=httpx.Response(500))
    respx.get(CSINDEX_CONS_URLS[1]).mock(
        return_value=httpx.Response(200, content=load_bytes("csindex_cons_tsv.txt"))
    )
    payload, url, _ = await fetch_current()
    assert url == CSINDEX_CONS_URLS[1]
    assert payload


@respx.mock
async def test_all_official_urls_down_fails_closed() -> None:
    for url in CSINDEX_CONS_URLS:
        respx.get(url).mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUpstreamError, match="不可用"):
        await fetch_current()


@respx.mock
async def test_rate_limited_fails_closed() -> None:
    for url in CSINDEX_CONS_URLS:
        respx.get(url).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderUpstreamError, match="429"):
        await fetch_current()


@respx.mock
async def test_timeout_fails_closed() -> None:
    for url in CSINDEX_CONS_URLS:
        respx.get(url).mock(side_effect=httpx.ReadTimeout("timed out after 30s"))
    with pytest.raises(ProviderUpstreamError, match="超时"):
        await fetch_current()


@respx.mock
async def test_empty_body_fails_closed() -> None:
    for url in CSINDEX_CONS_URLS:
        respx.get(url).mock(return_value=httpx.Response(200, content=b""))
    with pytest.raises(ProviderUpstreamError, match="响应体为空"):
        await fetch_current()


# ── 历史快照：幸存者偏差防线 ─────────────────────────────────────────────────
def test_missing_historical_snapshot_fails_closed_not_current(tmp_path: Path) -> None:
    """**核心测试**：没有历史快照时必须报错。

    如果这里退回"当前成分"，2024 年的训练样本就会包含 2026 年才调入的股票 ——
    幸存者偏差，回测会好看得离谱且完全不可信（spec §9.3）。
    """
    with pytest.raises(SnapshotNotFound, match="幸存者偏差"):
        get_snapshot_constituents(date(2024, 3, 1), directory=tmp_path, observed_at=NOW)


def test_snapshot_lookup_uses_latest_on_or_before_as_of(tmp_path: Path) -> None:
    (tmp_path / "000300cons_20260615.txt").write_bytes(load_bytes("csindex_cons_tsv.txt"))
    (tmp_path / "000300cons_20260713.txt").write_bytes(load_bytes("csindex_cons_after_rebalance.txt"))

    # 2026-07-01：只能看到 06-15 的成分（含 000001）
    early = get_snapshot_constituents(date(2026, 7, 1), directory=tmp_path, observed_at=NOW)
    assert "000001" in {item["symbol"] for item in early}
    assert early[0]["snapshot_date"] == date(2026, 6, 15)

    # 2026-07-14：看到 07-13 调整后的成分（000001 已调出，600036 调入）
    late = get_snapshot_constituents(date(2026, 7, 14), directory=tmp_path, observed_at=NOW)
    symbols = {item["symbol"] for item in late}
    assert "000001" not in symbols
    assert "600036" in symbols
    assert late[0]["snapshot_date"] == date(2026, 7, 13)


def test_snapshot_date_mismatch_is_rejected(tmp_path: Path) -> None:
    """文件名日期与文件内日期不一致 → 归档被污染，拒绝使用。"""
    (tmp_path / "000300cons_20260101.txt").write_bytes(load_bytes("csindex_cons_tsv.txt"))
    with pytest.raises(ProviderDataError, match="不一致"):
        get_snapshot_constituents(date(2026, 3, 1), directory=tmp_path, observed_at=NOW)


@respx.mock
async def test_successful_fetch_archives_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """write-through 归档：每次成功抓取都留一份官方快照，历史越跑越全。"""
    monkeypatch.setenv("CSI300_SNAPSHOT_DIR", str(tmp_path))
    respx.get(CSINDEX_CONS_URLS[0]).mock(
        return_value=httpx.Response(200, content=load_bytes("csindex_cons_tsv.txt"))
    )
    await get_current_constituents(AS_OF, observed_at=NOW)

    snapshots = list_snapshots(tmp_path)
    assert [item[0] for item in snapshots] == [date(2026, 6, 15)]


def test_snapshot_archive_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CSI300_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("CSI300_SNAPSHOT_ARCHIVE", "0")
    assert list_snapshots(tmp_path) == []
