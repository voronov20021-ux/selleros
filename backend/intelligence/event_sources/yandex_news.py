"""
event_sources/yandex_news.py — адаптер рыночных событий через Yandex Search.

YandexNewsAdapter оборачивает YandexSearchAdapter: получает KnowledgeItem
из поисковой выдачи, классифицирует их rule-based в MarketEvent.

Классификация:
    sale        — акция, распродажа, скидка, промо
    holiday     — праздник, Новый год, 8 марта, 1 сентября, …
    regulation  — закон, постановление, требование, ограничение, сертификат
    competitor  — конкурент, другой продавец, бренд запустил, лидер рынка
    platform    — WB/Wildberries/Ozon изменил, комиссия, алгоритм, правила площадки

Правило строгости: если текст не попадает однозначно ни в одну категорию —
событие не создаётся.

Impact direction:
    positive — явные слова роста, прибыли, роста продаж
    negative — запреты, штрафы, падение, санкции
    neutral  — всё остальное (умолчание)
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Sequence

from backend.intelligence.event_sources.base import EventSourceAdapter
from backend.intelligence.models import (
    EventType,
    ImpactDirection,
    ItemType,
    KnowledgeItem,
    MarketEvent,
)
from backend.intelligence.sources.yandex_search import YandexSearchAdapter

log = logging.getLogger("selleros.intelligence.event_sources.yandex_news")

# ─────────────────────────── минимальный confidence ──────────────────────── #
_MIN_CONFIDENCE = 0.45

# ─────────────────────────────── паттерны классификации ───────────────────── #

_PATTERNS: dict[EventType, list[str]] = {
    EventType.SALE: [
        r"распродаж", r"акци[яи]", r"скидк[аи]", r"промо", r"sale",
        r"чёрная пятница", r"черная пятница", r"киберпонедельник",
        r"специальн\w+ предложени", r"сезонн\w+ скидк", r"%-off",
        r"мега.?акци", r"распродажа сезона",
    ],
    EventType.HOLIDAY: [
        r"новый год", r"8 март", r"23 феврал", r"1 сентябр", r"14 феврал",
        r"день матери", r"день защиты детей", r"пасх", r"масленниц",
        r"halloween", r"хэллоуин", r"день рожден", r"праздник",
        r"ноябрьские праздники", r"майские праздники", r"летние каникул",
    ],
    EventType.REGULATION: [
        r"закон\w*", r"постановлени", r"требовани", r"сертификат",
        r"маркировк", r"честный знак", r"роспотребнадзор", r"гост",
        r"тр тс", r"технический регламент", r"ограничени[ея]",
        r"запрет\w*", r"обязательн\w+ требовани", r"фас\b",
    ],
    EventType.PLATFORM: [
        r"wildberries", r"\bwb\b", r"вайлдберриз", r"маркетплейс\w* изменил",
        r"комисси[яи]", r"алгоритм\w* поиска", r"правила площадки",
        r"условия продавц", r"логистик\w+ тариф", r"ozon изменил",
        r"новые правила wb", r"wb повысил", r"wb снизил",
    ],
    EventType.COMPETITOR: [
        r"конкурент", r"другой продавец", r"лидер рынка",
        r"бренд запустил", r"топ продавец", r"доля рынка",
        r"новый игрок", r"вышел на рынок", r"захват рынка",
    ],
}

# ─────────────────────────── паттерны impact direction ────────────────────── #

_POSITIVE_SIGNALS = [
    r"рост\w* продаж", r"увеличени\w+ спроса", r"прибыль\w* выросл",
    r"повышени\w+ конверси", r"позитивн", r"выгодн", r"прибыльн",
    r"рекорд\w+ продаж",
]

_NEGATIVE_SIGNALS = [
    r"запрет\w*", r"штраф\w*", r"санкци", r"падени\w+ продаж",
    r"снижени\w+ спроса", r"убыток", r"блокировк", r"исчез\w+ с полок",
]


def _compile(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(patterns), re.IGNORECASE)


_COMPILED: dict[EventType, re.Pattern] = {
    k: _compile(v) for k, v in _PATTERNS.items()
}
_POS_RE = _compile(_POSITIVE_SIGNALS)
_NEG_RE = _compile(_NEGATIVE_SIGNALS)


def _classify(text: str) -> EventType | None:
    """
    Вернуть EventType если текст однозначно попадает в одну категорию.
    Если совпадений нет или их больше одной категории — вернуть None.
    """
    hits: list[EventType] = []
    for event_type, pattern in _COMPILED.items():
        if pattern.search(text):
            hits.append(event_type)

    if len(hits) == 1:
        return hits[0]

    # При нескольких совпадениях берём первый по приоритету (sale > holiday > …)
    _priority = [
        EventType.SALE,
        EventType.HOLIDAY,
        EventType.PLATFORM,
        EventType.REGULATION,
        EventType.COMPETITOR,
    ]
    for pt in _priority:
        if pt in hits:
            return pt

    return None


def _impact(text: str) -> ImpactDirection:
    pos = bool(_POS_RE.search(text))
    neg = bool(_NEG_RE.search(text))
    if pos and not neg:
        return ImpactDirection.POSITIVE
    if neg and not pos:
        return ImpactDirection.NEGATIVE
    return ImpactDirection.NEUTRAL


def _confidence(item: KnowledgeItem, event_type: EventType) -> float:
    """
    Консервативная оценка confidence.

    Базовая: 0.50 (текст не верифицирован из первоисточника).
    +0.05 если есть source_url.
    +0.05 если есть числа в тексте (дата, %, цена).
    -0.10 для COMPETITOR (самый субъективный тип).
    Итог округляется до 0.40–0.70.
    """
    conf = 0.50
    if item.source_url:
        conf += 0.05
    if re.search(r"\d", item.content or ""):
        conf += 0.05
    if event_type == EventType.COMPETITOR:
        conf -= 0.10
    return round(max(0.35, min(0.70, conf)), 4)


def _extract_title(item: KnowledgeItem) -> str:
    """Извлечь заголовок из метаданных или первую строку content."""
    title = (item.metadata or {}).get("title", "")
    if title:
        return str(title)[:200]
    first_line = (item.content or "").split("\n")[0]
    return first_line[:200]


def _extract_description(item: KnowledgeItem) -> str | None:
    """Извлечь сниппет/описание из метаданных."""
    snippet = (item.metadata or {}).get("snippet", "")
    if snippet:
        return str(snippet)[:500]
    return None


class YandexNewsAdapter(EventSourceAdapter):
    """
    Адаптер рыночных событий поверх Yandex Search.

    Использует YandexSearchAdapter для получения KnowledgeItem,
    затем классифицирует их rule-based в MarketEvent.
    Не делает отдельный HTTP-клиент.
    """

    _SOURCE_ID = "yandex_news"

    def __init__(self, search_adapter: YandexSearchAdapter | None = None) -> None:
        self._search = search_adapter or YandexSearchAdapter()

    @property
    def source_id(self) -> str:
        return self._SOURCE_ID

    @property
    def capabilities(self) -> list[str]:
        return [
            "market_news",
            "platform_news",
            "regulation_news",
            "competitor_news",
            "category_news",
            "sale_events",
            "holiday_events",
        ]

    async def is_available(self) -> bool:
        return await self._search.is_available()

    async def fetch(
        self,
        *,
        query: str,
        category: str | None = None,
        region: str = "RU",
        limit: int = 10,
    ) -> list[MarketEvent]:
        """
        Получить KnowledgeItem из Yandex Search и классифицировать в MarketEvent.

        Если item не классифицируется однозначно — пропустить.
        Если confidence ниже _MIN_CONFIDENCE — пропустить.
        """
        items: list[KnowledgeItem] = await self._search.fetch(
            query=query,
            category=category,
            region=region,
        )

        events: list[MarketEvent] = []
        now = time.time()

        for item in items[:limit]:
            text = " ".join(filter(None, [
                (item.metadata or {}).get("title", ""),
                (item.metadata or {}).get("snippet", ""),
                item.content or "",
            ]))

            event_type = _classify(text)
            if event_type is None:
                log.debug(
                    "YandexNewsAdapter: пропускаем неклассифицируемый item %s",
                    item.id,
                )
                continue

            conf = _confidence(item, event_type)
            if conf < _MIN_CONFIDENCE:
                log.debug(
                    "YandexNewsAdapter: confidence %.2f < %.2f для %s, пропускаем",
                    conf, _MIN_CONFIDENCE, item.id,
                )
                continue

            title = _extract_title(item)
            if not title:
                continue

            ev = MarketEvent(
                id=str(uuid.uuid4()),
                event_type=event_type,
                title=title,
                source_id=self.source_id,
                event_date=item.published_at or now,
                created_at=now,
                description=_extract_description(item),
                category=category,
                region=region,
                impact_direction=_impact(text),
                confidence=conf,
                metadata={
                    "source_url":    item.source_url,
                    "knowledge_item_id": item.id,
                    "query":         query,
                    "domain":        (item.metadata or {}).get("domain"),
                    "classifier":    "rule_based_v1",
                },
            )
            events.append(ev)

        log.info(
            "YandexNewsAdapter: query=%r → %d items → %d events",
            query, len(items), len(events),
        )
        return events
