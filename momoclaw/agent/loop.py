from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any

from momoclaw.ai.stream import stream_simple
from momoclaw.ai.types import (
    AssistantMessage,
    Context,
    Message,
    SimpleStreamOptions,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
)

from .types import (
    AgentContext,
    AgentEvent,
    AgentEventSink,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
)

StreamFn = Callable[[Any, Context, SimpleStreamOptions | None], Any | Awaitable[Any]]


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _emit(emit: AgentEventSink, event: AgentEvent) -> None:
    await _maybe_await(emit(event))


def _default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    return list(messages)


def _to_ai_tools(tools: list[AgentTool]) -> list[Tool]:
    return [Tool(name=t.name, description=t.description, parameters=t.parameters) for t in tools]


def _error_tool_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=message)])


def _validate_tool_args(tool: AgentTool, args: dict[str, Any]) -> str | None:
    schema = tool.parameters or {}
    required = schema.get("required", [])
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in args:
                return f"Missing required argument: {name}"

    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for name, spec in properties.items():
            if name not in args or not isinstance(spec, dict):
                continue
            expected = spec.get("type")
            if expected == "string" and not isinstance(args[name], str):
                return f"Argument {name} must be string"
            if expected == "number" and not isinstance(args[name], int | float):
                return f"Argument {name} must be number"
            if expected == "integer" and not isinstance(args[name], int):
                return f"Argument {name} must be integer"
            if expected == "boolean" and not isinstance(args[name], bool):
                return f"Argument {name} must be boolean"
            if expected == "object" and not isinstance(args[name], dict):
                return f"Argument {name} must be object"
            if expected == "array" and not isinstance(args[name], list):
                return f"Argument {name} must be array"
    return None


async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=list(context.tools),
    )

    await _emit(emit, {"type": "agent_start"})
    await _emit(emit, {"type": "turn_start"})
    for prompt in prompts:
        await _emit(emit, {"type": "message_start", "message": prompt})
        await _emit(emit, {"type": "message_end", "message": prompt})

    while True:
        assistant = await _stream_assistant_response(current_context, config, emit, signal, stream_fn)
        new_messages.append(assistant)

        if assistant.stop_reason in {"error", "aborted"}:
            await _emit(emit, {"type": "turn_end", "message": assistant})
            await _emit(emit, {"type": "agent_end", "messages": new_messages})
            return new_messages

        tool_calls = [block for block in assistant.content if isinstance(block, ToolCall)]
        if not tool_calls:
            await _emit(emit, {"type": "turn_end", "message": assistant})
            await _emit(emit, {"type": "agent_end", "messages": new_messages})
            return new_messages

        tool_results = await _execute_tool_calls(current_context, assistant, tool_calls, config, emit, signal)
        for result in tool_results:
            current_context.messages.append(result)
            new_messages.append(result)
        await _emit(emit, {"type": "turn_end", "message": assistant})
        await _emit(emit, {"type": "turn_start"})


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if isinstance(context.messages[-1], AssistantMessage):
        raise ValueError("Cannot continue from message role: assistant")

    await _emit(emit, {"type": "agent_start"})
    await _emit(emit, {"type": "turn_start"})
    current_context = AgentContext(context.system_prompt, list(context.messages), list(context.tools))
    new_messages: list[AgentMessage] = []

    while True:
        assistant = await _stream_assistant_response(current_context, config, emit, signal, stream_fn)
        new_messages.append(assistant)
        if assistant.stop_reason in {"error", "aborted"}:
            await _emit(emit, {"type": "turn_end", "message": assistant})
            await _emit(emit, {"type": "agent_end", "messages": new_messages})
            return new_messages

        tool_calls = [block for block in assistant.content if isinstance(block, ToolCall)]
        if not tool_calls:
            await _emit(emit, {"type": "turn_end", "message": assistant})
            await _emit(emit, {"type": "agent_end", "messages": new_messages})
            return new_messages

        tool_results = await _execute_tool_calls(current_context, assistant, tool_calls, config, emit, signal)
        for result in tool_results:
            current_context.messages.append(result)
            new_messages.append(result)
        await _emit(emit, {"type": "turn_end", "message": assistant})
        await _emit(emit, {"type": "turn_start"})


