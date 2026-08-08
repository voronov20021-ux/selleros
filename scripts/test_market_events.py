"""
scripts/test_market_events.py

Тесты Market Event Engine.
Реальные HTTP-запросы к Yandex Search НЕ выполняются.
Используется FakeNewsAdapter с заготовленными KnowledgeItem.

Проверяет:
  A. sale событие создаётся корректно
  B. holiday событие создаётся корректно
  C. platform/regulation событие создаётся корректно
  D. ambiguous news — MarketEvent НЕ создаётся
  E. duplicate URL — повторно НЕ сохраняется
  F. confidence ∈ [0, 1]
  G. impact_direction только positive/negative/neutral
  H. cooldown не вызывает повторный fetch
  I. события сохраняются в IntelligenceStore
  J. Evidence создаётся через EvidenceEngine
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


# ─────────────────────── FakeSearchAdapter / FakeNewsAdapter ─────────────── #

from backend.intelligence.event_sources.base import EventSourceAdapter
from backend.intelligence.event_sources.yandex_news import (
    YandexNewsAdapter,
    _classify,
    _impact,
    _confidence,
)
from backend.intelligence.models import (
    EventType,
    ImpactDirection,
    ItemType,
    KnowledgeItem,
    MarketEvent,
)
from backend.intelligence.sources.yandex_search import YandexSearchAdapter


class FakeSearchAdapter(YandexSearchAdapter):
    """Заглушка YandexSearchAdapter — возвращает предопределённые KnowledgeItem."""

    def __init__(self, items: list[KnowledgeItem]) -> None:
        # Не вызываем super().__init__() — нет API-ключа
        self._fake_items = items
        self._call_count = 0

    async def is_available(self) -> bool:
        return True

    async def fetch(self, *, query: str, category=None, region="RU") -> list[KnowledgeItem]:
        self._call_count += 1
        return list(self._fake_items)


def make_item(content: str, title: str = "", snippet: str = "",
              url: str | None = None) -> KnowledgeItem:
    return KnowledgeItem(
        id=str(uuid.uuid4()),
        source_id="yandex_news",
        source_url=url,
        collected_at=time.time(),
        published_at=None,
        item_type=ItemType.FACT,
        category=None,
        region="RU",
        period=None,
        confidence=0.70,
        content=content,
        metadata={
            "title":   title,
            "snippet": snippet,
            "url":     url or "",
            "domain":  "news.example.ru",
        },
    )


# ────────────────────────────── FakeEventAdapter ──────────────────────────── #

class FakeEventAdapter(EventSourceAdapter):
    """Возвращает готовые MarketEvent без HTTP."""

    def __init__(self, events: list[MarketEvent]) -> None:
        self._events = events
        self._call_count = 0

    @property
    def source_id(self) -> str:
        return "fake_news"

    @property
    def capabilities(self) -> list[str]:
        return ["market_news"]

    async def is_available(self) -> bool:
        return True

    async def fetch(self, *, query, category=None, region="RU", limit=10) -> list[MarketEvent]:
        self._call_count += 1
        return list(self._events[:limit])


def make_event(
    event_type: EventType,
    title: str,
    url: str | None = None,
    impact: ImpactDirection = ImpactDirection.NEUTRAL,
    confidence: float = 0.55,
    category: str | None = None,
    query: str = "test",
) -> MarketEvent:
    return MarketEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        title=title,
        source_id="fake_news",
        event_date=time.time(),
        created_at=time.time(),
        description=None,
        category=category,
        region="RU",
        impact_direction=impact,
        confidence=confidence,
        metadata={"source_url": url, "query": query},
    )


# ──────────────────────────────── тесты ──────────────────────────────────── #

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "FAIL"
    RESULTS.append((label, condition, detail))
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))


async def run_tests() -> None:
    from backend.intelligence.evidence.engine import EvidenceEngine
    from backend.intelligence.market_event_engine import MarketEventEngine
    from backend.intelligence.store import IntelligenceStore

    db_path = "data/_tmp_market_events_test.db"
    # Удаляем старый файл, если существует
    for suffix in ("", "-shm", "-wal"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)

    store = IntelligenceStore(db_path)
    await store.connect()
    ev_engine = EvidenceEngine(store=store)

    print("\n" + "=" * 60)
    print("MARKET EVENT ENGINE — тесты")
    print("=" * 60)

    # ── A. sale классифицируется корректно ──────────────────────────────── #
    print("\n--- A. sale event ---")
    sale_item = make_item(
        content="Wildberries запускает грандиозную распродажу со скидками до 70%",
        title="Распродажа WB: скидки до 70%",
        snippet="Акция стартует 1 ноября 2026",
        url="https://news.example.ru/wb-sale-nov2026",
    )
    sale_adapter = YandexNewsAdapter(search_adapter=FakeSearchAdapter([sale_item]))
    events_a = await sale_adapter.fetch(
        query="распродажа WB 2026", category="Одежда", region="RU"
    )
    check(
        "A. sale: событие создано",
        len(events_a) >= 1 and events_a[0].event_type == EventType.SALE,
        f"count={len(events_a)}, type={events_a[0].event_type.value if events_a else '-'}",
    )
    if events_a:
        check("A. sale: title непустой", bool(events_a[0].title))

    # ── B. holiday классифицируется корректно ──────────────────────────── #
    print("\n--- B. holiday event ---")
    hol_item = make_item(
        content="Новый год 2027 — рост продаж новогодних подарков ожидается рекордным",
        title="Новогодние подарки 2027: прогнозы",
        url="https://news.example.ru/newyear2027",
    )
    hol_adapter = YandexNewsAdapter(search_adapter=FakeSearchAdapter([hol_item]))
    events_b = await hol_adapter.fetch(query="новый год подарки", category="Игрушки")
    check(
        "B. holiday: событие создано",
        len(events_b) >= 1 and events_b[0].event_type == EventType.HOLIDAY,
        f"count={len(events_b)}, type={events_b[0].event_type.value if events_b else '-'}",
    )

    # ── C. platform / regulation ────────────────────────────────────────── #
    print("\n--- C. platform/regulation event ---")
    platform_item = make_item(
        content="Wildberries изменил комиссию для продавцов категории Электроника с 15% до 12%",
        title="WB снизил комиссию для электроники",
        url="https://news.example.ru/wb-commission-2026",
    )
    reg_item = make_item(
        content="Роспотребнадзор ввёл новые требования к сертификации детских товаров с 2027",
        title="Новые требования к сертификации детских товаров",
        url="https://news.example.ru/regulation-kids-2027",
    )
    plat_adapter = YandexNewsAdapter(search_adapter=FakeSearchAdapter([platform_item, reg_item]))
    events_c = await plat_adapter.fetch(query="маркетплейс правила 2026", region="RU")
    c_types = {e.event_type for e in events_c}
    check(
        "C. platform или regulation: создан(ы)",
        EventType.PLATFORM in c_types or EventType.REGULATION in c_types,
        f"types={[e.value for e in c_types]}",
    )

    # ── D. ambiguous → не создаётся ─────────────────────────────────────── #
    print("\n--- D. ambiguous → skip ---")
    ambiguous_item = make_item(
        content="Сегодня хорошая погода в Москве. Продажи идут как обычно.",
        title="Обычный день на рынке",
        url="https://news.example.ru/plain-day",
    )
    ambig_adapter = YandexNewsAdapter(search_adapter=FakeSearchAdapter([ambiguous_item]))
    events_d = await ambig_adapter.fetch(query="новости рынка")
    check(
        "D. ambiguous: событие НЕ создано",
        len(events_d) == 0,
        f"count={len(events_d)}",
    )

    # ── E. duplicate URL → не сохраняется повторно ──────────────────────── #
    print("\n--- E. duplicate URL → skip ---")
    dup_event = make_event(
        EventType.SALE,
        title="Распродажа WB: скидки до 70%",
        url="https://news.example.ru/wb-sale-dup",
        query="распродажа dup",
    )
    engine_e = MarketEventEngine(
        store=store, ev_engine=ev_engine,
        adapter=FakeEventAdapter([dup_event]),
    )
    saved1 = await engine_e.ingest([dup_event])
    saved2 = await engine_e.ingest([dup_event])  # дубликат
    check(
        "E. duplicate: первый раз сохранён",
        saved1 == 1,
        f"saved1={saved1}",
    )
    check(
        "E. duplicate: второй раз пропущен",
        saved2 == 0,
        f"saved2={saved2}",
    )

    # ── F. confidence ∈ [0, 1] ───────────────────────────────────────────── #
    print("\n--- F. confidence range ---")
    all_ev_f = events_a + events_b + events_c
    conf_ok = all(0.0 <= e.confidence <= 1.0 for e in all_ev_f)
    check(
        "F. confidence ∈ [0, 1]",
        conf_ok,
        f"events checked: {len(all_ev_f)}",
    )

    # ── G. impact_direction валидные значения ────────────────────────────── #
    print("\n--- G. impact_direction values ---")
    valid_impacts = {ImpactDirection.POSITIVE, ImpactDirection.NEGATIVE, ImpactDirection.NEUTRAL}
    impact_ok = all(
        e.impact_direction in valid_impacts for e in all_ev_f if e.impact_direction
    )
    check(
        "G. impact_direction: только valid значения",
        impact_ok,
        f"events checked: {len(all_ev_f)}",
    )

    # ── H. cooldown не вызывает повторный HTTP ──────────────────────────── #
    print("\n--- H. cooldown ---")
    cooldown_event = make_event(
        EventType.HOLIDAY,
        title="8 марта: распродажа подарков",
        url="https://news.example.ru/8march-sale",
        query="cooldown_test_query_8march",
    )
    cool_adapter = FakeEventAdapter([cooldown_event])
    engine_h = MarketEventEngine(
        store=store, ev_engine=ev_engine, adapter=cool_adapter,
    )
    # Первый collect — должен вызвать adapter.fetch
    events_h1 = await engine_h.collect(
        query="cooldown_test_query_8march",
        category=None,
        region="RU",
    )
    await engine_h.ingest(events_h1)
    calls_after_first = cool_adapter._call_count

    # Второй collect с тем же query — должен вернуть из cache
    events_h2 = await engine_h.collect(
        query="cooldown_test_query_8march",
        category=None,
        region="RU",
    )
    calls_after_second = cool_adapter._call_count

    check(
        "H. cooldown: первый вызов делает fetch",
        calls_after_first >= 1,
        f"calls={calls_after_first}",
    )
    check(
        "H. cooldown: второй вызов НЕ делает fetch",
        calls_after_second == calls_after_first,
        f"calls before={calls_after_first}, after={calls_after_second}",
    )

    # ── I. события сохраняются в store ──────────────────────────────────── #
    print("\n--- I. events saved in store ---")
    engine_i = MarketEventEngine(store=store, ev_engine=ev_engine)
    i_events = [
        make_event(EventType.SALE, "Мега-акция WB ноябрь", url="https://wb.ru/mega-sale-i",
                   query="wb mega sale i"),
        make_event(EventType.PLATFORM, "WB изменил правила возврата",
                   url="https://wb.ru/return-rules-i", query="wb return rules i"),
    ]
    saved_i = await engine_i.ingest(i_events)
    stored = await store.list_market_events(limit=50)
    check(
        "I. события сохранены в store",
        saved_i == len(i_events) and len(stored) >= saved_i,
        f"saved={saved_i}, in store={len(stored)}",
    )

    # ── J. Evidence создаётся через EvidenceEngine ──────────────────────── #
    print("\n--- J. Evidence created ---")
    j_event = make_event(
        EventType.REGULATION,
        "Новые требования к маркировке обуви с 2027",
        url="https://news.example.ru/marking-shoes-j",
        query="marking shoes j",
    )
    engine_j = MarketEventEngine(store=store, ev_engine=ev_engine)
    await engine_j.ingest([j_event])

    evidence_list = await store.retrieve_evidence(limit=200, min_confidence=0.0)
    market_evidence = [
        e for e in evidence_list
        if (e.supporting_data or {}).get("signal_type") == "market_event"
    ]
    check(
        "J. Evidence с signal_type='market_event' создан",
        len(market_evidence) >= 1,
        f"market event evidence count={len(market_evidence)}",
    )

    # ── Итог ─────────────────────────────────────────────────────────────── #
    await store.close()
    for suffix in ("", "-shm", "-wal"):
        p = db_path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    print("\n" + "=" * 60)
    print("ИТОГ")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total  = len(RESULTS)
    print(f"  Пройдено: {passed}/{total}")
    if passed < total:
        print("\n  FAILED:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"    [FAIL] {label} — {detail}")
        sys.exit(1)
    else:
        print("  Все проверки пройдены.")


if __name__ == "__main__":
    asyncio.run(run_tests())
