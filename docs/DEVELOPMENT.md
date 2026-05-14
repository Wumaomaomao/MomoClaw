# MomoClaw Development Guide

This document is the project-level technical plan for MomoClaw. Keep it updated
when a module is added, a phase is completed, or an architectural decision
changes.

## Project Goal

MomoClaw is a Python coding-agent plus IM integration project inspired by the
layered design of `pi-mono` and `XingClaw`.

The target shape is:

```text
IM platform message
  -> IM adapter/service
  -> long-lived coding agent session
  -> agent core loop
  -> unified AI provider layer
  -> OpenAI / Anthropic / compatible model APIs
```

The main design rule is dependency direction: upper layers depend on lower
abstractions, while lower layers must not import application-specific code.

## Architecture Layers

### 1. AI Provider Layer

Path: `momoclaw/ai/`

Responsibility:

- Normalize provider-specific APIs into one message model.
- Provide streaming events for text, thinking, and tool calls.
- Register and dispatch provider implementations by protocol API.
- Hold built-in model metadata and runtime model registration.

Current implementation:

- `openai-completions` provider using the official `openai` SDK.
- `anthropic-messages` provider using the official `anthropic` SDK.
- Shared `Context`, `Message`, `Tool`, `Model`, `Usage`, and stream option
  dataclasses.
- Async `AssistantMessageEventStream`.
- Unit tests for registration and message/tool conversion.

Reference doc: `momoclaw/ai/README.md`.

### 2. Agent Core Layer

Path: `momoclaw/agent/`

Responsibility:

- Own the model/tool execution loop.
- Convert agent messages to AI-layer messages at the provider boundary.
- Execute tool calls and append `ToolResultMessage` results.
- Emit stable runtime events for UI/CLI/IM consumers.

Expected MVP modules:

- `types.py`: `AgentTool`, `AgentToolResult`, `AgentContext`,
  `AgentLoopConfig`, event types.
- `loop.py`: user prompt -> model stream -> tool execution -> next model turn.
- `agent.py`: stateful wrapper with `prompt`, `continue_run`, `subscribe`.

MVP constraints:

- Sequential tool execution is enough initially.
- Add parallel tool execution only after correctness is covered by tests.
- Tool calls should be validated before execution.
- Provider exceptions should already be normalized by the AI layer.

### 3. Coding Agent Application Layer

Path: `momoclaw/coding_agent/`

Responsibility:

- Build a coding-specific `AgentSession`.
- Manage workspace resources, system prompt, tools, persistence, and retries.
- Provide a stable API for CLI and IM layers.

Expected MVP modules:

- `session.py`: long-lived session object.
- `session_store.py`: JSONL or simple file-backed message persistence.
- `builtin_tools.py`: `read`, `write`, `edit`, `bash`.
- `factory.py`: create a session from workspace/model/tool options.
- `system_prompt.py`: coding-agent system prompt assembly.

MVP constraints:

- Start with read-only mode by default.
- Implement `read` and `bash` first; add mutating tools after guardrails exist.
- Keep persistence simple: session metadata plus append-only message/event log.
- Context compaction can wait until basic sessions work end-to-end.

### 4. IM Layer

Path: `momoclaw/im/`

Responsibility:

- Convert platform events into normalized incoming messages.
- Route each IM channel/thread to a long-lived coding agent session.
- Queue messages per channel to avoid concurrent mutation of a session.
- Send or update replies through platform adapters.

Expected MVP modules:

- `types.py`: `IMIncomingMessage`, `IMOutgoingText`, `IMAdapter` protocol.
- `session_router.py`: map `platform/channel/thread` to session IDs.
- `service.py`: dedup, queue, command handling, prompt session, reply.
- `memory.py`: optional global/channel memory loading.

MVP constraints:

- Start with a fake/local adapter for tests before adding Feishu or Slack.
- Control commands: `/new`, `/clear`, `/usage`.
- Streaming updates should be optional; plain reply mode should work first.

### 5. CLI / Runtime Entrypoints

Path: project root and future package modules.

Responsibility:

- Expose local development workflows.
- Provide simple smoke-test commands before full IM integration exists.

