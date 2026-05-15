from __future__ import annotations

import pytest

from momoclaw.ai.models import get_model
from momoclaw.ai.env import get_env_api_key
from momoclaw.ai.providers._common import to_anthropic_messages, to_anthropic_tools, to_openai_messages, to_openai_tools
from momoclaw.ai.providers.openai_provider import _apply_openai_compat_options
from momoclaw.ai.registry import get_api_provider, get_api_providers
from momoclaw.ai.types import (
    AssistantMessage,
    Context,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def test_builtin_providers_are_registered() -> None:
    apis = {provider.api for provider in get_api_providers()}
    assert {"openai-completions", "anthropic-messages"} <= apis


def test_deepseek_models_are_openai_compatible() -> None:
    flash = get_model("deepseek", "deepseek-v4-flash")
    pro = get_model("deepseek", "deepseek-v4-pro")

    assert flash.api == "openai-completions"
    assert flash.provider == "deepseek"
    assert flash.base_url == "https://api.deepseek.com"
    assert flash.context_window == 1_000_000
    assert pro.max_tokens == 393_216
    assert pro.compat and pro.compat["thinking_format"] == "deepseek"


def test_deepseek_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")

    assert get_env_api_key("deepseek") == "ds-test"


def test_registry_rejects_mismatched_model_api() -> None:
    provider = get_api_provider("openai-completions")
    assert provider is not None

    model = get_model("anthropic", "claude-sonnet-4-5")
    with pytest.raises(ValueError, match="Mismatched api"):
        provider.stream(model, Context(messages=[]))


def test_openai_message_and_tool_conversion() -> None:
    context = Context(
        system_prompt="system",
        messages=[
            UserMessage(content="hello"),
            AssistantMessage(content=[ToolCall(id="call_1", name="read", arguments={"path": "README.md"})]),
            ToolResultMessage(tool_call_id="call_1", tool_name="read", content=[TextContent(text="ok")]),
        ],
        tools=[Tool(name="read", description="Read file", parameters={"type": "object"})],
    )

    messages = to_openai_messages(context)
    tools = to_openai_tools(context.tools)

    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[2]["tool_calls"][0]["function"]["name"] == "read"
    assert messages[3]["role"] == "tool"
    assert tools and tools[0]["function"]["name"] == "read"


def test_deepseek_openai_message_includes_reasoning_content() -> None:
    model = get_model("deepseek", "deepseek-v4-pro")
    context = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent(thinking="reasoning"),
                    TextContent(text="answer"),
                    ToolCall(id="call_1", name="read", arguments={}),
                ]
            )
        ]
    )

    messages = to_openai_messages(context, model)

    assert messages[0]["reasoning_content"] == "reasoning"
    assert messages[0]["content"] == "answer"


def test_deepseek_openai_payload_thinking_options() -> None:
    model = get_model("deepseek", "deepseek-v4-pro")
    payload: dict = {"temperature": 0.5}

    _apply_openai_compat_options(payload, model, SimpleStreamOptions(reasoning="xhigh"))

    assert payload["reasoning_effort"] == "max"
    assert payload["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in payload

    payload = {}
    _apply_openai_compat_options(payload, model, SimpleStreamOptions())
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}


def test_anthropic_message_and_tool_conversion() -> None:
    context = Context(
        messages=[
            UserMessage(content="hello"),
            AssistantMessage(content=[TextContent(text="hi"), ToolCall(id="toolu_1", name="read", arguments={})]),
            ToolResultMessage(tool_call_id="toolu_1", tool_name="read", content=[TextContent(text="ok")]),
        ],
        tools=[Tool(name="read", description="Read file", parameters={"type": "object"})],
    )

    messages = to_anthropic_messages(context)
    tools = to_anthropic_tools(context.tools)

    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1]["content"][1]["type"] == "tool_use"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert tools and tools[0]["input_schema"] == {"type": "object"}
