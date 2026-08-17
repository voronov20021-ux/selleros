"""
market_events.py — MarketEvent Intelligence v2.

MarketEventIngestor — поточная обработка KnowledgeItem → MarketEvent.

Ключевые отличия от MarketEventEngine (v1):
  * Работает на уровне одного KnowledgeItem, не пакета от адаптера.
  * Более точное определение event_type (SALE / HOLIDAY / REGULATION /
    PLATFORM / COMPETITOR / ECONOMIC).
  * impact_score -1..+1 в event.metadata["impact_score"].
  * Строгий CategoryResolver (min_matches=2): неоднозначная категория → None.
  * Temporal relevance: retrieve() возвращает только актуальные события
    с confidence decay для старых, исключает будущие и истёкшие.
  * Не создаёт событие при confidence < MIN_INGEST_CONFIDENCE.
  * Дедупликация через EvidenceEngine.fingerprint.

Данные в production-коде не синтетические.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import replace

from backend.intelligence.evidence.category import CategoryResolver
from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    DataSource,
    Evidence,
    EvidenceType,
    EventType,
    ImpactDirection,
    ItemType,
    KnowledgeItem,
    MarketEvent,
    SourceType,
)

log = logging.getLogger("selleros.intelligence.market_events_v2")

# ─── пороги ──────────────────────────────────────────────────────────── #

MIN_INGEST_CONFIDENCE: float = 0.40   # ниже — событие не создаётся
MIN_RETRIEVE_CONFIDENCE: float = 0.30  # ниже — не возвращается из retrieve()
DECAY_HALF_LIFE_DAYS: float = 15.0    # за сколько дней confidence падает вдвое

# ─── паттерны типов событий ──────────────────────────────────────────── #

_TYPE_PATTERNS: list[tuple[EventType, re.Pattern, float]] = [
    # (EventType, compiled pattern, base_impact_score)
    (
        EventType.SALE,
        re.compile(
            r"распродаж|акци[яи]|скидк|sale\b|промо|распродает|скидочн|"
            r"flash.?sale|day[s]?\s+sale|mega\s+sale|черная\s+пятница|"
            r"киберпонедельник|киберпоне|wildberries\s+(акция|скидки)",
            re.I | re.U,
        ),
        +0.60,
    ),
    (
        EventType.HOLIDAY,
        re.compile(
            r"праздник|новый\s+год|рождество|8\s+март|23\s+февраля|"
            r"день\s+(рождения|матери|отца|влюбленных|победы|защитника)|"
            r"14\s+февраля|хэллоуин|пасха|ид|курбан|"
            r"holiday|christmas|new\s+year|valentine",
            re.I | re.U,
        ),
        +0.40,
    ),
    (
        EventType.REGULATION,
        re.compile(
            r"требовани[яе]|сертификац|маркировк|закон|регулятор|"
            r"запрет|ограничени[яе]|проверк[аи]\s+(Роспотреб|ФТС|таможн)|"
            r"пошлин|тарифы|лицензи|норматив|стандарт\s+(безопасности|качества)|"
            r"regulation|compliance|ban\b|sanction",
            re.I | re.U,
        ),
        -0.25,
    ),
    (
        EventType.PLATFORM,
        re.compile(
            r"wildberries|wb\b|вб\b|ozon|озон|маркетплейс.+(изменил|обновил|"
            r"ввёл|запустил|новый)|алгоритм.+(поиска|ранжирования|выдачи)|"
            r"комиссия.+(wb|вб|маркетплейс)|сбо[яи]|платформ.+(обновлени|изменени)|"
            r"выдача\s+(wb|вб)|новые\s+правила\s+(wb|вб|маркетплейс)",
            re.I | re.U,
        ),
        -0.10,
    ),
    (
        EventType.COMPETITOR,
        re.compile(
            r"конкурент|конкуренц|rival|competitor|лидер.+(рынк|категори)|"
            r"новый\s+(игрок|бренд|поставщик)\s+на\s+рынк|"
            r"захват\s+(рынка|доли)|демпинг|ценовая\s+война|"
            r"вошел\s+на\s+рынок",
            re.I | re.U,
        ),
        -0.20,
    ),
    (
        EventType.ECONOMIC,
        re.compile(
            r"курс\s+(доллара|евро|юаня|рубля)|инфляци[яи]|девальваци|"
            r"экономик[аи]\s+(рост|падение|кризис|замедл)|"
            r"ключевая\s+ставка|цб\s+рф|центробанк|снижение\s+(ставки|спроса)|"
            r"санкци[яи]|импортозамещени|GDP|ВВП|recession|кризис",
            re.I | re.U,
        ),
        -0.15,
    ),
]

# порядок приоритетности при конкурирующих совпадениях
_TYPE_PRIORITY: dict[EventType, int] = {
    EventType.SALE:       1,
    EventType.HOLIDAY:    2,
    EventType.REGULATION: 3,
    EventType.PLATFORM:   4,
    EventType.COMPETITOR: 5,
    EventType.ECONOMIC:   6,
}


# ─── CategoryResolver (строгий) ───────────────────────────────────────── #

class _StrictCategoryResolver:
    """
    Обёртка над CategoryResolver с повышенным порогом (min_matches=2).
    Неоднозначная или слабо подкреплённая категория → None.
    """

    def __init__(self) -> None:
        self._inner = CategoryResolver()

    def resolve(self, query: str = "", content: str = "") -> str | None:
        return self._inner.resolve(query=query, content=content, min_matches=2)


# ─── temporal helpers ────────────────────────────────────────────────── #

def _decay(confidence: float, age_days: float) -> float:
    """
    Экспоненциальный decay: каждые DECAY_HALF_LIFE_DAYS дней confidence
    уменьшается вдвое. Результат ≥ 0.
    """
    factor = 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)
    return max(0.0, confidence * factor)


# ─── MarketEventIngestor ─────────────────────────────────────────────── #

class MarketEventIngestor:
    """
    MarketEvent Intelligence v2.

    ingest_event(ki)       — создать и сохранить MarketEvent из KnowledgeItem.
    retrieve(cat, reg, days) — вернуть актуальные события с temporal decay.
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        ev_engine: EvidenceEngine,
    ) -> None:
        self._store    = store
        self._ev       = ev_engine
        self._cat_res  = _StrictCategoryResolver()

    # ─────────────────────────────── ingest ──────────────────────────── #

    async def ingest_event(self, ki: KnowledgeItem) -> MarketEvent | None:
        """
        Создать MarketEvent из KnowledgeItem.

        Возвращает None если:
          - контент не распознан как рыночное событие;
          - confidence после оценки < MIN_INGEST_CONFIDENCE;
          - событие-дубликат уже в store.
        """
        text = f"{ki.source_url or ''} {ki.content}"

        event_type = self._detect_event_type(text)
        if event_type is None:
            log.debug("ingest_event: тип не определён для %r", text[:80])
            return None

        base_impact_score = self._base_impact(event_type)
        impact_score = self._adjust_impact(base_impact_score, text, event_type)
        impact_dir   = self._impact_direction(impact_score)

        confidence = self._calc_confidence(ki, event_type)
        if confidence < MIN_INGEST_CONFIDENCE:
            log.debug(
                "ingest_event: confidence %.2f ниже порога для %r",
                confidence, ki.content[:80],
            )
            return None

        # Дедупликация: сначала точная проверка по URL (без ограничения 5 записей),
        # затем fingerprint-проверка через EvidenceEngine.
        if await self._is_url_in_store(ki):
            log.debug("ingest_event: дубликат (URL) пропущен — %r", ki.source_url)
            return None
        if await self._ev.is_duplicate(ki):
            log.debug("ingest_event: дубликат (fingerprint) пропущен — %r", ki.content[:60])
            return None

        category = ki.category or self._cat_res.resolve(
            query=ki.metadata.get("query", "") if ki.metadata else "",
            content=ki.content,
        )

        region = ki.region or "RU"
        event_date = ki.published_at or ki.collected_at

        event = MarketEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            title=ki.content[:200].strip(),
            source_id=ki.source_id,
            event_date=event_date,
            created_at=time.time(),
            description=None,
            category=category,
            region=region,
            impact_direction=impact_dir,
            confidence=confidence,
            metadata={
                "impact_score": round(impact_score, 3),
                "source_url":   ki.source_url,
                "ki_id":        ki.id,
                "query":        (ki.metadata or {}).get("query"),
            },
        )

        # Гарантируем наличие source_id в data_sources (FK)
        await self._ensure_source(ki.source_id)

        await self._store.save_item(ki)
        await self._store.save_market_event(event)

        evidence = self._make_evidence(event, ki.id)
        await self._store.save_evidence(evidence)

        log.info(
            "ingest_event: %s [%s] conf=%.2f impact=%.2f — %r",
            event_type.value, category or "?",
            confidence, impact_score, event.title[:60],
        )
        return event

    # ─────────────────────────────── retrieve ────────────────────────── #

    async def retrieve(
        self,
        category: str | None = None,
        region: str | None = None,
        days: int = 30,
    ) -> list[MarketEvent]:
        """
        Вернуть актуальные события с temporal confidence decay.

        Правила временно́й фильтрации:
          - future events (event_date > now) → исключаются;
          - events старше `days` дней → исключаются;
          - events старше days/2 → confidence × decay;
          - эффективный confidence < MIN_RETRIEVE_CONFIDENCE → исключается.

        Результат отсортирован по event_date DESC.
        """
        now = time.time()
        cutoff_ts  = now - days * 86400
        half_ts    = now - (days / 2) * 86400

        raw = await self._store.list_market_events(
            category=category,
            after_ts=cutoff_ts,
            limit=200,
        )

        result: list[MarketEvent] = []
        for ev in raw:
            # Исключить будущие события
            if ev.event_date > now:
                continue

            # Исключить события за пределами окна
            if ev.event_date < cutoff_ts:
                continue

            # Применить region-фильтр
            if region and ev.region and ev.region != region:
                continue

            # Temporal decay
            age_days = (now - ev.event_date) / 86400
            eff_conf = _decay(ev.confidence, age_days)

            if eff_conf < MIN_RETRIEVE_CONFIDENCE:
                continue

            # Возвращаем событие с обновлённым confidence в metadata
            meta = dict(ev.metadata or {})
            meta["effective_confidence"] = round(eff_conf, 3)
            meta["age_days"] = round(age_days, 1)
            result.append(replace(ev, confidence=eff_conf, metadata=meta))

        result.sort(key=lambda e: e.event_date, reverse=True)
        return result

    # ─────────────────────────────── helpers ─────────────────────────── #

    def _detect_event_type(self, text: str) -> EventType | None:
        """
        Определить тип события из текста.

        При совпадении нескольких типов — выбирается с наименьшим priority
        (более специфичный). При полном отсутствии совпадений → None.
        """
        matches: list[tuple[int, EventType]] = []
        for event_type, pattern, _ in _TYPE_PATTERNS:
            if pattern.search(text):
                matches.append((_TYPE_PRIORITY[event_type], event_type))

        if not matches:
            return None

        # Выбираем тип с наименьшим priority-числом (наиболее специфичный)
        matches.sort(key=lambda x: x[0])
        return matches[0][1]

    @staticmethod
    def _base_impact(event_type: EventType) -> float:
        return {
            EventType.SALE:       +0.60,
            EventType.HOLIDAY:    +0.40,
            EventType.REGULATION: -0.25,
            EventType.PLATFORM:   -0.10,
            EventType.COMPETITOR: -0.20,
            EventType.ECONOMIC:   -0.15,
        }.get(event_type, 0.0)

    @staticmethod
    def _adjust_impact(base: float, text: str, event_type: EventType) -> float:
        """
        Скорректировать impact_score на основе текстовых маркеров.
        Диапазон ограничен [-1, +1].
        """
        score = base
        text_l = text.lower()

        # Позитивные усилители
        if any(w in text_l for w in ("рост", "увеличение", "рекорд", "выгода", "profit")):
            score = min(1.0, score + 0.10)

        # Негативные усилители
        if any(w in text_l for w in ("штраф", "блокировк", "запрет", "кризис", "крах", "обвал")):
            score = max(-1.0, score - 0.15)

        # Нейтрализаторы — «слухи», «возможно», «предполагается»
        if any(w in text_l for w in ("слух", "возможно", "предположительно", "планируется")):
            score *= 0.5

        return round(max(-1.0, min(1.0, score)), 3)

    @staticmethod
    def _impact_direction(impact_score: float) -> ImpactDirection:
        if impact_score > 0.15:
            return ImpactDirection.POSITIVE
        if impact_score < -0.15:
            return ImpactDirection.NEGATIVE
        return ImpactDirection.NEUTRAL

    @staticmethod
    def _calc_confidence(ki: KnowledgeItem, event_type: EventType) -> float:
        """
        Рассчитать confidence для MarketEvent на основе KnowledgeItem.

        Факторы:
          + базовый confidence из KI
          + наличие source_url
          + наличие published_at (датированный источник)
          + авторитетный event_type (REGULATION > ECONOMIC > остальные)
          - короткий текст (< 50 символов)
        """
        score = ki.confidence

        if ki.source_url:
            score += 0.05
        if ki.published_at:
            score += 0.05
        if event_type == EventType.REGULATION:
            score += 0.05
        elif event_type == EventType.ECONOMIC:
            score += 0.03

        if len(ki.content) < 50:
            score -= 0.10

        return round(min(1.0, max(0.0, score)), 3)

    @staticmethod
    def _make_evidence(event: MarketEvent, ki_id: str) -> Evidence:
        impact_str = ""
        if event.impact_direction and event.impact_direction != ImpactDirection.NEUTRAL:
            impact_str = f" (влияние: {event.impact_direction.value})"

        impact_score = event.metadata.get("impact_score", 0.0) if event.metadata else 0.0

        return Evidence(
            id=str(uuid.uuid4()),
            knowledge_item_id=ki_id,
            evidence_type=EvidenceType.FACT,
            claim=(
                f"[{event.event_type.value.upper()}]{impact_str} "
                f"{event.title[:150]}"
            ),
            created_at=time.time(),
            confidence=event.confidence,
            supporting_data={
                "signal_type":     "market_event",
                "event_type":      event.event_type.value,
                "impact_direction": event.impact_direction.value
                                    if event.impact_direction else "neutral",
                "impact_score":    impact_score,
                "category":        event.category,
                "region":          event.region,
                "source_url":      (event.metadata or {}).get("source_url"),
                "event_date":      event.event_date,
            },
        )

    async def _is_url_in_store(self, ki: KnowledgeItem) -> bool:
        """True если KnowledgeItem с таким же source_url уже сохранён."""
        if not ki.source_url:
            return False
        url_norm = ki.source_url.strip().rstrip("/")
        # Берём больше записей, чем дефолтный limit=5 в EvidenceEngine.is_duplicate
        existing = await self._store.search_items(source_id=ki.source_id, limit=500)
        for ex in existing:
            if (ex.source_url or "").strip().rstrip("/") == url_norm and ex.id != ki.id:
                return True
        return False

    async def _ensure_source(self, source_id: str) -> None:
        existing = await self._store.get_source(source_id)
        if existing is not None:
            return
        await self._store.save_source(DataSource(
            id=source_id,
            name=source_id.replace("_", " ").title(),
            source_type=SourceType.PUBLIC_API,
            authority=0.55,
            freshness_hours=6,
            capabilities=["market_news"],
        ))
