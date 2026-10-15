from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from momoclaw.ai.models import get_model, get_models, get_providers
from momoclaw.ai.types import AssistantMessage, TextContent
from momoclaw.coding_agent import CreateAgentSessionOptions, create_agent_session
from momoclaw.coding_agent.session import AgentSession
from momoclaw.coding_agent.memory import WorkspaceMemoryManager
from momoclaw.coding_agent.memory.retrieval import render_entry

DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL_ID = "deepseek-v4-pro"


@dataclass
class CliConfig:
    provider: str = DEFAULT_PROVIDER
    model_id: str = DEFAULT_MODEL_ID
    base_url: str | None = None
    read_only_mode: bool = True


class MomoClawCli:
    def __init__(
        self,
        *,
        workspace_dir: str | Path,
        provider: str | None = None,
        model_id: str | None = None,
        base_url: str | None = None,
        read_only_mode: bool = True,
        session_id: str | None = None,
        skill_names: list[str] | None = None,
        memory_enabled: bool = False,
        context_enabled: bool = False,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.config_path = self.workspace_dir / ".momoclaw" / "config.json"
        self.config = self._load_config()
        if provider:
            self.config.provider = provider
        if model_id:
            self.config.model_id = model_id
        if base_url is not None:
            self.config.base_url = base_url or None
        self.config.read_only_mode = read_only_mode
        self.session_id = session_id
        self.skill_names = list(skill_names or [])
        self.memory_enabled = memory_enabled
        self.context_enabled = context_enabled
        self.session: AgentSession | None = None
        self._printed_stream_text = False

    def start_session(self) -> AgentSession:
        self._save_config()
        if self.session is not None:
            self.session.close()
        self.session = create_agent_session(
            CreateAgentSessionOptions(
                workspace_dir=self.workspace_dir,
                model=self._resolve_model(),
                provider=self.config.provider,
                model_id=self.config.model_id,
                session_id=self.session_id,
                read_only_mode=self.config.read_only_mode,
                skill_names=self.skill_names,
                memory_enabled=self.memory_enabled,
                context_enabled=self.context_enabled,
            )
        )
        self.session_id = self.session.session_id
        self.session.subscribe(self._on_event)
        return self.session

    async def run(self) -> None:
        self.start_session()
        print("MomoClaw CLI. Type /help for commands.")
        print(self._model_text(prefix="Model"))
        while True:
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not raw:
                continue
            if raw.startswith("/"):
                should_continue = await self.handle_command(raw)
                if not should_continue:
                    break
                continue
            await self.prompt(raw)
        if self.session is not None:
            self.session.close()

    async def prompt(self, text: str) -> None:
        session = self.session or self.start_session()
        self._printed_stream_text = False
        messages = await session.prompt(text)
        last = messages[-1] if messages else None
        if isinstance(last, AssistantMessage) and last.stop_reason == "error":
            print(f"[error] {last.error_message or 'unknown error'}")
        elif isinstance(last, AssistantMessage) and not self._printed_stream_text:
            print(_assistant_text(last))
        elif self._printed_stream_text:
            print()

    async def handle_command(self, raw: str) -> bool:
        parts = raw.split()
        command = parts[0].lower()
        args = parts[1:]
        if command in {"/exit", "/quit"}:
            print("bye")
            return False
        if command == "/help":
            print(help_text())
            return True
        if command == "/model":
            print(self._handle_model(args))
            return True
        if command == "/new":
            self.session_id = None
            self.start_session()
            print(f"Started new session: {self.session_id}")
            return True
        if command == "/clear":
            if self.session is not None:
                self.session.clear_messages()
            print("Cleared messages and context checkpoints for the current session.")
            return True
        if command == "/usage":
            print(self._usage_text())
            return True
        if command == "/tools":
            print(self._tools_text())
            return True
        if command == "/memory":
            print(self._handle_memory(args))
            return True
        if command == "/context":
            print(self._handle_context(args))
            return True
        if command == "/compact":
            print(self._handle_compact(args))
            return True
        print(f"Unknown command: {command}. Type /help.")
        return True

    def _handle_memory(self, args: list[str]) -> str:
        if not args:
            return "Usage: /memory status|init|search|list|show|add|approve|refresh|on|off|clear confirm|rebuild confirm"
        command, *rest = args
        manager = WorkspaceMemoryManager(self.workspace_dir)
        if command == "on":
            self.memory_enabled = True
            if self.session is not None:
                self.session.memory_enabled = True
                self.session.memory_manager = manager
            return "Workspace memory enabled for this CLI session."
        if command == "off":
            self.memory_enabled = False
            if self.session is not None:
                self.session.memory_enabled = False
                self.session.memory_manager = None
            return "Workspace memory disabled for this CLI session."
        if command == "init":
            count, created = manager.initialize()
            suffix = "created a candidate overview" if created else "overview already exists or no eligible files were found"
            return f"Scanned {count} files; {suffix}."
        if command == "status":
            counts = manager.status()
            return "Memory: " + ", ".join(f"{key}={value}" for key, value in counts.items()) + f"; enabled={'yes' if self.memory_enabled else 'no'}; budget=1200 tokens."
        if command == "refresh":
            checked, stale = manager.refresh()
            return f"Checked {checked} verified entries; marked {stale} stale."
        if command == "search":
            query = " ".join(rest).strip()
            if not query:
                return "Usage: /memory search <query>"
            entries, tokens, stale = manager.search(query)
            if not entries:
                return f"No verified memory matched. Stale filtered: {stale}."
            return f"Estimated {tokens} tokens; stale filtered: {stale}.\n" + "\n".join(render_entry(entry) for entry in entries)
        if command == "list":
            status = rest[0] if rest else None
            if status and status not in {"candidate", "verified", "stale", "rejected"}:
                return "Status must be candidate, verified, stale, or rejected."
            entries = manager.list_entries(status)
            if not entries:
                return "No memory entries."
            return "\n".join(f"{entry.id} [{entry.status}] {entry.title}" for entry in entries)
        if command == "show":
            if len(rest) != 1:
                return "Usage: /memory show <id>"
            entry = manager.get(rest[0])
            return manager.render(entry) if entry else "Memory entry not found."
        if command == "add":
            raw = " ".join(rest)
            fields = [part.strip() for part in raw.split("|", 3)]
            if len(fields) != 4:
                return "Usage: /memory add <source-path> | <title> | <summary> | <body>"
            try:
                entry = manager.add_candidate(source_path=fields[0], title=fields[1], summary=fields[2], body=fields[3])
            except ValueError as exc:
                return f"Cannot add memory: {exc}"
            return f"Added candidate {entry.id}; inspect and approve it before retrieval."
        if command == "approve":
            if len(rest) != 1:
                return "Usage: /memory approve <id>"
            try:
                entry = manager.approve(rest[0])
            except ValueError as exc:
                return f"Cannot approve memory: {exc}"
            return f"Approved {entry.id}; it can now be retrieved."
        if command == "clear":
            if rest != ["confirm"]:
                return "This deletes the workspace memory index and entries. Run /memory clear confirm to proceed."
            manager.clear()
            return "Workspace memory cleared; session history was kept."
        if command == "rebuild":
            if rest != ["confirm"]:
                return "This deletes and recreates the workspace memory index. Run /memory rebuild confirm to proceed."
            manager.clear()
            count, created = manager.initialize()
            return f"Rebuilt memory from {count} files; candidate created={'yes' if created else 'no'}."
        return "Unknown memory command. Use /memory for help."

    def _handle_context(self, args: list[str]) -> str:
        if self.session is None:
            return "No active session."
        if not args or args[0] == "status":
            manager = self.session.context_manager
            metrics = manager.last_metrics
            latest = metrics.get("request_estimated_tokens", "n/a")
            budget = metrics.get("input_budget_tokens", manager.input_budget)
            actual = metrics.get("actual_input_tokens")
            actual_text = f"; provider input={actual} tokens" if actual is not None else ""
            ratio = metrics.get("estimate_ratio")
            ratio_text = f"; provider/estimate={ratio}" if ratio is not None else ""
            return (
                f"Context compaction: {'on' if manager.config.enabled else 'off'}; "
                f"estimated request={latest}/{budget} tokens{actual_text}{ratio_text}; "
                f"system_prompt_sha256={manager.system_prompt_sha256[:12]}."
            )
        if args == ["on"]:
            self.context_enabled = True
            self.session.context_manager.set_enabled(True)
            return "Automatic context compaction enabled for this session."
        if args == ["off"]:
            self.context_enabled = False
            self.session.context_manager.set_enabled(False)
            return "Automatic compaction disabled; the hard input budget guard remains active."
        if args == ["explain"]:
            return (
                "Every model request uses the same system prompt. Context summaries, skills, and "
                "retrieved memory are placed in an untrusted application_context block. "
                "Old complete turns and oversized tool outputs are compacted in a request-only view; "
                "the raw session log remains available."
            )
        return "Usage: /context status|on|off|explain"

    def _handle_compact(self, args: list[str]) -> str:
        if self.session is None:
            return "No active session."
        if args not in ([], ["preview"]):
            return "Usage: /compact [preview]"
        try:
            metrics = self.session.context_manager.preview(self.session.messages)
        except Exception as exc:
            return f"Cannot compact this request: {exc}"
        summary = (
            f"Estimated request: {metrics['original_estimated_tokens']} -> "
            f"{metrics['request_estimated_tokens']} / {metrics['input_budget_tokens']} tokens; "
            f"old messages summarized={metrics['summarized_messages']}; "
            f"tool results pruned={metrics['pruned_tool_results']}."
        )
        if args == ["preview"]:
            return summary
        self.session.context_manager.request_compaction()
        return summary + " Compaction is scheduled for the next model request."
    def _handle_model(self, args: list[str]) -> str:
        if not args or args[0] in {"current", "show"}:
            return self._model_text(prefix="Current model")
        if args[0] == "list":
            lines: list[str] = []
            for provider in get_providers():
                models = ", ".join(f"{model.id} ({model.base_url})" for model in get_models(provider))
                lines.append(f"{provider}: {models}")
            return "\n".join(lines)
        if args[0] == "set":
            if len(args) not in {3, 4}:
                return "Usage: /model set <provider> <model_id> [base_url]"
            provider, model_id = args[1], args[2]
            base_url = args[3] if len(args) == 4 else None
            try:
                get_model(provider, model_id)
            except KeyError as exc:
                return str(exc)
            self.config.provider = provider
            self.config.model_id = model_id
            self.config.base_url = base_url
            self.session_id = None
            self.start_session()
            return f"{self._model_text(prefix='Model set to')}. Started session: {self.session_id}"
        if args[0] == "base-url":
            if len(args) != 2:
                return "Usage: /model base-url <base_url|default>"
            self.config.base_url = None if args[1] == "default" else args[1]
            self.session_id = None
            self.start_session()
            return f"{self._model_text(prefix='Base URL updated for')}. Started session: {self.session_id}"
        return "Usage: /model [current|list|set <provider> <model_id> [base_url]|base-url <base_url|default>]"

    def _usage_text(self) -> str:
        if self.session is None or self.session.last_usage is None:
            return "No usage yet."
        usage = self.session.last_usage
        return (
            f"Last usage: input={usage['input_tokens']} "
            f"output={usage['output_tokens']} total={usage['total_tokens']}"
        )

    def _tools_text(self) -> str:
        if self.session is None:
            return "No session."
        names = [tool.name for tool in self.session.agent.state.tools]
        return "Tools: " + (", ".join(names) if names else "(none)")

    def _resolve_model(self):
        model = get_model(self.config.provider, self.config.model_id)
        if self.config.base_url:
            return replace(model, base_url=self.config.base_url)
        return model

    def _model_text(self, *, prefix: str) -> str:
        base = f"{prefix}: {self.config.provider}/{self.config.model_id}"
        model = self._resolve_model()
        return f"{base} base_url={model.base_url}"

    def _on_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "tool_execution_start":
            name = event.get("toolName", "unknown")
            args = event.get("args", {})
            print(f"\n[tool] {name} {self._format_tool_args(name, args)}")
            return
        if event_type == "tool_execution_end":
            name = event.get("toolName", "unknown")
            result = event.get("result")
            is_error = bool(event.get("isError"))
            summary = self._format_tool_result(result)
            status = "error" if is_error else "ok"
            print(f"[tool:{status}] {name}{summary}")
            return
        if event_type != "message_update":
            return
        assistant_event = event.get("assistantMessageEvent")
        if not isinstance(assistant_event, dict):
            return
        if assistant_event.get("type") == "text_delta":
            print(str(assistant_event.get("delta", "")), end="", flush=True)
            self._printed_stream_text = True

    def _format_tool_args(self, name: Any, args: Any) -> str:
        if not isinstance(args, dict):
            return ""
        if name in {"read", "write", "edit"} and isinstance(args.get("path"), str):
            return args["path"]
        if name == "bash" and isinstance(args.get("command"), str):
            command = args["command"].replace("\n", "\\n")
            return command if len(command) <= 120 else command[:117] + "..."
        return json.dumps(args, ensure_ascii=False)[:160]

    def _format_tool_result(self, result: Any) -> str:
        details = getattr(result, "details", None)
        if isinstance(details, dict):
            if "bytes" in details:
                return f" ({details['bytes']} bytes)"
            if "exit_code" in details:
                return f" (exit={details['exit_code']})"
            if "diff" in details:
                diff_lines = str(details["diff"]).splitlines()
                changed = sum(1 for line in diff_lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
                return f" ({changed} changed lines)"
            if "kind" in details and "path" in details:
                return f" ({details['kind']})"
        return ""

    def _load_config(self) -> CliConfig:
        if not self.config_path.exists():
            return CliConfig()
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return CliConfig()
        if not isinstance(raw, dict):
            return CliConfig()
        return CliConfig(
            provider=str(raw.get("provider", DEFAULT_PROVIDER)),
            model_id=str(raw.get("model_id", DEFAULT_MODEL_ID)),
            base_url=str(raw["base_url"]) if raw.get("base_url") else None,
            read_only_mode=bool(raw.get("read_only_mode", True)),
        )

    def _save_config(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {
                    "provider": self.config.provider,
                    "model_id": self.config.model_id,
                    "base_url": self.config.base_url,
                    "read_only_mode": self.config.read_only_mode,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def help_text() -> str:
    return "\n".join(
        [
            "Commands:",
            "  /help                         Show this help.",
            "  /model                        Show current provider/model.",
            "  /model list                   List built-in providers, models, and default base URLs.",
            "  /model set <provider> <id> [base_url]",
            "                                Save model config and start a new session.",
            "  /model base-url <url|default> Override or reset base URL for current model.",
            "  /tools                        Show enabled tools.",
            "  /memory <command>             Manage opt-in workspace memory.",
            "  /context status|on|off         Inspect or control context compaction.",
            "  /compact [preview]             Preview or schedule context compaction.",
            "  /new                          Start a new session with current model.",
            "  /usage                        Show last model usage.",
            "  /clear                        Clear current messages and context checkpoints.",
            "  /exit                         Exit the CLI.",
        ]
    )


def _assistant_text(message: AssistantMessage) -> str:
    return "".join(block.text for block in message.content if isinstance(block, TextContent))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="momoclaw", description="MomoClaw interactive coding-agent CLI")
    parser.add_argument("--workspace", default=".", help="Workspace directory. Defaults to current directory.")
    parser.add_argument("--provider", help="Provider name, for example openai or anthropic.")
    parser.add_argument("--model", dest="model_id", help="Model id for the selected provider.")
    parser.add_argument("--base-url", help="Override the configured model base URL.")
    parser.add_argument("--session", dest="session_id", help="Existing session id to resume.")
    parser.add_argument("--read-write", action="store_true", help="Enable non-read-only MVP tools such as bash.")
    parser.add_argument("--skill", dest="skill_names", action="append", default=[], help="Load a workspace skill by name.")
    parser.add_argument("--memory", action="store_true", help="Enable verified workspace memory for this CLI session.")
    parser.add_argument("--context", action="store_true", help="Enable automatic context compaction for this CLI session.")
    return parser


async def async_main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cli = MomoClawCli(
        workspace_dir=args.workspace,
        provider=args.provider,
        model_id=args.model_id,
        base_url=args.base_url,
        read_only_mode=not args.read_write,
        session_id=args.session_id,
        skill_names=args.skill_names,
        memory_enabled=args.memory,
        context_enabled=args.context,
    )
    await cli.run()


def main(argv: list[str] | None = None) -> None:
    asyncio.run(async_main(argv))


if __name__ == "__main__":
    main()