async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Any | None,
    stream_fn: StreamFn | None,
) -> AssistantMessage:
    del signal
    llm_messages = await _maybe_await(config.convert_to_llm(context.messages))
    llm_context = Context(system_prompt=context.system_prompt, messages=llm_messages, tools=_to_ai_tools(context.tools))
    api_key = await _maybe_await(config.get_api_key(config.model.provider)) if config.get_api_key else None
    options = SimpleStreamOptions(api_key=api_key, reasoning=config.reasoning, session_id=config.session_id)

    fn = stream_fn or stream_simple
    response_stream = await _maybe_await(fn(config.model, llm_context, options))

    partial: AssistantMessage | None = None
    added_partial = False
    async for event in response_stream:
        event_type = event.get("type")
        if event_type == "start":
            partial = event["partial"]
            context.messages.append(partial)
            added_partial = True
            await _emit(emit, {"type": "message_start", "message": partial})
        elif event_type in {
            "text_start",
            "text_delta",
            "text_end",
            "thinking_start",
            "thinking_delta",
            "thinking_end",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_end",
        }:
            if partial is not None:
                partial = event["partial"]
                context.messages[-1] = partial
                await _emit(
                    emit,
                    {"type": "message_update", "message": partial, "assistantMessageEvent": event},
                )
        elif event_type in {"done", "error"}:
            final_message = await response_stream.result()
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
                await _emit(emit, {"type": "message_start", "message": final_message})
            await _emit(emit, {"type": "message_end", "message": final_message})
            return final_message

    final_message = await response_stream.result()
    if added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await _emit(emit, {"type": "message_start", "message": final_message})
    await _emit(emit, {"type": "message_end", "message": final_message})
    return final_message


async def _execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[ToolCall],
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Any | None,
) -> list[ToolResultMessage]:
    results: list[ToolResultMessage] = []
    for tool_call in tool_calls:
        await _emit(
            emit,
            {
                "type": "tool_execution_start",
                "toolCallId": tool_call.id,
                "toolName": tool_call.name,
                "args": tool_call.arguments,
            },
        )
        tool = next((candidate for candidate in current_context.tools if candidate.name == tool_call.name), None)
        if tool is None:
            result = _error_tool_result(f"Tool not found: {tool_call.name}")
            results.append(await _finish_tool_call(tool_call, result, True, emit))
            continue

        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        validation_error = _validate_tool_args(tool, args)
        if validation_error:
            result = _error_tool_result(validation_error)
            results.append(await _finish_tool_call(tool_call, result, True, emit))
            continue

        if config.before_tool_call:
            before = await _maybe_await(
                config.before_tool_call(
                    BeforeToolCallContext(
                        assistant_message=assistant_message,
                        tool_call=tool_call,
                        args=args,
                        context=current_context,
                    ),
                    signal,
                )
            )
            if before and before.block:
                result = _error_tool_result(before.reason or "Tool execution was blocked")
                results.append(await _finish_tool_call(tool_call, result, True, emit))
                continue

        try:
            updates: list[AgentToolResult] = []

            def _on_update(update: AgentToolResult) -> None:
                updates.append(update)

            result = await _maybe_await(tool.execute(tool_call.id, args, signal, _on_update))
            for update in updates:
                await _emit(
                    emit,
                    {
                        "type": "tool_execution_update",
                        "toolCallId": tool_call.id,
                        "toolName": tool_call.name,
                        "args": args,
                        "result": update,
                    },
                )
            results.append(await _finish_tool_call(tool_call, result, False, emit))
        except Exception as exc:
            results.append(await _finish_tool_call(tool_call, _error_tool_result(str(exc)), True, emit))

    return results


async def _finish_tool_call(
    tool_call: ToolCall,
    result: AgentToolResult,
    is_error: bool,
    emit: AgentEventSink,
) -> ToolResultMessage:
    await _emit(
        emit,
        {
            "type": "tool_execution_end",
            "toolCallId": tool_call.id,
            "toolName": tool_call.name,
            "result": result,
            "isError": is_error,
        },
    )
    message = ToolResultMessage(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=result.content,
        is_error=is_error,
        details=result.details,
        timestamp=_now_ms(),
    )
    await _emit(emit, {"type": "message_start", "message": message})
    await _emit(emit, {"type": "message_end", "message": message})
    return message