Expected MVP commands:

- list providers/models
- send one prompt to a selected model
- start an interactive coding-agent session
- replay or inspect a saved session

## Data Flow

### Plain LLM Call

```text
caller
  -> ai.stream_simple(model, context)
  -> registry resolves model.api
  -> provider SDK stream
  -> normalized stream events
  -> final AssistantMessage
```

### Coding Agent Turn

```text
UserMessage
  -> agent loop emits message_start/message_end
  -> AI provider emits assistant stream events
  -> assistant contains ToolCall blocks
  -> agent executes tools
  -> ToolResultMessage appended
  -> agent calls provider again
  -> final assistant answer
```

### IM Message

```text
platform event
  -> adapter parses IMIncomingMessage
  -> service dedups and queues by channel key
  -> session_router resolves session ID
  -> coding_agent session prompt
  -> adapter sends or updates reply
```

## Progress Tracker

Status values:

- `Done`: implemented and covered by at least a smoke/unit test.
- `In Progress`: implementation exists but needs tests, cleanup, or integration.
- `Planned`: not started.

| Area | Status | Notes |
| --- | --- | --- |
| Project scaffold | In Progress | Basic package exists; root README still needs project overview. |
| AI types and stream protocol | Done | Implemented in `momoclaw/ai/types.py` and `event_stream.py`. |
| Provider registry | Done | Built-in provider auto-registration and mismatch checks are covered. |
| OpenAI provider | In Progress | SDK provider implemented; no live API test yet. |
| Anthropic provider | In Progress | SDK provider implemented; no live API test yet. |
| AI documentation | Done | See `momoclaw/ai/README.md`. |
| Agent core | Planned | Next major implementation phase. |
| Coding agent session | Planned | Depends on agent core. |
| Built-in coding tools | Planned | Start with read-only tools. |
| IM service | Planned | Depends on coding agent session. |
| CLI entrypoints | Planned | Useful after AI layer and basic agent loop are stable. |

## Immediate Roadmap

### Phase 1: AI Layer Hardening

- Add mocked stream tests for OpenAI text/tool-call chunks.
- Add mocked stream tests for Anthropic text/thinking/tool-use events.
- Decide whether OpenAI Responses API belongs in MVP or post-MVP.
- Add cost calculation once model pricing policy is defined.
- Add a small local smoke command that performs a real API call when a key is
  present.

### Phase 2: Agent Core MVP

- Implement `AgentTool` and tool result types.
- Implement one-turn prompt flow with text-only response.
- Implement tool-call loop.
- Add event subscription.
- Add tests with a fake stream provider.

### Phase 3: Coding Agent MVP

- Implement session object around agent core.
- Add session persistence.
- Add read-only file tools.
- Add basic system prompt.
- Add retry behavior around failed turns.

### Phase 4: IM MVP

- Implement normalized IM types and local fake adapter.
- Implement per-channel queue and session routing.
- Add plain reply mode.
- Add optional streaming update mode.
- Add platform adapter after local service tests pass.

## Testing Policy

Use focused tests at the module boundary:

- AI layer: conversion, provider event normalization, registry dispatch.
- Agent core: loop behavior with fake streams and fake tools.
- Coding agent: session persistence, tool safety, retry behavior.
- IM layer: dedup, queue ordering, session routing, command handling.

Run all tests with:

```bash
uv run pytest tests
```

## Design Decisions

### AI layer owns provider SDKs

Only `momoclaw.ai.providers` should import `openai`, `anthropic`, or future
provider SDKs. The agent and IM layers consume normalized MomoClaw types.

### Protocol API is not provider identity

`api` decides request format and stream parser. `provider` decides API key and
model grouping. This supports OpenAI-compatible gateways without new agent code.

### Errors are normalized into messages

After a provider stream is created, provider failures should terminate the stream
with an assistant message whose `stop_reason` is `error`. This lets the agent
and IM layers render failures consistently.

### Documentation is part of implementation

Each major module should have a local `README.md` once its public boundary is
stable enough for another layer to depend on it. This project-level document is
the progress source of truth.
