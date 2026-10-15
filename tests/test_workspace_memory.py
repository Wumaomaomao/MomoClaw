from __future__ import annotations

from pathlib import Path

import pytest

from momoclaw.coding_agent.memory import (
    MemoryEntry,
    MemoryStore,
    SourceRef,
    WorkspaceMemoryManager,
    WorkspaceScanner,
    build_memory_context,
    estimate_tokens,
)


def test_scanner_skips_secrets_gitignore_and_redacts_readme(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.txt\nprivate_dir/\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=very-secret", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "private_dir").mkdir()
    (tmp_path / "private_dir" / "secret.md").write_text("private", encoding="utf-8")
    (tmp_path / "README.md").write_text("Hello project\nAPI_KEY=very-secret\nAWS_SECRET_ACCESS_KEY=another-secret\n", encoding="utf-8")

    scanned = WorkspaceScanner(tmp_path).scan()
    paths = [path for path, _, _ in scanned]
    assert "README.md" in paths
    assert ".env" not in paths
    assert "ignored.txt" not in paths
    assert not any(path.startswith("private_dir/") for path in paths)

    manager = WorkspaceMemoryManager(tmp_path)
    manager.initialize()
    overview = (tmp_path / ".momoclaw" / "memory" / "pages" / "overview.md").read_text(encoding="utf-8")
    assert "very-secret" not in overview
    assert "another-secret" not in overview
    assert "[sensitive line redacted]" in overview


def test_initialize_is_idempotent_and_approval_checks_sources(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    manager = WorkspaceMemoryManager(tmp_path)

    assert manager.initialize() == (1, True)
    assert manager.initialize() == (1, False)
    entries = manager.list_entries()
    assert len(entries) == 1
    assert entries[0].status == "candidate"

    approved = manager.approve(entries[0].id)
    assert approved.status == "verified"
    results, _, _ = manager.search("workspace overview")
    assert [entry.id for entry in results] == [approved.id]

    (tmp_path / "README.md").write_text("# Changed project\n", encoding="utf-8")
    results, _, stale_count = manager.search("workspace overview")
    assert results == []
    assert stale_count == 1
    assert manager.get(approved.id).status == "stale"


def test_approval_rejects_missing_or_escaping_source(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    manager = WorkspaceMemoryManager(tmp_path)
    entry = MemoryEntry(
        workspace_id=store.workspace_id,
        type="fact",
        title="Outside",
        summary="No",
        body="unverified",
        source_refs=[SourceRef(path="../outside.py", content_sha256="0" * 64)],
    )
    store.save(entry)

    with pytest.raises(ValueError, match="missing or outside"):
        manager.approve(entry.id)
    entry.source_refs = []
    with pytest.raises(ValueError, match="at least one source"):
        store.save(entry)


def test_retrieval_context_respects_token_budget_and_excludes_candidates(tmp_path: Path) -> None:
    source = tmp_path / "src.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    store = MemoryStore(tmp_path)
    from momoclaw.coding_agent.memory.scanner import sha256_bytes

    entry = MemoryEntry(
        workspace_id=store.workspace_id,
        type="fact",
        title="Authentication flow",
        summary="The authenticate function is in src.py.",
        body="Use authenticate to check credentials.",
        source_refs=[SourceRef(path="src.py", content_sha256=sha256_bytes(source.read_bytes()))],
        status="verified",
        tags=["auth"],
    )
    store.save(entry)
    context = build_memory_context([entry], max_tokens=300)
    assert context
    assert estimate_tokens(context) <= 300
    assert "untrusted project data" in context

    candidate = MemoryEntry(
        workspace_id=store.workspace_id,
        type="fact",
        title="Candidate",
        summary="must not be injected",
        body="candidate",
        source_refs=entry.source_refs,
    )
    store.save(candidate)
    manager = WorkspaceMemoryManager(tmp_path)
    results, _, _ = manager.search("authentication")
    assert [item.id for item in results] == [entry.id]


def test_memory_is_workspace_scoped(tmp_path: Path) -> None:
    first = MemoryStore(tmp_path / "first")
    second = MemoryStore(tmp_path / "second")
    entry = MemoryEntry(
        workspace_id=first.workspace_id,
        type="fact",
        title="Only here",
        summary="workspace local",
        body="local fact",
        source_refs=[SourceRef(path="README.md", content_sha256="abc")],
    )
    first.save(entry)
    assert second.list_entries() == []


def test_clear_only_removes_memory_directory(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("keep", encoding="utf-8")
    (tmp_path / ".momoclaw" / "sessions" / "session_keep").mkdir(parents=True)
    manager = WorkspaceMemoryManager(tmp_path)
    manager.initialize()

    manager.clear()

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / ".momoclaw" / "sessions" / "session_keep").is_dir()
    assert not (tmp_path / ".momoclaw" / "memory").exists()

def test_add_candidate_requires_in_workspace_text_source_and_rejects_credentials(tmp_path: Path) -> None:
    (tmp_path / "architecture.md").write_text("The app uses a worker queue.", encoding="utf-8")
    manager = WorkspaceMemoryManager(tmp_path)
    entry = manager.add_candidate(
        source_path="architecture.md",
        title="Worker queue",
        summary="The app uses a worker queue.",
        body="Architecture notes describe a worker queue.",
    )
    assert entry.status == "candidate"
    assert manager.search("worker queue")[0] == []

    with pytest.raises(ValueError, match="credential"):
        manager.add_candidate(
            source_path="architecture.md",
            title="Bad",
            summary="Bad",
            body="API_KEY=do-not-save",
        )


def test_memory_storage_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    momoclaw = workspace / ".momoclaw"
    momoclaw.mkdir()
    (momoclaw / "memory").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes"):
        MemoryStore(workspace).ensure()
    assert list(outside.iterdir()) == []