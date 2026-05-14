from __future__ import annotations

from ..registry import ApiProvider, clear_api_providers, register_api_provider
from .anthropic_provider import stream_anthropic, stream_simple_anthropic
from .openai_provider import stream_openai, stream_simple_openai


def register_builtin_api_providers() -> None:
    register_api_provider(
        ApiProvider(
            api="openai-completions",
            stream=stream_openai,
            stream_simple=stream_simple_openai,
            source_id="builtin",
        )
    )
    register_api_provider(
        ApiProvider(
            api="anthropic-messages",
            stream=stream_anthropic,
            stream_simple=stream_simple_anthropic,
            source_id="builtin",
        )
    )


def reset_api_providers() -> None:
    clear_api_providers()
    register_builtin_api_providers()


register_builtin_api_providers()

