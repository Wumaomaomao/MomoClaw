from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from momoclaw.agent import Agent, AgentEvent, AgentEventSink, AgentOptions, AgentTool
from momoclaw.agent.types import StreamFn
from momoclaw.ai.types import AssistantMessage, Message, Model, UserMessage

from .session_store import SessionStore, new_session_id
from .memory import WorkspaceMemoryManager
from .skills import Skill, SkillLoader
from .system_prompt import format_active_skills


@dataclass
class AgentSessionOptions:
    model: Model
    workspace_dir: str | Path
    system_prompt: str
    tools: list[AgentTool] = field(default_factory=list)
    session_id: str | None = None
    messages: list[Message] = field(default_factory=list)
    reasoning: str | None = None
    stream_fn: StreamFn | None = None
    skill_names: list[str] = field(default_factory=list)
    adaptive_skills: bool = True
    keyword_skill_activation: bool = False
    memory_enabled: bool = False


class AgentSession:
    def __init__(self, options: AgentSessionOptions) -> None:
        self.workspace_dir = Path(options.workspace_dir)
        self.base_system_prompt = options.system_prompt
        self.skill_names = list(options.skill_names)
        self.adaptive_skills = options.adaptive_skills
        self.keyword_skill_activation = options.keyword_skill_activation
        self.memory_enabled = options.memory_enabled
        self.memory_manager = WorkspaceMemoryManager(self.workspace_dir) if self.memory_enabled else None
        self.skill_loader = SkillLoader(self.workspace_dir)
        self.session_id = options.session_id or new_session_id()
        self.store = SessionStore(self.workspace_dir, self.session_id)
        self.store.ensure_initialized(
            model_id=options.model.id,
            provider=options.model.provider,
            system_prompt=options.system_prompt,
        )
        persisted = self.store.load_messages()
        initial_messages = [*persisted, *options.messages]
        self.agent = Agent(
            AgentOptions(
                model=options.model,
                system_prompt=options.system_prompt,
                tools=options.tools,
                messages=initial_messages,
                reasoning=options.reasoning,
                session_id=self.session_id,
                stream_fn=options.stream_fn,
            )
        )
        self._listeners: list[AgentEventSink] = []
        self._unsubscribe_agent = self.agent.subscribe(self._on_agent_event)

    @property
    def messages(self) -> list[Message]:
        return self.agent.state.messages

    @property
    def last_usage(self) -> dict[str, Any] | None:
        for message in reversed(self.messages):
            if isinstance(message, AssistantMessage):
                return {
                    "input_tokens": message.usage.input,
                    "output_tokens": message.usage.output,
                    "total_tokens": message.usage.total_tokens,
                    "cost": message.usage.cost.total,
                }
        return None

    async def prompt(self, text: str) -> list[Message]:
        new_messages = await self._prompt_with_skills(text, lambda: self.agent.prompt(text))
        self.store.append_messages(new_messages)
        return new_messages

    async def prompt_message(self, message: UserMessage) -> list[Message]:
        text = message.content if isinstance(message.content, str) else ""
        new_messages = await self._prompt_with_skills(text, lambda: self.agent.prompt(message))
        self.store.append_messages(new_messages)
        return new_messages

    async def continue_run(self) -> list[Message]:
        new_messages = await self.agent.continue_run()
        self.store.append_messages(new_messages)
        return new_messages

    def subscribe(self, listener: AgentEventSink):
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    def close(self) -> None:
        self._unsubscribe_agent()

    async def _prompt_with_skills(self, text: str, run_prompt):
        selected = self._select_skills(text)
        try:
            memory_context = self.memory_manager.context_for(text) if self.memory_manager is not None else ""
        except Exception:
            memory_context = ""
        original_prompt = self.agent.state.system_prompt
        self.agent.state.system_prompt = self._system_prompt_with_skills(selected, memory_context)
        try:
            return await run_prompt()
        finally:
            self.agent.state.system_prompt = original_prompt

    def _select_skills(self, text: str) -> list[Skill]:
        if not self.skill_names and not self.keyword_skill_activation:
            return []
        prompt_text = text if self.keyword_skill_activation else ""
        return self.skill_loader.select_for_prompt(prompt_text, manual_names=self.skill_names)

    def _system_prompt_with_skills(self, skills: list[Skill], memory_context: str = "") -> str:
        parts = [self.base_system_prompt]
        if skills:
            parts.append(format_active_skills(skills))
        if memory_context:
            parts.append(memory_context)
        return "\n".join(parts)

    async def _on_agent_event(self, event: AgentEvent) -> None:
        self.store.append_event(event)
        for listener in list(self._listeners):
            result = listener(event)
            if hasattr(result, "__await__"):
                await result  # type: ignore[misc]
