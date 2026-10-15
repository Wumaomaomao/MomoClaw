from __future__ import annotations

from pathlib import Path
import re

from .models import MemoryEntry, SourceRef, utc_now
from .retrieval import MemoryRetriever, build_memory_context, estimate_tokens, render_entry
from .scanner import WorkspaceScanner, sha256_bytes
from .store import MemoryStore


class WorkspaceMemoryManager:
    def __init__(self, workspace_dir: str | Path, *, max_tokens: int = 1200, top_k: int = 5) -> None:
        self.store = MemoryStore(workspace_dir)
        self.scanner = WorkspaceScanner(workspace_dir)
        self.retriever = MemoryRetriever(self.store, max_tokens=max_tokens, top_k=top_k)
        self.last_metrics = {"retrieved_count": 0, "injected_count": 0, "estimated_tokens": 0, "stale_filtered_count": 0}

    def initialize(self) -> tuple[int, bool]:
        return self.scanner.initialize(self.store)

    def status(self) -> dict[str, int]:
        return self.retriever.status_counts()

    def search(self, query: str) -> tuple[list[MemoryEntry], int, int]:
        return self.retriever.search(query)

    def context_for(self, query: str) -> str:
        entries, tokens, stale = self.search(query)
        context = build_memory_context(entries, max_tokens=self.retriever.max_tokens)
        self.last_metrics = {
            "retrieved_count": len(entries),
            "injected_count": len(entries) if context else 0,
            "estimated_tokens": estimate_tokens(context) if context else 0,
            "stale_filtered_count": stale,
        }
        return context

    def list_entries(self, status: str | None = None) -> list[MemoryEntry]:
        entries = self.store.list_entries()
        return [entry for entry in entries if status is None or entry.status == status]

    def get(self, entry_id: str) -> MemoryEntry | None:
        return self.store.get(entry_id)

    def add_candidate(self, *, source_path: str, title: str, summary: str, body: str, kind: str = "fact", tags: list[str] | None = None) -> MemoryEntry:
        if kind not in {"fact", "practice"}:
            raise ValueError("memory type must be fact or practice")
        source = (self.store.workspace_dir / source_path).resolve()
        try:
            relative = source.relative_to(self.store.workspace_dir).as_posix()
            data = source.read_bytes()
        except (OSError, ValueError):
            raise ValueError("source is missing or outside workspace") from None
        if source.name.lower().startswith(".env") or any(term in part.casefold() for part in relative.split("/") for term in ("secret", "credential", "token", "private_key")):
            raise ValueError("sensitive-looking files cannot be used as memory sources")
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("source file is too large to reference")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("binary files cannot be used as memory sources") from None
        if re.search(r"(?i)(api[_-]?key|secret|token|password|credential|private[_-]?key)[A-Z0-9_]*\s*[:=]\s*\S+", " ".join((title, summary, body))):
            raise ValueError("memory body appears to contain a credential")
        entry = MemoryEntry(
            workspace_id=self.store.workspace_id,
            type=kind,
            title=title.strip(),
            summary=summary.strip(),
            body=body.strip(),
            source_refs=[SourceRef(path=relative, content_sha256=sha256_bytes(data))],
            status="candidate",
            tags=list(tags or []),
        )
        if not entry.title or not entry.summary or not entry.body:
            raise ValueError("title, summary, and body are required")
        self.store.save(entry)
        return entry

    def approve(self, entry_id: str) -> MemoryEntry:
        entry = self.store.get(entry_id)
        if entry is None:
            raise ValueError("memory entry not found")
        if entry.status != "candidate":
            raise ValueError(f"entry status is {entry.status}, expected candidate")
        if not entry.source_refs:
            raise ValueError("entry has no source references and cannot be verified")
        for source in entry.source_refs:
            path = (self.store.workspace_dir / source.path).resolve()
            try:
                path.relative_to(self.store.workspace_dir)
                current_hash = sha256_bytes(path.read_bytes())
            except (OSError, ValueError):
                raise ValueError(f"source is missing or outside workspace: {source.path}") from None
            if current_hash != source.content_sha256:
                entry.status = "stale"
                entry.updated_at = utc_now()
                self.store.save(entry)
                raise ValueError(f"source changed; entry marked stale: {source.path}")
        entry.status = "verified"
        entry.verified_at = utc_now()
        entry.updated_at = entry.verified_at
        self.store.save(entry)
        return entry

    def refresh(self) -> tuple[int, int]:
        checked, stale = self.retriever.validate()
        return checked, stale

    def clear(self) -> None:
        self.store.clear()

    def render(self, entry: MemoryEntry) -> str:
        sources = "\n".join(
            f"- {source.path} sha256={source.content_sha256}"
            + (f" lines={source.line_range[0]}-{source.line_range[1]}" if source.line_range else "")
            for source in entry.source_refs
        )
        return (
            f"ID: {entry.id}\nStatus: {entry.status}\nType: {entry.type}\nTitle: {entry.title}\n"
            f"Summary: {entry.summary}\nBody:\n{entry.body}\nSources:\n{sources or '(none)'}"
        )