from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from momoclaw.ai.event_stream import AssistantMessageEventStream
from momoclaw.ai.types import AssistantMessage, Context, Model, SimpleStreamOptions, TextContent, Usage
from momoclaw.im import IMIncomingMessage, IMService, IMServiceConfig, SessionRouter
from momoclaw.im.adapters.fake import FakeIMAdapter
from momoclaw.im.adapters.feishu import FeishuAdapter, FeishuAdapterConfig
from momoclaw.im.adapters.feishu_longconn import _parse_ws_message


def _model() -> Model:
    return Model(id="fake", name="Fake", api="fake-api", provider="fake", base_url="http://fake")


def _stream_replies(texts: list[str]) -> Callable:
    calls: list[Context] = []

    def _stream(model: Model, context: Context, options: SimpleStreamOptions | None = None) -> AssistantMessageEventStream:
        del model, options
        calls.append(context)
        message = AssistantMessage(
            content=[TextContent(text=texts[len(calls) - 1])],
            api="fake-api",
            provider="fake",
            model="fake",
            usage=Usage(input=5, output=7, total_tokens=12),
        )
        stream = AssistantMessageEventStream()
        stream.push({"type": "start", "partial": message})
        stream.push({"type": "text_start", "contentIndex": 0, "partial": message})
        stream.push({"type": "text_delta", "contentIndex": 0, "delta": message.content[0].text, "partial": message})
        stream.push({"type": "text_end", "contentIndex": 0, "content": message.content[0].text, "partial": message})
        stream.push({"type": "done", "reason": "stop", "message": message})
        stream.end(message)
        return stream

    _stream.calls = calls  # type: ignore[attr-defined]
    return _stream


@pytest.mark.asyncio
async def test_im_service_replies_and_deduplicates(tmp_path: Path) -> None:
    adapter = FakeIMAdapter()
    stream_fn = _stream_replies(["hello from agent"])
    service = IMService(
        adapter,
        IMServiceConfig(workspace_dir=tmp_path, model=_model(), stream_fn=stream_fn, show_usage_in_reply=True),
    )
    message = IMIncomingMessage(
        platform="fake",
        channel_id="chat_1",
        user_id="user_1",
        text="hello",
        message_id="msg_1",
    )

    await service.handle_incoming_message(message)
    await service.handle_incoming_message(message)

    assert len(adapter.sent_texts) == 1
    assert adapter.sent_texts[0].reply_to_message_id == "msg_1"
    assert "hello from agent" in adapter.sent_texts[0].text
    assert "tokens: 12" in adapter.sent_texts[0].text
    assert len(stream_fn.calls) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_im_service_webhook_and_channel_order(tmp_path: Path) -> None:
    adapter = FakeIMAdapter()
    stream_fn = _stream_replies(["first", "second"])
    service = IMService(
        adapter,
        IMServiceConfig(workspace_dir=tmp_path, model=_model(), stream_fn=stream_fn, show_usage_in_reply=False),
    )
    body = json.dumps(
        {
            "messages": [
                {"channel_id": "chat_1", "user_id": "u", "text": "one", "message_id": "m1"},
                {"channel_id": "chat_1", "user_id": "u", "text": "two", "message_id": "m2"},
            ]
        }
    ).encode("utf-8")

    ack = await service.handle_webhook({}, body)

    assert ack == {"ok": True}
    assert [message.text for message in adapter.sent_texts] == ["first", "second"]


@pytest.mark.asyncio
async def test_im_service_commands_rotate_session(tmp_path: Path) -> None:
    adapter = FakeIMAdapter()
    service = IMService(
        adapter,
        IMServiceConfig(workspace_dir=tmp_path, model=_model(), stream_fn=_stream_replies(["unused"])),
    )
    router = service.router
    before = router.get_or_create_session_id(platform="fake", channel_id="chat_1")

    await service.handle_incoming_message(
        IMIncomingMessage(platform="fake", channel_id="chat_1", user_id="u", text="/new", message_id="m1")
    )

    after = router.get_or_create_session_id(platform="fake", channel_id="chat_1")
    assert before != after
    assert after in adapter.sent_texts[-1].text


@pytest.mark.asyncio
async def test_im_service_skips_stale_events(tmp_path: Path) -> None:
    adapter = FakeIMAdapter()
    service = IMService(
        adapter,
        IMServiceConfig(workspace_dir=tmp_path, model=_model(), stream_fn=_stream_replies(["unused"])),
    )

    await service.handle_incoming_message(
        IMIncomingMessage(
            platform="fake",
            channel_id="chat_1",
            user_id="u",
            text="old",
            message_id="m1",
            created_at=time.time() - 120,
        )
    )

    assert adapter.sent_texts == []


def test_session_router_maps_channel_and_thread(tmp_path: Path) -> None:
    router = SessionRouter(tmp_path)

    channel = router.get_or_create_session_id(platform="fake", channel_id="chat")
    same_channel = router.get_or_create_session_id(platform="fake", channel_id="chat")
    thread = router.get_or_create_session_id(platform="fake", channel_id="chat", thread_id="thread")

    assert channel == same_channel
    assert channel != thread
    assert SessionRouter(tmp_path).get_session_id(platform="fake", channel_id="chat") == channel


def test_feishu_adapter_parses_url_verification_and_text_message() -> None:
    adapter = FeishuAdapter(FeishuAdapterConfig(app_id="app", app_secret="secret", verify_token="token"))

    verification = adapter.handle_webhook({}, b'{"type":"url_verification","challenge":"abc"}')
    assert verification.ack == {"challenge": "abc"}
    assert verification.messages == []

    payload = {
        "token": "token",
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 hello"}),
                "mentions": [{"mentioned_type": "bot", "key": "@_user_1"}],
                "create_time": "1710000000000",
            },
        },
    }

    result = adapter.handle_webhook({}, json.dumps(payload).encode("utf-8"))

    assert result.ack == {"code": 0}
    assert len(result.messages) == 1
    assert result.messages[0].platform == "feishu"
    assert result.messages[0].channel_id == "oc_1"
    assert result.messages[0].message_id == "om_1"
    assert result.messages[0].text == "hello"


def test_feishu_adapter_builds_card_content() -> None:
    content = FeishuAdapter._build_card_content("MomoClaw", "**done**")
    parsed = json.loads(content)

    assert parsed["header"]["title"]["content"] == "MomoClaw"
    assert parsed["elements"][0]["tag"] == "markdown"
    assert parsed["elements"][0]["content"] == "**done**"


def test_feishu_longconn_parses_ws_message_dict() -> None:
    payload = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 ping"}),
                "mentions": [{"mentioned_type": "bot", "key": "@_user_1"}],
                "create_time": "1710000000000",
            },
        },
    }

    message = _parse_ws_message(payload)

    assert message is not None
    assert message.channel_id == "oc_1"
    assert message.user_id == "ou_1"
    assert message.text == "ping"
