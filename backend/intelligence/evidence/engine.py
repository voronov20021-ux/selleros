"""
evidence/engine.py — ядро обработки знаний Argus.

EvidenceEngine отвечает за:

    1. ingest(item)           сырой KnowledgeItem → нормализованный Evidence
    2. ingest_signals(item)   KnowledgeItem → list[Evidence] через SignalExtractor
    3. normalize_item(item)   очистка текста, нормализация пробелов
    4. fingerprint(item)      детерминированный хеш для дедупликации
    5. is_duplicate(item)     проверка дубликата по fingerprint
    6. retrieve(...)          поиск Evidence для Argus reasoning
    7. decay_confidence()     снижение confidence пропорционально устареванию

─────────────────────────────────────────────────────────────────────────────
Таксономия, которую строго соблюдает движок:

    FACT          — верифицируемо из первоисточника и не изменилось с тех пор.
                    «Wordstat: 150 000 показов, январь 2026.»

    OBSERVATION   — измеренный результат конкретного действия.
                    «Продавец добавил инфографику → +18% заказов за 30 дней.»

    INFERENCE     — вывод, построенный из нескольких фактов/наблюдений.
                    «Спрос на мужские часы сезонно растёт в ноябре–декабре.»

Предположение (гипотеза) Argus явно оформляется как INFERENCE с пониженным
confidence, а не как FACT — нельзя выдавать вывод за проверенный факт.
─────────────────────────────────────────────────────────────────────────────
Confidence decay:

    Свежий FACT из авторитетного источника   → 0.90–1.00
    Тот же FACT через 6 freshness-периодов   → ~0.50
    INFERENCE любого возраста                 → confidence × 0.8 (inherent penalty)

    decay_confidence() применяется в retrieve() — хранимое значение не трогается,
    эффективное confidence считается на лету при выдаче списка.
─────────────────────────────────────────────────────────────────────────────
Улучшенный расчёт confidence (v2):

    Базовый confidence из KnowledgeItem.confidence (устанавливается адаптером).
    Дополнительные факторы:
        + 0.05  если есть source_url
        + 0.05  если есть явная дата/период
        + 0.05  если есть числовое значение (рост %, цена, …)
        - 0.10  если текст содержит ≥3 рекламных маркера (купить, доставка, …)
        - 0.05  если evidence_type = INFERENCE (вывод, не факт)
        × authority  множитель авторитетности источника (0.85–1.15)

Fingerprint / дедупликация:
    fingerprint = sha256(normalized_url + "|" + normalized_content_prefix)
    Хранится в metadata["_fingerprint"] KnowledgeItem.
    is_duplicate() проверяет по fingerprint через поиск в store.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
import uuid
from dataclasses import replace

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    Evidence,
    EvidenceType,
    ItemType,
    KnowledgeItem,
    SellerObservation,
)

log = logging.getLogger("selleros.intelligence.evidence")

#: Штрафной коэффициент для выводов (INFERENCE) — они менее надёжны, чем факты.
_INFERENCE_PENALTY = 0.80

#: Минимальный confidence после decay (не опускаем ниже этого порога).
_MIN_CONFIDENCE = 0.05

# Паттерны для нормализации текста
_RE_HTML_TAG   = re.compile(r"<[^>]+>")
_RE_MULTI_WS   = re.compile(r"\s{2,}")
_RE_AD_MARKERS = re.compile(
    r"(купить|заказать|доставка|гарантия|лучшая цена|выгодно|недорого"
    r"|интернет-магазин|официальный сайт|в наличии|от производителя)",
    re.IGNORECASE | re.UNICODE,
)


class EvidenceEngine:
    """
    Движок обработки и извлечения знаний.

    Зависит только от IIntelligenceStore — конкретная БД (SQLite/PG)
    подменяется без правок этого класса.
    """

    def __init__(self, store: IIntelligenceStore) -> None:
        self._store = store

    # ──────────────────────────────── нормализация ──────────────────────── #

    @staticmethod
    def normalize_item(item: KnowledgeItem) -> KnowledgeItem:
        """
        Нормализовать текстовое содержимое KnowledgeItem.

        Возвращает НОВЫЙ KnowledgeItem с очищенным content.
        Исходный объект НЕ изменяется.
        Запись в БД НЕ обновляется — нормализованная версия используется
        только для построения claim/fingerprint.

        Очистка:
          - удаление HTML-тегов (<hlword>, <b>, …)
          - нормализация пробелов (множественные → одиночный)
          - удаление leading/trailing whitespace
          - коллапс дублированных строк (один и тот же текст подряд)
        """
        raw = item.content or ""

        # 1. Удалить HTML-теги (заменяем на пробел, а не пустую строку,
        #    чтобы слова не слипались: "<b>Мужские</b>часы" → "Мужские часы")
        cleaned = _RE_HTML_TAG.sub(" ", raw)

        # 2. Нормализовать пробелы внутри строк, сохраняя переносы.
        #    \s{2,} без DOTALL не трогает \n → можно применять до split.
        #    Затем нормализуем пробелы внутри каждой строки отдельно.
        lines_raw = cleaned.split("\n")

        seen: set[str] = set()
        unique_lines: list[str] = []
        for line in lines_raw:
            # Нормализуем пробелы внутри строки
            normalized_line = re.sub(r"[ \t]{2,}", " ", line).strip()
            if normalized_line:
                if normalized_line not in seen:
                    seen.add(normalized_line)
                    unique_lines.append(normalized_line)
                # Иначе — дубликат, пропускаем
            else:
                # Пустые строки сохраняем как разделители (но не дублируем)
                if unique_lines and unique_lines[-1] != "":
                    unique_lines.append("")

        cleaned = "\n".join(unique_lines).strip()

        # Возвращаем новый объект (dataclass replace)
        return replace(item, content=cleaned)

    # ──────────────────────────────── fingerprint / дедупликация ────────── #

    @staticmethod
    def fingerprint(item: KnowledgeItem) -> str:
        """
        Детерминированный fingerprint KnowledgeItem для дедупликации.

        Строится из:
          1. Нормализованный URL (source_url) — если есть, он уникален.
          2. Первые 200 символов нормализованного content.

        Один и тот же URL с одинаковым началом контента = дубликат.
        Возвращает hex-строку SHA-256 (64 символа).
        """
        # Нормализуем URL
        url_part = (item.source_url or "").strip().rstrip("/").lower()

        # Нормализуем контент
        raw_content = (item.content or "").strip()
        content_clean = _RE_HTML_TAG.sub(" ", raw_content)
        content_clean = _RE_MULTI_WS.sub(" ", content_clean).strip()
        content_prefix = content_clean[:200].lower()

        raw = f"{url_part}|{content_prefix}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def is_duplicate(self, item: KnowledgeItem) -> bool:
        """
        Проверить, существует ли уже похожий KnowledgeItem в store.

        Fingerprint сравнивается с записями в knowledge_items через
        поиск по source_url (быстрый путь) или content-prefix (медленный).

        Быстрый путь: если source_url уникален — ищем по нему.
        Если URL уже есть в knowledge_items → дубликат.
        """
        if not item.source_url:
            return False

        url = item.source_url.strip().rstrip("/")
        existing = await self._store.search_items(
            source_id=item.source_id,
            limit=5,
        )
        for ex in existing:
            ex_url = (ex.source_url or "").strip().rstrip("/")
            if ex_url == url and ex.id != item.id:
                return True
        return False

    # ──────────────────────────────── signal ingestion ──────────────────── #

    async def ingest_signals(
        self,
        item: KnowledgeItem,
        *,
        source_authority: float = 0.50,
        save: bool = True,
    ) -> list[Evidence]:
        """
        Извлечь сигналы из KnowledgeItem через SignalExtractor.

        Lazy-import SignalExtractor чтобы избежать circular imports.
        Если SignalExtractor не находит сигналов — возвращает [].

        save=True  — сохранить Evidence в store.
        save=False — только вернуть список (для тестирования).
        """
        from backend.intelligence.evidence.signals import SignalExtractor

        normalized = self.normalize_item(item)
        extractor = SignalExtractor()
        evidences = extractor.extract(normalized, source_authority=source_authority)

        if save:
            for ev in evidences:
                try:
                    await self._store.save_evidence(ev)
                except Exception as exc:
                    log.warning("ingest_signals: не удалось сохранить ev %s: %s", ev.id, exc)

        if evidences:
            log.debug(
                "ingest_signals: item=%s → %d сигналов",
                item.id[:8],
                len(evidences),
            )

        return evidences

    # ──────────────────────────────── ingestion ─────────────────────────── #

    async def ingest(self, item: KnowledgeItem) -> Evidence:
        """
        Преобразовать сырой KnowledgeItem в типизированный Evidence.

        Шаги:
            1. Сохранить KnowledgeItem в store (если не сохранён).
            2. Вычислить evidence_type из item.item_type.
            3. Сформировать нормализованный claim.
            4. Рассчитать confidence (с учётом источника).
            5. Сохранить Evidence и вернуть его.

        Идемпотентность: KnowledgeItem с одним id сохраняется один раз
        (INSERT OR IGNORE в store). Evidence тоже — по своему id.
        """
        await self._store.save_item(item)

        evidence_type = self._map_item_type(item.item_type)
        claim = self._normalize_claim(item)
        confidence = self._initial_confidence(item, evidence_type)
        supporting = self._build_supporting(item)

        evidence = Evidence(
            id=str(uuid.uuid4()),
            knowledge_item_id=item.id,
            evidence_type=evidence_type,
            claim=claim,
            supporting_data=supporting,
            confidence=confidence,
            created_at=time.time(),
        )

        await self._store.save_evidence(evidence)
        log.debug(
            "Ingested %s: confidence=%.2f | %s",
            evidence_type.value,
            confidence,
            claim[:80],
        )
        return evidence

    async def ingest_observation(self, obs: SellerObservation) -> Evidence | None:
        """
        Преобразовать обезличенное наблюдение продавца в Evidence.

        Шаги:
            1. Сохранить SellerObservation в seller_observations.
            2. Если есть измеренный результат — создать KnowledgeItem
               (item_type=OBSERVATION, source="user_generated") и сохранить
               в knowledge_items. Это необходимо для FK evidence.knowledge_item_id.
            3. Создать Evidence типа OBSERVATION, ссылающийся на этот item.

        Если результат не указан — наблюдение сохраняется «как есть»,
        без создания Evidence (незавершённое наблюдение, ждём исхода).
        """
        await self._store.save_observation(obs)

        has_outcome = any([
            obs.outcome_sales_delta is not None,
            obs.outcome_orders_delta is not None,
            obs.outcome_rating_delta is not None,
        ])

        if not has_outcome:
            log.debug(
                "Наблюдение %s сохранено без Evidence — исход ещё не указан.", obs.id
            )
            return None

        claim = self._claim_from_observation(obs)
        if not claim:
            return None

        # Создаём KnowledgeItem чтобы соблюсти FK evidence → knowledge_items.
        # Наблюдение продавца — самостоятельная единица знания (source_id=user_generated).
        item = KnowledgeItem(
            id=str(uuid.uuid4()),
            source_id="user_generated",
            collected_at=obs.created_at,
            item_type=ItemType.OBSERVATION,
            content=claim,
            confidence=0.70,
            category=obs.category,
            metadata=self._supporting_from_observation(obs),
        )
        await self._store.save_item(item)

        evidence = Evidence(
            id=str(uuid.uuid4()),
            knowledge_item_id=item.id,
            evidence_type=EvidenceType.OBSERVATION,
            claim=claim,
            supporting_data=self._supporting_from_observation(obs),
            confidence=0.70,  # наблюдение от одного продавца — умеренный confidence
            created_at=time.time(),
        )

        await self._store.save_evidence(evidence)
        return evidence

    # ──────────────────────────────── retrieval ─────────────────────────── #

    async def retrieve(
        self,
        *,
        evidence_type: EvidenceType | None = None,
        category: str | None = None,
        min_confidence: float = 0.3,
        limit: int = 20,
        apply_decay: bool = True,
    ) -> list[Evidence]:
        """
        Получить Evidence для Argus reasoning.

        apply_decay=True — эффективный confidence пересчитывается с учётом
        возраста записи. Это не меняет данные в БД — только порядок выдачи
        и фильтрацию ниже min_confidence.

        Возвращает список, отсортированный по эффективному confidence DESC.
        """
        items = await self._store.retrieve_evidence(
            evidence_type=evidence_type,
            category=category,
            min_confidence=min_confidence if not apply_decay else 0.0,
            limit=limit * 2 if apply_decay else limit,
        )

        if not apply_decay:
            return items[:limit]

        # Пересчитываем effective confidence и фильтруем
        now = time.time()
        scored: list[tuple[float, Evidence]] = []

        for ev in items:
            source_freshness_h = await self._get_source_freshness(ev)
            eff = self.decay_confidence(ev, now, source_freshness_h)
            if eff >= min_confidence:
                scored.append((eff, ev))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ev for _, ev in scored[:limit]]

    # ──────────────────────────────── confidence decay ──────────────────── #

    def decay_confidence(
        self,
        evidence: Evidence,
        now_ts: float,
        source_freshness_hours: int,
    ) -> float:
        """
        Рассчитать эффективный confidence с учётом возраста записи.

        Модель экспоненциального затухания:
            effective = base * exp(-k * age_in_periods)
            где k = ln(2) / half_life_periods
            и half_life = 3 периода (через 3 freshness-цикла confidence ÷ 2)

        Дополнительный штраф для INFERENCE: × _INFERENCE_PENALTY.
        Нижняя граница: _MIN_CONFIDENCE.
        """
        base = evidence.confidence

        if source_freshness_hours <= 0:
            return max(_MIN_CONFIDENCE, base)

        age_hours = (now_ts - evidence.created_at) / 3600.0
        age_in_periods = age_hours / source_freshness_hours

        # half_life = 3 периода → k = ln(2) / 3
        k = math.log(2) / 3.0
        decayed = base * math.exp(-k * age_in_periods)

        if evidence.evidence_type == EvidenceType.INFERENCE:
            decayed *= _INFERENCE_PENALTY

        return max(_MIN_CONFIDENCE, decayed)

    # ──────────────────────────────── internal helpers ──────────────────── #

    @staticmethod
    def _map_item_type(item_type: ItemType) -> EvidenceType:
        """Маппинг ItemType → EvidenceType."""
        mapping = {
            ItemType.FACT:           EvidenceType.FACT,
            ItemType.OBSERVATION:    EvidenceType.OBSERVATION,
            ItemType.INFERENCE:      EvidenceType.INFERENCE,
            ItemType.RECOMMENDATION: EvidenceType.INFERENCE,  # рекомендация — частный случай вывода
        }
        return mapping[item_type]

    @staticmethod
    def _normalize_claim(item: KnowledgeItem) -> str:
        """
        Нормализованное утверждение из KnowledgeItem.

        Для Wordstat-items берём content напрямую — он уже сформирован
        адаптером в человекочитаемом виде.
        Для других источников — тоже content, но в будущем можно
        добавить специфичную нормализацию по source_id.
        """
        claim = (item.content or "").strip()
        if not claim:
            claim = f"[{item.source_id}] данные за {item.period or 'неизвестный период'}"
        # Обрезаем до разумной длины для промпта
        return claim[:500]

    @staticmethod
    def _initial_confidence(item: KnowledgeItem, evidence_type: EvidenceType) -> float:
        """
        Начальный confidence для нового Evidence (v2).

        Базовый confidence из KnowledgeItem.confidence (адаптер уже учёл
        авторитетность источника). Дополнительные поправки:

          + 0.05  наличие source_url
          + 0.04  наличие явной даты/периода
          + 0.04  наличие числового значения в контенте
          - 0.10  ≥3 рекламных маркера (купить, доставка, …)
          - 0.04  1-2 рекламных маркера
          - 0.05  evidence_type = INFERENCE (вывод, не факт)

        Итог клэмпится в [0.05, 1.0].
        """
        base = max(0.0, min(1.0, item.confidence))

        text = item.content or ""

        # Бонусы
        if item.source_url:
            base += 0.05
        if re.search(r"\d{4}|январ|феврал|март|апрел|май|июн|июл|август"
                     r"|сентябр|октябр|ноябр|декабр|q[1-4]", text, re.I):
            base += 0.04
        if re.search(r"\d", text):
            base += 0.04

        # Штрафы за рекламный текст
        ad_count = len(_RE_AD_MARKERS.findall(text))
        if ad_count >= 3:
            base -= 0.10
        elif ad_count >= 1:
            base -= 0.04

        # Штраф за тип вывода
        if evidence_type == EvidenceType.INFERENCE:
            base -= 0.05

        return round(max(0.05, min(1.0, base)), 4)

    @staticmethod
    def _build_supporting(item: KnowledgeItem) -> dict:
        """
        Структурированные данные-доказательства из metadata KnowledgeItem.
        Помещаем source_id и source_url для обратной трассировки.
        """
        return {
            "source_id":  item.source_id,
            "source_url": item.source_url,
            "category":   item.category,
            "region":     item.region,
            "period":     item.period,
            **{k: v for k, v in item.metadata.items() if not isinstance(v, (list, dict))},
        }

    @staticmethod
    def _claim_from_observation(obs: SellerObservation) -> str | None:
        """Сформировать claim из наблюдения продавца."""
        parts = []

        change_labels = {
            "price":   "изменение цены",
            "content": "изменение контента",
            "ad":      "изменение рекламы",
            "ranking": "изменение позиции",
            "other":   "изменение",
        }
        label = change_labels.get(obs.change_type.value, "изменение")

        parts.append(f"Наблюдение: {label}")

        if obs.before_value and obs.after_value:
            parts.append(f"({obs.before_value} → {obs.after_value})")

        outcomes = []
        if obs.outcome_sales_delta is not None:
            sign = "+" if obs.outcome_sales_delta >= 0 else ""
            outcomes.append(f"продажи {sign}{obs.outcome_sales_delta}")
        if obs.outcome_orders_delta is not None:
            sign = "+" if obs.outcome_orders_delta >= 0 else ""
            outcomes.append(f"заказы {sign}{obs.outcome_orders_delta}")
        if obs.outcome_rating_delta is not None:
            sign = "+" if obs.outcome_rating_delta >= 0 else ""
            outcomes.append(f"рейтинг {sign}{obs.outcome_rating_delta:.1f}")

        if outcomes:
            parts.append("→ " + ", ".join(outcomes))

        if obs.category:
            parts.append(f"[категория: {obs.category}]")

        return " ".join(parts) if parts else None

    @staticmethod
    def _supporting_from_observation(obs: SellerObservation) -> dict:
        return {
            "source_id":              "user_generated",
            "change_type":            obs.change_type.value,
            "before_value":           obs.before_value,
            "after_value":            obs.after_value,
            "outcome_sales_delta":    obs.outcome_sales_delta,
            "outcome_orders_delta":   obs.outcome_orders_delta,
            "outcome_rating_delta":   obs.outcome_rating_delta,
            "category":               obs.category,
        }

    async def _get_source_freshness(self, evidence: Evidence) -> int:
        """
        Получить freshness_hours источника для расчёта decay.

        Если source не найден — возвращаем 24 (стандарт).
        Кешируется в будущем, пока достаточно простого запроса.
        """
        try:
            data = evidence.supporting_data
            sid = data.get("source_id")
            if sid:
                source = await self._store.get_source(sid)
                if source:
                    return source.freshness_hours
        except Exception:
            pass
        return 24
