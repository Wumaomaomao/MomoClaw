from __future__ import annotations

from pathlib import Path

import pytest

from momoclaw.cli import MomoClawCli, build_parser
from momoclaw.coding_agent import CreateAgentSessionOptions, create_agent_session
from momoclaw.ai.types import Model


def _model() -> Model:
    return Model(id="fake", name="Fake", api="fake-api", provider="fake", base_url="http://fake")


def test_memory_flag_is_opt_in(tmp_path: Path) -> None:
    assert build_parser().parse_args(["--workspace", str(tmp_path)]).memory is False
    assert build_parser().parse_args(["--workspace", str(tmp_path), "--memory"]).memory is True
    assert build_parser().parse_args(["--workspace", str(tmp_path)]).context is False
    assert build_parser().parse_args(["--workspace", str(tmp_path), "--context"]).context is True

    disabled = create_agent_session(CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model()))
    enabled = create_agent_session(CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model(), memory_enabled=True))
    assert disabled.memory_manager is None
    assert enabled.memory_manager is not None


@pytest.mark.asyncio
async def test_memory_cli_commands_and_confirmation(tmp_path: Path, capsys) -> None:
    (tmp_path / "README.md").write_text("Workspace README.", encoding="utf-8")
    cli = MomoClawCli(workspace_dir=tmp_path)
    cli.start_session()

    assert await cli.handle_command("/memory init") is True
    assert "candidate overview" in capsys.readouterr().out
    assert await cli.handle_command("/memory status") is True
    assert "candidate=1" in capsys.readouterr().out
    assert await cli.handle_command("/memory approve bad-id") is True
    assert "not found" in capsys.readouterr().out
    assert await cli.handle_command("/memory clear") is True
    assert "Run /memory clear confirm" in capsys.readouterr().out
    assert (tmp_path / ".momoclaw" / "memory").exists()

    await cli.handle_command("/memory on")
    assert cli.session is not None and cli.session.memory_manager is not None
    await cli.handle_command("/memory off")
    assert cli.session is not None and cli.session.memory_manager is None

def _reply_stream(message):
    from momoclaw.ai.event_stream import AssistantMessageEventStream

    calls = []
    def stream_fn(model, context, options=None):
        del model, options
        stream = AssistantMessageEventStream()
        stream.push({"type": "start", "partial": message})
        for index, block in enumerate(message.content):
            if hasattr(block, "text"):
                stream.push({"type": "text_start", "contentIndex": index, "partial": message})
                stream.push({"type": "text_delta", "contentIndex": index, "delta": block.text, "partial": message})
                stream.push({"type": "text_end", "contentIndex": index, "content": block.text, "partial": message})
        stream.push({"type": "done", "reason": message.stop_reason, "message": message})
        stream.end(message)
        calls.append(context)
        return stream

    stream_fn.calls = calls
    return stream_fn


@pytest.mark.asyncio
async def test_memory_is_shared_across_sessions_and_injected_only_when_enabled(tmp_path: Path) -> None:
    from momoclaw.ai.types import AssistantMessage, TextContent
    from momoclaw.coding_agent.memory import MemoryEntry, MemoryStore, SourceRef
    from momoclaw.coding_agent.memory.scanner import sha256_bytes

    source = tmp_path / "auth.py"
    source.write_text("def authenticate_user(): return True\\n", encoding="utf-8")
    store = MemoryStore(tmp_path)
    entry = MemoryEntry(
        workspace_id=store.workspace_id,
        type="fact",
        title="Authentication",
        summary="authenticate_user is the authentication entry point.",
        body="Use authenticate_user for authentication.",
        source_refs=[SourceRef(path="auth.py", content_sha256=sha256_bytes(source.read_bytes()))],
        status="verified",
    )
    store.save(entry)
    response = AssistantMessage(content=[TextContent(text="ok")], api="fake-api", provider="fake", model="fake")
    enabled_stream = _reply_stream(response)
    enabled = create_agent_session(
        CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model(), memory_enabled=True, stream_fn=enabled_stream)
    )
    await enabled.prompt("Where is authentication?")
    assert len(enabled_stream.calls) == 1
    assert "Retrieved workspace memory" in " ".join(block.text for message in enabled_stream.calls[0].messages if hasattr(message, "content") and isinstance(message.content, list) for block in message.content if isinstance(block, TextContent))
    assert "authenticate_user" in " ".join(block.text for message in enabled_stream.calls[0].messages if hasattr(message, "content") and isinstance(message.content, list) for block in message.content if isinstance(block, TextContent))

    disabled_stream = _reply_stream(response)
    disabled = create_agent_session(
        CreateAgentSessionOptions(workspace_dir=tmp_path, model=_model(), stream_fn=disabled_stream)
    )
    await disabled.prompt("Where is authentication?")
    assert "Retrieved workspace memory" not in " ".join(block.text for message in disabled_stream.calls[0].messages if hasattr(message, "content") and isinstance(message.content, list) for block in message.content if isinstance(block, TextContent))
    assert disabled_stream.calls[0].system_prompt == disabled.agent.state.system_prompt
    assert enabled_stream.calls[0].system_prompt == enabled.agent.state.system_prompt
    assert disabled_stream.calls[0].system_prompt == disabled.agent.state.system_prompt
    assert enabled.session_id != disabled.session_id

@pytest.mark.asyncio
async def test_memory_cli_add_creates_reviewable_candidate(tmp_path: Path, capsys) -> None:
    (tmp_path / "design.md").write_text("Queue worker architecture.", encoding="utf-8")
    cli = MomoClawCli(workspace_dir=tmp_path, memory_enabled=True)
    cli.start_session()

    await cli.handle_command("/memory add design.md | Queue worker | Uses a worker | Queue workers process jobs")
    output = capsys.readouterr().out
    assert "Added candidate entry-" in output
    assert len(cli.session.memory_manager.list_entries()) == 1