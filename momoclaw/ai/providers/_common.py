from __future__ import annotations

import inspect
import json
import time
from typing import Any

from ..types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def now_ms() -> int:
    return int(time.time() * 1000)


def get_value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def apply_payload_hook(payload: dict[str, Any], model: Model, hook: Any) -> dict[str, Any]:
    if hook is None:
        return payload
    next_payload = await maybe_await(hook(payload, model))
    return payload if next_payload is None else next_payload


def parse_partial_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def empty_assistant_message(api: str, provider: str, model: str) -> AssistantMessage:
    return AssistantMessage(api=api, provider=provider, model=model, usage=Usage(), timestamp=now_ms())


def to_openai_messages(context: Context, model: Model | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if context.system_prompt:
        out.append({"role": "system", "content": context.system_prompt})
    for msg in context.messages:
        if isinstance(msg, UserMessage):
            out.append({"role": "user", "content": _to_openai_user_content(msg.content)})
        elif isinstance(msg, AssistantMessage):
            out.append(_to_openai_assistant_message(msg, model))
        elif isinstance(msg, ToolResultMessage):
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": _join_text(msg),
                }
            )
    return out


def _to_openai_user_content(content: str | list[TextContent | ImageContent]) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, TextContent):
            parts.append({"type": "text", "text": part.text})
        elif isinstance(part, ImageContent):
            parts.append({"type": "image_url", "image_url": {"url": f"data:{part.mime_type};base64,{part.data}"}})
    return parts


def _to_openai_assistant_message(msg: AssistantMessage, model: Model | None = None) -> dict[str, Any]:
    text = "".join(block.text for block in msg.content if isinstance(block, TextContent))
    thinking = "".join(block.thinking for block in msg.content if isinstance(block, ThinkingContent))
    tool_calls = [block for block in msg.content if isinstance(block, ToolCall)]
    payload: dict[str, Any] = {"role": "assistant", "content": text or None}
    if thinking and model is not None and model.compat and model.compat.get("assistant_reasoning_content"):
        payload["reasoning_content"] = thinking
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
            }
            for call in tool_calls
        ]
    return payload


def to_openai_tools(tools: list[Tool] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
        }
        for tool in tools
    ]


def to_anthropic_messages(context: Context) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in context.messages:
        if isinstance(msg, UserMessage):
            out.append({"role": "user", "content": _to_anthropic_user_content(msg.content)})
        elif isinstance(msg, AssistantMessage):
            parts: list[dict[str, Any]] = []
            for block in msg.content:
                if isinstance(block, TextContent):
                    parts.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolCall):
                    parts.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments})
            if parts:
                out.append({"role": "assistant", "content": parts})
        elif isinstance(msg, ToolResultMessage):
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": _join_text(msg),
                            "is_error": msg.is_error,
                        }
                    ],
                }
            )
    return out


def _to_anthropic_user_content(content: str | list[TextContent | ImageContent]) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, TextContent):
            parts.append({"type": "text", "text": part.text})
        elif isinstance(part, ImageContent):
            parts.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": part.mime_type, "data": part.data},
                }
            )
    return parts or "(empty)"


def to_anthropic_tools(tools: list[Tool] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [{"name": tool.name, "description": tool.description, "input_schema": tool.parameters} for tool in tools]


def _join_text(msg: ToolResultMessage) -> str:
    return "\n".join(part.text for part in msg.content if isinstance(part, TextContent))
