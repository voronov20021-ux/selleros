"""
dialog.py — память разговора.

Seller AI помнит последние 20 сообщений диалога, чтобы понимать
реплики вида «а если ещё дешевле?» — они бессмысленны без предыдущих.

Формат сообщений совпадает с форматом chat-API, поэтому история
уходит в модель как есть, без преобразований.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

#: Сколько последних сообщений храним (10 пар «вопрос-ответ»).
DIALOG_DEPTH = 20

#: Ограничение длины одной реплики в истории — чтобы не раздувать промпт.
MAX_MESSAGE_CHARS = 800


@dataclass
class ChatMessage:
    role: str      # "user" | "assistant"
    content: str

    def to_api(self) -> dict[str, str]:
        return {
            "role": self.role,
            "content": self.content[:MAX_MESSAGE_CHARS],
        }


class DialogMemory:
    """Кольцевой буфер сообщений одного разговора."""

    def __init__(self, depth: int = DIALOG_DEPTH):
        self._messages: deque[ChatMessage] = deque(maxlen=depth)

    def add_user(self, text: str) -> None:
        self._messages.append(ChatMessage("user", text))

    def add_assistant(self, text: str) -> None:
        self._messages.append(ChatMessage("assistant", text))

    def messages(self, limit: int | None = None) -> list[ChatMessage]:
        items = list(self._messages)
        return items[-limit:] if limit else items

    def to_api(self, limit: int | None = None) -> list[dict[str, str]]:
        """История в формате chat-API."""
        return [message.to_api() for message in self.messages(limit)]

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)
