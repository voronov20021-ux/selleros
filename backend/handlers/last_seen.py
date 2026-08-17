"""Thin Telegram last_seen hook. Calls existing MemoryStore.touch_user."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

log = logging.getLogger("selleros.last_seen")


class LastSeenMiddleware(BaseMiddleware):
    def __init__(self, memory_store) -> None:
        self._memory = memory_store

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None and self._memory is not None:
            try:
                await self._memory.touch_user(int(user.id))
            except Exception as exc:
                log.debug("touch_user skip: %s", exc)
        return await handler(event, data)
