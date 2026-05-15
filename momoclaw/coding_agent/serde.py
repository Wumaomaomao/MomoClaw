from __future__ import annotations

from typing import Any

from momoclaw.ai.types import (
    AssistantBlock,
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserBlock,
    UserMessage,
    Usage,
    Cost,
)


def content_to_dict(block: AssistantBlock | UserBlock) -> dict[str, Any]:
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text, "text_signature": block.text_signature}
    if isinstance(block, ThinkingContent):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "thinking_signature": block.thinking_signature,
            "redacted": block.redacted,
        }
    if isinstance(block, ImageContent):
        return {"type": "image", "data": block.data, "mime_type": block.mime_type}
    if isinstance(block, ToolCall):
        return {"type": "toolCall", "id": block.id, "name": block.name, "arguments": block.arguments}
    raise TypeError(f"Unsupported content block: {type(block)!r}")


def content_from_dict(raw: dict[str, Any]) -> TextContent | ThinkingContent | ImageContent | ToolCall:
    block_type = raw.get("type")
    if block_type == "text":
        return TextContent(text=str(raw.get("text", "")), text_signature=raw.get("text_signature"))
    if block_type == "thinking":
        return ThinkingContent(
            thinking=str(raw.get("thinking", "")),
            thinking_signature=raw.get("thinking_signature"),
            redacted=bool(raw.get("redacted", False)),
        )
    if block_type == "image":
        return ImageContent(data=str(raw.get("data", "")), mime_type=str(raw.get("mime_type", "image/png")))
    if block_type == "toolCall":
        args = raw.get("arguments", {})
        return ToolCall(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            arguments=args if isinstance(args, dict) else {},
        )
    raise ValueError(f"Unknown content block type: {block_type}")


def message_to_dict(message: Message) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        content: Any = message.content
        if isinstance(content, list):
            content = [content_to_dict(block) for block in content]
        return {"role": "user", "content": content, "timestamp": message.timestamp}
    if isinstance(message, AssistantMessage):
        return {
            "role": "assistant",
            "content": [content_to_dict(block) for block in message.content],
            "api": message.api,
            "provider": message.provider,
            "model": message.model,
            "usage": usage_to_dict(message.usage),
            "stop_reason": message.stop_reason,
            "response_id": message.response_id,
            "response_model": message.response_model,
            "error_message": message.error_message,
            "timestamp": message.timestamp,
        }
    if isinstance(message, ToolResultMessage):
        return {
            "role": "toolResult",
            "tool_call_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "content": [content_to_dict(block) for block in message.content],
            "is_error": message.is_error,
            "details": message.details,
            "timestamp": message.timestamp,
        }
    raise TypeError(f"Unsupported message: {type(message)!r}")


def message_from_dict(raw: dict[str, Any]) -> Message:
    role = raw.get("role")
    if role == "user":
        content = raw.get("content", "")
        if isinstance(content, list):
            content = [content_from_dict(block) for block in content if isinstance(block, dict)]
        return UserMessage(content=content, timestamp=int(raw.get("timestamp", 0)))
    if role == "assistant":
        return AssistantMessage(
            content=[content_from_dict(block) for block in raw.get("content", []) if isinstance(block, dict)],
            api=str(raw.get("api", "")),
            provider=str(raw.get("provider", "")),
            model=str(raw.get("model", "")),
            usage=usage_from_dict(raw.get("usage", {})),
            stop_reason=raw.get("stop_reason", "stop"),
            response_id=raw.get("response_id"),
            response_model=raw.get("response_model"),
            error_message=raw.get("error_message"),
            timestamp=int(raw.get("timestamp", 0)),
        )
    if role == "toolResult":
        return ToolResultMessage(
            tool_call_id=str(raw.get("tool_call_id", "")),
            tool_name=str(raw.get("tool_name", "")),
            content=[content_from_dict(block) for block in raw.get("content", []) if isinstance(block, dict)],
            is_error=bool(raw.get("is_error", False)),
            details=raw.get("details"),
            timestamp=int(raw.get("timestamp", 0)),
        )
    raise ValueError(f"Unknown message role: {role}")


def usage_to_dict(usage: Usage) -> dict[str, Any]:
    return {
        "input": usage.input,
        "output": usage.output,
        "cache_read": usage.cache_read,
        "cache_write": usage.cache_write,
        "total_tokens": usage.total_tokens,
        "cost": {
            "input": usage.cost.input,
            "output": usage.cost.output,
            "cache_read": usage.cost.cache_read,
            "cache_write": usage.cost.cache_write,
            "total": usage.cost.total,
        },
    }


def usage_from_dict(raw: Any) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    cost_raw = raw.get("cost", {})
    cost = Cost()
    if isinstance(cost_raw, dict):
        cost = Cost(
            input=float(cost_raw.get("input", 0.0)),
            output=float(cost_raw.get("output", 0.0)),
            cache_read=float(cost_raw.get("cache_read", 0.0)),
            cache_write=float(cost_raw.get("cache_write", 0.0)),
            total=float(cost_raw.get("total", 0.0)),
        )
    return Usage(
        input=int(raw.get("input", 0)),
        output=int(raw.get("output", 0)),
        cache_read=int(raw.get("cache_read", 0)),
        cache_write=int(raw.get("cache_write", 0)),
        total_tokens=int(raw.get("total_tokens", 0)),
        cost=cost,
    )

