from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Api = str
Provider = str
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]
ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]


@dataclass
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: Cost = field(default_factory=Cost)


@dataclass
class TextContent:
    type: Literal["text"] = "text"
    text: str = ""
    text_signature: str | None = None


@dataclass
class ThinkingContent:
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    thinking_signature: str | None = None
    redacted: bool = False


@dataclass
class ImageContent:
    type: Literal["image"] = "image"
    data: str = ""
    mime_type: str = "image/png"


@dataclass
class ToolCall:
    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


AssistantBlock = TextContent | ThinkingContent | ToolCall
UserBlock = TextContent | ImageContent
ToolResultBlock = TextContent | ImageContent


@dataclass
class UserMessage:
    role: Literal["user"] = "user"
    content: str | list[UserBlock] = ""
    timestamp: int = 0


@dataclass
class AssistantMessage:
    role: Literal["assistant"] = "assistant"
    content: list[AssistantBlock] = field(default_factory=list)
    api: Api = ""
    provider: Provider = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = "stop"
    response_id: str | None = None
    response_model: str | None = None
    error_message: str | None = None
    timestamp: int = 0


@dataclass
class ToolResultMessage:
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = ""
    tool_name: str = ""
    content: list[ToolResultBlock] = field(default_factory=list)
    is_error: bool = False
    details: Any = None
    timestamp: int = 0


Message = UserMessage | AssistantMessage | ToolResultMessage


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Context:
    messages: list[Message]
    system_prompt: str | None = None
    tools: list[Tool] | None = None


OnPayload = Callable[[dict[str, Any], "Model"], dict[str, Any] | None | Awaitable[dict[str, Any] | None]]
OnResponse = Callable[[dict[str, Any], "Model"], None | Awaitable[None]]


@dataclass
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    max_retry_delay_seconds: float | None = None
    session_id: str | None = None
    on_payload: OnPayload | None = None
    on_response: OnResponse | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class SimpleStreamOptions(StreamOptions):
    reasoning: ThinkingLevel | None = None


@dataclass
class Model:
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool = False
    input: list[Literal["text", "image"]] = field(default_factory=lambda: ["text"])
    context_window: int = 128_000
    max_tokens: int = 4096
    cost: Cost = field(default_factory=Cost)
    headers: dict[str, str] | None = None
    compat: dict[str, Any] | None = None
    thinking_level_map: dict[str, str | None] | None = None

