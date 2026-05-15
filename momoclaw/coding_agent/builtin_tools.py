from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

from momoclaw.agent import AgentTool, AgentToolResult
from momoclaw.ai.types import TextContent

READ_ONLY_TOOL_NAMES = {"read"}
MUTATING_TOOL_NAMES = {"write", "edit", "bash"}


def create_builtin_tools(
    workspace_dir: str | Path,
    *,
    enabled_names: list[str] | None = None,
    read_only_mode: bool = True,
    bash_timeout_seconds: float = 30.0,
) -> list[AgentTool]:
    workspace = Path(workspace_dir).resolve()
    requested = set(enabled_names or (["read"] if read_only_mode else ["read", "write", "edit", "bash"]))
    tools: list[AgentTool] = []
    if "read" in requested:
        tools.append(_read_tool(workspace))
    if "write" in requested and not read_only_mode:
        tools.append(_write_tool(workspace))
    if "edit" in requested and not read_only_mode:
        tools.append(_edit_tool(workspace))
    if "bash" in requested and not read_only_mode:
        tools.append(_bash_tool(workspace, bash_timeout_seconds))
    return tools


def _resolve_inside_workspace(workspace: Path, path: str) -> Path:
    candidate = (workspace / path).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise ValueError(f"Path escapes workspace: {path}")
    return candidate


def _read_tool(workspace: Path) -> AgentTool:
    def execute(tool_call_id: str, params: dict[str, Any], signal=None, on_update=None) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = params.get("path")
        offset = params.get("offset")
        limit = params.get("limit")
        if not isinstance(path, str) or not path:
            raise ValueError("path is required")
        if offset is not None and (not isinstance(offset, int) or offset < 1):
            raise ValueError("offset must be a positive integer")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise ValueError("limit must be a positive integer")
        target = _resolve_inside_workspace(workspace, path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            entries = sorted(item.name + ("/" if item.is_dir() else "") for item in target.iterdir())
            text = "\n".join(entries)
            return AgentToolResult(content=[TextContent(text=text)], details={"path": str(target), "kind": "directory"})
        full_text = target.read_text(encoding="utf-8", errors="replace")
        lines = full_text.splitlines()
        if offset is not None or limit is not None:
            start = (offset or 1) - 1
            end = start + limit if limit is not None else None
            text = "\n".join(lines[start:end])
            details = {
                "path": str(target),
                "kind": "file",
                "offset": offset or 1,
                "limit": limit,
                "total_lines": len(lines),
            }
            return AgentToolResult(content=[TextContent(text=text)], details=details)
        return AgentToolResult(content=[TextContent(text=full_text)], details={"path": str(target), "kind": "file"})

    return AgentTool(
        name="read",
        label="Read",
        description="Read a UTF-8 text file or list a directory inside the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path to read."},
                "offset": {"type": "integer", "description": "Optional 1-indexed starting line."},
                "limit": {"type": "integer", "description": "Optional maximum number of lines."},
            },
            "required": ["path"],
        },
        execute=execute,
    )


def _write_tool(workspace: Path) -> AgentTool:
    def execute(tool_call_id: str, params: dict[str, Any], signal=None, on_update=None) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = params.get("path")
        content = params.get("content")
        if not isinstance(path, str) or not path:
            raise ValueError("path is required")
        if not isinstance(content, str):
            raise ValueError("content is required and must be a string")
        target = _resolve_inside_workspace(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return AgentToolResult(
            content=[TextContent(text=f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}.")],
            details={"path": str(target), "bytes": len(content.encode("utf-8"))},
        )

    return AgentTool(
        name="write",
        label="Write",
        description="Create or overwrite a UTF-8 text file inside the workspace. Parent directories are created.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path to write."},
                "content": {"type": "string", "description": "Complete file content."},
            },
            "required": ["path", "content"],
        },
        execute=execute,
    )


