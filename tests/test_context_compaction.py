from __future__ import annotations

import json
from pathlib import Path

import pytest

from momoclaw.ai.types import AssistantMessage, Model, TextContent, ToolCall, ToolResultMessage, UserMessage
from momoclaw.agent import AgentTool
from momoclaw.coding_agent.context import ContextBudgetError, ContextCompactionConfig, ContextManager
from momoclaw.coding_agent.session_store import SessionStore
from momoclaw.coding_agent.serde import message_to_dict


def _model(*, context_window: int = 4096, max_tokens: int = 512) -> Model:
    return Model(
        id="fake", name="Fake", api="fake-api", provider="fake", base_url="http://fake",
        context_window=context_window, max_tokens=max_tokens,
    )


def _store(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path, "session_context_test")
    store.ensure_initialized(model_id="fake", provider="fake", system_prompt="fixed prompt")
    return store


def _tool() -> AgentTool:
    return AgentTool(name="read", description="Read a file", parameters={}, execute=lambda *_: None)


def _text(messages) -> str:
    chunks = []
    for message in messages:
        if isinstance(message, UserMessage) and isinstance(message.content, str):
            chunks.append(message.content)
        elif hasattr(message, "content") and isinstance(message.content, list):
            chunks.extend(block.text for block in message.content if isinstance(block, TextContent))
    return "\n".join(chunks)


def test_system_prompt_is_stable_and_dynamic_context_is_in_request_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manager = ContextManager(
        model=_model(), system_prompt="fixed prompt", tools=[_tool()], store=store,
        config=ContextCompactionConfig(enabled=True),
    )
    manager.set_dynamic_context("Retrieved workspace memory: use auth.py")
    raw = [UserMessage(content="Where is auth?")]

    view = manager.convert_to_llm(raw)

    assert manager.system_prompt == "fixed prompt"
    assert manager.last_metrics["system_prompt_sha256"]
    assert "Retrieved workspace memory" in _text(view)
    assert 'untrusted_data="true"' in _text(view)
    assert raw[0].content == "Where is auth?"


def test_compaction_keeps_recent_turns_and_tool_protocol_intact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manager = ContextManager(
        model=_model(), system_prompt="fixed prompt", tools=[_tool()], store=store,
        config=ContextCompactionConfig(enabled=True, recent_user_turns=2, max_tool_chars=80),
    )
    messages = [
        UserMessage(content="old request"),
        AssistantMessage(content=[ToolCall(id="call-old", name="read", arguments={})], stop_reason="toolUse"),
        ToolResultMessage(tool_call_id="call-old", tool_name="read", content=[TextContent(text="old result")]),
        AssistantMessage(content=[TextContent(text="old answer")]),
        UserMessage(content="second old request"),
        AssistantMessage(content=[TextContent(text="second old answer")]),
        UserMessage(content="recent request one"),
        AssistantMessage(content=[TextContent(text="recent answer one")]),
        UserMessage(content="recent request two"),
        AssistantMessage(content=[TextContent(text="recent answer two")]),
    ]
    original = [message_to_dict(message) for message in messages]
    manager.request_compaction()

    view = manager.convert_to_llm(messages)

    assert manager.last_metrics["summarized_messages"] == 6
    assert "old request" in _text(view)
    assert not any(isinstance(message, UserMessage) and message.content == "old request" for message in view)
    assert any(isinstance(message, UserMessage) and "recent request one" in _text([message]) for message in view)
    assert any(isinstance(message, UserMessage) and "recent request two" in _text([message]) for message in view)
    assert not any(
        isinstance(message, AssistantMessage)
        and any(isinstance(block, ToolCall) and block.id == "call-old" for block in message.content)
        for message in view
    )
    assert [message_to_dict(message) for message in messages] == original
    checkpoint = store.latest_context_checkpoint()
    assert checkpoint["covered_until_sequence"] == 6
    restored = ContextManager(model=_model(), system_prompt="fixed prompt", tools=[_tool()], store=store, config=ContextCompactionConfig(enabled=True, recent_user_turns=2, max_tool_chars=80))
    assert restored.restore_checkpoint(messages) is True
    assert restored.last_metrics["restored_checkpoint"] == checkpoint["checkpoint_id"]


