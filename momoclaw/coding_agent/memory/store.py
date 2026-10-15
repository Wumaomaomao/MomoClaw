from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Iterable

from .models import MemoryEntry

_LOCK = threading.RLock()


def workspace_id(root: str | Path) -> str:
    import hashlib
    canonical = os.path.normcase(str(Path(root).resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryStore:
    def __init__(self, workspace_dir: str | Path) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.root = self.workspace_dir / ".momoclaw" / "memory"
        self.entries_dir = self.root / "entries"
        self.pages_dir = self.root / "pages"
        self.index_file = self.root / "index.json"
        self.lock_file = self.root.parent / "memory.lock"
        self.workspace_id = workspace_id(self.workspace_dir)

    def ensure(self) -> None:
        self._assert_storage_safe()
        with _LOCK, self._file_lock():
            self.entries_dir.mkdir(parents=True, exist_ok=True)
            self.pages_dir.mkdir(parents=True, exist_ok=True)
            if not self.index_file.exists():
                self._atomic_json(self.index_file, {"schema_version": 1, "workspace_id": self.workspace_id, "entries": []})

    def list_entries(self) -> list[MemoryEntry]:
        self._assert_storage_safe()
        if not self.root.exists():
            return []
        out: list[MemoryEntry] = []
        for path in sorted(self.entries_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    entry = MemoryEntry.from_dict(raw)
                    if entry.workspace_id == self.workspace_id:
                        out.append(entry)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        return out

    def get(self, entry_id: str) -> MemoryEntry | None:
        self._assert_storage_safe()
        if not entry_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in entry_id):
            return None
        path = self.entries_dir / f"{entry_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entry = MemoryEntry.from_dict(raw)
            return entry if entry.workspace_id == self.workspace_id else None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def save(self, entry: MemoryEntry) -> None:
        if entry.workspace_id != self.workspace_id:
            raise ValueError("memory entry belongs to a different workspace")
        if entry.type == "fact" and not entry.source_refs:
            raise ValueError("fact entries require at least one source reference")
        if not entry.id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in entry.id):
            raise ValueError("invalid memory entry id")
        if entry.status == "verified":
            if not entry.source_refs:
                raise ValueError("verified entries require source references")
            for source_ref in entry.source_refs:
                source_path = (self.workspace_dir / source_ref.path).resolve()
                try:
                    source_path.relative_to(self.workspace_dir)
                    data = source_path.read_bytes()
                except (OSError, ValueError):
                    raise ValueError("verified entry source is missing or outside workspace") from None
                if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != source_ref.content_sha256:
                    raise ValueError("verified entry source hash does not match current file")
        self.ensure()
        with _LOCK, self._file_lock():
            self._atomic_json(self.entries_dir / f"{entry.id}.json", entry.to_dict())
            self._write_index()

    def save_many(self, entries: Iterable[MemoryEntry]) -> None:
        for entry in entries:
            self.save(entry)

    def write_page(self, relative_name: str, text: str) -> None:
        self._assert_storage_safe()
        target = (self.pages_dir / relative_name).resolve()
        if not target.is_relative_to(self.pages_dir.resolve()) or not target.is_relative_to(self.workspace_dir):
            raise ValueError("memory page path escapes its directory")
        with _LOCK, self._file_lock():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, target)

    def clear(self) -> None:
        self._assert_storage_safe()
        import shutil
        with _LOCK, self._file_lock():
            if self.root.exists():
                shutil.rmtree(self.root)

    def _assert_storage_safe(self) -> None:
        root = self.root.resolve()
        if not root.is_relative_to(self.workspace_dir):
            raise ValueError("memory storage path escapes the workspace")
        for child in (self.entries_dir, self.pages_dir):
            if not child.resolve().is_relative_to(root):
                raise ValueError("memory subdirectory escapes the memory store")
        if not self.lock_file.resolve().is_relative_to(self.workspace_dir):
            raise ValueError("memory lock path escapes the workspace")

    @staticmethod
    def _file_lock_for_path(path: Path):
        import contextlib

        @contextlib.contextmanager
        def acquire():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a+b") as handle:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        yield
                    finally:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        return acquire()

    def _file_lock(self):
        return self._file_lock_for_path(self.lock_file)

    def _write_index(self) -> None:
        entries = self.list_entries()
        self._atomic_json(
            self.index_file,
            {"schema_version": 1, "workspace_id": self.workspace_id, "entries": [e.id for e in entries]},
        )

    @staticmethod
    def _atomic_json(path: Path, raw: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)