def _edit_tool(workspace: Path) -> AgentTool:
    def execute(tool_call_id: str, params: dict[str, Any], signal=None, on_update=None) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = params.get("path")
        edits = _normalize_edits(params)
        if not isinstance(path, str) or not path:
            raise ValueError("path is required")
        if not edits:
            raise ValueError("edits must contain at least one replacement")
        target = _resolve_inside_workspace(workspace, path)
        if not target.exists():
            raise FileNotFoundError(path)
        original = target.read_text(encoding="utf-8", errors="replace")
        new_content = original
        spans: list[tuple[int, int, str]] = []
        for index, edit in enumerate(edits):
            old_text = edit.get("oldText")
            new_text = edit.get("newText")
            if not isinstance(old_text, str) or not old_text:
                raise ValueError(f"edits[{index}].oldText must be a non-empty string")
            if not isinstance(new_text, str):
                raise ValueError(f"edits[{index}].newText must be a string")
            count = original.count(old_text)
            if count == 0:
                raise ValueError(f"Could not find edits[{index}].oldText in {path}")
            if count > 1:
                raise ValueError(f"Found {count} occurrences of edits[{index}].oldText in {path}; text must be unique")
            start = original.index(old_text)
            end = start + len(old_text)
            spans.append((start, end, new_text))
        spans.sort(key=lambda item: item[0])
        for previous, current in zip(spans, spans[1:]):
            if previous[1] > current[0]:
                raise ValueError("edits contain overlapping replacements")
        for start, end, new_text in reversed(spans):
            new_content = new_content[:start] + new_text + new_content[end:]
        if new_content == original:
            raise ValueError(f"No changes made to {path}")
        target.write_text(new_content, encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=path,
                tofile=path,
            )
        )
        return AgentToolResult(
            content=[TextContent(text=f"Successfully replaced {len(edits)} block(s) in {path}.")],
            details={"path": str(target), "diff": diff},
        )

    return AgentTool(
        name="edit",
        label="Edit",
        description="Edit a file using exact, unique text replacements. Each oldText must match exactly once.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "edits": {
                    "type": "array",
                    "description": "Replacements matched against the original file.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string"},
                            "newText": {"type": "string"},
                        },
                        "required": ["oldText", "newText"],
                    },
                },
                "oldText": {"type": "string", "description": "Legacy single replacement old text."},
                "newText": {"type": "string", "description": "Legacy single replacement new text."},
            },
            "required": ["path"],
        },
        execute=execute,
    )


def _bash_tool(workspace: Path, timeout_seconds: float) -> AgentTool:
    async def execute(tool_call_id: str, params: dict[str, Any], signal=None, on_update=None) -> AgentToolResult:
        del tool_call_id, signal, on_update
        command = params.get("command")
        timeout = params.get("timeout")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command is required")
        if timeout is not None and (not isinstance(timeout, int | float) or timeout <= 0):
            raise ValueError("timeout must be a positive number")
        effective_timeout = float(timeout or timeout_seconds)
        if _is_blocked_command(command):
            raise ValueError("command is blocked by the MVP bash safety policy")

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=effective_timeout)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            raise TimeoutError(f"command timed out after {effective_timeout}s")

        output = stdout.decode("utf-8", errors="replace")
        error = stderr.decode("utf-8", errors="replace")
        text = output
        if error:
            text = f"{output}\n[stderr]\n{error}" if output else f"[stderr]\n{error}"
        return AgentToolResult(content=[TextContent(text=text or "(no output)")], details={"exit_code": process.returncode})

    return AgentTool(
        name="bash",
        label="Bash",
        description="Run a non-mutating shell command in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout": {"type": "number", "description": "Optional timeout in seconds."},
            },
            "required": ["command"],
        },
        execute=execute,
    )


def _normalize_edits(params: dict[str, Any]) -> list[dict[str, Any]]:
    edits = params.get("edits")
    if isinstance(edits, str):
        import json

        parsed = json.loads(edits)
        edits = parsed
    if isinstance(edits, list):
        return [item for item in edits if isinstance(item, dict)]
    if isinstance(params.get("oldText"), str) and isinstance(params.get("newText"), str):
        return [{"oldText": params["oldText"], "newText": params["newText"]}]
    return []


def _is_blocked_command(command: str) -> bool:
    stripped = command.strip()
    blocked_prefixes = (
        "rm ",
        "rm\t",
        "rm -",
        "mv ",
        "cp ",
        "chmod ",
        "chown ",
        "git reset",
        "git checkout",
        "sudo ",
    )
    blocked_fragments = (" > ", ">>", " 2>", "| sh", "| bash")
    return stripped.startswith(blocked_prefixes) or any(fragment in command for fragment in blocked_fragments)
