# Workspace Memory

MomoClaw can keep a small, local project index between coding sessions. Memory
is opt-in and stored under <workspace>/.momoclaw/memory/; it does not share
session history.

## Quick start

~~~bash
uv run momoclaw --workspace /path/to/project --memory
~~~

Inside the CLI:

~~~text
/memory init
/memory list candidate
/memory show <id>
/memory approve <id>
/memory search authentication
/memory add src/auth.py | Auth flow | Entry point | Authentication starts in ...
/memory status
~~~

Initialization scans a bounded set of text files, writes a human-readable
pages/overview.md, and creates an overview entry as a candidate. Candidates
are not injected. After reviewing its sources, approve it to make it retrievable.
A new session in the same workspace can then retrieve verified entries.

The commands /memory on and /memory off toggle retrieval for the current CLI
process. /memory refresh checks the source hashes for verified entries and marks
changed or missing sources stale. Stale entries are excluded. /memory clear
confirm removes only the memory directory. /memory rebuild confirm recreates it
from the current workspace.

## Storage and safety

- JSON entries are authoritative; writes use temporary files followed by atomic
  replacement and a process-level file lock. The workspace index and pages live
  under .momoclaw/memory/.
- Memory is local to the canonical workspace path. Source references are
  workspace-relative and resolved paths must remain inside the workspace.
- Scanning skips .git, .momoclaw, virtual environments, build output,
  symlinks, .env files, sensitive-looking filenames, ignored paths, binary
  files, and files outside count/size limits. .gitignore support is a simple
  conservative pattern matcher, not a full Git implementation.
- README excerpts are scrubbed for common credential assignments and key
  formats. Manual candidate text rejects common credential assignments.
  Do not put secrets in project documentation.
- Facts require a source reference. Approval rechecks every source hash.
  Candidate, stale, and rejected entries are never injected.
- Retrieved text is explicitly labeled as untrusted project data. It cannot
  grant tool permissions or override the agent's existing rules.
- Retrieval uses lexical overlap and a path boost, with a conservative byte
  based token estimate and a default 1200-token budget.

## Current limits

This first version does not summarize arbitrary source code with a model, learn
from task traces, automatically approve entries, or integrate with IM sessions.
Initialization creates a deterministic file inventory and a bounded README
excerpt. Practice entries and future extraction workflows can be added on top
of the store API.