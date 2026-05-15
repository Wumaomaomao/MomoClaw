from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SystemPromptOptions:
    workspace_dir: str | Path
    custom_prompt: str | None = None
    read_only_mode: bool = True
    enabled_tools: list[str] = field(default_factory=list)


def build_system_prompt(options: SystemPromptOptions) -> str:
    cwd = Path(options.workspace_dir).resolve()
    parts = [
        "You are MomoClaw, a pragmatic coding agent.",
        f"Workspace: {cwd}",
        "Work with the existing repository structure and keep changes focused.",
        "Use tools when you need local file or command context.",
    ]
    if options.read_only_mode:
        parts.append("Current mode is read-only: do not modify files.")
    else:
        parts.append("Current mode allows file changes when explicitly useful.")
    if options.enabled_tools:
        parts.append("Enabled tools: " + ", ".join(options.enabled_tools))
    if options.custom_prompt:
        parts.append(options.custom_prompt.strip())
    return "\n".join(parts)

