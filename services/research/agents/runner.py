"""Agent 执行循环：工具调用 → 固定 Schema 输出 → 最多重试一次 → 模板摘要兜底。

降级（degraded）是**一等公民**，任何一条都不会让作业崩：

* ``settings.agent_enabled == False``（未配置）→ 模板摘要；
* 模型不可达 / 超时 / 结构非法（``AgentUnavailable``）→ 模板摘要；
* 输出不符合固定 Schema（含"无证据必须 unknown"）→ **重试一次** → 再失败 → 模板摘要；
* 工具调用超出预算 → 模板摘要。

降级时 ``model_provider`` / ``model_name`` 一律留空：这条结论不是模型产出的，
账面上就不能记成模型产出（spec §11.3 / 要求 6）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from services.research.agents.client import (
    PROVIDER_LABEL,
    AgentUnavailable,
    ChatClient,
    ChatCompletion,
    ToolCall,
)
from services.research.agents.prompts import SCHEMA_RETRY_PROMPT, SYSTEM_PROMPT
from services.research.agents.schema import (
    AgentOutput,
    AgentSchemaError,
    parse_agent_output,
    template_output,
)
from services.research.agents.tools import AgentToolbox, ToolError

logger = logging.getLogger(__name__)

# 一次分析最多允许的工具调用轮次（防止模型在工具里打转）
MAX_TOOL_ROUNDS = 6
# spec §11.3：JSON Schema 校验失败时**最多重试一次**
MAX_SCHEMA_RETRIES = 1


class DegradeReason:
    AGENT_DISABLED = "agent_disabled"
    AGENT_UNAVAILABLE = "agent_unavailable"
    SCHEMA_INVALID = "schema_invalid"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
    EVIDENCE_INVALID = "evidence_invalid"


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    output: AgentOutput
    model_provider: str | None
    model_name: str | None
    degraded: bool
    degrade_reason: str | None = None

    @staticmethod
    def degrade(
        reason: str,
        *,
        summary_prefix: str = "",
        unknowns: list[str] | None = None,
        risk_flags: list[str] | None = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            output=template_output(
                summary_prefix=summary_prefix,
                unknowns=unknowns,
                risk_flags=risk_flags,
            ),
            model_provider=None,  # 模板摘要不是模型产出 → 不留模型身份
            model_name=None,
            degraded=True,
            degrade_reason=reason,
        )


async def run_agent(
    *,
    client: ChatClient | None,
    toolbox: AgentToolbox,
    task_prompt: str,
    summary_prefix: str = "",
    unknowns: list[str] | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> AgentRunResult:
    """跑一次分析。永不抛异常给调用方 —— 一切失败都变成可审计的降级。"""
    if client is None:
        return AgentRunResult.degrade(
            DegradeReason.AGENT_DISABLED, summary_prefix=summary_prefix, unknowns=unknowns
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task_prompt},
    ]
    specs = toolbox.specs
    schema_retries = 0

    for _round in range(max_tool_rounds):
        try:
            completion: ChatCompletion = await client.complete(messages, specs)
        except AgentUnavailable:
            return AgentRunResult.degrade(
                DegradeReason.AGENT_UNAVAILABLE, summary_prefix=summary_prefix, unknowns=unknowns
            )

        if completion.wants_tools:
            messages.append(_assistant_tool_message(completion.tool_calls))
            for call in completion.tool_calls:
                messages.append(await _execute_tool(toolbox, call))
            continue

        try:
            output = parse_agent_output(completion.content or "")
        except AgentSchemaError as exc:
            if schema_retries >= MAX_SCHEMA_RETRIES:
                logger.warning(
                    "Agent 输出两次不符合 Schema，降级模板摘要 symbol=%s reason=%s",
                    toolbox.symbol,
                    exc,
                )
                return AgentRunResult.degrade(
                    DegradeReason.SCHEMA_INVALID, summary_prefix=summary_prefix, unknowns=unknowns
                )
            schema_retries += 1
            logger.info("Agent 输出不符合 Schema，重试第 %d 次 symbol=%s", schema_retries, toolbox.symbol)
            messages.append({"role": "assistant", "content": completion.content or ""})
            messages.append({"role": "user", "content": SCHEMA_RETRY_PROMPT})
            continue

        return AgentRunResult(
            output=output,
            model_provider=PROVIDER_LABEL,
            model_name=client.model,
            degraded=False,
        )

    logger.warning("Agent 工具预算耗尽，降级模板摘要 symbol=%s", toolbox.symbol)
    return AgentRunResult.degrade(
        DegradeReason.TOOL_BUDGET_EXHAUSTED, summary_prefix=summary_prefix, unknowns=unknowns
    )


def _assistant_tool_message(tool_calls: tuple[ToolCall, ...]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
            }
            for call in tool_calls
        ],
    }


async def _execute_tool(toolbox: AgentToolbox, call: ToolCall) -> dict[str, Any]:
    """执行一次工具调用。白名单外的名字 / 越权参数 → 以 tool 错误消息回给模型，不扩权、不崩。"""
    try:
        result = await toolbox.call(call.name, call.arguments)
        content = json.dumps(result, ensure_ascii=False)
    except ToolError as exc:
        content = json.dumps({"error": str(exc)}, ensure_ascii=False)
    except Exception as exc:  # 数据库等底层异常：不泄漏细节，也不中断分析
        logger.exception("工具执行异常 name=%s symbol=%s", call.name, toolbox.symbol)
        content = json.dumps({"error": f"工具执行失败：{type(exc).__name__}"}, ensure_ascii=False)
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}
