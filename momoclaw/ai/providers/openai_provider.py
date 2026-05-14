from __future__ import annotations

import asyncio
from typing import Any

from ..env import get_env_api_key
from ..event_stream import AssistantMessageEventStream
from ..types import Context, Model, SimpleStreamOptions, StreamOptions, TextContent, ThinkingContent, ToolCall
from ._common import apply_payload_hook, empty_assistant_message, get_value, parse_partial_json, to_openai_messages, to_openai_tools


def _map_stop_reason(reason: str | None) -> str:
    if reason == "tool_calls":
        return "toolUse"
    if reason == "length":
        return "length"
    return "stop"


def stream_openai(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    resolved = options or StreamOptions()

    async def _run() -> None:
        out = empty_assistant_message(api=model.api, provider=model.provider, model=model.id)
        try:
            from openai import AsyncOpenAI

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
            client = AsyncOpenAI(**client_kwargs)

            payload: dict[str, Any] = {
                "model": model.id,
                "messages": to_openai_messages(context),
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if resolved.temperature is not None:
                payload["temperature"] = resolved.temperature
            if resolved.max_tokens is not None:
                payload["max_tokens"] = resolved.max_tokens
            tools = to_openai_tools(context.tools)
            if tools:
                payload["tools"] = tools
            if resolved.metadata:
                payload["metadata"] = resolved.metadata

            payload = await apply_payload_hook(payload, model, resolved.on_payload)
            response = await client.chat.completions.create(**payload)
            stream.push({"type": "start", "partial": out})

            current_text: TextContent | None = None
            current_thinking: ThinkingContent | None = None
            tool_call_index_map: dict[int, ToolCall] = {}
            tool_call_partial_json: dict[int, str] = {}

            async for chunk in response:
                if get_value(chunk, "id"):
                    out.response_id = get_value(chunk, "id")
                if get_value(chunk, "model") and get_value(chunk, "model") != model.id:
                    out.response_model = get_value(chunk, "model")

                usage = get_value(chunk, "usage")
                if usage:
                    out.usage.input = get_value(usage, "prompt_tokens", out.usage.input)
                    out.usage.output = get_value(usage, "completion_tokens", out.usage.output)
                    out.usage.total_tokens = get_value(usage, "total_tokens", out.usage.total_tokens)

                choices = get_value(chunk, "choices", []) or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = get_value(choice, "finish_reason")
                if finish_reason:
                    out.stop_reason = _map_stop_reason(finish_reason)

                delta = get_value(choice, "delta", {})
                text_delta = get_value(delta, "content")
                if text_delta:
                    if current_text is None:
                        current_text = TextContent(text="")
                        out.content.append(current_text)
                        stream.push({"type": "text_start", "contentIndex": len(out.content) - 1, "partial": out})
                    current_text.text += text_delta
                    stream.push({"type": "text_delta", "contentIndex": out.content.index(current_text), "delta": text_delta, "partial": out})

                reasoning_delta = (
                    get_value(delta, "reasoning_content")
                    or get_value(delta, "reasoning")
                    or get_value(delta, "reasoning_text")
                )
                if reasoning_delta:
                    if current_thinking is None:
                        current_thinking = ThinkingContent(thinking="")
                        out.content.append(current_thinking)
                        stream.push({"type": "thinking_start", "contentIndex": len(out.content) - 1, "partial": out})
                    current_thinking.thinking += reasoning_delta
                    stream.push({"type": "thinking_delta", "contentIndex": out.content.index(current_thinking), "delta": reasoning_delta, "partial": out})

                for tc_delta in get_value(delta, "tool_calls", []) or []:
                    index = get_value(tc_delta, "index", 0)
                    tc = tool_call_index_map.get(index)
                    if tc is None:
                        tc = ToolCall(id=get_value(tc_delta, "id", ""), name="", arguments={})
                        tool_call_index_map[index] = tc
                        tool_call_partial_json[index] = ""
                        out.content.append(tc)
                        stream.push({"type": "toolcall_start", "contentIndex": len(out.content) - 1, "partial": out})
                    if get_value(tc_delta, "id"):
                        tc.id = get_value(tc_delta, "id")
                    fn = get_value(tc_delta, "function", {})
                    if get_value(fn, "name"):
                        tc.name = get_value(fn, "name")
                    args_delta = get_value(fn, "arguments")
                    if args_delta:
                        tool_call_partial_json[index] += args_delta
                        tc.arguments = parse_partial_json(tool_call_partial_json[index])
                        stream.push({"type": "toolcall_delta", "contentIndex": out.content.index(tc), "delta": args_delta, "partial": out})

            if resolved.on_response:
                await resolved.on_response({"status": 200, "headers": {}}, model)
            if current_text is not None:
                stream.push({"type": "text_end", "contentIndex": out.content.index(current_text), "content": current_text.text, "partial": out})
            if current_thinking is not None:
                stream.push({"type": "thinking_end", "contentIndex": out.content.index(current_thinking), "content": current_thinking.thinking, "partial": out})
            for tc in tool_call_index_map.values():
                stream.push({"type": "toolcall_end", "contentIndex": out.content.index(tc), "toolCall": tc, "partial": out})
            stream.push({"type": "done", "reason": out.stop_reason, "message": out})
            stream.end(out)
        except Exception as exc:
            out.stop_reason = "error"
            out.error_message = str(exc)
            stream.push({"type": "error", "reason": "error", "error": out})
            stream.end(out)

    asyncio.create_task(_run())
    return stream


def stream_simple_openai(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    return stream_openai(model, context, options)

