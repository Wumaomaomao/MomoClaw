from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .event_stream import AssistantMessageEventStream
from .types import Context, Model, SimpleStreamOptions, StreamOptions

StreamFn = Callable[[Model, Context, StreamOptions | None], AssistantMessageEventStream]
SimpleStreamFn = Callable[[Model, Context, SimpleStreamOptions | None], AssistantMessageEventStream]


@dataclass(frozen=True)
class ApiProvider:
    api: str
    stream: StreamFn
    stream_simple: SimpleStreamFn
    source_id: str | None = None


_REGISTRY: dict[str, ApiProvider] = {}


def _wrap_stream(api: str, fn: StreamFn) -> StreamFn:
    def _wrapped(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessageEventStream:
        if model.api != api:
            raise ValueError(f"Mismatched api: {model.api} expected {api}")
        return fn(model, context, options)

    return _wrapped


def _wrap_stream_simple(api: str, fn: SimpleStreamFn) -> SimpleStreamFn:
    def _wrapped(
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        if model.api != api:
            raise ValueError(f"Mismatched api: {model.api} expected {api}")
        return fn(model, context, options)

    return _wrapped


def register_api_provider(provider: ApiProvider, source_id: str | None = None) -> None:
    resolved_source_id = source_id if source_id is not None else provider.source_id
    _REGISTRY[provider.api] = ApiProvider(
        api=provider.api,
        stream=_wrap_stream(provider.api, provider.stream),
        stream_simple=_wrap_stream_simple(provider.api, provider.stream_simple),
        source_id=resolved_source_id,
    )


def get_api_provider(api: str) -> ApiProvider | None:
    return _REGISTRY.get(api)


def get_api_providers() -> list[ApiProvider]:
    return list(_REGISTRY.values())


def unregister_api_providers(source_id: str) -> None:
    for api, provider in list(_REGISTRY.items()):
        if provider.source_id == source_id:
            del _REGISTRY[api]


def clear_api_providers() -> None:
    _REGISTRY.clear()

