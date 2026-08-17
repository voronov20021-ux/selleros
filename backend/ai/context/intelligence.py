"""
ai/context/intelligence.py — Market Intelligence источник для Argus.

CategoryIntelligenceSource подключает Intelligence Layer к ContextBuilder.
Каждый запрос к Seller AI автоматически получает компактный рыночный
контекст о категории текущего товара — тренды, сезонность, события.

Что НЕ делает этот источник:
- не делает прямых HTTP-запросов (всё через CategoryIntelligence);
- не генерирует рекомендации (только факты);
- не ломает ContextBuilder если Intelligence Layer недоступен (graceful None).

Формат контекста: компактный текст, пригодный для LLM-промпта.
Сырые данные (XML, found_phrase-числа) не передаются — только нормализованные факты.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent

log = logging.getLogger("selleros.ai.context.intelligence")

#: Для каких интентов Market Intelligence полезен.
_RELEVANT_INTENTS = frozenset({
    Intent.COMPETITOR,
    Intent.MARKETING,
    Intent.PRICING,
    Intent.SELLER_ANALYTICS,
    Intent.GENERAL_QUESTION,
    Intent.PRODUCT_DISCUSSION,
})

#: Максимальное кол-во строк Evidence в блоке (LLM не нужен весь список)
_MAX_EVIDENCE_LINES = 3
_MAX_EVENT_LINES    = 3


def _fmt_date(ts: float) -> str:
    return datetime.utcfromtimestamp(ts).strftime("%d.%m.%Y")


def _format_context(ctx) -> str:
    """
    Сформировать компактный текст для LLM из CategoryContext.
    Максимум ~600 символов.
    """
    lines: list[str] = []

    # ── Спрос ──────────────────────────────────────────────────────────── #
    demand_items = [
        it for it in ctx.demand_signals
        if (it.metadata or {}).get("found_phrase")
    ]
    if demand_items:
        best = max(demand_items, key=lambda i: (i.metadata or {}).get("found_phrase", 0))
        fp  = best.metadata.get("found_phrase", 0)
        hum = best.metadata.get("found_human", "") or f"{fp:,}".replace(",", " ")
        period = best.period or ""
        src    = best.source_id.replace("_", " ")
        lines.append(f"Спрос: {hum} результатов ({src}, {period})")

    # ── Тренды ─────────────────────────────────────────────────────────── #
    if ctx.trend_signals:
        # Берём с наибольшей confidence
        best_tr = max(ctx.trend_signals, key=lambda t: t.confidence)
        dir_map = {"up": "↑ рост", "down": "↓ падение", "stable": "→ стабильно"}
        dir_label = dir_map.get(best_tr.direction.value, best_tr.direction.value)
        pct_part = f" {best_tr.change_pct:+.1f}%" if best_tr.change_pct else ""
        lines.append(f"Тренд: {dir_label}{pct_part} (confidence {best_tr.confidence:.0%})")

    # ── Сезонность ─────────────────────────────────────────────────────── #
    if ctx.seasonal_signals:
        current_month = datetime.utcnow().month
        if current_month in ctx.seasonal_signals:
            idx = ctx.seasonal_signals[current_month]
            level = "выше нормы" if idx > 1.05 else ("ниже нормы" if idx < 0.95 else "норма")
            lines.append(
                f"Сезонность: текущий месяц — индекс {idx:.2f} ({level})"
            )

    # ── Рыночные события ───────────────────────────────────────────────── #
    shown_events = 0
    for ev_list, label in [
        (ctx.regulation_events, "REG"),
        (ctx.platform_events,   "WB"),
        (ctx.market_events,     ""),
    ]:
        for ev in ev_list[:_MAX_EVENT_LINES - shown_events]:
            prefix = f"[{label}] " if label else ""
            lines.append(f"Событие: {prefix}{ev.title[:80]}")
            shown_events += 1
            if shown_events >= _MAX_EVENT_LINES:
                break
        if shown_events >= _MAX_EVENT_LINES:
            break

    # ── Evidence ───────────────────────────────────────────────────────── #
    top_ev = sorted(ctx.evidence, key=lambda e: e.confidence, reverse=True)
    for ev in top_ev[:_MAX_EVIDENCE_LINES]:
        lines.append(f"Факт: {ev.claim[:100]} (conf {ev.confidence:.0%})")

    return "\n".join(lines)


class CategoryIntelligenceSource(ContextSource):
    """
    Источник рыночного контекста для Argus.

    Получает CategoryContext из Intelligence Layer и передаёт
    компактный срез знаний в промпт Argus.

    Если Intelligence Layer недоступен или категория товара неизвестна —
    возвращает None (не ломает ContextBuilder).
    """

    name = "market_intelligence"
    intents = _RELEVANT_INTENTS

    def __init__(self, category_intelligence, session) -> None:
        """
        category_intelligence — CategoryIntelligence
        session               — SessionService (для получения текущего товара)
        """
        self._ci      = category_intelligence
        self._session = session

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        if self._ci is None:
            return None

        # Получаем категорию из текущего товара продавца
        product = self._session.get_product(request.user_id)
        if product is None:
            return None

        category = getattr(product, "subject_name", None)
        if not category:
            return None

        try:
            ctx = await self._ci.analyze(
                category=category,
                region="RU",
                limit=20,
            )
        except Exception as exc:
            log.warning(
                "CategoryIntelligenceSource: ошибка analyze(%r): %s",
                category, exc,
            )
            return None

        # Пустой контекст — нет смысла добавлять блок
        if (not ctx.demand_signals and not ctx.trend_signals
                and not ctx.seasonal_signals and not ctx.market_events
                and not ctx.evidence):
            return None

        body = _format_context(ctx)
        if not body.strip():
            return None

        date_str = _fmt_date(ctx.generated_at)
        cache_marker = " [кэш]" if ctx.from_cache else ""
        title = (
            f"РЫНОЧНЫЙ КОНТЕКСТ: {category} (RU){cache_marker}"
            f" • confidence {ctx.confidence:.0%} • {date_str}"
        )

        return ContextBlock(
            title=title,
            body=body,
            priority=30,   # ниже ТОВАР В РАБОТЕ (10), выше ИСТОРИЯ (40)
        )
