from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from momoclaw.ai.event_stream import AssistantMessageEventStream
from momoclaw.ai.types import AssistantMessage, Context, Model, SimpleStreamOptions, TextContent, ToolCall, ToolResultMessage
from momoclaw.coding_agent import CreateAgentSessionOptions, SkillLoader, create_agent_session
from momoclaw.coding_agent.builtin_tools import create_builtin_tools


def _model() -> Model:
    return Model(id="fake", name="Fake", api="fake-api", provider="fake", base_url="http://fake")


def _stream_from_messages(messages: list[AssistantMessage]) -> Callable:
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


def test_read_tool_reads_file_and_blocks_escape(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    tool = create_builtin_tools(tmp_path, read_only_mode=True)[0]

    result = tool.execute("call_1", {"path": "README.md"})

    assert result.content == [TextContent(text="one\ntwo\nthree\n")]
    result = tool.execute("call_1", {"path": "README.md", "offset": 2, "limit": 1})
    assert result.content == [TextContent(text="two")]
    with pytest.raises(ValueError, match="escapes workspace"):
        tool.execute("call_2", {"path": "../outside.txt"})


def test_write_and_edit_tools_modify_workspace_files(tmp_path: Path) -> None:
    tools = {tool.name: tool for tool in create_builtin_tools(tmp_path, read_only_mode=False)}

    result = tools["write"].execute("call_1", {"path": "src/app.py", "content": "print('old')\n"})
    assert "Successfully wrote" in result.content[0].text
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "print('old')\n"

    result = tools["edit"].execute(
        "call_2",
        {
            "path": "src/app.py",
            "edits": [{"oldText": "print('old')", "newText": "print('new')"}],
        },
    )
    assert "Successfully replaced" in result.content[0].text
    assert "print('new')" in (tmp_path / "src" / "app.py").read_text(encoding="utf-8")
    assert "-print('old')" in result.details["diff"]
    assert "+print('new')" in result.details["diff"]


def test_edit_requires_unique_match(tmp_path: Path) -> None:
    (tmp_path / "dup.txt").write_text("same\nsame\n", encoding="utf-8")
    tools = {tool.name: tool for tool in create_builtin_tools(tmp_path, read_only_mode=False)}

    with pytest.raises(ValueError, match="text must be unique"):
        tools["edit"].execute("call_1", {"path": "dup.txt", "oldText": "same", "newText": "other"})


@pytest.mark.asyncio
async def test_bash_tool_runs_safe_command_and_blocks_dangerous(tmp_path: Path) -> None:
    tools = {tool.name: tool for tool in create_builtin_tools(tmp_path, read_only_mode=False)}

    result = await tools["bash"].execute("call_1", {"command": "printf hello", "timeout": 5})
    assert result.content == [TextContent(text="hello")]
    assert result.details["exit_code"] == 0

    with pytest.raises(ValueError, match="blocked"):
        await tools["bash"].execute("call_2", {"command": "rm -rf ."})


@pytest.mark.asyncio
async def test_coding_agent_reads_file_through_tool_and_persists(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("project notes", encoding="utf-8")
    first = AssistantMessage(
        content=[ToolCall(id="call_1", name="read", arguments={"path": "README.md"})],
        api="fake-api",
        provider="fake",
        model="fake",
        stop_reason="toolUse",
    )
    second = AssistantMessage(content=[TextContent(text="README says project notes")], api="fake-api", provider="fake", model="fake")
    stream_fn = _stream_from_messages([first, second])
    session = create_agent_session(
        CreateAgentSessionOptions(
            workspace_dir=tmp_path,
            model=_model(),
            read_only_mode=True,
            stream_fn=stream_fn,
        )
    )
    events: list[dict] = []
    session.subscribe(events.append)

    new_messages = await session.prompt("read README")

    assert len(new_messages) == 4
    assert isinstance(new_messages[2], ToolResultMessage)
    assert new_messages[2].content == [TextContent(text="project notes")]
    assert (tmp_path / ".momoclaw" / "sessions" / session.session_id / "messages.jsonl").exists()
    assert any(event["type"] == "tool_execution_end" for event in events)

    reopened = create_agent_session(
        CreateAgentSessionOptions(
            workspace_dir=tmp_path,
            model=_model(),
            session_id=session.session_id,
            read_only_mode=True,
            stream_fn=stream_fn,
        )
    )
    assert len(reopened.messages) == 4
    assert isinstance(reopened.messages[-1], AssistantMessage)


@pytest.mark.asyncio
async def test_context_output_budget_is_forwarded_to_provider_options(tmp_path: Path) -> None:
    from momoclaw.ai.types import SimpleStreamOptions

    model = Model(
        id="fake", name="Fake", api="fake-api", provider="fake", base_url="http://fake",
        context_window=2048, max_tokens=1024,
    )
    received: list[SimpleStreamOptions | None] = []
    delegate = _stream_from_messages([
        AssistantMessage(content=[TextContent(text="ok")], api="fake-api", provider="fake", model="fake")
    ])

    def stream_fn(model, context, options=None):
        received.append(options)
        return delegate(model, context, options)

    session = create_agent_session(
        CreateAgentSessionOptions(workspace_dir=tmp_path, model=model, stream_fn=stream_fn)
    )
    await session.prompt("hello")

    assert received[0] is not None
    assert received[0].max_tokens == 512

def test_factory_defaults_to_read_only_tools(tmp_path: Path) -> None:
    session = create_agent_session(CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model()))

    assert [tool.name for tool in session.agent.state.tools] == ["read", "skill"]
    assert "read-only" in session.agent.state.system_prompt


def test_factory_read_write_mode_enables_mutating_tools(tmp_path: Path) -> None:
    session = create_agent_session(CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model(), read_only_mode=False))

    assert [tool.name for tool in session.agent.state.tools] == ["read", "write", "edit", "bash", "skill"]


