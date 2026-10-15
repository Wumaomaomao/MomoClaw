# Workspace Memory Implementation Notes

## Phase 0 audit

- The CLI creates sessions through CreateAgentSessionOptions and
  create_agent_session in momoclaw/cli.py and
  momoclaw/coding_agent/factory.py.
- AgentSession.prompt and prompt_message run immediately before the agent loop.
  The session can temporarily replace Agent.state.system_prompt and restores it
  in a finally block. This supports per-task retrieval without appending memory
  to the conversation or duplicating it in later turns.
- Factory builds the baseline prompt and tools. The memory feature does not add
  a tool and does not change the generic agent core.
- SessionStore persists messages under .momoclaw/sessions/<session-id>/. Memory
  uses a separate .momoclaw/memory directory and does not modify session logs.
- CLI slash commands dispatch in MomoClawCli.handle_command. --memory and
  /memory on/off are explicit opt-ins. /new keeps the current CLI memory toggle.

## Implemented scope

- Local JSON store and readable overview page with atomic replacement.
- Bounded scanner, common ignore rules, sensitive filename exclusions, README
  excerpt scrubbing, workspace-contained paths, and source SHA-256 references.
- Candidate creation, manual candidate add, approval, stale detection, lexical
  retrieval, prompt injection with a token budget, and clear/rebuild commands.
- Fake-provider tests cover session injection and isolation without API keys.

## Compatibility and limitations

- Existing sessions and ordinary CLI behavior keep memory disabled by default.
- The supplied workspace path defines the isolation boundary. Scanner ignore
  rules are intentionally simple and conservative; they are not full Git
  ignore semantics.
- Initialization creates a deterministic file inventory and a scrubbed README
  excerpt. It does not use an LLM to summarize arbitrary source code.
- Task-trace practice extraction, fine-grained diff invalidation, multi-process
  locking, and IM integration remain future work.