def test_old_constraint_candidate_is_kept_verbatim_even_over_summary_target(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manager = ContextManager(
        model=_model(), system_prompt="fixed prompt", tools=[], store=store,
        config=ContextCompactionConfig(enabled=True, recent_user_turns=1, summary_max_tokens=8),
    )
    constraint = "必须保留这个用户约束：" + "保留原始行为。" * 100
    messages = [
        UserMessage(content=constraint),
        AssistantMessage(content=[TextContent(text="acknowledged")]),
        UserMessage(content="current task"),
    ]
    manager.request_compaction()

    view = manager.convert_to_llm(messages)

    assert constraint in _text(view)

def test_tool_output_is_pruned_only_in_request_view_and_keeps_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manager = ContextManager(
        model=_model(context_window=2048, max_tokens=256), system_prompt="fixed prompt", tools=[_tool()], store=store,
        config=ContextCompactionConfig(enabled=False),
    )
    tool_result = ToolResultMessage(
        tool_call_id="call_1", tool_name="read", content=[TextContent(text="A" * 9000)],
    )
    raw = [
        UserMessage(content="inspect"),
        AssistantMessage(content=[ToolCall(id="call_1", name="read", arguments={})], stop_reason="toolUse"),
        tool_result,
    ]

    view = manager.convert_to_llm(raw)

    result = next(message for message in view if isinstance(message, ToolResultMessage))
    assert result.tool_call_id == "call_1"
    assert len(result.content[0].text) < 9000
    assert len(tool_result.content[0].text) == 9000
    assert manager.last_metrics["pruned_tool_results"] > 0


def test_protected_context_overflow_returns_clear_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manager = ContextManager(
        model=_model(context_window=512, max_tokens=256), system_prompt="fixed prompt", tools=[], store=store,
        config=ContextCompactionConfig(enabled=True, safety_margin_tokens=128),
    )

    with pytest.raises(ContextBudgetError, match="above the input budget"):
        manager.convert_to_llm([UserMessage(content="x" * 2000)])


def test_actual_provider_usage_is_compared_with_estimate(tmp_path: Path) -> None:
    from momoclaw.ai.types import Usage

    manager = ContextManager(model=_model(), system_prompt="fixed prompt", tools=[], store=_store(tmp_path))
    manager.convert_to_llm([UserMessage(content="hello")])
    manager.record_usage(AssistantMessage(usage=Usage(input=80, cache_read=10, cache_write=3)))

    assert manager.last_metrics["actual_input_tokens"] == 80
    assert manager.last_metrics["estimate_ratio"] > 0
    assert manager.last_metrics["cache_read_tokens"] == 10
    assert manager.last_metrics["cache_write_tokens"] == 3

def test_message_log_reads_legacy_and_v2_records_and_clear_removes_checkpoints(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.messages_file.parent.mkdir(parents=True, exist_ok=True)
    legacy = UserMessage(content="legacy")
    store.messages_file.write_text(json.dumps(message_to_dict(legacy)) + "\n", encoding="utf-8")
    store.append_messages([UserMessage(content="new")])
    loaded = store.load_messages()
    assert [message.content for message in loaded if isinstance(message, UserMessage)] == ["legacy", "new"]

    store.write_context_checkpoint({"system_prompt_sha256": "abc", "summary": "safe summary"})
    assert store.latest_context_checkpoint()["summary"] == "safe summary"
    store.clear_messages()
    assert store.load_messages() == []
    assert store.latest_context_checkpoint() is None


def test_recovery_closes_unknown_tool_calls_without_reexecution(tmp_path: Path) -> None:
    store = _store(tmp_path)
    call = ToolCall(id="call_uncertain", name="bash", arguments={"command": "touch marker"})
    original = [UserMessage(content="run it"), AssistantMessage(content=[call], stop_reason="toolUse")]

    recovered = store.recover_pending_tool_calls(original)

    assert len(recovered) == 1
    assert recovered[0].tool_call_id == "call_uncertain"
    assert recovered[0].is_error is True
    assert "not rerun" in recovered[0].content[0].text
    assert isinstance(store.load_messages()[-1], ToolResultMessage)

def test_context_cli_switches_and_preview(tmp_path: Path) -> None:
    from momoclaw.cli import MomoClawCli

    cli = MomoClawCli(workspace_dir=tmp_path)
    cli.start_session()
    assert "off" in cli._handle_context(["status"])
    assert "enabled" in cli._handle_context(["on"])
    assert cli.session.context_manager.config.enabled is True
    assert "Estimated request" in cli._handle_compact(["preview"])
    assert "disabled" in cli._handle_context(["off"])
    assert cli.session.context_manager.config.enabled is False
