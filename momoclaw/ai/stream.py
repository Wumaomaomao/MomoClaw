from __future__ import annotations

from .event_stream import AssistantMessageEventStream
from .providers import register_builtins as _register_builtins  # noqa: F401
from .registry import get_api_provider
from .types import AssistantMessage, Context, Model, SimpleStreamOptions, StreamOptions, ThinkingLevel


def _resolve_provider(api: str):
    provider = get_api_provider(api)
    if provider is None:
        raise RuntimeError(f"No API provider registered for api: {api}")
    return provider


def stream(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessageEventStream:
    return _resolve_provider(model.api).stream(model, context, options)


async def complete(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
    return await stream(model, context, options).result()


def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
    *,
    reasoning: ThinkingLevel | None = None,
) -> AssistantMessageEventStream:
    effective_options = options or SimpleStreamOptions()
    if reasoning is not None:
        effective_options.reasoning = reasoning
    return _resolve_provider(model.api).stream_simple(model, context, effective_options)


async def complete_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
    *,
    reasoning: ThinkingLevel | None = None,
) -> AssistantMessage:
    return await stream_simple(model, context, options, reasoning=reasoning).result()

