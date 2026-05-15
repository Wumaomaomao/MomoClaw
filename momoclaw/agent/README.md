# MomoClaw Agent Core

`momoclaw.agent` owns the provider/tool execution loop. It does not know about
workspace files, IM platforms, or CLI rendering. Those concerns belong to upper
layers.

## Responsibilities

- Accept user prompts and existing conversation context.
- Call the AI layer through `stream_simple`.
- Mirror assistant stream updates as agent events.
- Execute requested tools sequentially.
- Append `ToolResultMessage` values and continue the model loop until no tool
  calls remain.

## Public Surface

- `Agent`: stateful wrapper with `prompt`, `prompt_messages`, `continue_run`,
  `subscribe`, and `set_messages`.
- `run_agent_loop`: stateless function for a new user prompt.
- `run_agent_loop_continue`: stateless function for resuming from a context that
  already ends in a user/tool-result message.
- `AgentTool`: executable tool definition exposed to the model as an AI-layer
  `Tool`.

## Events

The current MVP emits dictionary events with these `type` values:

- `agent_start`
- `turn_start`
- `message_start`
- `message_update`
- `message_end`
- `tool_execution_start`
- `tool_execution_update`
- `tool_execution_end`
- `turn_end`
- `agent_end`

`message_update` includes the underlying AI stream event in
`assistantMessageEvent`.

## Tool Execution

Tool execution is sequential in the MVP. Before a tool runs, the loop performs a
small JSON Schema check for `required` fields and primitive `type` constraints.
Full JSON Schema validation can be added later if needed.

Tool failures are converted into `ToolResultMessage(is_error=True)` so the model
can observe and recover from the failure in the next turn.

## Testing

Tests use fake stream functions instead of live providers. This keeps agent-loop
behavior deterministic and independent from API keys.
