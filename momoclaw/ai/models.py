from __future__ import annotations

from .types import Model

_MODELS: dict[str, dict[str, Model]] = {
    "openai": {
        "gpt-4.1": Model(
            id="gpt-4.1",
            name="GPT-4.1",
            api="openai-completions",
            provider="openai",
            base_url="https://api.openai.com/v1",
            reasoning=False,
            input=["text", "image"],
            context_window=1_047_576,
            max_tokens=32_768,
        ),
        "gpt-4.1-mini": Model(
            id="gpt-4.1-mini",
            name="GPT-4.1 mini",
            api="openai-completions",
            provider="openai",
            base_url="https://api.openai.com/v1",
            reasoning=False,
            input=["text", "image"],
            context_window=1_047_576,
            max_tokens=32_768,
        ),
    },
    "anthropic": {
        "claude-sonnet-4-5": Model(
            id="claude-sonnet-4-5",
            name="Claude Sonnet 4.5",
            api="anthropic-messages",
            provider="anthropic",
            base_url="https://api.anthropic.com",
            reasoning=True,
            input=["text", "image"],
            context_window=200_000,
            max_tokens=8192,
        ),
    },
}


def register_model(provider: str, model: Model) -> None:
    _MODELS.setdefault(provider, {})[model.id] = model


def get_model(provider: str, model_id: str) -> Model:
    try:
        return _MODELS[provider][model_id]
    except KeyError as exc:
        raise KeyError(f"Unknown model: {provider}/{model_id}") from exc


def get_models(provider: str) -> list[Model]:
    return list(_MODELS.get(provider, {}).values())


def get_providers() -> list[str]:
    return list(_MODELS.keys())

