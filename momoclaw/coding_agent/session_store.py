from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from momoclaw.ai.types import Message

from .serde import message_from_dict, message_to_dict


def new_session_id() -> str:
    return f"session_{uuid.uuid4().hex[:12]}"


class SessionStore:
    def __init__(self, workspace_dir: str | Path, session_id: str) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.session_id = session_id
        self.session_dir = self.workspace_dir / ".momoclaw" / "sessions" / session_id
        self.meta_file = self.session_dir / "meta.json"
        self.messages_file = self.session_dir / "messages.jsonl"
        self.events_file = self.session_dir / "events.jsonl"

    def ensure_initialized(self, *, model_id: str, provider: str, system_prompt: str) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        if not self.meta_file.exists():
            self.write_meta({"session_id": self.session_id, "model_id": model_id, "provider": provider, "system_prompt": system_prompt})

    def read_meta(self) -> dict[str, Any] | None:
        if not self.meta_file.exists():
            return None
        try:
            raw = json.loads(self.meta_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def write_meta(self, meta: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_messages(self, messages: list[Message]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.messages_file.open("a", encoding="utf-8") as handle:
            for message in messages:
                handle.write(json.dumps(message_to_dict(message), ensure_ascii=False) + "\n")

    def load_messages(self) -> list[Message]:
        if not self.messages_file.exists():
            return []
        messages: list[Message] = []
        for line in self.messages_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if isinstance(raw, dict):
                    messages.append(message_from_dict(raw))
            except Exception:
                continue
        return messages

    def append_event(self, event: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        serializable = _to_jsonable(event)
        with self.events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(serializable, ensure_ascii=False) + "\n")


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("role") in {"user", "assistant", "toolResult"}:
            return value
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "role"):
        return message_to_dict(value)
    if hasattr(value, "__dict__"):
        return {k: _to_jsonable(v) for k, v in value.__dict__.items()}
    return value

