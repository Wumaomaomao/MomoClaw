"""Local, source-backed workspace memory for coding-agent sessions."""

from .manager import WorkspaceMemoryManager
from .models import MemoryEntry, SourceRef
from .retrieval import MemoryRetriever, build_memory_context, estimate_tokens
from .scanner import WorkspaceScanner
from .store import MemoryStore, workspace_id

__all__ = [
    "MemoryEntry",
    "MemoryRetriever",
    "MemoryStore",
    "SourceRef",
    "WorkspaceMemoryManager",
    "WorkspaceScanner",
    "build_memory_context",
    "estimate_tokens",
    "workspace_id",
]