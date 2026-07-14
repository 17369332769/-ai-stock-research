"""OpenAI 兼容的 Chat 客户端（本地 Ollama / vLLM / 任何兼容 provider 都走这条路）。

安全（spec §14.3）：

* API 密钥只从 ``Settings`` 注入（环境变量或本地 secret 文件），**不写日志、不写库**。
* 日志只记 model 名、消息条数、耗时与状态码；**不记 prompt 正文、不记模型回复正文**
  —— 文档正文是不可信外部内容，也可能含敏感信息。

Agent 未配置（``settings.agent_enabled == False``）时 ``build_chat_client`` 返回 ``None``，
调用方降级为模板摘要，**不得报错阻断其他功能**。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from apps.api.app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_TEMPERATURE = 0.0  # 证据整理器：确定性优先
PROVIDER_LABEL = "openai_compatible"


class AgentUnavailable(Exception):
    """模型不可达 / 超时 / 返回非法结构。调用方降级为模板摘要，不崩作业。"""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    """一次补全的结果：要么是文本，要么是若干工具调用。"""

    content: str | None
    tool_calls: tuple[ToolCall, ...] = field(default=())

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class ChatClient(Protocol):
    """LLM 调用的唯一出口。测试用假 client / respx 打桩，不访问公网。"""

    @property
    def model(self) -> str: ...

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatCompletion: ...


class OpenAICompatibleChatClient:
    """``POST {base_url}/chat/completions``，OpenAI 协议。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key  # 只用于构造 Authorization 头，永不进日志
        self._model = model
        self._timeout = timeout_seconds
        self._client = client

    @property
    def model(self) -> str:
        return self._model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatCompletion:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": DEFAULT_TEMPERATURE,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self._base_url}/chat/completions"
        try:
            if self._client is not None:
                response = await self._client.post(
                    url, json=payload, headers=self._headers(), timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload, headers=self._headers())
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            # 只记异常类型与 model，不记 payload（含不可信文档正文）
            logger.warning("Agent 调用失败 model=%s error=%s", self._model, type(exc).__name__)
            raise AgentUnavailable(f"Agent 调用失败：{type(exc).__name__}") from exc
        except ValueError as exc:  # JSON 解析失败
            logger.warning("Agent 返回非 JSON model=%s", self._model)
            raise AgentUnavailable("Agent 返回的不是 JSON") from exc

        logger.info("Agent 调用完成 model=%s messages=%d", self._model, len(messages))
        return _parse_completion(body)


def _parse_completion(body: Any) -> ChatCompletion:
    if not isinstance(body, dict):
        raise AgentUnavailable("Agent 响应结构非法：顶层不是对象")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AgentUnavailable("Agent 响应结构非法：缺少 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise AgentUnavailable("Agent 响应结构非法：缺少 message")

    raw_calls = message.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    if isinstance(raw_calls, list):
        for index, raw in enumerate(raw_calls):
            if not isinstance(raw, dict):
                continue
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name", ""))
            arguments = _parse_arguments(function.get("arguments"))
            tool_calls.append(
                ToolCall(id=str(raw.get("id") or f"call_{index}"), name=name, arguments=arguments)
            )

    content = message.get("content")
    return ChatCompletion(
        content=content if isinstance(content, str) else None,
        tool_calls=tuple(tool_calls),
    )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """工具参数可能是 JSON 字符串或已解析的对象；解析不了就当空参数（由工具层报参数错）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def build_chat_client(settings: Settings | None = None) -> ChatClient | None:
    """Agent 未配置 → ``None``（调用方降级模板摘要，不抛错）。"""
    settings = settings or get_settings()
    if not settings.agent_enabled:
        logger.info("Agent 未配置（agent_enabled=False），分析将使用模板摘要")
        return None
    return OpenAICompatibleChatClient(
        base_url=settings.agent_base_url,
        api_key=settings.agent_api_key,
        model=settings.agent_model,
    )
