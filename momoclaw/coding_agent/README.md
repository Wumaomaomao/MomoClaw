# MomoClaw Coding Agent

`momoclaw.coding_agent` is the application layer around `momoclaw.agent`. It
adds coding-specific prompts, workspace tools, and session persistence.

## MVP Scope

Implemented now:

- `AgentSession`: long-lived session wrapper.
- `SessionStore`: file-backed metadata, message log, and event log.
- `create_agent_session`: model/tool/system-prompt factory.
- Built-in `read` tool with optional `offset` and `limit`.
- Built-in `write` tool when `read_only_mode=False`.
- Built-in `edit` tool when `read_only_mode=False`, using exact unique text
  replacement.
- Built-in `bash` tool when `read_only_mode=False`, with timeout and MVP safety
  blocking for destructive commands.

Not implemented yet:

- context compaction
- retry policy
- workspace resource loading
- session branching

## Storage Layout

Sessions are stored under the workspace:

```text
.momoclaw/
  sessions/
    session_xxxxxxxx/
      meta.json
      messages.jsonl
      events.jsonl
```

The message log is append-only JSONL. Reopening the same `session_id` restores
messages from `messages.jsonl`.

## Usage

```python
from momoclaw.coding_agent import CreateAgentSessionOptions, create_agent_session

session = create_agent_session(
    CreateAgentSessionOptions(
        workspace_dir=".",
        provider="openai",
        model_id="gpt-4.1-mini",
        read_only_mode=True,
    )
)

await session.prompt("Read README.md and summarize it.")
```

To enable mutating tools:

```python
session = create_agent_session(
    CreateAgentSessionOptions(
        workspace_dir=".",
        provider="openai",
        model_id="gpt-4.1-mini",
        read_only_mode=False,
    )
)
```
