from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from pathlib import Path

from .models import MemoryEntry, SourceRef
from .store import MemoryStore

MAX_FILES = 120
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 512 * 1024
ALLOWED_SUFFIXES = {".md", ".rst", ".py", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".txt"}
SKIP_DIRS = {".git", ".momoclaw", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "target", ".next"}
SKIP_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
SECRET_NAME = re.compile(r"(^\.env($|\.)|secret|credential|token|private.?key)", re.I)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkspaceScanner:
    def __init__(self, workspace_dir: str | Path) -> None:
        self.root = Path(workspace_dir).resolve()

    def scan(self) -> list[tuple[str, str, str]]:
        if not self.root.is_dir():
            raise ValueError("workspace directory does not exist")
        ignore = self._gitignore_patterns()
        found: list[tuple[str, str, str]] = []
        total = 0
        for current, dirs, files in os.walk(self.root, followlinks=False):
            dirs.sort()
            current_path = Path(current)
            dirs[:] = [
                name for name in dirs
                if name not in SKIP_DIRS
                and not SECRET_NAME.search(name)
                and not (current_path / name).is_symlink()
                and not self._ignored((current_path / name).relative_to(self.root).as_posix(), ignore, is_dir=True)
            ]
            for name in sorted(files):
                if len(found) >= MAX_FILES:
                    return found
                path = current_path / name
                rel = path.relative_to(self.root).as_posix()
                if path.is_symlink() or name in SKIP_NAMES or SECRET_NAME.search(name):
                    continue
                if path.suffix.lower() not in ALLOWED_SUFFIXES or self._ignored(rel, ignore):
                    continue
                try:
                    size = path.stat().st_size
                    if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                        continue
                    data = path.read_bytes()
                    text = data.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                total += len(data)
                found.append((rel, text, sha256_bytes(data)))
        return found

    def initialize(self, store: MemoryStore) -> tuple[int, bool]:
        files = self.scan()
        if not files:
            store.ensure()
            store.write_page("overview.md", "# Workspace overview\n\nNo eligible text files were found.\n")
            return 0, False
        store.ensure()
        existing = store.list_entries()
        overview_sources = [SourceRef(path=path, content_sha256=digest) for path, _, digest in files]
        readme = next((item for item in files if Path(item[0]).name.lower() in {"readme.md", "readme.rst", "readme.txt"}), None)
        lines = [
            "# Workspace overview",
            "",
            f"Workspace files indexed: {len(files)}.",
            "",
            "## Selected files",
            *[f"- <{path}>" for path, _, _ in files[:80]],
        ]
        if readme:
            snippet = self._safe_excerpt(readme[1])[:6000]
            lines.extend(["", f"## README excerpt ({readme[0]})", "", snippet])
        store.write_page("overview.md", "\n".join(lines) + "\n")
        if any(entry.tags and "workspace-overview" in entry.tags for entry in existing):
            return len(files), False
        source_text = f"Workspace contains {len(files)} eligible text files. Selected paths: " + ", ".join(path for path, _, _ in files[:80])
        if readme:
            source_text += f"\nREADME excerpt from {readme[0]}:\n" + self._safe_excerpt(readme[1])[:4000]
        entry = MemoryEntry(
            workspace_id=store.workspace_id,
            type="fact",
            title="Workspace overview",
            summary=f"Scanned {len(files)} eligible source and documentation files.",
            body=source_text,
            source_refs=overview_sources,
            status="candidate",
            tags=["workspace-overview"],
        )
        store.save(entry)
        return len(files), True

    @staticmethod
    def _safe_excerpt(text: str) -> str:
        secret_assignment = re.compile(r"(?i)(api[_-]?key|secret[A-Z0-9_]*|token[A-Z0-9_]*|password[A-Z0-9_]*|credential[A-Z0-9_]*|private[_-]?key[A-Z0-9_]*)\s*[:=]\s*\S+")
        api_token = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b")
        output = []
        for line in text.splitlines():
            if secret_assignment.search(line):
                output.append("[sensitive line redacted]")
            else:
                output.append(api_token.sub("[redacted]", line))
        return "\n".join(output)

    def _gitignore_patterns(self) -> list[str]:
        path = self.root / ".gitignore"
        try:
            return [
                line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#") and not line.startswith("!")
            ]
        except OSError:
            return []

    @staticmethod
    def _ignored(relative: str, patterns: list[str], *, is_dir: bool = False) -> bool:
        parts = relative.split("/")
        for pattern in patterns:
            pattern = pattern.removeprefix("/")
            directory_pattern = pattern.rstrip("/")
            if "/" not in directory_pattern and any(fnmatch.fnmatch(part, directory_pattern) for part in parts):
                return True
            if fnmatch.fnmatch(relative, directory_pattern):
                return True
            if is_dir and fnmatch.fnmatch(relative + "/", pattern):
                return True
        return False