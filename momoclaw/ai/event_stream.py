from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from .types import AssistantMessage

_SENTINEL = object()


class AssistantMessageEventStream:
    """Async event stream with a final AssistantMessage result."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._result: asyncio.Future[AssistantMessage] = asyncio.get_running_loop().create_future()
        self._closed = False

    def push(self, event: dict[str, Any]) -> None:
        if not self._closed:
            self._queue.put_nowait(event)

    def end(self, message: AssistantMessage) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._result.done():
            self._result.set_result(message)
        self._queue.put_nowait(_SENTINEL)

    def fail(self, error: Exception, fallback: AssistantMessage | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._result.done():
            if fallback is not None:
                self._result.set_result(fallback)
            else:
                self._result.set_exception(error)
        self._queue.put_nowait(_SENTINEL)

    async def result(self) -> AssistantMessage:
        return await self._result

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iter_events()

    async def _iter_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                break
            yield item

