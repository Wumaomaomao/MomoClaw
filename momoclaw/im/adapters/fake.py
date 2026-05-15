from __future__ import annotations

import json
from typing import Mapping

from momoclaw.im.types import (
    IMChannelInfo,
    IMIncomingMessage,
    IMOutgoingCard,
    IMOutgoingText,
    IMUserInfo,
    IMWebhookResult,
)


class FakeIMAdapter:
    """In-memory adapter for service tests and local experiments."""

    def __init__(self) -> None:
        self.sent_texts: list[IMOutgoingText] = []
        self.sent_cards: list[IMOutgoingCard] = []
        self.updated_texts: list[tuple[str, str]] = []
        self.users: dict[str, IMUserInfo] = {}
        self.chats: dict[str, IMChannelInfo] = {}

    def handle_webhook(self, headers: Mapping[str, str], body: bytes) -> IMWebhookResult:
        del headers
        raw = json.loads(body.decode("utf-8"))
        if isinstance(raw, dict) and "messages" in raw:
            messages = [self._parse_message(item) for item in raw.get("messages", [])]
        else:
            messages = [self._parse_message(raw)]
        return IMWebhookResult(ack={"ok": True}, messages=[message for message in messages if message is not None])

    def send_text(self, message: IMOutgoingText) -> str | None:
        self.sent_texts.append(message)
        return f"fake_{len(self.sent_texts)}"

    def send_card(self, message: IMOutgoingCard) -> str | None:
        self.sent_cards.append(message)
        return f"fake_card_{len(self.sent_cards)}"

    def update_text(self, message_id: str, text: str) -> None:
        self.updated_texts.append((message_id, text))

    def get_user_info(self, user_id: str) -> IMUserInfo | None:
        return self.users.get(user_id)

    def get_chat_info(self, chat_id: str) -> IMChannelInfo | None:
        return self.chats.get(chat_id)

    @staticmethod
    def _parse_message(raw: object) -> IMIncomingMessage | None:
        if not isinstance(raw, dict):
            return None
        text = raw.get("text")
        channel_id = raw.get("channel_id")
        user_id = raw.get("user_id", "fake_user")
        if not isinstance(text, str) or not isinstance(channel_id, str):
            return None
        return IMIncomingMessage(
            platform=str(raw.get("platform", "fake")),
            channel_id=channel_id,
            user_id=str(user_id),
            text=text,
            thread_id=raw.get("thread_id") if isinstance(raw.get("thread_id"), str) else None,
            message_id=raw.get("message_id") if isinstance(raw.get("message_id"), str) else None,
            created_at=float(raw["created_at"]) if isinstance(raw.get("created_at"), int | float) else None,
            raw=raw,
        )
