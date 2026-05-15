from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from momoclaw.im.service import IMService
from momoclaw.im.types import IMIncomingMessage

logger = logging.getLogger("momoclaw.im.feishu.longconn")


@dataclass
class FeishuLongConnOptions:
    app_id: str
    app_secret: str
    log_level: str = "info"


def run_feishu_long_connection(service: IMService, options: FeishuLongConnOptions) -> None:
    """Run Feishu long connection mode through optional lark-oapi."""

    lark = _import_lark_sdk()
    warn = getattr(lark.LogLevel, "WARN", None) or getattr(lark.LogLevel, "WARNING", None) or lark.LogLevel.INFO
    log_level_map = {
        "debug": lark.LogLevel.DEBUG,
        "info": lark.LogLevel.INFO,
        "warning": warn,
        "error": lark.LogLevel.ERROR,
    }

    def _on_p2_im_message_receive_v1(data: Any) -> None:
        try:
            raw = _to_dict(data)
            message = _parse_event_object(data) or _parse_ws_message(raw)
            if message is None:
                return
            _run_async(service.handle_incoming_message(message))
        except Exception:
            logger.exception("failed to process feishu long connection event")

    event_handler = (
        lark.EventDispatcherHandler
        .builder("", "")
        .register_p2_im_message_receive_v1(_on_p2_im_message_receive_v1)
        .build()
    )
    client = lark.ws.Client(
        options.app_id,
        options.app_secret,
        event_handler=event_handler,
        log_level=log_level_map.get(options.log_level.lower(), lark.LogLevel.INFO),
    )
    client.start()


def _run_async(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import threading

        done = threading.Event()
        errors: list[BaseException] = []

        def _target() -> None:
            try:
                asyncio.run(coro)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        done.wait(timeout=120)
        if errors:
            raise errors[0]
    else:
        asyncio.run(coro)


def _parse_event_object(data: Any) -> IMIncomingMessage | None:
    event = getattr(data, "event", None)
    if event is None:
        return None
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    if message is None or sender is None:
        return None

    sender_type = getattr(sender, "sender_type", "")
    if isinstance(sender_type, str) and sender_type.lower() in {"app", "bot"}:
        return None
    if getattr(message, "message_type", "") != "text":
        return None

    text = _extract_text_from_raw(getattr(message, "content", ""))
    text = _strip_bot_mentions_from_objects(text, getattr(message, "mentions", None) or [])
    chat_id = getattr(message, "chat_id", "") or ""
    if not chat_id or not text:
        return None

    sender_id = _extract_sender_id_from_object(getattr(sender, "sender_id", None))
    create_time = _extract_created_at(getattr(message, "create_time", None))
    root_id = getattr(message, "root_id", None)
    message_id = getattr(message, "message_id", None)
    return IMIncomingMessage(
        platform="feishu",
        channel_id=str(chat_id),
        user_id=sender_id or "unknown",
        text=text,
        thread_id=str(root_id) if isinstance(root_id, str) and root_id else None,
        message_id=str(message_id) if isinstance(message_id, str) and message_id else None,
        created_at=create_time,
        raw=_to_dict(data),
    )


def _parse_ws_message(payload: dict[str, Any]) -> IMIncomingMessage | None:
    header = payload.get("header")
    event_type = header.get("event_type") if isinstance(header, dict) else ""
    if event_type != "im.message.receive_v1":
        return None
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    message = event.get("message")
    sender = event.get("sender")
    if not isinstance(message, dict) or not isinstance(sender, dict):
        return None
    if str(sender.get("sender_type", "")).lower() in {"app", "bot"}:
        return None
    if message.get("message_type") != "text":
        return None

    text = _extract_text_from_raw(message.get("content"))
    text = _strip_bot_mentions_from_dicts(text, message.get("mentions") or [])
    chat_id = str(message.get("chat_id", ""))
    if not chat_id or not text:
        return None

    sender_id = ""
    sender_node = sender.get("sender_id")
    if isinstance(sender_node, dict):
        for key in ("open_id", "user_id", "union_id"):
            value = sender_node.get(key)
            if isinstance(value, str) and value:
                sender_id = value
                break

    root_id = message.get("root_id")
    return IMIncomingMessage(
        platform="feishu",
        channel_id=chat_id,
        user_id=sender_id or "unknown",
        text=text,
        thread_id=root_id if isinstance(root_id, str) and root_id else None,
        message_id=message.get("message_id") if isinstance(message.get("message_id"), str) else None,
        created_at=_extract_created_at(message.get("create_time")),
        raw=payload,
    )


def _extract_text_from_raw(raw_content: object) -> str:
    if not isinstance(raw_content, str):
        return ""
    try:
        content = json.loads(raw_content)
    except Exception:
        return raw_content.strip()
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    return raw_content.strip()


def _strip_bot_mentions_from_objects(text: str, mentions: list[Any]) -> str:
    for mention in mentions:
        if str(getattr(mention, "mentioned_type", "")).lower() != "bot":
            continue
        key = getattr(mention, "key", "") or ""
        if key:
            text = text.replace(key, "")
    return text.strip()


def _strip_bot_mentions_from_dicts(text: str, mentions: list[dict[str, Any]]) -> str:
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        if str(mention.get("mentioned_type", "")).lower() != "bot":
            continue
        key = str(mention.get("key", ""))
        if key:
            text = text.replace(key, "")
    return text.strip()


def _extract_sender_id_from_object(sender_id: Any) -> str:
    if sender_id is None:
        return ""
    for key in ("open_id", "user_id", "union_id"):
        value = getattr(sender_id, key, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_created_at(value: object) -> float | None:
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str) and value.isdigit():
        number = float(value)
    else:
        return None
    return number / 1000.0 if number > 10_000_000_000 else number


def _to_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    for attr in ("to_dict", "model_dump", "dict"):
        fn = getattr(payload, attr, None)
        if callable(fn):
            try:
                obj = fn()
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    for attr in ("raw_body", "raw", "body"):
        value = getattr(payload, attr, None)
        if isinstance(value, (bytes, str)):
            try:
                obj = json.loads(value.decode("utf-8") if isinstance(value, bytes) else value)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    return {}


def _import_lark_sdk():
    try:
        import lark_oapi as lark  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Feishu long connection requires `lark-oapi`. Install with: uv add lark-oapi") from exc
    return lark
