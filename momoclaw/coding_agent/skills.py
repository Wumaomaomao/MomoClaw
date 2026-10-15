from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from momoclaw.agent import AgentTool, AgentToolResult
from momoclaw.ai.types import TextContent


@dataclass
class SkillPermissions:
    read: bool = True
    write: bool = False
    edit: bool = False
    bash: bool = False


@dataclass
class SkillActivation:
    manual_names: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass
class Skill:
    name: str
    description: str = ""
    instructions: str = ""
    permissions: SkillPermissions = field(default_factory=SkillPermissions)
    activation: SkillActivation = field(default_factory=SkillActivation)
    path: Path | None = None


class SkillLoader:
    def __init__(self, workspace_dir: str | Path) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.skills_dir = self.workspace_dir / ".momoclaw" / "skills"

    def load(self, name: str) -> Skill:
        skill_dir = self._skill_dir(name)
        if not skill_dir.exists() or not skill_dir.is_dir():
            available = ", ".join(skill.name for skill in self.list()) or "none"
            raise FileNotFoundError(f"Skill not found: {name}. Available skills: {available}")

        instructions_path = skill_dir / "SKILL.md"
        if not instructions_path.exists() or not instructions_path.is_file():
            raise FileNotFoundError(f"Skill {name} is missing SKILL.md")

        metadata = self._read_metadata(skill_dir)
        skill_name = str(metadata.get("name") or name)
        description = str(metadata.get("description") or "")
        permissions = _parse_permissions(metadata.get("permissions"))
        activation = _parse_activation(metadata.get("activation"))
        return Skill(
            name=skill_name,
            description=description,
            instructions=instructions_path.read_text(encoding="utf-8"),
            permissions=permissions,
            activation=activation,
            path=skill_dir,
        )

    def list(self) -> list[Skill]:
        if not self.skills_dir.exists() or not self.skills_dir.is_dir():
            return []
        skills: list[Skill] = []
        for skill_dir in sorted(item for item in self.skills_dir.iterdir() if item.is_dir()):
            if not (skill_dir / "SKILL.md").is_file():
                continue
            metadata = self._read_metadata(skill_dir)
            skills.append(
                Skill(
                    name=str(metadata.get("name") or skill_dir.name),
                    description=str(metadata.get("description") or ""),
                    permissions=_parse_permissions(metadata.get("permissions")),
                    activation=_parse_activation(metadata.get("activation")),
                    path=skill_dir,
                )
            )
        return skills

    def select_for_prompt(self, text: str, *, manual_names: list[str] | None = None) -> list[Skill]:
        selected_names: list[str] = []
        manual = set(manual_names or [])
        lowered = text.lower()
        for skill in self.list():
            aliases = {skill.name, skill.path.name if skill.path else skill.name, *skill.activation.manual_names}
            if manual.intersection(aliases):
                selected_names.append(skill.path.name if skill.path else skill.name)
                continue
            if any(keyword and keyword.lower() in lowered for keyword in skill.activation.keywords):
                selected_names.append(skill.path.name if skill.path else skill.name)

        selected: list[Skill] = []
        seen: set[str] = set()
        for name in selected_names:
            if name in seen:
                continue
            seen.add(name)
            selected.append(self.load(name))
        return selected

    def tool(self) -> AgentTool:
        def execute(tool_call_id: str, params: dict[str, Any], signal=None, on_update=None) -> AgentToolResult:
            del tool_call_id, signal, on_update
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("name is required")
            skill = self.load(name)
            text = format_skill_content(skill)
            return AgentToolResult(
                content=[TextContent(text=text)],
                details={"name": skill.name, "path": str(skill.path) if skill.path else None},
            )

        return AgentTool(
            name="skill",
            label="Skill",
            description="Load the full instructions for an available workspace skill by name.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the skill to load."},
                },
                "required": ["name"],
            },
            execute=execute,
        )

    def _skill_dir(self, name: str) -> Path:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name is required")
        path = Path(name)
        if path.is_absolute() or path.name != name or ".." in path.parts:
            raise ValueError(f"Invalid skill name: {name}")
        return self.skills_dir / name

    def _read_metadata(self, skill_dir: Path) -> dict[str, Any]:
        metadata_path = skill_dir / "skill.json"
        if not metadata_path.exists():
            return {}
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid skill metadata: {metadata_path}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Skill metadata must be an object: {metadata_path}")
        return raw


def _parse_permissions(value: Any) -> SkillPermissions:
    if not isinstance(value, dict):
        return SkillPermissions()
    return SkillPermissions(
        read=bool(value.get("read", True)),
        write=bool(value.get("write", False)),
        edit=bool(value.get("edit", False)),
        bash=bool(value.get("bash", False)),
    )


def _parse_activation(value: Any) -> SkillActivation:
    if not isinstance(value, dict):
        return SkillActivation()
    return SkillActivation(
        manual_names=_string_list(value.get("manual_names")),
        keywords=_string_list(value.get("keywords")),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def format_skill_content(skill: Skill) -> str:
    parts = [f"# Skill: {skill.name}"]
    if skill.description:
        parts.append(skill.description)
    if skill.instructions:
        parts.append(skill.instructions.strip())
    return "\n\n".join(parts)
