from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

from momoclaw.ai.models import get_model
from momoclaw.ai.types import AssistantMessage, Model, TextContent
from momoclaw.coding_agent import AgentSession, CreateAgentSessionOptions, create_agent_session

from .session_router import SessionRouter
from .types import IMAdapter, IMChannelInfo, IMIncomingMessage, IMOutgoingCard, IMOutgoingText, IMUserInfo

logger = logging.getLogger("momoclaw.im.service")

_STALE_EVENT_SECONDS = 60.0


@dataclass
class IMServiceConfig:
    workspace_dir: str | Path
    provider: str = "deepseek"
    model_id: str = "deepseek-v4-pro"
    model: Model | None = None
    base_url: str | None = None
    read_only_mode: bool = True
    max_reply_chars: int = 4000
    channel_queue_limit: int = 20
    use_card_reply: bool = False
    show_usage_in_reply: bool = True
    include_metadata_context: bool = False
    session_idle_timeout: float = 3600.0
    stream_fn: object | None = None


@dataclass
class _ChannelState:
    session: AgentSession
    session_id: str
    last_active: float
    user_cache: dict[str, IMUserInfo]
    channel_info: IMChannelInfo | None = None


class IMService:
    """Platform-independent IM bridge from normalized messages to coding-agent sessions."""

    def __init__(self, adapter: IMAdapter, config: IMServiceConfig, router: SessionRouter | None = None) -> None:
        self.adapter = adapter
        self.config = config
        self.router = router or SessionRouter(config.workspace_dir)
        self._processed_ids: set[str] = set()
        self._processed_id_order: deque[str] = deque()
        self._processed_id_limit = 2000
        self._channel_queues: dict[str, deque[IMIncomingMessage]] = {}
        self._channel_running: set[str] = set()
        self._channel_states: dict[str, _ChannelState] = {}

    async def handle_webhook(self, headers: dict[str, str], body: bytes) -> dict:
        result = self.adapter.handle_webhook(headers, body)
        for message in result.messages:
            await self.handle_incoming_message(message)
        return result.ack

    async def handle_incoming_message(self, message: IMIncomingMessage) -> None:
        if self._is_duplicate_message(message.message_id):
            logger.info("skip duplicate message message_id=%s", message.message_id)
            return
        if self._is_stale_event(message):
            logger.info("skip stale message message_id=%s", message.message_id)
            self._mark_processed(message.message_id)
            return

        channel_key = self._channel_key(message)
        queue = self._channel_queues.setdefault(channel_key, deque())
        if len(queue) >= self.config.channel_queue_limit:
            logger.warning("drop message due to channel queue limit key=%s", channel_key)
            self._mark_processed(message.message_id)
            return

        queue.append(message)
        if channel_key in self._channel_running:
            return

        self._channel_running.add(channel_key)
        try:
            while queue:
                current = queue.popleft()
                await self._handle_single_message(current)
        finally:
            self._channel_running.discard(channel_key)
            if not queue:
                self._channel_queues.pop(channel_key, None)

    async def _handle_single_message(self, message: IMIncomingMessage) -> None:
        if await self._handle_control_command(message):
            self._mark_processed(message.message_id)
            return

        session, _ = self._get_or_create_channel_session(message)
        prompt_text = self._build_prompt_text(message)
        try:
            new_messages = await session.prompt(prompt_text)
            reply = self._extract_assistant_text(new_messages) or self._extract_last_assistant_text(session)
        except Exception as exc:
            logger.exception("agent prompt failed")
            reply = f"[MomoClaw IM error] {exc}"

        reply = reply or "(empty)"
        if len(reply) > self.config.max_reply_chars:
            reply = reply[: self.config.max_reply_chars] + "\n...<truncated>..."

        usage_line = self._format_usage(session) if self.config.show_usage_in_reply else ""
        if usage_line:
            reply = f"{reply}\n\n{usage_line}"

        self._send_reply(message, reply)
        self._mark_processed(message.message_id)

    def _get_or_create_channel_session(self, message: IMIncomingMessage) -> tuple[AgentSession, str]:
        channel_key = self._channel_key(message)
        state = self._channel_states.get(channel_key)
        if state:
            state.last_active = time.time()
            return state.session, state.session_id

        session_id = self.router.get_or_create_session_id(
            platform=message.platform,
            channel_id=message.channel_id,
            thread_id=message.thread_id,
        )
        session = create_agent_session(
            CreateAgentSessionOptions(
                workspace_dir=self.config.workspace_dir,
                model=self._resolve_model(),
                session_id=session_id,
                read_only_mode=self.config.read_only_mode,
                stream_fn=self.config.stream_fn,  # type: ignore[arg-type]
            )
        )
        channel_info = self._safe_get_chat_info(message.channel_id) if self.config.include_metadata_context else None
        self._channel_states[channel_key] = _ChannelState(
            session=session,
            session_id=session_id,
            last_active=time.time(),
            user_cache={},
            channel_info=channel_info,
        )
        self._evict_idle_channels()
        return session, session_id

    async def _handle_control_command(self, message: IMIncomingMessage) -> bool:
        text = message.text.strip()
        if not text.startswith("/"):
            return False

        if text in {"/new", "/clear"}:
            self._invalidate_channel_session(message)
            session_id = self.router.rotate_session_id(
                platform=message.platform,
                channel_id=message.channel_id,
                thread_id=message.thread_id,
            )
            self._send_reply(message, f"Started a new MomoClaw session: `{session_id}`")
            return True

        if text == "/session":
            session_id = self.router.get_or_create_session_id(
                platform=message.platform,
                channel_id=message.channel_id,
                thread_id=message.thread_id,
            )
            self._send_reply(message, f"Current session: `{session_id}`")
            return True

        if text == "/usage":
            state = self._channel_states.get(self._channel_key(message))
            usage = self._format_usage(state.session) if state else ""
            self._send_reply(message, usage or "No usage recorded for this IM session yet.")
            return True

        if text == "/help":
            self._send_reply(
                message,
                "\n".join(
                    [
                        "MomoClaw IM commands:",
                        "/help - show this help",
                        "/new - start a new channel session",
                        "/clear - start a new channel session",
                        "/session - show current session id",
                        "/usage - show last model usage",
                    ]
                ),
            )
            return True

        return False

    def _build_prompt_text(self, message: IMIncomingMessage) -> str:
        parts: list[str] = []
        if not self.config.include_metadata_context:
            return message.text
        user_info = self._safe_get_user_info(message.user_id)
        if user_info and user_info.name:
            parts.append(f"[sender: {user_info.name}]")
        state = self._channel_states.get(self._channel_key(message))
        if state and state.channel_info and state.channel_info.name:
            parts.append(f"[channel: {state.channel_info.name}]")
        if not parts:
            return message.text
        return f"{' '.join(parts)}\n\n{message.text}"

    def _resolve_model(self) -> Model:
        model = self.config.model or get_model(self.config.provider, self.config.model_id)
        if self.config.base_url:
            model = replace(model, base_url=self.config.base_url)
        return model

    def _send_reply(self, message: IMIncomingMessage, text: str) -> None:
        if self.config.use_card_reply:
            try:
                self.adapter.send_card(
                    IMOutgoingCard(
                        channel_id=message.channel_id,
                        title="MomoClaw",
                        markdown_content=text,
                        thread_id=message.thread_id,
                        reply_to_message_id=message.message_id,
                    )
                )
                return
            except Exception:
                logger.exception("card reply failed, falling back to text")

        self.adapter.send_text(
            IMOutgoingText(
                channel_id=message.channel_id,
                text=text,
                thread_id=message.thread_id,
                reply_to_message_id=message.message_id,
            )
        )

    def _invalidate_channel_session(self, message: IMIncomingMessage) -> None:
        state = self._channel_states.pop(self._channel_key(message), None)
        if state:
            state.session.close()

    def _evict_idle_channels(self) -> None:
        now = time.time()
        expired = [
            key for key, state in self._channel_states.items()
            if now - state.last_active > self.config.session_idle_timeout
        ]
        for key in expired:
            state = self._channel_states.pop(key)
            state.session.close()

    def _safe_get_user_info(self, user_id: str) -> IMUserInfo | None:
        try:
            return self.adapter.get_user_info(user_id)
        except Exception:
            logger.exception("get_user_info failed user_id=%s", user_id)
            return None

    def _safe_get_chat_info(self, chat_id: str) -> IMChannelInfo | None:
        try:
            return self.adapter.get_chat_info(chat_id)
        except Exception:
            logger.exception("get_chat_info failed chat_id=%s", chat_id)
            return None

    @staticmethod
    def _format_usage(session: AgentSession) -> str:
        usage = session.last_usage
        if not usage:
            return ""
        tokens = usage.get("total_tokens", 0)
        if not tokens:
            return ""
        cost = usage.get("cost", 0.0)
        parts = [f"tokens: {tokens}"]
        if isinstance(cost, int | float) and cost > 0:
            parts.append(f"cost: ${cost:.4f}")
        return " | ".join(parts)

    @staticmethod
    def _extract_assistant_text(messages: list[object]) -> str:
        for message in reversed(messages):
            if isinstance(message, AssistantMessage):
                return "".join(block.text for block in message.content if isinstance(block, TextContent)).strip()
        return ""

    @staticmethod
    def _extract_last_assistant_text(session: AgentSession) -> str:
        return IMService._extract_assistant_text(session.messages)

    @staticmethod
    def _is_stale_event(message: IMIncomingMessage) -> bool:
        return message.created_at is not None and time.time() - message.created_at > _STALE_EVENT_SECONDS

    def _is_duplicate_message(self, message_id: str | None) -> bool:
        return bool(message_id and message_id in self._processed_ids)

    def _mark_processed(self, message_id: str | None) -> None:
        if not message_id or message_id in self._processed_ids:
            return
        self._processed_ids.add(message_id)
        self._processed_id_order.append(message_id)
        while len(self._processed_id_order) > self._processed_id_limit:
            old = self._processed_id_order.popleft()
            self._processed_ids.discard(old)

    @staticmethod
    def _channel_key(message: IMIncomingMessage) -> str:
        return f"{message.platform}:{message.channel_id}:{message.thread_id or '_'}"
