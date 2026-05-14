from __future__ import annotations

import asyncio
from typing import Any

from ..env import get_env_api_key
from ..event_stream import AssistantMessageEventStream
from ..types import Context, Model, SimpleStreamOptions, StreamOptions, TextContent, ThinkingContent, ToolCall
from ._common import apply_payload_hook, empty_assistant_message, get_value, parse_partial_json, to_anthropic_messages, to_anthropic_tools


def _map_stop_reason(reason: str | None) -> str:
    if reason == "tool_use":
        return "toolUse"
    if reason == "max_tokens":
        return "length"
    return "stop"


def _anthropic_thinking(options: StreamOptions | None, model: Model) -> dict[str, Any] | None:
    if not isinstance(options, SimpleStreamOptions) or not options.reasoning or not model.reasoning:
        return None
    if model.compat and model.compat.get("thinking") == "adaptive":
        effort = model.thinking_level_map.get(options.reasoning, options.reasoning) if model.thinking_level_map else options.reasoning
        return {"type": "adaptive", "effort": effort, "display": "summarized"}
    budgets = {"minimal": 1024, "low": 2048, "medium": 4096, "high": 8192, "xhigh": 16384}
    return {"type": "enabled", "budget_tokens": budgets[options.reasoning]}


def stream_anthropic(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    resolved = options or StreamOptions()

    async def _run() -> None:
        out = empty_assistant_message(api=model.api, provider=model.provider, model=model.id)
        try:
            from anthropic import AsyncAnthropic

            api_key = resolved.api_key or get_env_api_key(model.provider)
            if not api_key:
                raise RuntimeError(f"Missing API key for provider: {model.provider}")

            headers = dict(model.headers or {})
            if resolved.headers:
                headers.update(resolved.headers)

            client_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "base_url": model.base_url,
                "default_headers": headers or None,
            }
            if resolved.timeout_seconds is not None:
                client_kwargs["timeout"] = resolved.timeout_seconds
            if resolved.max_retries is not None:
                client_kwargs["max_retries"] = resolved.max_retries
            client = AsyncAnthropic(**client_kwargs)

            payload: dict[str, Any] = {
                "model": model.id,
                "max_tokens": resolved.max_tokens or model.max_tokens,
                "messages": to_anthropic_messages(context),
            }
            if context.system_prompt:
                payload["system"] = context.system_prompt
            if resolved.temperature is not None:
                payload["temperature"] = resolved.temperature
            if resolved.metadata:
                payload["metadata"] = resolved.metadata
            thinking = _anthropic_thinking(resolved, model)
            if thinking:
                payload["thinking"] = thinking
                payload.pop("temperature", None)
            tools = to_anthropic_tools(context.tools)
            if tools:
                payload["tools"] = tools

            payload = await apply_payload_hook(payload, model, resolved.on_payload)
            stream.push({"type": "start", "partial": out})

            blocks_by_anthropic_index: dict[int, TextContent | ThinkingContent | ToolCall] = {}
            tool_partial_json: dict[int, str] = {}

            async with client.messages.stream(**payload) as response_stream:
                async for event in response_stream:
                    event_type = get_value(event, "type")

                    if event_type == "message_start":
                        message = get_value(event, "message", {})
                        out.response_id = get_value(message, "id", out.response_id)
                        usage = get_value(message, "usage", {})
                        out.usage.input = get_value(usage, "input_tokens", out.usage.input)

                    elif event_type == "content_block_start":
                        idx = get_value(event, "index", 0)
                        block = get_value(event, "content_block", {})
                        block_type = get_value(block, "type")
                        if block_type == "text":
                            text = TextContent(text=get_value(block, "text", "") or "")
                            blocks_by_anthropic_index[idx] = text
                            out.content.append(text)
                            stream.push({"type": "text_start", "contentIndex": len(out.content) - 1, "partial": out})
                        elif block_type in {"thinking", "redacted_thinking"}:
                            thinking_block = ThinkingContent(
                                thinking=get_value(block, "thinking", "") or "",
                                thinking_signature=get_value(block, "signature") or get_value(block, "data"),
                                redacted=block_type == "redacted_thinking",
                            )
                            blocks_by_anthropic_index[idx] = thinking_block
                            out.content.append(thinking_block)
                            stream.push({"type": "thinking_start", "contentIndex": len(out.content) - 1, "partial": out})
                        elif block_type == "tool_use":
                            tc = ToolCall(id=get_value(block, "id", ""), name=get_value(block, "name", ""), arguments=get_value(block, "input", {}) or {})
                            blocks_by_anthropic_index[idx] = tc
                            tool_partial_json[idx] = ""
                            out.content.append(tc)
                            stream.push({"type": "toolcall_start", "contentIndex": len(out.content) - 1, "partial": out})

                    elif event_type == "content_block_delta":
                        idx = get_value(event, "index", 0)
                        delta = get_value(event, "delta", {})
                        delta_type = get_value(delta, "type")
                        block = blocks_by_anthropic_index.get(idx)
                        if delta_type == "text_delta" and isinstance(block, TextContent):
                            text_delta = get_value(delta, "text", "") or ""
                            block.text += text_delta
                            stream.push({"type": "text_delta", "contentIndex": out.content.index(block), "delta": text_delta, "partial": out})
                        elif delta_type == "thinking_delta" and isinstance(block, ThinkingContent):
                            thinking_delta = get_value(delta, "thinking", "") or ""
                            block.thinking += thinking_delta
                            stream.push({"type": "thinking_delta", "contentIndex": out.content.index(block), "delta": thinking_delta, "partial": out})
                        elif delta_type == "signature_delta" and isinstance(block, ThinkingContent):
                            block.thinking_signature = (block.thinking_signature or "") + (get_value(delta, "signature", "") or "")
                        elif delta_type == "input_json_delta" and isinstance(block, ToolCall):
                            piece = get_value(delta, "partial_json", "") or ""
                            tool_partial_json[idx] = tool_partial_json.get(idx, "") + piece
                            block.arguments = parse_partial_json(tool_partial_json[idx])
                            stream.push({"type": "toolcall_delta", "contentIndex": out.content.index(block), "delta": piece, "partial": out})

                    elif event_type == "content_block_stop":
                        idx = get_value(event, "index", 0)
                        block = blocks_by_anthropic_index.get(idx)
                        if isinstance(block, TextContent):
                            stream.push({"type": "text_end", "contentIndex": out.content.index(block), "content": block.text, "partial": out})
                        elif isinstance(block, ThinkingContent):
                            stream.push({"type": "thinking_end", "contentIndex": out.content.index(block), "content": block.thinking, "partial": out})
                        elif isinstance(block, ToolCall):
                            stream.push({"type": "toolcall_end", "contentIndex": out.content.index(block), "toolCall": block, "partial": out})

                    elif event_type == "message_delta":
                        delta = get_value(event, "delta", {})
                        out.stop_reason = _map_stop_reason(get_value(delta, "stop_reason"))
                        usage = get_value(event, "usage", {})
                        out.usage.output = get_value(usage, "output_tokens", out.usage.output)

                final_message = await response_stream.get_final_message()
                final_usage = get_value(final_message, "usage", {})
                out.usage.input = get_value(final_usage, "input_tokens", out.usage.input)
                out.usage.output = get_value(final_usage, "output_tokens", out.usage.output)
                out.usage.total_tokens = out.usage.input + out.usage.output

            if resolved.on_response:
                await resolved.on_response({"status": 200, "headers": {}}, model)
            stream.push({"type": "done", "reason": out.stop_reason, "message": out})
            stream.end(out)
        except Exception as exc:
            out.stop_reason = "error"
            out.error_message = str(exc)
            stream.push({"type": "error", "reason": "error", "error": out})
            stream.end(out)

    asyncio.create_task(_run())
    return stream


def stream_simple_anthropic(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    return stream_anthropic(model, context, options)

