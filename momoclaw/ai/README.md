# MomoClaw AI Layer

`momoclaw.ai` is the provider boundary for MomoClaw. Its job is to hide
OpenAI/Anthropic protocol differences behind one message model, one streaming
event protocol, and one dispatch API. The coding agent and IM layers should
depend on this package instead of importing provider SDKs directly.

## Module Map

- `types.py`: stable dataclasses for messages, content blocks, tools, models,
  usage, and stream options.
- `event_stream.py`: async stream container used by all providers. Consumers can
  `async for` events and also await `result()` for the final assistant message.
- `registry.py`: `api -> ApiProvider` registry. `api` means wire protocol, such
  as `openai-completions` or `anthropic-messages`.
- `models.py`: built-in model table and runtime model registration.
- `stream.py`: public dispatch functions: `stream`, `complete`,
  `stream_simple`, and `complete_simple`.
- `env.py`: environment-variable API key lookup.
- `providers/`: built-in provider implementations and provider-specific message
  conversion helpers.

## Core Concepts

`Model.api` and `Model.provider` are intentionally separate.

- `api` selects request/stream parsing logic. Example: `openai-completions`.
- `provider` selects credentials/default provider config. Example: `openai`.
- `base_url` points at the official API or a compatible gateway.

This lets a model use an OpenAI-compatible protocol while still being a custom
provider with its own API key and endpoint.

## Message Model

The normalized conversation model is:

- `UserMessage`: user input, either plain text or `TextContent`/`ImageContent`
  blocks.
- `AssistantMessage`: model output containing `TextContent`,
  `ThinkingContent`, and `ToolCall` blocks.
- `ToolResultMessage`: tool execution result returned to the model.

Providers must convert their SDK-specific payloads into these types before
returning events to upper layers.

## Stream Event Contract

All provider streams emit the same event names:

- `start`
- `text_start`, `text_delta`, `text_end`
- `thinking_start`, `thinking_delta`, `thinking_end`
- `toolcall_start`, `toolcall_delta`, `toolcall_end`
- `done`
- `error`

`partial` on streaming events is the currently assembled `AssistantMessage`.
`done.message` and `await stream.result()` must return the final
`AssistantMessage`.

Provider failures should be represented as an `AssistantMessage` with
`stop_reason="error"` and `error_message` set. Provider implementations should
not leak SDK exceptions directly to normal callers after a stream has been
created.

## Public Usage

```python
from momoclaw.ai import Context, UserMessage, complete_simple, get_model

model = get_model("openai", "gpt-4.1-mini")
context = Context(messages=[UserMessage(content="Say hello in one sentence.")])

message = await complete_simple(model, context)
```

For streaming:

```python
from momoclaw.ai import Context, UserMessage, get_model, stream_simple

model = get_model("anthropic", "claude-sonnet-4-5")
events = stream_simple(
    model,
    Context(messages=[UserMessage(content="Draft a short plan.")]),
    reasoning="low",
)

async for event in events:
    if event["type"] == "text_delta":
        print(event["delta"], end="")

final_message = await events.result()
```

## Tool Calling

Tools are described with JSON Schema parameters:

```python
from momoclaw.ai import Context, Tool, UserMessage

tool = Tool(
    name="read",
    description="Read a file from the workspace.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)

context = Context(messages=[UserMessage(content="Read README.md")], tools=[tool])
```

The provider emits `ToolCall` blocks. Tool execution belongs in the future
`agent_core` layer, which should append `ToolResultMessage` instances and then
call the AI layer again.

## Provider Registration

Register a new protocol implementation through `registry.py`:

```python
from momoclaw.ai.registry import ApiProvider, register_api_provider

register_api_provider(
    ApiProvider(
        api="my-api",
        stream=stream_my_api,
        stream_simple=stream_simple_my_api,
        source_id="my-extension",
    )
)
```

The registry wraps providers and verifies that `model.api` matches the
registered API before dispatching. Use `unregister_api_providers(source_id)` to
remove extension-provided implementations.

## Adding Models

For a model that uses an existing protocol:

```python
from momoclaw.ai.models import register_model
from momoclaw.ai.types import Model

register_model(
    "local-openai",
    Model(
        id="local-model",
        name="Local Model",
        api="openai-completions",
        provider="local-openai",
        base_url="http://localhost:1234/v1",
        reasoning=False,
        input=["text"],
        context_window=128_000,
        max_tokens=4096,
    ),
)
```

Set `LOCAL_OPENAI_API_KEY` for this example. `env.py` maps unknown provider
names to `{PROVIDER}_API_KEY` after uppercasing and replacing `-` with `_`.

## Built-in Providers

Current MVP providers:

- `openai-completions`: official `openai` Python SDK, Chat Completions streaming.
- `anthropic-messages`: official `anthropic` Python SDK, Messages API streaming.

Built-in provider/model entries:

- `openai/gpt-4.1`
- `openai/gpt-4.1-mini`
- `anthropic/claude-sonnet-4-5`
- `deepseek/deepseek-v4-flash`
- `deepseek/deepseek-v4-pro`

DeepSeek uses the OpenAI-compatible provider with `base_url=https://api.deepseek.com`
and reads `DEEPSEEK_API_KEY`. DeepSeek thinking mode is disabled by default in
MomoClaw requests; when `SimpleStreamOptions.reasoning` is set, the provider
sends DeepSeek's `extra_body={"thinking": {"type": "enabled"}}` and maps
`xhigh` to `reasoning_effort=max`, other levels to `high`.

Supported MVP features:

- text streaming
- image input conversion
- tool definition conversion
- tool call streaming/aggregation
- basic reasoning/thinking block normalization
- token usage where the provider returns it
- custom `base_url`, headers, timeout, max retries, metadata, payload hook, and
  response hook

## Current Limits

- No real network tests are checked in. Unit tests cover registration and message
  conversion only.
- OpenAI Responses API is not implemented yet.
- Anthropic thinking signature replay is stored in the type model but not fully
  exercised by an agent loop yet.
- Cost calculation is represented in `Usage.cost` but not populated by provider
  code.
- `max_retry_delay_seconds` is reserved for higher-level retry policy and is not
  enforced by the SDK providers yet.

## Tests

Run:

```bash
uv run pytest tests
```

The current tests are in `tests/test_ai_layer.py`.
