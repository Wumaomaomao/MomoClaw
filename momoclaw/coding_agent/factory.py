from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from momoclaw.agent import AgentTool
from momoclaw.agent.types import StreamFn
from momoclaw.ai.models import get_model
from momoclaw.ai.types import Message, Model

from .builtin_tools import create_builtin_tools
from .session import AgentSession, AgentSessionOptions
from .skills import Skill, SkillLoader
from .system_prompt import SystemPromptOptions, build_system_prompt


@dataclass
class CreateAgentSessionOptions:
    workspace_dir: str | Path
    model: Model | None = None
    provider: str | None = None
    model_id: str | None = None
    system_prompt: str = ""
    tools: list[AgentTool] = field(default_factory=list)
    session_id: str | None = None
    messages: list[Message] = field(default_factory=list)
    reasoning: str | None = None
    read_only_mode: bool = True
    enabled_builtin_tools: list[str] | None = None
    skills: list[Skill] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)
    adaptive_skills: bool = True
    enable_skill_tool: bool = True
    keyword_skill_activation: bool = False
    stream_fn: StreamFn | None = None
    memory_enabled: bool = False
    context_enabled: bool = False


def create_agent_session(options: CreateAgentSessionOptions) -> AgentSession:
    model = options.model
    if model is None:
        if not options.provider or not options.model_id:
            raise ValueError("Provide model or provider + model_id")
        model = get_model(options.provider, options.model_id)

    builtin_tools = create_builtin_tools(
        options.workspace_dir,
        enabled_names=options.enabled_builtin_tools,
        read_only_mode=options.read_only_mode,
    )
    skill_loader = SkillLoader(options.workspace_dir)
    if options.enable_skill_tool and options.adaptive_skills:
        builtin_tools.append(skill_loader.tool())
    tool_map = {tool.name: tool for tool in builtin_tools}
    for tool in options.tools:
        tool_map[tool.name] = tool
    tools = list(tool_map.values())
    available_skills = skill_loader.list() if options.adaptive_skills else []

    system_prompt = build_system_prompt(
        SystemPromptOptions(
            workspace_dir=options.workspace_dir,
            custom_prompt=options.system_prompt or None,
            read_only_mode=options.read_only_mode,
            enabled_tools=[tool.name for tool in tools],
            skills=options.skills,
            available_skills=available_skills,
        )
    )

    return AgentSession(
        AgentSessionOptions(
            model=model,
            workspace_dir=options.workspace_dir,
            system_prompt=system_prompt,
            tools=tools,
            session_id=options.session_id,
            messages=options.messages,
            reasoning=options.reasoning,
            stream_fn=options.stream_fn,
            skill_names=options.skill_names,
            adaptive_skills=options.adaptive_skills,
            keyword_skill_activation=options.keyword_skill_activation,
            memory_enabled=options.memory_enabled,
            context_enabled=options.context_enabled,
        )
    )
