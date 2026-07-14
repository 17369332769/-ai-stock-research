"""中证指数官方成分文件 → 结构化成分记录的**纯函数**解析。

官方成分文件（``000300cons.xls``）的实际投递格式在中证历史上出现过多种：
真 Excel（BIFF）、制表符分隔的文本、以及 HTML 表格。为了不在上游换格式时静默失败，
这里做**显式格式探测**，三种都支持，都失败则抛 ``ProviderDataError``（fail closed）。

列名按**语义**匹配（"成分券代码" / "Constituent Code" / "证券代码" …），不按列序号，
因为中证调整过列顺序。匹配不到必需列同样 fail closed。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any

from .constants import (
    INDEX_CODE,
    MIN_ACCEPTABLE_CONSTITUENTS,
    SHANGHAI,
    SOURCE_NAME,
    UNIVERSE_CODE,
    ProviderDataError,
)

# 列语义 → 候选表头关键字。**按优先级从具体到通用**：官方成分文件里同时存在
# "指数代码Index Code" 与 "成分券代码Constituent Code"，若先用通用词 "code" 匹配，
# 会命中指数代码列，300 只成分全部变成 "000300" —— 因此必须先匹配具体词，
# 且通用词匹配时排除指数自身的列。
CODE_KEYS = ("成分券代码", "constituent code", "证券代码", "股票代码", "code")
NAME_KEYS = ("成分券名称", "constituent name", "证券名称", "股票名称", "name")
EXCHANGE_KEYS = ("交易所", "exchange")
DATE_KEYS = ("日期", "date")

# 通用词匹配时必须跳过的列（指数自身的代码/名称/英文名）
INDEX_OWN_COLUMNS = ("指数代码", "index code", "指数名称", "index name", "指数英文", "英文名称")

_ENCODINGS = ("utf-8-sig", "gbk", "gb18030", "utf-16")


class _TableParser(HTMLParser):
    """把第一张 ``<table>`` 解析成二维字符串数组。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _decode(payload: bytes) -> str | None:
    for encoding in _ENCODINGS:
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _rows_from_text(text: str) -> list[list[str]]:
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]


def _rows_from_excel(payload: bytes) -> list[list[str]]:
    """真 Excel（.xls/.xlsx）。pandas 的 Excel 引擎（xlrd/openpyxl）缺失时抛错，不猜。"""
    try:
        import pandas as pd  # 延迟导入：只有真遇到 Excel 才需要 pandas
    except ImportError as exc:  # pragma: no cover
        raise ProviderDataError("成分文件是 Excel 格式，但未安装 pandas，无法解析") from exc
    try:
        frame = pd.read_excel(io.BytesIO(payload), dtype=str)
    except Exception as exc:  # xlrd/openpyxl 缺失、文件损坏都走这里
        raise ProviderDataError(
            f"成分文件疑似 Excel 但解析失败：{exc}。"
            "若上游确为 .xls，需要在环境中提供 xlrd；否则请改用 CSV 快照。"
        ) from exc
    header = [str(col).strip() for col in frame.columns]
    body = [[("" if value is None else str(value).strip()) for value in row] for row in frame.values]
    return [header, *body]


# 二进制签名。必须在文本解码之前判定：GBK 等宽松编码会把 .xls 的 OLE2 魔数
# "成功"解码成乱码字符串，从而骗过文本分支、返回一行垃圾数据而不是 fail closed。
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .xls (OLE2 复合文档)
_ZIP_MAGIC = b"PK\x03\x04"  # .xlsx (OOXML = zip)


def _looks_binary(payload: bytes) -> bool:
    if payload.startswith(_OLE2_MAGIC) or payload.startswith(_ZIP_MAGIC):
        return True
    # 合法的 CSV/TSV/HTML 快照里不会出现 NUL
    return b"\x00" in payload[:512]


def detect_and_parse_rows(payload: bytes) -> list[list[str]]:
    """bytes → 二维表。三种格式显式探测，都不匹配则 fail closed。"""
    if not payload:
        raise ProviderDataError("成分文件为空")
    if _looks_binary(payload):
        return _rows_from_excel(payload)
    text = _decode(payload)
    if text is not None:
        lowered = text.lstrip()[:200].lower()
        if "<table" in text.lower() or lowered.startswith(("<!doctype", "<html")):
            parser = _TableParser()
            parser.feed(text)
            if parser.rows:
                return parser.rows
            raise ProviderDataError("成分文件是 HTML 但未找到 <table>")
        rows = _rows_from_text(text)
        if rows:
            return rows
        raise ProviderDataError("成分文件是文本但没有可解析的行")
    return _rows_from_excel(payload)


def _find_column(
    header: Sequence[str], keys: Sequence[str], exclude: Sequence[str] = ()
) -> int | None:
    """按 ``keys`` 的**优先级顺序**找列（先具体后通用），命中 ``exclude`` 的列跳过。"""
    lowered = [cell.strip().lower() for cell in header]
    banned = [item.lower() for item in exclude]
    for key in keys:
        needle = key.lower()
        for index, cell in enumerate(lowered):
            if needle not in cell:
                continue
            if any(bad in cell for bad in banned):
                continue
            return index
    return None


