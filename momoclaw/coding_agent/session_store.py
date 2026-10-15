from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from momoclaw.ai.types import AssistantMessage, Message, TextContent, ToolCall, ToolResultMessage

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
        self.context_dir = self.session_dir / "context"
        self.checkpoints_dir = self.context_dir / "checkpoints"
        self.manifest_file = self.context_dir / "manifest.json"
        self._sequence = self._last_sequence()

    def ensure_initialized(self, *, model_id: str, provider: str, system_prompt: str) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        current = self.read_meta()
        if current is None:
            self.write_meta({
                "session_id": self.session_id,
                "model_id": model_id,
                "provider": provider,
                "system_prompt": system_prompt,
                "system_prompt_sha256": prompt_hash,
                "configuration_epoch": 1,
            })
        elif (
            current.get("system_prompt_sha256") != prompt_hash
            or current.get("model_id") != model_id
            or current.get("provider") != provider
        ):
            self.write_meta({
                **current,
                "model_id": model_id,
                "provider": provider,
                "system_prompt": system_prompt,
                "system_prompt_sha256": prompt_hash,
                "configuration_epoch": int(current.get("configuration_epoch", 1)) + 1,
            })

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
        _atomic_json(self.meta_file, meta)

    def append_messages(self, messages: list[Message]) -> None:
        if not messages:
            return
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.messages_file.open("a", encoding="utf-8") as handle:
            for message in messages:
                self._sequence += 1
                record = {
                    "schema_version": 2,
                    "message_id": uuid.uuid4().hex,
                    "sequence": self._sequence,
                    "run_id": self.session_id,
                    "message": message_to_dict(message),
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def load_messages(self) -> list[Message]:
        if not self.messages_file.exists():
            return []
        messages: list[Message] = []
        for line in self.messages_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    continue
                record = raw.get("message") if raw.get("schema_version") == 2 else raw
                if isinstance(record, dict):
                    messages.append(message_from_dict(record))
            except Exception:
                continue
        return messages

    def recover_pending_tool_calls(self, messages: list[Message]) -> list[ToolResultMessage]:
        pending: dict[str, ToolCall] = {}
        for message in messages:
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolCall):
                        pending[block.id] = block
            elif isinstance(message, ToolResultMessage):
                pending.pop(message.tool_call_id, None)
        recovered = [
            ToolResultMessage(
                tool_call_id=call_id,
                tool_name=call.name,
                content=[
                    TextContent(
                        text="Previous process stopped before this tool result was recorded. "
                        "The tool was not rerun; inspect its side effects before continuing."
                    )
                ],
                is_error=True,
            )
            for call_id, call in pending.items()
        ]
        if recovered:
            self.append_messages(recovered)
        return recovered
    def clear_messages(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.messages_file.open("w", encoding="utf-8") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence = 0
        if self.checkpoints_dir.exists():
            for path in self.checkpoints_dir.glob("checkpoint-*.json"):
                path.unlink()
        if self.manifest_file.exists():
            self.manifest_file.unlink()

    def write_context_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        current = 0
        for path in self.checkpoints_dir.glob("checkpoint-*.json"):
            try:
                current = max(current, int(path.stem.split("-")[-1]))
            except ValueError:
                continue
        number = current + 1
        payload = {
            "schema_version": 1,
            "session_id": self.session_id,
            "checkpoint_id": f"checkpoint-{number:06d}",
            **checkpoint,
        }
        path = self.checkpoints_dir / f"checkpoint-{number:06d}.json"
        _atomic_json(path, payload)
        _atomic_json(self.manifest_file, {
            "schema_version": 1,
            "session_id": self.session_id,
            "latest_checkpoint": path.name,
            "system_prompt_sha256": checkpoint.get("system_prompt_sha256"),
        })

    def latest_context_checkpoint(self) -> dict[str, Any] | None:
        try:
            manifest = json.loads(self.manifest_file.read_text(encoding="utf-8"))
            name = manifest.get("latest_checkpoint")
            if not isinstance(name, str) or Path(name).name != name:
                return None
            checkpoint = json.loads((self.checkpoints_dir / name).read_text(encoding="utf-8"))
            return checkpoint if isinstance(checkpoint, dict) else None
        except Exception:
            return None

    def append_event(self, event: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        serializable = _to_jsonable(event)
        with self.events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(serializable, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _last_sequence(self) -> int:
        if not self.messages_file.exists():
            return 0
        last = 0
        for line in self.messages_file.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
                if isinstance(raw, dict) and raw.get("schema_version") == 2:
                    last = max(last, int(raw.get("sequence", 0)))
                else:
                    last += 1
            except Exception:
                continue
        return last


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


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
