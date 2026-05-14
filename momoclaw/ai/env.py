from __future__ import annotations

import os


def get_env_api_key(provider: str) -> str | None:
    normalized = provider.lower().replace("_", "-")
    if normalized == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY")
    if normalized in {"openai", "openai-compatible", "openai-completions"}:
        return os.getenv("OPENAI_API_KEY")

    specific = normalized.replace("-", "_").upper()
    return os.getenv(f"{specific}_API_KEY")

