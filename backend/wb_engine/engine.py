"""
engine.py — WBEngine, оркестратор источников данных.

Путь запроса:

    get_product(article)
        ↓
    1. кэш — вдруг уже спрашивали недавно (или кто-то спрашивает
       прямо сейчас параллельно — тогда просто ждём тот же ответ)
        ↓
    2. источники по приоритету:
        для каждого:
            - недоступен (нет ключа) -> пропустить
            - "остывает" после недавней блокировки -> пропустить,
              не тратя время на заведомо обречённую попытку
            - SourceBlocked -> запомнить остывание ИМЕННО для
              этого источника, перейти к следующему
            - SourceNotFound -> остановиться прямо сейчас,
              это окончательный ответ, других источников не спрашивать
            - SourceUnavailable / любая другая ошибка -> залогировать,
              перейти к следующему
            - нашли -> положить в кэш, вернуть
        ↓
    3. если совсем никто не ответил — вернуть None
       (вызывающий код уже умеет показать "карточка недоступна")

Именно на шаге 2 чинится главный баг старой системы: там исключение
после исчерпания попыток пролетало мимо проверки "пробуем
следующего" и падало наружу. Здесь оно ловится РОВНО там,
где должно, и цепочка идёт дальше.
"""

from __future__ import annotations

import logging

from backend.wb.cdn_provider import WBProduct
from backend.wb_engine.cache import ProductCache
from backend.wb_engine.cooldown import AdaptiveCooldown
from backend.wb_engine.errors import SourceBlocked, SourceNotFound, SourceUnavailable
from backend.wb_engine.source import DataSource

log = logging.getLogger("selleros.wb_engine")


class WBEngine:

    def __init__(self, cache: ProductCache | None = None):
        self.cache = cache or ProductCache()
        # (приоритет, источник) — сортируется при каждой регистрации,
        # источников мало, лишней сложности сортировка не добавляет.
        self._sources: list[tuple[int, DataSource]] = []
        self._cooldowns: dict[str, AdaptiveCooldown] = {}

    def register(self, source: DataSource, priority: int = 10) -> None:
        """Подключить источник. priority меньше = пробуется раньше."""
        self._sources.append((priority, source))
        self._sources.sort(key=lambda item: item[0])
        self._cooldowns[source.name] = AdaptiveCooldown()
        log.info("WB Engine: источник подключён — %s (приоритет %d)", source.name, priority)

    async def get_product(self, article: int) -> WBProduct | None:
        return await self.cache.get_or_fetch(
            key=f"product:{article}",
            fetch=lambda: self._fetch_from_sources(article),
        )

    async def _fetch_from_sources(self, article: int) -> WBProduct | None:
        for priority, source in self._sources:
            cooldown = self._cooldowns[source.name]

            if cooldown.is_cooling():
                log.info(
                    "WB Engine: %s остывает ещё %.0f сек — пропускаю",
                    source.name, cooldown.seconds_left(),
                )
                continue

            if not await self._safe_available(source):
                continue

            try:
                product = await source.fetch(article)

            except SourceBlocked as error:
                cooldown.mark_blocked()
                log.warning(
                    "WB Engine: %s заблокирован (%s), остывает %.0f сек",
                    source.name, error, cooldown.seconds_left(),
                )
                continue

            except SourceNotFound:
                log.info("WB Engine: %s сообщил — товара %s не существует", source.name, article)
                return None

            except SourceUnavailable as error:
                log.info("WB Engine: %s временно недоступен: %s", source.name, error)
                continue

            except Exception as error:
                # Источник написан не по контракту (не поднял наш класс
                # ошибки) — не даём ему уронить всю цепочку, но и не
                # притворяемся, что всё в порядке: пишем в лог погромче.
                log.exception("WB Engine: %s упал неожиданно: %s", source.name, error)
                continue

            if product is not None:
                cooldown.mark_success()
                log.info("WB Engine: товар %s получен через %s", article, source.name)
                log.info(
                    "WB Engine: article=%s источник=%s | фото=%s | описание=%s | "
                    "характеристики=%s | цена=%s | рейтинг=%s | отзывы=%s",
                    article,
                    source.name,
                    bool(product.photos or product.photo_count),
                    bool(product.description),
                    bool(product.characteristics),
                    product.price,  # может быть None — это ОК, не ошибка
                    product.rating,  # может быть None — это ОК, не ошибка
                    product.feedbacks,  # может быть None — это ОК, не ошибка
                )
                return product

            # Источник отработал штатно, но ничего не нашёл — это НЕ
            # ошибка источника (mark_success не трогаем cooldown,
            # но и не штрафуем), просто пробуем следующего.

        log.warning("WB Engine: товар %s не найден ни в одном источнике", article)
        return None

    @staticmethod
    async def _safe_available(source: DataSource) -> bool:
        try:
            return await source.is_available()
        except Exception as error:
            log.warning("WB Engine: %s.is_available() упал: %s", source.name, error)
            return False
