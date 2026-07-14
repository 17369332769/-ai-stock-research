"""巨潮资讯原始 JSON → OpenBB Data 字段的**纯函数**映射。不依赖 httpx / openbb_core。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from .constants import (
    ALLOWED_SOURCES,
    CNINFO_DETAIL_URL,
    CNINFO_STATIC_BASE,
    SHANGHAI,
    SOURCE_CNINFO,
    ProviderDataError,
)

Record = Mapping[str, Any]

# ── 巨潮 hisAnnouncement/query 的字段名（上游契约）────────────────────────────
F_ID = "announcementId"
F_TITLE = "announcementTitle"
F_TIME = "announcementTime"  # epoch 毫秒（Asia/Shanghai 语义）
F_URL = "adjunctUrl"  # 相对路径，拼 static.cninfo.com.cn 得到 PDF 原文
F_SEC_CODE = "secCode"
F_SEC_NAME = "secName"
F_ORG_ID = "orgId"
F_TYPE = "announcementType"


def normalize_symbol(raw: str) -> str:
    text = str(raw).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.split(".")[0]
    if not (len(text) == 6 and text.isdigit()):
        raise ProviderDataError(f"非法 A 股代码：{raw!r}")
    return text


def _require(record: Record, field: str) -> Any:
    if field not in record:
        raise ProviderDataError(f"巨潮公告缺少字段 {field!r}（可用字段：{sorted(record)}）")
    value = record[field]
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ProviderDataError(f"巨潮公告必填字段 {field!r} 为空")
    return value


def _epoch_ms_to_datetime(value: Any) -> datetime:
    """``announcementTime`` 是毫秒时间戳。

    巨潮的时间戳按东八区语义生成，转成带时区 datetime（Asia/Shanghai）。
    """
    if isinstance(value, bool):
        raise ProviderDataError(f"字段 {F_TIME!r} 类型异常（bool）")
    if isinstance(value, int | float):
        millis = float(value)
    elif isinstance(value, str):
        try:
            millis = float(value.strip())
        except ValueError as exc:
            raise ProviderDataError(f"字段 {F_TIME!r} 无法解析为毫秒时间戳：{value!r}") from exc
    else:
        raise ProviderDataError(f"字段 {F_TIME!r} 类型异常（{type(value).__name__}）")
    if millis <= 0:
        raise ProviderDataError(f"字段 {F_TIME!r} 非法：{value!r}")
    return datetime.fromtimestamp(millis / 1000.0, tz=SHANGHAI)


def announcement_pdf_url(adjunct_url: str) -> str:
    """``finalpage/2026-07-14/1234.PDF`` → 原文 PDF 绝对地址。"""
    path = str(adjunct_url).strip().lstrip("/")
    if not path:
        raise ProviderDataError(f"字段 {F_URL!r} 为空，无法定位公告原文")
    return f"{CNINFO_STATIC_BASE}/{path}"


def announcement_detail_url(symbol: str, announcement_id: str, org_id: str, published: datetime) -> str:
    """巨潮网页版详情页（人可读入口；原文仍以 PDF 为准）。"""
    return (
        f"{CNINFO_DETAIL_URL}?stockCode={symbol}"
        f"&announcementId={announcement_id}"
        f"&orgId={org_id}"
        f"&announcementTime={published.date().isoformat()}"
    )


def transform_announcements(
    records: Iterable[Record], symbol: str, source: str = SOURCE_CNINFO
) -> list[dict[str, Any]]:
    """巨潮公告 → CompanyNews 形态的 dict。

    ``text`` 恒为 ``None``：公告原文是 PDF，MVP **不做 PDF 解析**（没有引入解析依赖）。
    这不是"数据缺失被静默吞掉"，而是明确的能力边界，已写入 docs/data-sources.md；
    下游 Agent 只能引用标题与原文链接，不得凭空生成正文内容（spec §11.3）。
    """
    if source not in ALLOWED_SOURCES:
        raise ProviderDataError(
            f"法定披露来源 {source!r} 不在白名单内：{sorted(ALLOWED_SOURCES)}（spec §5.2）"
        )
    code = normalize_symbol(symbol)
    out: list[dict[str, Any]] = []
    for record in records:
        title = str(_require(record, F_TITLE)).strip()
        published_at = _epoch_ms_to_datetime(_require(record, F_TIME))
        pdf_url = announcement_pdf_url(str(_require(record, F_URL)))
        announcement_id = str(_require(record, F_ID))
        org_id = str(record.get(F_ORG_ID) or "")

        sec_code = record.get(F_SEC_CODE)
        # 上游偶尔在同一响应里混入其它证券（如同一 orgId 下的 B 股/债券）——不属于本次请求，剔除
        if sec_code is not None and str(sec_code).strip():
            try:
                if normalize_symbol(str(sec_code)) != code:
                    continue
            except ProviderDataError:
                continue

        out.append(
            {
                "date": published_at,
                "title": title,
                "text": None,
                "url": pdf_url,
                "symbols": code,
                "source": source,
                "document_type": "announcement",
                "announcement_id": announcement_id,
                "org_id": org_id or None,
                "announcement_type": record.get(F_TYPE) or None,
                "sec_name": record.get(F_SEC_NAME) or None,
                "detail_url": announcement_detail_url(code, announcement_id, org_id, published_at),
            }
        )
    out.sort(key=lambda item: item["date"], reverse=True)
    return out


def extract_org_id(records: Iterable[Record], symbol: str) -> str:
    """``topSearch/query`` 结果 → orgId（巨潮公告查询的必需参数）。"""
    code = normalize_symbol(symbol)
    for record in records:
        raw_code = record.get("code")
        if raw_code is None:
            continue
        try:
            if normalize_symbol(str(raw_code)) != code:
                continue
        except ProviderDataError:
            continue
        org_id = record.get("orgId")
        if org_id:
            return str(org_id).strip()
    raise ProviderDataError(f"巨潮未返回 {symbol} 的 orgId，无法查询公告")
