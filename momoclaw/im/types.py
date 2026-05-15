from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass
class IMIncomingMessage:
    """Platform-normalized inbound IM message."""

    platform: str
    channel_id: str
    user_id: str
    text: str
    thread_id: str | None = None
    message_id: str | None = None
    created_at: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class IMOutgoingText:
    """Platform-normalized text reply."""

    channel_id: str
    text: str
    thread_id: str | None = None
    reply_to_message_id: str | None = None


@dataclass
class IMOutgoingCard:
    """Platform-normalized markdown card reply."""

    channel_id: str
    title: str
    markdown_content: str
    thread_id: str | None = None
    reply_to_message_id: str | None = None


@dataclass
class IMUserInfo:
    user_id: str
    name: str = ""
    avatar_url: str = ""
    department: str = ""


@dataclass
class IMChannelInfo:
    channel_id: str
    name: str = ""
    description: str = ""
    owner_id: str = ""
    member_count: int = 0


@dataclass
class IMWebhookResult:
    ack: dict[str, Any]
    messages: list[IMIncomingMessage]


class IMAdapter(Protocol):
    def handle_webhook(self, headers: Mapping[str, str], body: bytes) -> IMWebhookResult:
        """Parse a platform webhook into normalized messages."""

    def send_text(self, message: IMOutgoingText) -> str | None:
        """Send a text reply and optionally return the platform message id."""

    def send_card(self, message: IMOutgoingCard) -> str | None:
        """Send a markdown card reply and optionally return the platform message id."""

    def update_text(self, message_id: str, text: str) -> None:
        """Update a previously sent platform message when supported."""

    def get_user_info(self, user_id: str) -> IMUserInfo | None:
        """Return platform user metadata when available."""

    def get_chat_info(self, chat_id: str) -> IMChannelInfo | None:
        """Return platform chat/channel metadata when available."""