def normalize_symbol(raw: str) -> str:
    text = str(raw).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.split(".")[0]
    if text.isdigit() and len(text) < 6:
        text = text.zfill(6)  # Excel 常把 000001 读成 1
    if not (len(text) == 6 and text.isdigit()):
        raise ProviderDataError(f"非法 A 股代码：{raw!r}")
    return text


def exchange_of(symbol: str, hint: str | None = None) -> str:
    """交易所。优先用官方"交易所"列，缺列则由代码前缀推断，两者冲突以代码前缀为准。"""
    if symbol.startswith(("6", "9")):
        by_prefix: str | None = "SSE"
    elif symbol.startswith(("0", "2", "3")):
        by_prefix = "SZSE"
    else:
        by_prefix = None
    if by_prefix is None:
        raise ProviderDataError(f"无法判定交易所：{symbol!r} 不是沪深 A 股代码")
    if hint:
        text = hint.strip().lower()
        by_hint = (
            "SSE"
            if ("上海" in hint or "sse" in text or "shanghai" in text)
            else "SZSE"
            if ("深圳" in hint or "szse" in text or "shenzhen" in text)
            else None
        )
        if by_hint is not None and by_hint != by_prefix:
            raise ProviderDataError(
                f"成分文件自相矛盾：{symbol} 代码前缀指向 {by_prefix}，交易所列却是 {hint!r}"
            )
    return by_prefix


def _parse_snapshot_date(value: str) -> date | None:
    text = value.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=SHANGHAI).date()
        except ValueError:
            continue
    return None


def parse_constituents(payload: bytes) -> tuple[list[dict[str, Any]], date | None]:
    """官方成分文件 → (成分记录列表, 文件自带的生效日期)。

    数量守门：少于 ``MIN_ACCEPTABLE_CONSTITUENTS`` 视为脏数据（半截文件 / 上游改版），
    直接抛错。否则一次坏抓取就会把 universe_memberships 里几十只股票误标为"已调出"。
    """
    rows = detect_and_parse_rows(payload)
    if len(rows) < 2:
        raise ProviderDataError("成分文件没有数据行")

    header_index = 0
    code_col = _find_column(rows[0], CODE_KEYS, INDEX_OWN_COLUMNS)
    if code_col is None:
        # 有的投递格式前面有标题行，向下找 5 行
        for index in range(1, min(6, len(rows))):
            candidate = _find_column(rows[index], CODE_KEYS, INDEX_OWN_COLUMNS)
            if candidate is not None:
                header_index, code_col = index, candidate
                break
    if code_col is None:
        raise ProviderDataError(f"成分文件找不到代码列（表头：{rows[0]}）")

    header = rows[header_index]
    name_col = _find_column(header, NAME_KEYS, INDEX_OWN_COLUMNS)
    exchange_col = _find_column(header, EXCHANGE_KEYS)
    date_col = _find_column(header, DATE_KEYS)
    if name_col is None:
        raise ProviderDataError(f"成分文件找不到名称列（表头：{header}）")

    snapshot_date: date | None = None
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows[header_index + 1 :]:
        if len(row) <= max(code_col, name_col):
            continue
        raw_code = row[code_col].strip()
        if not raw_code:
            continue
        symbol = normalize_symbol(raw_code)
        if symbol in seen:
            continue
        seen.add(symbol)
        hint = row[exchange_col].strip() if exchange_col is not None and len(row) > exchange_col else None
        if snapshot_date is None and date_col is not None and len(row) > date_col:
            snapshot_date = _parse_snapshot_date(row[date_col])
        out.append(
            {
                "symbol": symbol,
                "name": row[name_col].strip(),
                "exchange": exchange_of(symbol, hint),
            }
        )

    if len(out) < MIN_ACCEPTABLE_CONSTITUENTS:
        raise ProviderDataError(
            f"沪深300 成分只解析出 {len(out)} 只（低于下限 {MIN_ACCEPTABLE_CONSTITUENTS}）："
            "疑似上游返回半截文件或改版，拒绝使用"
        )
    return out, snapshot_date


def to_constituent_records(
    constituents: Iterable[dict[str, Any]],
    *,
    as_of: date,
    snapshot_date: date,
    source_url: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """成分 → OpenBB Data 形态。

    ``snapshot_date`` 是**这份成分表的官方生效日期**，可能早于 ``as_of``（成分只在调整日变化）。
    两个日期都原样透出，下游据此判断"这条成分信息到底是哪天观测到的"，不做隐式对齐。
    """
    if observed_at.tzinfo is None:
        raise ProviderDataError("observed_at 必须带时区")
    return [
        {
            "symbol": item["symbol"],
            "name": item["name"],
            "exchange": item["exchange"],
            "index_code": INDEX_CODE,
            "universe": UNIVERSE_CODE,
            "as_of": as_of,
            "snapshot_date": snapshot_date,
            "source": SOURCE_NAME,
            "source_url": source_url,
            "observed_at": observed_at,
        }
        for item in constituents
    ]
