"""Shared backend utilities (Telegram helpers, etc.)."""

from backend.utils.telegram_split import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    answer_long,
    edit_or_answer_long,
    split_telegram_message,
)

__all__ = [
    "TELEGRAM_MAX_MESSAGE_LENGTH",
    "answer_long",
    "edit_or_answer_long",
    "split_telegram_message",
]
