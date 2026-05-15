from __future__ import annotations

from collections.abc import Callable

import pytest

from momoclaw.agent import Agent, AgentOptions, AgentTool, AgentToolResult
from momoclaw.ai.event_stream import AssistantMessageEventStream
from momoclaw.ai.types import AssistantMessage, Context, Model, SimpleStreamOptions, TextContent, ToolCall, ToolResultMessage


def _model() -> Model:
    return Model(id="fake", name="Fake", api="fake-api", provider="fake", base_url="http://fake")


def _fake_stream_factory(messages: list[AssistantMessage]) -> Callable:
    calls: list[Context] = []

    def _stream(model: Model, context: Context, options: SimpleStreamOptions | None = None) -> AssistantMessageEventStream:
        del model, options
        calls.append(context)
        message = messages[len(calls) - 1]
        stream = AssistantMessageEventStream()
        stream.push({"type": "start", "partial": message})
        for index, block in enumerate(message.content):
            if isinstance(block, TextContent):
                stream.push({"type": "text_start", "contentIndex": index, "partial": message})
                stream.push({"type": "text_delta", "contentIndex": index, "delta": block.text, "partial": message})
                stream.push({"type": "text_end", "contentIndex": index, "content": block.text, "partial": message})
            elif isinstance(block, ToolCall):
                stream.push({"type": "toolcall_start", "contentIndex": index, "partial": message})
                stream.push({"type": "toolcall_end", "contentIndex": index, "toolCall": block, "partial": message})
        stream.push({"type": "done", "reason": message.stop_reason, "message": message})
        stream.end(message)
        return stream

    _stream.calls = calls  # type: ignore[attr-defined]
    return _stream


@pytest.mark.asyncio
async def test_agent_prompt_text_response() -> None:
    assistant = AssistantMessage(content=[TextContent(text="hello")], api="fake-api", provider="fake", model="fake")
    stream_fn = _fake_stream_factory([assistant])
    agent = Agent(AgentOptions(model=_model(), stream_fn=stream_fn))
    events: list[dict] = []
    agent.subscribe(events.append)

    new_messages = await agent.prompt("hi")

    assert len(new_messages) == 2
    assert isinstance(new_messages[-1], AssistantMessage)
    assert new_messages[-1].content == [TextContent(text="hello")]
    assert [event["type"] for event in events].count("message_update") == 3
    assert len(agent.state.messages) == 2


@pytest.mark.asyncio
async def test_agent_executes_tool_and_continues() -> None:
    first = AssistantMessage(
        content=[ToolCall(id="call_1", name="echo", arguments={"text": "hello"})],
        api="fake-api",
        provider="fake",
        model="fake",
        stop_reason="toolUse",
    )
    second = AssistantMessage(content=[TextContent(text="done")], api="fake-api", provider="fake", model="fake")
    stream_fn = _fake_stream_factory([first, second])

    def execute(tool_call_id: str, params: dict, signal=None, on_update=None) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(content=[TextContent(text=params["text"])])

    tool = AgentTool(
        name="echo",
        description="Echo text",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=execute,
    )
    agent = Agent(AgentOptions(model=_model(), tools=[tool], stream_fn=stream_fn))
    events: list[dict] = []
    agent.subscribe(events.append)

    new_messages = await agent.prompt("use tool")

    assert len(new_messages) == 4
    assert isinstance(new_messages[2], ToolResultMessage)
    assert new_messages[2].content == [TextContent(text="hello")]
    assert isinstance(new_messages[-1], AssistantMessage)
    assert new_messages[-1].content == [TextContent(text="done")]
    assert any(event["type"] == "tool_execution_start" for event in events)
    assert len(stream_fn.calls) == 2  # type: ignore[attr-defined]
    second_context = stream_fn.calls[1]  # type: ignore[attr-defined]
    assert isinstance(second_context.messages[-1], ToolResultMessage)


@pytest.mark.asyncio
async def test_agent_returns_error_tool_result_for_invalid_args() -> None:
    first = AssistantMessage(
        content=[ToolCall(id="call_1", name="echo", arguments={})],
        api="fake-api",
        provider="fake",
        model="fake",
        stop_reason="toolUse",
    )
    second = AssistantMessage(content=[TextContent(text="recovered")], api="fake-api", provider="fake", model="fake")
    stream_fn = _fake_stream_factory([first, second])

    def execute(tool_call_id: str, params: dict, signal=None, on_update=None) -> AgentToolResult:
        raise AssertionError("tool should not run with invalid args")

    tool = AgentTool(
        name="echo",
        description="Echo text",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        execute=execute,
    )
    agent = Agent(AgentOptions(model=_model(), tools=[tool], stream_fn=stream_fn))

    new_messages = await agent.prompt("use bad tool")

    assert isinstance(new_messages[2], ToolResultMessage)
    assert new_messages[2].is_error is True
    assert "Missing required argument" in new_messages[2].content[0].text
