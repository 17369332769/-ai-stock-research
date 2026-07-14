"""Agent 的**固定**输出 Schema（spec §11.2）。

::

    {
      "summary": "string",
      "direction": "positive|negative|neutral|unknown",
      "horizon": "intraday|short|medium|unknown",
      "confidence": 0.0,
      "evidence_ids": ["uuid"],
      "unknowns": ["string"],
      "risk_flags": ["string"]
    }

铁律：

* Schema **封闭**（``additionalProperties: false`` / pydantic ``extra="forbid"``）。
  模型多吐一个字段（比如自己造一个 ``probability_up``）即视为校验失败 —— 定量概率永远来自量化模型。
* ``evidence_ids`` 为空 → ``direction`` 必须是 ``unknown``（spec §11.3），这条直接写进模型校验，
  违反即校验失败，走"最多重试一次 → 再失败用模板摘要"。
* 校验失败最多重试一次；再失败使用模板摘要（``template_output``），**不得编造**。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from apps.api.app.core.enums import NO_VERIFIABLE_CAUSE_TEXT, Direction, EventHorizon

MAX_SUMMARY_CHARS = 1200
MAX_LIST_ITEMS = 12

AGENT_OUTPUT_JSON_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "direction",
        "horizon",
        "confidence",
        "evidence_ids",
        "unknowns",
        "risk_flags",
    ],
    "properties": {
        "summary": {"type": "string", "maxLength": MAX_SUMMARY_CHARS},
        "direction": {"type": "string", "enum": ["positive", "negative", "neutral", "unknown"]},
        "horizon": {"type": "string", "enum": ["intraday", "short", "medium", "unknown"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
            "maxItems": MAX_LIST_ITEMS,
        },
        "unknowns": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_LIST_ITEMS},
        "risk_flags": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_LIST_ITEMS},
    },
}


class AgentSchemaError(ValueError):
    """模型输出不符合固定 Schema。调用方据此重试一次，再失败即降级模板摘要。"""


class AgentOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(max_length=MAX_SUMMARY_CHARS)
    direction: Direction
    horizon: EventHorizon
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    unknowns: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    risk_flags: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)

    @model_validator(mode="after")
    def _no_evidence_means_unknown(self) -> AgentOutput:
        # spec §11.3：evidence_ids 为空时，方向必须为 unknown
        if not self.evidence_ids and self.direction is not Direction.UNKNOWN:
            raise ValueError("evidence_ids 为空时 direction 必须为 unknown")
        return self


_JSON_FENCE = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型回复里取出 JSON 对象。容忍 ```json 围栏与前后寒暄，但不容忍缺字段。"""
    if not text or not text.strip():
        raise AgentSchemaError("模型回复为空")

    candidates: list[str] = []
    fence = _JSON_FENCE.search(text)
    if fence is not None:
        candidates.append(fence.group("body"))
    stripped = text.strip()
    candidates.append(stripped)
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AgentSchemaError("模型回复中没有可解析的 JSON 对象")


def parse_agent_output(text: str) -> AgentOutput:
    """解析 + 严格校验。任何不符即抛 ``AgentSchemaError``（触发一次重试）。"""
    payload = extract_json_object(text)
    try:
        return AgentOutput.model_validate(payload)
    except ValidationError as exc:
        raise AgentSchemaError(f"输出不符合固定 Schema：{exc.error_count()} 处错误") from exc


def template_output(
    *,
    summary_prefix: str = "",
    unknowns: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> AgentOutput:
    """模板摘要：Agent 不可用 / 两次校验失败 / 无证据时的**唯一**兜底。

    只陈述固定文案与（可选的）确定性事实前缀，绝不编造公司事件；
    方向恒为 ``unknown``，置信度恒为 0。
    """
    if summary_prefix:
        summary = f"{summary_prefix}\n\n{NO_VERIFIABLE_CAUSE_TEXT}"
    else:
        summary = NO_VERIFIABLE_CAUSE_TEXT
    return AgentOutput(
        summary=summary[:MAX_SUMMARY_CHARS],
        direction=Direction.UNKNOWN,
        horizon=EventHorizon.UNKNOWN,
        confidence=0.0,
        evidence_ids=[],
        unknowns=(unknowns or [])[:MAX_LIST_ITEMS],
        risk_flags=(risk_flags or [])[:MAX_LIST_ITEMS],
    )
