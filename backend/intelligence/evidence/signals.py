"""
evidence/signals.py — rule-based извлечение сигналов из KnowledgeItem.

SignalExtractor находит явные рыночные сигналы в тексте без LLM.
Принцип строгости: если сигнал невозможно уверенно извлечь → НЕ создавать.
Лучше вернуть [], чем сфабриковать ложный Evidence.

Поддерживаемые типы сигналов (SignalType):
──────────────────────────────────────────────────────────────────────────────
  FACT              — проверяемый факт без интерпретации
  TREND             — направление изменения (рост/падение + % если есть)
  SEASONALITY       — привязка к сезону/периоду/событию
  MARKET_EVENT      — акция, распродажа, внешнее событие
  COMPETITOR        — упоминание действия конкурента
  PRODUCT_DEMAND    — данные о спросе на продукт/категорию
  PRICE             — информация о цене/скидке
  ADVERTISING       — влияние рекламы
  CONSUMER_BEHAVIOR — поведенческий паттерн покупателей

Каждый сигнал → отдельный Evidence с:
  - evidence_type = FACT | OBSERVATION | INFERENCE
  - claim         = нормализованное утверждение
  - supporting_data["signal_type"]  = SignalType.value
  - supporting_data["direction"]    = "up"|"down"|"stable" (для TREND)
  - supporting_data["change_pct"]   = float (если извлечено)
  - supporting_data["period_hint"]  = "ноябрь-декабрь" (для SEASONALITY)
  - supporting_data["confidence_factors"] = list[str] объяснение confidence
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from backend.intelligence.models import Evidence, EvidenceType, KnowledgeItem


# ────────────────────────────────────────────────── SignalType ──────────── #


class SignalType(str, Enum):
    FACT              = "fact"
    TREND             = "trend"
    SEASONALITY       = "seasonality"
    MARKET_EVENT      = "market_event"
    COMPETITOR        = "competitor"
    PRODUCT_DEMAND    = "product_demand"
    PRICE             = "price"
    ADVERTISING       = "advertising"
    CONSUMER_BEHAVIOR = "consumer_behavior"


# ────────────────────────────────────────────── промежуточная структура ─── #


@dataclass
class _RawSignal:
    """Внутреннее представление извлечённого сигнала до превращения в Evidence."""

    signal_type: SignalType
    evidence_type: EvidenceType
    claim: str
    confidence: float
    direction: str | None = None          # "up" | "down" | "stable"
    change_pct: float | None = None       # числовой процент изменения
    period_hint: str | None = None        # "ноябрь-декабрь", "перед НГ", …
    confidence_factors: list[str] = field(default_factory=list)


# ────────────────────────────────────────────────── паттерны ────────────── #

# Маркеры роста
_RE_UP = re.compile(
    r"(вырос|вырасти|растёт|растет|рост|увеличил|увеличивает|повышен|поднял"
    r"|взлетел|ускорил|набирает|популярн|высокий спрос|высокий|рекордн)",
    re.IGNORECASE | re.UNICODE,
)

# Маркеры падения
_RE_DOWN = re.compile(
    r"(упал|снизил|снижение|падение|спад|сократил|уменьшил|ослаб|просел"
    r"|меньше|ниже спроса|низкий спрос|депрессия рынка)",
    re.IGNORECASE | re.UNICODE,
)

# Числовой процент изменения  →  group(1)=знак, group(2)=число
_RE_PCT = re.compile(
    r"(?:на\s+)?([+-]?\s*\d+(?:[.,]\d+)?)\s*%",
    re.UNICODE,
)

# Сезонные маркеры
_RE_SEASON = re.compile(
    r"(январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр"
    r"|весн|лет[ое]|осен|зим|новый год|нг|8 марта|14 февраля|23 февраля"
    r"|день матери|пасх|рождеств|сезон|квартал|q[1-4]"
    r"|пик спроса|предпраздни|перед праздник)",
    re.IGNORECASE | re.UNICODE,
)

# Акции и события
_RE_MARKET_EVENT = re.compile(
    r"(распродаж|акци[яи]|скидк|промо|распродаж|black friday|черная пятниц"
    r"|распродажа|sale|11[./]11|двойная цена|суперцена|мегаскидк|дни распродаж)",
    re.IGNORECASE | re.UNICODE,
)

# Конкурент
_RE_COMPETITOR = re.compile(
    r"(конкурент|аналог|альтернатив|другой бренд|лидер рынка|топ[\- ]продавец"
    r"|ozon|озон|wb|wildberries|lamoda|ali|tmall|сберм)",
    re.IGNORECASE | re.UNICODE,
)

# Спрос
_RE_DEMAND = re.compile(
    r"(спрос|popularity|популярность|ищут|запрос|поиск|интерес)",
    re.IGNORECASE | re.UNICODE,
)

# Цена / скидка
_RE_PRICE = re.compile(
    r"(\d[\d\s]*(?:руб|₽|р\.)|скидк[аи]|цена|дешевле|дороже|стоимость"
    r"|наценк|ценообразование|markup)",
    re.IGNORECASE | re.UNICODE,
)

# Реклама
_RE_AD = re.compile(
    r"(реклам[аы]|promoted|спонсор|продвижение|ставк[аи] рекламы"
    r"|кампани[ия]|cpc|cpm|roi рекламы)",
    re.IGNORECASE | re.UNICODE,
)

# Поведение потребителей
_RE_CONSUMER = re.compile(
    r"(покупатель|потребитель|клиент|пользователь|аудитори|лояльн"
    r"|возвра[тщ]|отзыв|рейтинг|nps|удовлетвор)",
    re.IGNORECASE | re.UNICODE,
)

# Явные маркеры «рекламного» текста (→ снижаем confidence)
_RE_AD_FLUFF = re.compile(
    r"(купить|заказать|доставка|гарантия|лучшая цена|выгодно|недорого"
    r"|интернет-магазин|официальный сайт|в наличии|от производителя)",
    re.IGNORECASE | re.UNICODE,
)

# Сезонные месяцы для извлечения period_hint
_RE_MONTHS = re.compile(
    r"(?:январ[ея]|феврал[ея]|марта?|апрел[ея]|ма[йя]|июн[ея]"
    r"|июл[ея]|август[а]?|сентябр[ея]|октябр[ея]|ноябр[ея]|декабр[ея]"
    r"|весной|летом|осенью|зимой|перед новым годом|новогодн)",
    re.IGNORECASE | re.UNICODE,
)


def _extract_pct(text: str) -> float | None:
    """Извлечь первое упоминание процентного значения из текста."""
    m = _RE_PCT.search(text)
    if not m:
        return None
    raw = m.group(1).replace(",", ".").replace(" ", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_period_hint(text: str) -> str | None:
    """Извлечь текстовое обозначение периода."""
    m = _RE_MONTHS.search(text)
    if not m:
        return None
    # возвращаем слово + небольшой контекст после него
    start = max(0, m.start() - 5)
    end = min(len(text), m.end() + 20)
    return text[start:end].strip().rstrip(".,;:")


def _has_date(text: str) -> bool:
    """Содержит ли текст явную дату/период."""
    return bool(re.search(r"\d{4}|январ|феврал|март|апрел|май|июн|июл|август"
                           r"|сентябр|октябр|ноябр|декабр|q[1-4]", text, re.I))


def _has_number(text: str) -> bool:
    """Есть ли в тексте хотя бы одно числовое значение."""
    return bool(re.search(r"\d", text))


def _ad_fluff_count(text: str) -> int:
    """Количество 'рекламных' маркеров в тексте → понижающий фактор."""
    return len(_RE_AD_FLUFF.findall(text))


# ────────────────────────────────────────────────── SignalExtractor ──────── #


class SignalExtractor:
    """
    Rule-based извлечение сигналов из KnowledgeItem.

    Порядок извлечения:
        1. Собрать текст из content + metadata title + snippet.
        2. Применить паттерны по убыванию приоритета.
        3. Для каждого найденного сигнала рассчитать confidence.
        4. Вернуть список Evidence (без сохранения в store).

    Принцип: нет уверенности → нет Evidence.
    Минимальный порог confidence для создания Evidence: 0.35.
    """

    _MIN_SIGNAL_CONFIDENCE = 0.35

    def extract(
        self,
        item: KnowledgeItem,
        *,
        source_authority: float = 0.50,
    ) -> list[Evidence]:
        """
        Извлечь сигналы из одного KnowledgeItem.

        source_authority — authority источника из DataSource (0.0–1.0).
        Влияет на базовый confidence сигнала.

        Возвращает список Evidence (не сохранены в store).
        """
        text = self._collect_text(item)
        if not text.strip():
            return []

        raw_signals = self._detect_all(text, item)
        evidences: list[Evidence] = []

        for sig in raw_signals:
            conf = self._calc_confidence(
                sig=sig,
                text=text,
                item=item,
                source_authority=source_authority,
            )
            if conf < self._MIN_SIGNAL_CONFIDENCE:
                continue

            ev = Evidence(
                id=str(uuid.uuid4()),
                knowledge_item_id=item.id,
                evidence_type=sig.evidence_type,
                claim=sig.claim,
                supporting_data={
                    "signal_type":          sig.signal_type.value,
                    "source_id":            item.source_id,
                    "source_url":           item.source_url,
                    "category":             item.category,
                    "region":               item.region,
                    "period":               item.period,
                    "direction":            sig.direction,
                    "change_pct":           sig.change_pct,
                    "period_hint":          sig.period_hint,
                    "confidence_factors":   sig.confidence_factors,
                },
                confidence=round(conf, 4),
                created_at=time.time(),
            )
            evidences.append(ev)

        return evidences

    # ─────────────────────────── текст для анализа ──────────────────────── #

    @staticmethod
    def _collect_text(item: KnowledgeItem) -> str:
        """Собрать все текстовые поля item в единую строку для анализа."""
        parts = [item.content or ""]
        meta = item.metadata or {}
        for key in ("title", "snippet", "headline"):
            if key in meta and meta[key]:
                parts.append(str(meta[key]))
        return " ".join(parts)

    # ─────────────────────────── детекторы ──────────────────────────────── #

    def _detect_all(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        results: list[_RawSignal] = []

        # Порядок: более специфичные — первыми
        results.extend(self._detect_trend(text, item))
        results.extend(self._detect_seasonality(text, item))
        results.extend(self._detect_market_event(text, item))
        results.extend(self._detect_price(text, item))
        results.extend(self._detect_advertising(text, item))
        results.extend(self._detect_competitor(text, item))
        results.extend(self._detect_demand(text, item))
        results.extend(self._detect_consumer(text, item))

        return results

    def _detect_trend(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Сигналы роста/падения спроса, продаж, интереса."""
        is_up   = bool(_RE_UP.search(text))
        is_down = bool(_RE_DOWN.search(text))

        if not (is_up or is_down):
            return []

        # Оба направления одновременно — неоднозначность, не создаём
        if is_up and is_down:
            return []

        direction  = "up" if is_up else "down"
        change_pct = _extract_pct(text)
        period     = _extract_period_hint(text)

        cat = item.category or "категории"
        pct_str = f" на {abs(change_pct):.0f}%" if change_pct else ""
        dir_word = "вырос" if direction == "up" else "снизился"
        claim = f"Тренд: спрос на {cat}{pct_str} {dir_word}"
        if period:
            claim += f" ({period})"

        factors = ["trend_keyword_match"]
        if change_pct is not None:
            factors.append("has_numeric_value")
        if period:
            factors.append("has_period")

        return [_RawSignal(
            signal_type=SignalType.TREND,
            evidence_type=EvidenceType.INFERENCE,  # тренд — вывод, не факт
            claim=claim,
            confidence=0.60,
            direction=direction,
            change_pct=change_pct,
            period_hint=period,
            confidence_factors=factors,
        )]

    def _detect_seasonality(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Сезонные паттерны спроса."""
        if not _RE_SEASON.search(text):
            return []

        period = _extract_period_hint(text)
        cat = item.category or "категории"
        claim = f"Сезонность: спрос на {cat} привязан к периоду"
        if period:
            claim += f" «{period}»"

        # Сезонность требует и сезонного маркера, и чего-то про спрос
        # иначе это просто упоминание месяца без контекста
        demand_present = bool(_RE_DEMAND.search(text) or _RE_UP.search(text) or _RE_DOWN.search(text))
        if not demand_present:
            return []

        return [_RawSignal(
            signal_type=SignalType.SEASONALITY,
            evidence_type=EvidenceType.INFERENCE,
            claim=claim,
            confidence=0.55,
            period_hint=period,
            confidence_factors=["season_keyword", "demand_keyword"],
        )]

    def _detect_market_event(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Акции, распродажи, внешние события."""
        if not _RE_MARKET_EVENT.search(text):
            return []

        pct = _extract_pct(text)
        period = _extract_period_hint(text)
        cat = item.category or "категории"

        claim = f"Рыночное событие: акция/распродажа в категории {cat}"
        if pct:
            claim += f", скидка до {pct:.0f}%"
        if period:
            claim += f" ({period})"

        factors = ["market_event_keyword"]
        if pct:
            factors.append("has_discount_pct")

        return [_RawSignal(
            signal_type=SignalType.MARKET_EVENT,
            evidence_type=EvidenceType.FACT,
            claim=claim,
            confidence=0.60,
            change_pct=pct,
            period_hint=period,
            confidence_factors=factors,
        )]

    def _detect_price(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Ценовые сигналы."""
        if not _RE_PRICE.search(text):
            return []

        # Без числа цена — слишком размыто, пропускаем
        if not _has_number(text):
            return []

        pct = _extract_pct(text)
        cat = item.category or "категории"
        claim = f"Цена: информация о ценах/скидках в категории {cat}"
        if pct:
            claim += f" (скидка ~{pct:.0f}%)"

        return [_RawSignal(
            signal_type=SignalType.PRICE,
            evidence_type=EvidenceType.FACT,
            claim=claim,
            confidence=0.55,
            change_pct=pct,
            confidence_factors=["price_keyword", "has_number"],
        )]

    def _detect_advertising(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Рекламные сигналы."""
        if not _RE_AD.search(text):
            return []

        pct = _extract_pct(text)
        cat = item.category or "категории"
        claim = f"Реклама: упоминание влияния рекламы в категории {cat}"
        if pct:
            claim += f" (+{pct:.0f}% к продажам)"

        factors = ["ad_keyword"]
        if pct:
            factors.append("has_numeric_result")

        return [_RawSignal(
            signal_type=SignalType.ADVERTISING,
            evidence_type=EvidenceType.OBSERVATION if pct else EvidenceType.INFERENCE,
            claim=claim,
            confidence=0.50,
            change_pct=pct,
            confidence_factors=factors,
        )]

    def _detect_competitor(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Упоминания конкурентов."""
        if not _RE_COMPETITOR.search(text):
            return []

        cat = item.category or "категории"
        claim = f"Конкуренты: упоминание конкурентов в контексте {cat}"

        return [_RawSignal(
            signal_type=SignalType.COMPETITOR,
            evidence_type=EvidenceType.FACT,
            claim=claim,
            confidence=0.45,
            confidence_factors=["competitor_keyword"],
        )]

    def _detect_demand(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Общие сигналы спроса (без направления)."""
        if not _RE_DEMAND.search(text):
            return []

        # Если уже нашли тренд (up/down) — не дублируем общим сигналом
        if _RE_UP.search(text) or _RE_DOWN.search(text):
            return []

        pct = _extract_pct(text)
        cat = item.category or "категории"
        claim = f"Спрос: данные о спросе на товары категории {cat}"
        if pct:
            claim += f" (~{pct:.0f}%)"

        return [_RawSignal(
            signal_type=SignalType.PRODUCT_DEMAND,
            evidence_type=EvidenceType.FACT,
            claim=claim,
            confidence=0.45,
            change_pct=pct,
            confidence_factors=["demand_keyword"],
        )]

    def _detect_consumer(self, text: str, item: KnowledgeItem) -> list[_RawSignal]:
        """Поведенческие паттерны покупателей."""
        if not _RE_CONSUMER.search(text):
            return []

        # Слишком слабый сигнал без дополнительного контекста
        # Требуем хотя бы число или направление
        if not (_has_number(text) or _RE_UP.search(text) or _RE_DOWN.search(text)):
            return []

        cat = item.category or "категории"
        claim = f"Поведение покупателей: данные об аудитории {cat}"

        return [_RawSignal(
            signal_type=SignalType.CONSUMER_BEHAVIOR,
            evidence_type=EvidenceType.OBSERVATION,
            claim=claim,
            confidence=0.40,
            confidence_factors=["consumer_keyword", "has_context"],
        )]

    # ─────────────────────────── confidence ─────────────────────────────── #

    @staticmethod
    def _calc_confidence(
        sig: _RawSignal,
        text: str,
        item: KnowledgeItem,
        source_authority: float,
    ) -> float:
        """
        Рассчитать confidence для конкретного сигнала.

        Базовая формула:
            conf = sig.confidence
                   * source_authority_factor
                   + number_bonus
                   + date_bonus
                   + url_bonus
                   - fluff_penalty
                   - inference_penalty

        Все итоги клэмпятся в [0.0, 1.0].
        """
        conf = sig.confidence

        # Фактор авторитетности источника (authority ∈ [0, 1])
        # 1.0 → +10%, 0.5 → нейтрально, 0.0 → -10%
        authority_factor = 0.9 + 0.2 * source_authority
        conf *= authority_factor

        # Бонусы
        if _has_number(text):
            conf += 0.05
        if _has_date(text):
            conf += 0.05
        if item.source_url:
            conf += 0.02

        # Штрафы
        fluff = _ad_fluff_count(text)
        if fluff >= 3:
            conf -= 0.10
        elif fluff >= 1:
            conf -= 0.04

        if sig.evidence_type == EvidenceType.INFERENCE:
            conf -= 0.05  # дополнительный штраф к уже сниженному базовому

        return max(0.0, min(1.0, conf))
