"""
rate_gate.py — единый глобальный шлюз для запросов к *.wb.ru.

Проблема, которую он решает: у CDNSource и SearchFallbackSource
(а в будущем — у источника отзывов feedbacks*.wb.ru) СВОЙ прокси и
своя HTTP-сессия. Если WBEngine при фейловере переходит от одного
источника к другому, оба могут отправить запрос к *.wb.ru почти
одновременно — независимо от того, какой прокси им достался.

WBRateGate — общий на весь процесс объект: перед ЛЮБЫМ запросом к
card.wb.ru / search.wb.ru / feedbacks*.wb.ru источник обязан
спросить try_acquire(). Гарантия — минимум 10 секунд между ЛЮБЫМИ
двумя запросами, кто бы их ни отправлял и через какой бы прокси.

Важно:
    • try_acquire() не спит и не крутит цикл — если слот занят,
      просто возвращает False. Источник в этом случае обязан не
      отправлять запрос вообще (SourceUnavailable), а не ждать.
    • Раз слот выдаётся ТОЛЬКО когда 10 секунд действительно прошли,
      403/429, полученный после успешного try_acquire(), — это
      честный сигнал о блокировке, а не следствие того, что мы сами
      выстрелили слишком часто. Поэтому источники могут смело
      блокировать прокси на таком ответе — интервал уже проверен
      здесь, до отправки запроса.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("selleros.wb_engine.rate_gate")


class WBRateGate:
    """Общий лимитер: не чаще одного запроса к *.wb.ru за MIN_INTERVAL секунд."""

    MIN_INTERVAL = 10.0

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0

    async def try_acquire(self) -> bool:
        """
        Занять слот на отправку запроса к *.wb.ru.

        True  — минимум MIN_INTERVAL секунд с прошлого запроса прошло,
                слот занят, запрос отправлять можно.
        False — слот ещё занят предыдущим запросом, отправлять НЕЛЬЗЯ.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if elapsed < self.MIN_INTERVAL:
                log.info(
                    "WB rate gate: запрос отклонён — с прошлого прошло %.1f сек (нужно %.0f)",
                    elapsed, self.MIN_INTERVAL,
                )
                return False
            self._last_request_at = now
            return True

    def seconds_since_last(self) -> float:
        return time.monotonic() - self._last_request_at


#: Единый на весь процесс шлюз. Импортируется всеми источниками WB —
#: у каждого источника СВОЙ прокси, но интервал между запросами общий.
wb_rate_gate = WBRateGate()
