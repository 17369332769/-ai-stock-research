"""领域枚举。数据库 CHECK 约束、Pydantic DTO 与业务代码共用同一份取值，防止三处漂移。"""

from __future__ import annotations

from enum import StrEnum


class Exchange(StrEnum):
    SSE = "SSE"
    SZSE = "SZSE"


class Timeframe(StrEnum):
    MIN5 = "5m"
    DAY1 = "1d"


class DocumentType(StrEnum):
    ANNOUNCEMENT = "announcement"
    NEWS = "news"


class AnalysisType(StrEnum):
    DOCUMENT = "document"
    ANOMALY = "anomaly"
    DAILY_BRIEF = "daily_brief"


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class EventHorizon(StrEnum):
    """Agent 分析的影响期限（analyses.horizon）。"""

    INTRADAY = "intraday"
    SHORT = "short"
    MEDIUM = "medium"
    UNKNOWN = "unknown"


class PredictionHorizon(StrEnum):
    """预测目标（predictions.horizon / model_versions.target_horizon）。"""

    TODAY_CLOSE = "today_close"
    NEXT_5D = "next_5d"


class ConfidenceLabel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelStatus(StrEnum):
    CANDIDATE = "candidate"  # 永远不对 API 提供预测（spec §9.4）
    ACTIVE = "active"
    RETIRED = "retired"


class JobType(StrEnum):
    INSTRUMENT_BACKFILL = "instrument_backfill"
    ANALYSIS_REFRESH = "analysis_refresh"
    PREDICTION = "prediction"
    SETTLEMENT = "settlement"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Freshness(StrEnum):
    """行情新鲜度。禁止把 stale 当 fresh 展示（spec §3.2）。"""

    FRESH = "fresh"
    STALE = "stale"


# 回补作业的固定步骤（spec §7.1）
BACKFILL_STEPS: tuple[str, ...] = ("daily_bars", "minute_bars", "documents")

# 无证据时的固定文案（spec §7.3 / §12 / 验收 §15.5）
NO_VERIFIABLE_CAUSE_TEXT = "未找到可验证事件原因"

# 预测区域必须出现的免责声明（spec §13.2）
RESEARCH_ONLY_DISCLAIMER = "仅供研究，不构成投资建议"

# 沪深300 与基准
CSI300_CODE = "CSI300"
CSI300_BENCHMARK_SYMBOL = "000300"
