"""CSI300 Provider 的常量与错误类型。纯 stdlib。"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

UNIVERSE_CODE = "CSI300"
INDEX_CODE = "000300"
INDEX_NAME = "沪深300"
BENCHMARK_SYMBOL = "000300"

# ── 权威来源：中证指数有限公司（China Securities Index Co., Ltd.）─────────────
SOURCE_NAME = "csindex"  # universes.source / universe_memberships.source（varchar(40)）

CSINDEX_SITE = "https://www.csindex.com.cn"
CSINDEX_INDEX_PAGE = f"{CSINDEX_SITE}/#/indices/family/detail?indexCode={INDEX_CODE}"

# 官方成分文件（当期）。只保留一个明确入口。
_CONS_PATH = f"static/html/csindex/public/uploads/file/autofile/cons/{INDEX_CODE}cons.xls"
_CSINDEX_OSS = "https://oss-ch.csindex.com.cn"
CSINDEX_CONS_URL = f"{_CSINDEX_OSS}/{_CONS_PATH}"

# 指数调整公告（人工核对入口；PDF，MVP 不自动解析）
CSINDEX_ANNOUNCEMENT_PAGE = f"{CSINDEX_SITE}/#/about-us/notice"

DEFAULT_TIMEOUT_SECONDS = 30.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)

# ── 历史成分快照归档 ────────────────────────────────────────────────────────
# 文件名固定 ``000300cons_YYYYMMDD.<ext>``；YYYYMMDD 是**该成分表的官方生效日期**。
SNAPSHOT_DIR_ENV = "CSI300_SNAPSHOT_DIR"
SNAPSHOT_ARCHIVE_ENV = "CSI300_SNAPSHOT_ARCHIVE"
DEFAULT_SNAPSHOT_DIR = "data/csi300_snapshots"
SNAPSHOT_PREFIX = f"{INDEX_CODE}cons_"
SNAPSHOT_EXTENSIONS: tuple[str, ...] = (".csv", ".tsv", ".txt", ".xls", ".xlsx", ".html")

# 沪深300 成分数量。上游返回数量严重偏离即判为脏数据（防止半截文件把 300 只清成 3 只）。
EXPECTED_CONSTITUENT_COUNT = 300
MIN_ACCEPTABLE_CONSTITUENTS = 250


def snapshot_dir() -> Path:
    return Path(os.environ.get(SNAPSHOT_DIR_ENV, DEFAULT_SNAPSHOT_DIR))


def archive_enabled() -> bool:
    return os.environ.get(SNAPSHOT_ARCHIVE_ENV, "1").strip().lower() not in {"0", "false", "no"}


class Csi300ProviderError(RuntimeError):
    """CSI300 Provider 错误基类。"""


class ProviderConfigError(Csi300ProviderError):
    """参数非法。"""


class ProviderDataError(Csi300ProviderError):
    """成分文件形态不符合契约：列缺失、无法解析、数量异常。"""


class ProviderUpstreamError(Csi300ProviderError):
    """上游不可用：网络错误、限流、超时、非 2xx。"""


class SnapshotNotFound(Csi300ProviderError):
    """请求的历史成分快照不存在。

    **绝不降级为当前成分** —— 那会把已调出的股票塞回历史样本，制造幸存者偏差
    （spec §9.3：禁止用当前 300 只股票回填全部历史）。
    """