def test_skill_loader_loads_workspace_skill_metadata(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".momoclaw" / "skills" / "code_review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Review changed code carefully.", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        (
            '{"name": "code_review", "description": "Review code.", '
            '"activation": {"manual_names": ["review"], "keywords": ["代码审查"]}, '
            '"permissions": {"read": true}}'
        ),
        encoding="utf-8",
    )

    skill = SkillLoader(tmp_path).load("code_review")

    assert skill.name == "code_review"
    assert skill.description == "Review code."
    assert skill.instructions == "Review changed code carefully."
    assert skill.activation.manual_names == ["review"]
    assert skill.activation.keywords == ["代码审查"]
    assert skill.permissions.read is True
    assert skill.permissions.write is False


def test_skill_loader_rejects_invalid_names(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)

    with pytest.raises(ValueError, match="Invalid skill name"):
        loader.load("../outside")


def test_factory_adds_skill_index_without_elevating_tools(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".momoclaw" / "skills" / "pytest_fix"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Run focused pytest checks before finalizing.", encoding="utf-8")
    (skill_dir / "skill.json").write_text('{"description": "Fix pytest failures."}', encoding="utf-8")

    session = create_agent_session(
        CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model(), skill_names=["pytest_fix"])
    )

    assert [tool.name for tool in session.agent.state.tools] == ["read", "skill"]
    assert "Available skills for adaptive loading:" in session.agent.state.system_prompt
    assert "- pytest_fix: Fix pytest failures." in session.agent.state.system_prompt
    assert "Use the skill tool to load full instructions" in session.agent.state.system_prompt
    assert "Run focused pytest checks before finalizing." not in session.agent.state.system_prompt


@pytest.mark.asyncio
async def test_session_progressively_loads_manual_skill_for_prompt(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".momoclaw" / "skills" / "pytest_fix"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Run focused pytest checks before finalizing.", encoding="utf-8")
    stream_fn = _stream_from_messages(
        [AssistantMessage(content=[TextContent(text="ok")], api="fake-api", provider="fake", model="fake")]
    )
    session = create_agent_session(
        CreateAgentSessionOptions(
            workspace_dir=tmp_path,
            model=_model(),
            skill_names=["pytest_fix"],
            stream_fn=stream_fn,
        )
    )

    await session.prompt("fix the tests")

    assert "Run focused pytest checks before finalizing." not in session.agent.state.system_prompt
    assert "Run focused pytest checks before finalizing." in " ".join(block.text for message in stream_fn.calls[0].messages if hasattr(message, "content") and isinstance(message.content, list) for block in message.content if isinstance(block, TextContent))
    assert stream_fn.calls[0].system_prompt == session.agent.state.system_prompt


@pytest.mark.asyncio
async def test_model_loads_skill_with_skill_tool(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".momoclaw" / "skills" / "code_review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Report findings first.", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        '{"description": "Review code changes and report findings first."}',
        encoding="utf-8",
    )
    first = AssistantMessage(
        content=[ToolCall(id="call_1", name="skill", arguments={"name": "code_review"})],
        api="fake-api",
        provider="fake",
        model="fake",
        stop_reason="toolUse",
    )
    second = AssistantMessage(content=[TextContent(text="reviewed")], api="fake-api", provider="fake", model="fake")
    stream_fn = _stream_from_messages([first, second])
    session = create_agent_session(
        CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model(), stream_fn=stream_fn)
    )

    new_messages = await session.prompt("please review this change")

    assert isinstance(new_messages[2], ToolResultMessage)
    assert new_messages[2].tool_name == "skill"
    assert "Report findings first." in new_messages[2].content[0].text


@pytest.mark.asyncio
async def test_keyword_skill_activation_is_opt_in(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".momoclaw" / "skills" / "code_review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Report findings first.", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        '{"activation": {"keywords": ["review", "代码审查"]}}',
        encoding="utf-8",
    )
    stream_fn = _stream_from_messages(
        [AssistantMessage(content=[TextContent(text="ok")], api="fake-api", provider="fake", model="fake")]
    )
    session = create_agent_session(
        CreateAgentSessionOptions(
            workspace_dir=tmp_path,
            model=_model(),
            stream_fn=stream_fn,
            keyword_skill_activation=True,
        )
    )

    await session.prompt("please review this change")

    assert "Report findings first." in " ".join(block.text for message in stream_fn.calls[0].messages if hasattr(message, "content") and isinstance(message.content, list) for block in message.content if isinstance(block, TextContent))
    assert stream_fn.calls[0].system_prompt == session.agent.state.system_prompt
