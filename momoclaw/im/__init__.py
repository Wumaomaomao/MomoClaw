"""IM bridge layer for MomoClaw."""

from .service import IMService, IMServiceConfig
from .session_router import SessionRouter
from .types import (
    IMAdapter,
    IMChannelInfo,
    IMIncomingMessage,
    IMOutgoingCard,
    IMOutgoingText,
    IMUserInfo,
    IMWebhookResult,
)

__all__ = [
    "IMAdapter",
    "IMChannelInfo",
    "IMIncomingMessage",
    "IMOutgoingCard",
    "IMOutgoingText",
    "IMService",
    "IMServiceConfig",
    "IMUserInfo",
    "IMWebhookResult",
    "SessionRouter",
]
