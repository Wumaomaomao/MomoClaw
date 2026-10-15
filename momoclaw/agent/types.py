from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict

from momoclaw.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    TextContent,
    ThinkingLevel,
    ToolCall,
    ToolResultMessage,
)

AgentMessage = Message


@dataclass
class AgentToolResult:
    content: list[TextContent | ImageContent]
    details: Any = None


AgentToolUpdateCallback = Callable[[AgentToolResult], None]


class ToolExecuteFn(Protocol):
    def __call__(
        self,
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> Awaitable[AgentToolResult] | AgentToolResult: ...


@dataclass
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecuteFn
    label: str | None = None


@dataclass
class AgentContext:
    system_prompt: str
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)


@dataclass
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None


@dataclass
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any]
    context: AgentContext


ConvertToLlmFn = Callable[[list[AgentMessage]], list[Message] | Awaitable[list[Message]]]
BeforeToolCallFn = Callable[
    [BeforeToolCallContext, Any | None],
    BeforeToolCallResult | None | Awaitable[BeforeToolCallResult | None],
]
GetApiKeyFn = Callable[[str], str | None | Awaitable[str | None]]
StreamFn = Callable[[Model, Context, Any | None], Any | Awaitable[Any]]


@dataclass
class AgentLoopConfig:
    model: Model
    convert_to_llm: ConvertToLlmFn
    get_api_key: GetApiKeyFn | None = None
    before_tool_call: BeforeToolCallFn | None = None
    reasoning: ThinkingLevel | None = None
    session_id: str | None = None
    max_output_tokens: int | None = None


class AgentEvent(TypedDict, total=False):
    type: str
    message: AgentMessage
    messages: list[AgentMessage]
    assistantMessageEvent: dict[str, Any]
    toolCallId: str
    toolName: str
    args: dict[str, Any]
    result: AgentToolResult
    isError: bool
    error: str


AgentEventSink = Callable[[AgentEvent], None | Awaitable[None]]
