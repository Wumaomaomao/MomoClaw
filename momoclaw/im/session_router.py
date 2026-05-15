from __future__ import annotations

import json
import uuid
from pathlib import Path


class SessionRouter:
    """Persistently maps an IM channel/thread to a coding-agent session id."""

    def __init__(self, workspace_dir: str | Path) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.state_file = self.workspace_dir / ".momoclaw" / "im" / "session_map.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def get_or_create_session_id(self, *, platform: str, channel_id: str, thread_id: str | None = None) -> str:
        key = self._build_key(platform=platform, channel_id=channel_id, thread_id=thread_id)
        state = self._read_state()
        existing = state.get(key)
        if existing:
            return existing
        session_id = self._new_session_id()
        state[key] = session_id
        self._write_state(state)
        return session_id

    def get_session_id(self, *, platform: str, channel_id: str, thread_id: str | None = None) -> str | None:
        key = self._build_key(platform=platform, channel_id=channel_id, thread_id=thread_id)
        return self._read_state().get(key)

    def rotate_session_id(self, *, platform: str, channel_id: str, thread_id: str | None = None) -> str:
        key = self._build_key(platform=platform, channel_id=channel_id, thread_id=thread_id)
        state = self._read_state()
        session_id = self._new_session_id()
        state[key] = session_id
        self._write_state(state)
        return session_id

    @staticmethod
    def _build_key(*, platform: str, channel_id: str, thread_id: str | None) -> str:
        return f"{platform}:{channel_id}:{thread_id}" if thread_id else f"{platform}:{channel_id}"

    @staticmethod
    def _new_session_id() -> str:
        return f"im_{uuid.uuid4().hex[:12]}"

    def _read_state(self) -> dict[str, str]:
        if not self.state_file.exists():
            return {}
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}

    def _write_state(self, state: dict[str, str]) -> None:
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
