from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from momoclaw.ai.types import AssistantMessage, Model, UserMessage

from .loop import _default_convert_to_llm, run_agent_loop, run_agent_loop_continue
from .types import (
    AgentContext,
    AgentEvent,
    AgentEventSink,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    BeforeToolCallFn,
    ConvertToLlmFn,
    GetApiKeyFn,
    StreamFn,
)


@dataclass
class AgentOptions:
    model: Model
    system_prompt: str = ""
    tools: list[AgentTool] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    convert_to_llm: ConvertToLlmFn = _default_convert_to_llm
    get_api_key: GetApiKeyFn | None = None
    before_tool_call: BeforeToolCallFn | None = None
    reasoning: str | None = None
    session_id: str | None = None
    stream_fn: StreamFn | None = None


@dataclass
class AgentState:
    model: Model
    system_prompt: str = ""
    tools: list[AgentTool] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    is_streaming: bool = False
    error: str | None = None


class Agent:
    def __init__(self, options: AgentOptions) -> None:
        self.state = AgentState(
            model=options.model,
            system_prompt=options.system_prompt,
            tools=list(options.tools),
            messages=list(options.messages),
        )
        self._convert_to_llm = options.convert_to_llm
        self._get_api_key = options.get_api_key
        self._before_tool_call = options.before_tool_call
        self._reasoning = options.reasoning
        self._session_id = options.session_id
        self._stream_fn = options.stream_fn
        self._listeners: list[AgentEventSink] = []

    def subscribe(self, listener: AgentEventSink) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    def set_messages(self, messages: list[AgentMessage]) -> None:
        self.state.messages = list(messages)

    async def prompt(self, text_or_message: str | UserMessage) -> list[AgentMessage]:
        message = text_or_message if isinstance(text_or_message, UserMessage) else UserMessage(content=text_or_message)
        return await self.prompt_messages([message])

    async def prompt_messages(self, prompts: list[AgentMessage]) -> list[AgentMessage]:
        config = self._build_config()
        context = AgentContext(self.state.system_prompt, list(self.state.messages), list(self.state.tools))
        self.state.is_streaming = True
        self.state.error = None
        try:
            new_messages = await run_agent_loop(
                prompts,
                context,
                config,
                self._emit,
                stream_fn=self._stream_fn,
            )
            self.state.messages.extend(new_messages)
            self._capture_error(new_messages)
            return new_messages
        finally:
            self.state.is_streaming = False

    async def continue_run(self) -> list[AgentMessage]:
        config = self._build_config()
        context = AgentContext(self.state.system_prompt, list(self.state.messages), list(self.state.tools))
        self.state.is_streaming = True
        self.state.error = None
        try:
            new_messages = await run_agent_loop_continue(
                context,
                config,
                self._emit,
                stream_fn=self._stream_fn,
            )
            self.state.messages.extend(new_messages)
            self._capture_error(new_messages)
            return new_messages
        finally:
            self.state.is_streaming = False

    def _build_config(self) -> AgentLoopConfig:
        return AgentLoopConfig(
            model=self.state.model,
            convert_to_llm=self._convert_to_llm,
            get_api_key=self._get_api_key,
            before_tool_call=self._before_tool_call,
            reasoning=self._reasoning,  # type: ignore[arg-type]
            session_id=self._session_id,
        )

    async def _emit(self, event: AgentEvent) -> None:
        for listener in list(self._listeners):
            result = listener(event)
            if hasattr(result, "__await__"):
                await result  # type: ignore[misc]

    def _capture_error(self, messages: list[AgentMessage]) -> None:
        for message in reversed(messages):
            if isinstance(message, AssistantMessage) and message.stop_reason == "error":
                self.state.error = message.error_message
                return

