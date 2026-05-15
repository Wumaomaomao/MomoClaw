from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from momoclaw.im.types import (
    IMChannelInfo,
    IMIncomingMessage,
    IMOutgoingCard,
    IMOutgoingText,
    IMUserInfo,
    IMWebhookResult,
)

logger = logging.getLogger("momoclaw.im.feishu")


@dataclass
class FeishuAdapterConfig:
    app_id: str
    app_secret: str
    verify_token: str | None = None
    api_base: str = "https://open.feishu.cn/open-apis"
    request_timeout_seconds: int = 15
    user_cache_ttl: float = 3600.0
    chat_cache_ttl: float = 3600.0


class FeishuAdapter:
    """Feishu adapter for webhook parsing, text replies, and card updates."""

    def __init__(self, config: FeishuAdapterConfig) -> None:
        self.config = config
        self._tenant_access_token: str | None = None
        self._token_expire_at = 0.0
        self._token_lock = threading.Lock()
        self._user_cache: dict[str, tuple[IMUserInfo, float]] = {}
        self._chat_cache: dict[str, tuple[IMChannelInfo, float]] = {}

    def handle_webhook(self, headers: Mapping[str, str], body: bytes) -> IMWebhookResult:
        del headers
        payload = self._parse_json(body)
        if payload.get("type") == "url_verification":
            return IMWebhookResult(ack={"challenge": str(payload.get("challenge", ""))}, messages=[])

        if self.config.verify_token and payload.get("token") != self.config.verify_token:
            return IMWebhookResult(ack={"code": 19021, "msg": "invalid token"}, messages=[])

        header = payload.get("header")
        event_type = header.get("event_type") if isinstance(header, dict) else ""
        if event_type != "im.message.receive_v1":
            return IMWebhookResult(ack={"code": 0}, messages=[])

        event = payload.get("event")
        if not isinstance(event, dict):
            return IMWebhookResult(ack={"code": 0}, messages=[])

        message = event.get("message")
        sender = event.get("sender")
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return IMWebhookResult(ack={"code": 0}, messages=[])

        if str(sender.get("sender_type", "")).lower() in {"app", "bot"}:
            return IMWebhookResult(ack={"code": 0}, messages=[])

        if message.get("message_type") != "text":
            return IMWebhookResult(ack={"code": 0}, messages=[])

        text = self._extract_text(message)
        chat_id = str(message.get("chat_id", ""))
        if not chat_id or not text:
            return IMWebhookResult(ack={"code": 0}, messages=[])

        sender_id = self._extract_sender_id(sender)
        created_at = self._extract_created_at(message.get("create_time"))
        incoming = IMIncomingMessage(
            platform="feishu",
            channel_id=chat_id,
            user_id=sender_id or "unknown",
            text=text,
            thread_id=self._optional_str(message.get("root_id")),
            message_id=self._optional_str(message.get("message_id")),
            created_at=created_at,
            raw=payload,
        )
        return IMWebhookResult(ack={"code": 0}, messages=[incoming])

    def send_text(self, message: IMOutgoingText) -> str | None:
        httpx = _import_httpx()
        token = self._get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        content = json.dumps({"text": message.text}, ensure_ascii=False)
        if message.reply_to_message_id:
            url = f"{self.config.api_base}/im/v1/messages/{message.reply_to_message_id}/reply"
            payload = {"msg_type": "text", "content": content}
        else:
            url = f"{self.config.api_base}/im/v1/messages?receive_id_type=chat_id"
            payload = {"receive_id": message.channel_id, "msg_type": "text", "content": content}
        with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        if data.get("code") not in (0, "0", None):
            raise RuntimeError(f"Feishu send_text failed: {data}")
        return data.get("data", {}).get("message_id")

    def send_card(self, message: IMOutgoingCard) -> str | None:
        httpx = _import_httpx()
        token = self._get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        content = self._build_card_content(message.title, message.markdown_content)
        if message.reply_to_message_id:
            url = f"{self.config.api_base}/im/v1/messages/{message.reply_to_message_id}/reply"
            payload = {"msg_type": "interactive", "content": content}
        else:
            url = f"{self.config.api_base}/im/v1/messages?receive_id_type=chat_id"
            payload = {"receive_id": message.channel_id, "msg_type": "interactive", "content": content}
        with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        if data.get("code") not in (0, "0", None):
            raise RuntimeError(f"Feishu send_card failed: {data}")
        return data.get("data", {}).get("message_id")

    def update_text(self, message_id: str, text: str) -> None:
        """Update a card message. Feishu does not support updating plain text messages."""
        httpx = _import_httpx()
        token = self._get_tenant_access_token()
        url = f"{self.config.api_base}/im/v1/messages/{message_id}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        content = self._build_card_content("MomoClaw", text)
        with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
            response = client.patch(url, headers=headers, json={"content": content})
            response.raise_for_status()
            data = response.json()
        if data.get("code") not in (0, "0", None):
            raise RuntimeError(f"Feishu update_text failed: {data}")

    def get_user_info(self, user_id: str) -> IMUserInfo | None:
        now = time.time()
        cached = self._user_cache.get(user_id)
        if cached and now - cached[1] < self.config.user_cache_ttl:
            return cached[0]
        httpx = _import_httpx()
        token = self._get_tenant_access_token()
        url = f"{self.config.api_base}/contact/v3/users/{user_id}?user_id_type=open_id"
        try:
            with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
                response = client.get(url, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("feishu get_user_info failed user_id=%s: %s", user_id, exc)
            return None
        if data.get("code") not in (0, "0"):
            return None
        user = data.get("data", {}).get("user", {})
        info = IMUserInfo(
            user_id=user_id,
            name=str(user.get("name", "")),
            avatar_url=str(user.get("avatar", {}).get("avatar_72", "")) if isinstance(user.get("avatar"), dict) else "",
            department=", ".join(user.get("department_ids", [])) if isinstance(user.get("department_ids"), list) else "",
        )
        self._user_cache[user_id] = (info, now)
        return info

    def get_chat_info(self, chat_id: str) -> IMChannelInfo | None:
        now = time.time()
        cached = self._chat_cache.get(chat_id)
        if cached and now - cached[1] < self.config.chat_cache_ttl:
            return cached[0]
        httpx = _import_httpx()
        token = self._get_tenant_access_token()
        url = f"{self.config.api_base}/im/v1/chats/{chat_id}"
        try:
            with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
                response = client.get(url, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("feishu get_chat_info failed chat_id=%s: %s", chat_id, exc)
            return None
        if data.get("code") not in (0, "0"):
            return None
        chat = data.get("data", {})
        info = IMChannelInfo(
            channel_id=chat_id,
            name=str(chat.get("name", "")),
            description=str(chat.get("description", "")),
            owner_id=str(chat.get("owner_id", "")),
            member_count=int(chat.get("user_count", 0) or 0),
        )
        self._chat_cache[chat_id] = (info, now)
        return info

    def _get_tenant_access_token(self) -> str:
        with self._token_lock:
            now = time.time()
            if self._tenant_access_token and now < self._token_expire_at - 60:
                return self._tenant_access_token
            httpx = _import_httpx()
            url = f"{self.config.api_base}/auth/v3/tenant_access_token/internal"
            payload = {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
            with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
            if data.get("code") not in (0, "0"):
                raise RuntimeError(f"Feishu auth failed: {data}")
            token = data.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("Feishu auth response missing tenant_access_token")
            ttl = int(data.get("expire", 7200) or 7200)
            self._tenant_access_token = token
            self._token_expire_at = now + ttl
            return token

    @staticmethod
    def _build_card_content(title: str, markdown: str) -> str:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": markdown}],
        }
        return json.dumps(card, ensure_ascii=False)

    @staticmethod
    def _parse_json(body: bytes) -> dict[str, Any]:
        try:
            raw = json.loads(body.decode("utf-8"))
        except Exception:
            logger.exception("failed to parse feishu webhook body")
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str:
        raw_content = message.get("content")
        text = ""
        if isinstance(raw_content, str):
            try:
                content = json.loads(raw_content)
                text = str(content.get("text", "")) if isinstance(content, dict) else raw_content
            except Exception:
                text = raw_content
        for mention in message.get("mentions", []) or []:
            if not isinstance(mention, dict):
                continue
            if str(mention.get("mentioned_type", "")).lower() != "bot":
                continue
            key = str(mention.get("key", ""))
            if key:
                text = text.replace(key, "")
        return text.strip()

    @staticmethod
    def _extract_sender_id(sender: dict[str, Any]) -> str:
        sender_id = sender.get("sender_id")
        if not isinstance(sender_id, dict):
            return ""
        for key in ("open_id", "user_id", "union_id"):
            value = sender_id.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _extract_created_at(value: object) -> float | None:
        if isinstance(value, int | float):
            number = float(value)
        elif isinstance(value, str) and value.isdigit():
            number = float(value)
        else:
            return None
        return number / 1000.0 if number > 10_000_000_000 else number

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None


def _import_httpx():
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for FeishuAdapter network calls") from exc
    return httpx
