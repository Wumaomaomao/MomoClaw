from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal
import uuid

MemoryType = Literal["fact", "practice"]
MemoryStatus = Literal["candidate", "verified", "stale", "rejected"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SourceRef:
    path: str
    content_sha256: str
    symbol: str | None = None
    line_range: tuple[int, int] | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "SourceRef":
        lines = raw.get("line_range")
        return cls(
            path=str(raw["path"]),
            content_sha256=str(raw["content_sha256"]),
            symbol=str(raw["symbol"]) if raw.get("symbol") else None,
            line_range=tuple(lines) if isinstance(lines, list) and len(lines) == 2 else None,
        )


@dataclass
class MemoryEntry:
    workspace_id: str
    type: MemoryType
    title: str
    summary: str
    body: str
    source_refs: list[SourceRef]
    status: MemoryStatus = "candidate"
    confidence: str = "source-backed"
    tags: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"entry-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    verified_at: str | None = None

    def to_dict(self) -> dict:
        raw = asdict(self)
        raw["source_refs"] = [asdict(ref) for ref in self.source_refs]
        return raw

    @classmethod
    def from_dict(cls, raw: dict) -> "MemoryEntry":
        kind = raw.get("type")
        status = raw.get("status", "candidate")
        if kind not in {"fact", "practice"} or status not in {"candidate", "verified", "stale", "rejected"}:
            raise ValueError("invalid memory entry type or status")
        return cls(
            id=str(raw["id"]),
            workspace_id=str(raw["workspace_id"]),
            type=kind,
            title=str(raw["title"]),
            summary=str(raw.get("summary", "")),
            body=str(raw.get("body", "")),
            source_refs=[SourceRef.from_dict(ref) for ref in raw.get("source_refs", []) if isinstance(ref, dict)],
            status=status,
            confidence=str(raw.get("confidence", "source-backed")),
            tags=[str(tag) for tag in raw.get("tags", [])],
            created_at=str(raw.get("created_at", utc_now())),
            updated_at=str(raw.get("updated_at", utc_now())),
            verified_at=str(raw["verified_at"]) if raw.get("verified_at") else None,
        )