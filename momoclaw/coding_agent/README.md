# MomoClaw Coding Agent

momoclaw.coding_agent is the application layer around momoclaw.agent. It
adds coding-specific prompts, workspace tools, session persistence, optional
workspace memory, skills, and request-view context management.

## Current Features

- AgentSession: long-lived session wrapper.
- SessionStore: file-backed metadata, append-only messages/events, and context checkpoints.
- Built-in read tool with optional offset and limit.
- Built-in write, edit, and bash tools when read_only_mode=False.
- Opt-in source-backed Workspace Memory, with reviewable candidates and stale-source checks.
- Workspace skill loading and an optional skill tool.
- Fixed system prompt per session; context compaction changes only the provider request view.

## Context Management

The session checks estimated input tokens before every provider request. The
hard input budget guard is always active; automatic compaction is opt-in with
context_enabled=True or CLI --context. Dynamic skills and retrieved memory are added
as untrusted application-context data in a user message. The system prompt and
tool definitions stay fixed during the session.

Older complete turns can be represented by structured, verbatim excerpts with
references to their positions in the raw message history. Large tool results
are shortened only in the request copy. The stored message log keeps the
original content. The estimate is conservative and may differ from the
provider's tokenizer.

CLI commands:

- /context status|on|off|explain shows metrics, toggles automatic compaction, or explains the policy.
- /compact preview estimates the compacted request without saving it.
- /compact schedules a compacted request on the next model call.

See the context management plan at ../../docs/CONTEXT_COMPACTION_PLAN.md for
the implementation details and current limitations.

## Workspace Memory

Workspace memory is opt-in. Pass memory_enabled=True to
CreateAgentSessionOptions, or start the CLI with --memory. Use /memory init to create a
candidate workspace overview, inspect it with /memory show <id>, and approve it with
/memory approve <id> before it can be retrieved. Retrieved entries are tied to source
files and excluded when those sources change. See
../../docs/WORKSPACE_MEMORY.md for commands, storage, scanning limits, and safety
boundaries.

## Storage Layout

Sessions are stored under the workspace:

~~~text
.momoclaw/
  sessions/
    session_xxxxxxxx/
      meta.json
      messages.jsonl
      events.jsonl
      context/
        manifest.json
        checkpoints/
          checkpoint-000001.json
~~~

messages.jsonl supports both legacy raw-message records and schema-versioned
records. Reopening the same session_id restores original messages. /clear removes the
message log and context checkpoints for that session.

## Usage

~~~python
from momoclaw.coding_agent import CreateAgentSessionOptions, create_agent_session

session = create_agent_session(
    CreateAgentSessionOptions(
        workspace_dir=".",
        provider="openai",
        model_id="gpt-4.1-mini",
        read_only_mode=True,
        context_enabled=True,
        memory_enabled=True,
    )
)

await session.prompt("Read README.md and summarize it.")
~~~
