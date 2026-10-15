from __future__ import annotations

import json
import math
from typing import Any

from momoclaw.ai.types import Message
from momoclaw.coding_agent.serde import message_to_dict


def estimate_tokens(value: Any) -> int:
    """Conservative tokenizer-free estimate: two UTF-8 bytes per token."""
    if isinstance(value, (str, bytes)):
        raw = value.encode("utf-8") if isinstance(value, str) else value
    else:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return math.ceil(len(raw) / 2)


def estimate_request_tokens(system_prompt: str, tools: list[dict[str, Any]], messages: list[Message]) -> int:
    return estimate_tokens(system_prompt) + estimate_tokens(tools) + estimate_tokens([message_to_dict(m) for m in messages])


def tool_to_dict(tool: Any) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}