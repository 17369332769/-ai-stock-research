"""唯一触达中证指数官网的模块 + 历史成分快照归档。

两条路径语义不同，**不得互相顶替**（spec §9.3 幸存者偏差）：

- ``fetch_current()``：抓官方当期成分文件；成功后 write-through 归档一份快照。
- ``load_snapshot(as_of)``：读归档中"生效日期 <= as_of 的最新一份"。没有 → ``SnapshotNotFound``。

``load_snapshot`` 返回的记录带 ``snapshot_date``，调用方能看到它和 ``as_of`` 的差距；
成分只在调整日变化，所以「<= as_of 的最新快照」是正确的 point-in-time 语义 ——
**前提是归档没有漏掉中间的调整日**。归档稀疏时的风险写在 docs/data-sources.md。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from .constants import (
    CSINDEX_CONS_URLS,
    CSINDEX_INDEX_PAGE,
    DEFAULT_TIMEOUT_SECONDS,
    SHANGHAI,
    SNAPSHOT_EXTENSIONS,
    SNAPSHOT_PREFIX,
    USER_AGENT,
    ProviderDataError,
    ProviderUpstreamError,
    SnapshotNotFound,
    archive_enabled,
    snapshot_dir,
)
from .transform import parse_constituents, to_constituent_records

_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": CSINDEX_INDEX_PAGE,
    "User-Agent": USER_AGENT,
}


def _extension_for(url: str, content_type: str | None) -> str:
    ctype = (content_type or "").lower()
    if "html" in ctype:
        return ".html"
    if "csv" in ctype or "plain" in ctype:
        return ".csv"
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix if suffix in SNAPSHOT_EXTENSIONS else ".xls"


def snapshot_path(directory: Path, effective: date, extension: str) -> Path:
    return directory / f"{SNAPSHOT_PREFIX}{effective.strftime('%Y%m%d')}{extension}"


def archive_snapshot(payload: bytes, effective: date, extension: str) -> Path | None:
    """把官方成分文件原样存档（原样 bytes，不做二次编码），供未来做无偏历史。"""
    if not archive_enabled():
        return None
    directory = snapshot_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = snapshot_path(directory, effective, extension)
    if not target.exists():
        target.write_bytes(payload)
    return target


def list_snapshots(directory: Path | None = None) -> list[tuple[date, Path]]:
    """归档目录里的全部快照，按生效日期升序。"""
    base = directory or snapshot_dir()
    if not base.is_dir():
        return []
    found: list[tuple[date, Path]] = []
    for path in base.iterdir():
        if not path.is_file() or not path.name.startswith(SNAPSHOT_PREFIX):
            continue
        if path.suffix.lower() not in SNAPSHOT_EXTENSIONS:
            continue
        stem = path.stem[len(SNAPSHOT_PREFIX) :]
        try:
            effective = datetime.strptime(stem, "%Y%m%d").replace(tzinfo=SHANGHAI).date()
        except ValueError:
            continue
        found.append((effective, path))
    return sorted(found, key=lambda item: item[0])


async def fetch_current(
    client: httpx.AsyncClient | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> tuple[bytes, str, str]:
    """下载官方当期成分文件 → (bytes, 命中的 URL, 文件扩展名)。

    两个候选 URL 是同一发行方的主站与 OSS 镜像（口径一致），全部失败即 unavailable。
    """
    owns = client is None
    http = client or httpx.AsyncClient(timeout=timeout, headers=_HEADERS, follow_redirects=True)
    errors: list[str] = []
    try:
        for url in CSINDEX_CONS_URLS:
            try:
                response = await http.get(url, headers=_HEADERS)
            except httpx.TimeoutException as exc:
                errors.append(f"{url}: 超时（{exc}）")
                continue
            except httpx.HTTPError as exc:
                errors.append(f"{url}: 网络错误（{exc}）")
                continue
            if response.status_code == 429:
                errors.append(f"{url}: 限流 HTTP 429")
                continue
            if response.status_code >= 400:
                errors.append(f"{url}: HTTP {response.status_code}")
                continue
            payload = response.content
            if not payload:
                errors.append(f"{url}: 响应体为空")
                continue
            return payload, url, _extension_for(url, response.headers.get("content-type"))
    finally:
        if owns:
            await http.aclose()
    raise ProviderUpstreamError("中证指数成分文件不可用：" + "；".join(errors))


async def get_current_constituents(
    as_of: date,
    client: httpx.AsyncClient | None = None,
    observed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    payload, url, extension = await fetch_current(client)
    constituents, file_date = parse_constituents(payload)
    effective = file_date or as_of
    archive_snapshot(payload, effective, extension)
    return to_constituent_records(
        constituents,
        as_of=as_of,
        snapshot_date=effective,
        source_url=url,
        observed_at=observed_at or datetime.now(tz=SHANGHAI),
    )


def get_snapshot_constituents(
    as_of: date,
    directory: Path | None = None,
    observed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """读取 ``<= as_of`` 的最新官方历史快照。缺失 → ``SnapshotNotFound``（绝不回退到当前成分）。"""
    candidates = [item for item in list_snapshots(directory) if item[0] <= as_of]
    if not candidates:
        raise SnapshotNotFound(
            f"没有 {as_of} 或更早的沪深300 官方成分快照（归档目录：{directory or snapshot_dir()}）。"
            "拒绝用当前成分冒充历史成分 —— 那会制造幸存者偏差（spec §9.3）。"
            "请从中证指数官网补齐该日期的官方成分文件后重试。"
        )
    effective, path = candidates[-1]
    payload = path.read_bytes()
    constituents, file_date = parse_constituents(payload)
    if file_date is not None and file_date != effective:
        raise ProviderDataError(
            f"快照 {path.name} 的文件内日期 {file_date} 与文件名日期 {effective} 不一致，拒绝使用"
        )
    return to_constituent_records(
        constituents,
        as_of=as_of,
        snapshot_date=effective,
        source_url=path.as_uri(),
        observed_at=observed_at or datetime.now(tz=SHANGHAI),
    )
