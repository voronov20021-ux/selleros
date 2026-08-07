"""
cooldown.py — «остывание» ОДНОГО источника.

Старая система держала один RateLimiter на все источники и всех
пользователей сразу: 10-120 секунд между любыми двумя запросами,
для всех разом. Здесь та же идея экспоненциального роста задержки
при блокировке, но у КАЖДОГО источника — свой отдельный счётчик.

Отличие по сути: старый RateLimiter.wait() физически СПИТ перед
каждым запросом. Здесь никто не спит — если источник "остывает",
WBEngine просто пропускает его и сразу идёт к следующему. Ждать
есть смысл, только если ждать больше некого — это отдельная,
осознанная политика "движка", а не самого счётчика.
"""

from __future__ import annotations

import time


class AdaptiveCooldown:

    def __init__(self, min_penalty: float = 5.0, max_penalty: float = 300.0):
        self.min_penalty = min_penalty
        self.max_penalty = max_penalty
        self.penalty = min_penalty
        self.blocked_until = 0.0

    def is_cooling(self) -> bool:
        return time.time() < self.blocked_until

    def seconds_left(self) -> float:
        return max(0.0, self.blocked_until - time.time())

    def mark_blocked(self) -> None:
        """Источник ответил 429 — отдыхает, в следующий раз пауза больше."""
        self.blocked_until = time.time() + self.penalty
        self.penalty = min(self.max_penalty, self.penalty * 2)

    def mark_success(self) -> None:
        """Источник ответил нормально — постепенно снижаем осторожность."""
        self.blocked_until = 0.0
        self.penalty = max(self.min_penalty, self.penalty * 0.7)
