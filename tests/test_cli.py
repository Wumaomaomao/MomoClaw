from __future__ import annotations

import json
from pathlib import Path

import pytest

from momoclaw.cli import MomoClawCli, help_text
from momoclaw.agent import AgentToolResult
from momoclaw.ai.types import TextContent


def test_help_text_mentions_required_commands() -> None:
    text = help_text()

    assert "/help" in text
    assert "/model" in text
    assert "/exit" in text


@pytest.mark.asyncio
async def test_model_commands_and_config_persistence(tmp_path: Path, capsys) -> None:
    cli = MomoClawCli(workspace_dir=tmp_path)
    cli.start_session()

    assert await cli.handle_command("/model") is True
    out = capsys.readouterr().out
    assert "deepseek/deepseek-v4-pro" in out
    assert "https://api.deepseek.com" in out

    assert await cli.handle_command("/model list") is True
    out = capsys.readouterr().out
    assert "openai:" in out
    assert "anthropic:" in out
    assert "deepseek:" in out

    assert await cli.handle_command("/model set anthropic claude-sonnet-4-5 https://proxy.example/anthropic") is True
    out = capsys.readouterr().out
    assert "anthropic/claude-sonnet-4-5" in out
    assert "base_url=https://proxy.example/anthropic" in out

    config = json.loads((tmp_path / ".momoclaw" / "config.json").read_text(encoding="utf-8"))
    assert config["provider"] == "anthropic"
    assert config["model_id"] == "claude-sonnet-4-5"
    assert config["base_url"] == "https://proxy.example/anthropic"
    assert cli.session is not None
    assert cli.session.agent.state.model.base_url == "https://proxy.example/anthropic"

    reopened = MomoClawCli(workspace_dir=tmp_path)
    assert reopened.config.provider == "anthropic"
    assert reopened.config.model_id == "claude-sonnet-4-5"
    assert reopened.config.base_url == "https://proxy.example/anthropic"

    assert await reopened.handle_command("/model base-url default") is True
    assert "https://api.anthropic.com" in capsys.readouterr().out
    config = json.loads((tmp_path / ".momoclaw" / "config.json").read_text(encoding="utf-8"))
    assert config["base_url"] is None


def test_cli_base_url_constructor_override(tmp_path: Path) -> None:
    cli = MomoClawCli(workspace_dir=tmp_path, provider="openai", model_id="gpt-4.1-mini", base_url="https://gateway.example/v1")
    cli.start_session()

    assert cli.config.base_url == "https://gateway.example/v1"
    assert cli.session is not None
    assert cli.session.agent.state.model.base_url == "https://gateway.example/v1"


def test_cli_formats_tool_events(tmp_path: Path, capsys) -> None:
    cli = MomoClawCli(workspace_dir=tmp_path)

    cli._on_event({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "printf hello"}})
    cli._on_event(
        {
            "type": "tool_execution_end",
            "toolName": "bash",
            "result": AgentToolResult(content=[TextContent(text="hello")], details={"exit_code": 0}),
            "isError": False,
        }
    )

    out = capsys.readouterr().out
    assert "[tool] bash printf hello" in out
    assert "[tool:ok] bash (exit=0)" in out


@pytest.mark.asyncio
async def test_cli_basic_commands(tmp_path: Path, capsys) -> None:
    cli = MomoClawCli(workspace_dir=tmp_path)
    cli.start_session()

    assert await cli.handle_command("/tools") is True
    assert "read" in capsys.readouterr().out

    assert await cli.handle_command("/usage") is True
    assert "No usage yet" in capsys.readouterr().out

    assert await cli.handle_command("/unknown") is True
    assert "Unknown command" in capsys.readouterr().out

    assert await cli.handle_command("/exit") is False
    assert "bye" in capsys.readouterr().out
