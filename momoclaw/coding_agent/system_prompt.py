from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .skills import Skill


@dataclass
class SystemPromptOptions:
    workspace_dir: str | Path
    custom_prompt: str | None = None
    read_only_mode: bool = True
    enabled_tools: list[str] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    available_skills: list[Skill] = field(default_factory=list)


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
    if options.available_skills:
        skill_lines = ["Available skills for adaptive loading:"]
        for skill in options.available_skills:
            description = f": {skill.description}" if skill.description else ""
            skill_lines.append(f"- {skill.name}{description}")
        if "skill" in options.enabled_tools:
            skill_lines.append("Use the skill tool to load full instructions when a skill is relevant.")
        parts.append("\n".join(skill_lines))
    if options.skills:
        parts.append(format_active_skills(options.skills))
    if options.custom_prompt:
        parts.append(options.custom_prompt.strip())
    return "\n".join(parts)


def format_active_skills(skills: list[Skill]) -> str:
    skill_parts = ["Active skills:"]
    for skill in skills:
        skill_parts.append(f"## {skill.name}")
        if skill.description:
            skill_parts.append(skill.description)
        if skill.instructions:
            skill_parts.append(skill.instructions.strip())
    return "\n".join(skill_parts)
