"""
evidence/conflicts.py — детектор противоречий между Evidence.

ConflictDetector находит пары Evidence, которые противоречат друг другу,
и возвращает список EvidenceConflict.

Принципы:
  1. Конфликтующие Evidence НЕ удаляются.
     Оба факта остаются в store — Argus знает о расхождении.
  2. Конфликт — сигнал для Argus: «у нас противоречивые данные на эту тему».
  3. Конфликты не сохраняются в store (нет таблицы) — возвращаются caller-у
     для логирования и последующей обработки.

Типы конфликтов (по severity):
  HIGH    — прямое противоречие (рост vs падение одной метрики)
  MEDIUM  — разные числовые значения одного направления (20% vs 5%)
  LOW     — разные периоды/источники при похожих утверждениях

Алгоритм:
  1. Сгруппировать Evidence по (category, signal_type).
  2. Внутри группы попарно сравнивать direction и change_pct.
  3. Если у двух Evidence одинаковый signal_type но разные direction → HIGH.
  4. Если одинаковые direction но change_pct расходится > threshold → MEDIUM.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations

from backend.intelligence.models import Evidence


# ────────────────────────────────────────────── ConflictSeverity ─────────── #


class ConflictSeverity(str, Enum):
    HIGH   = "high"    # прямое противоречие
    MEDIUM = "medium"  # расхождение в величинах
    LOW    = "low"     # незначительное несоответствие


# ────────────────────────────────────────────── EvidenceConflict ─────────── #


@dataclass
class EvidenceConflict:
    """
    Зафиксированное противоречие между двумя или более Evidence.

    evidence_ids    — id всех конфликтующих Evidence
    topic           — тема конфликта (например "trend|часы|RU")
    description     — текстовое объяснение
    severity        — HIGH / MEDIUM / LOW
    created_at      — unix timestamp момента детектирования
    details         — дополнительные данные (direction_a vs direction_b, pct-разница)
    """

    id: str
    evidence_ids: list[str]
    topic: str
    description: str
    severity: ConflictSeverity
    created_at: float
    details: dict = field(default_factory=dict)


# ────────────────────────────────────────────── ConflictDetector ─────────── #


class ConflictDetector:
    """
    Детектор противоречий между Evidence.

    Используется после extract/aggregation-шагов, когда накоплен
    список Evidence по одному запросу или категории.

    detect(evidences) → list[EvidenceConflict]
    """

    #: Порог расхождения change_pct для MEDIUM-конфликта (процентные пункты).
    PCT_DIFF_THRESHOLD = 15.0

    def detect(self, evidences: list[Evidence]) -> list[EvidenceConflict]:
        """
        Обнаружить конфликты в списке Evidence.

        Конфликты ищутся только среди Evidence одного сигнального типа
        (signal_type из supporting_data) и одной категории+региона.

        Возвращает список EvidenceConflict (не сохраняет в store).
        """
        conflicts: list[EvidenceConflict] = []
        groups = self._group(evidences)

        for topic, group in groups.items():
            if len(group) < 2:
                continue
            conflicts.extend(self._compare_group(topic, group))

        return conflicts

    # ─────────────────────────── группировка ────────────────────────────── #

    @staticmethod
    def _group(evidences: list[Evidence]) -> dict[str, list[Evidence]]:
        """
        Сгруппировать Evidence по ключу (signal_type, category, region).

        Evidence без signal_type в supporting_data группируются по
        evidence_type — для базовой дедупликации прямых противоречий.
        """
        groups: dict[str, list[Evidence]] = {}

        for ev in evidences:
            data = ev.supporting_data or {}
            sig_type = data.get("signal_type", ev.evidence_type.value)
            category = data.get("category") or "unknown"
            region   = data.get("region")   or "unknown"

            key = f"{sig_type}|{category}|{region}"
            groups.setdefault(key, []).append(ev)

        return groups

    # ─────────────────────────── сравнение пар ──────────────────────────── #

    def _compare_group(
        self,
        topic: str,
        group: list[Evidence],
    ) -> list[EvidenceConflict]:
        """Попарно сравнить Evidence внутри одной группы."""
        found: list[EvidenceConflict] = []

        for ev_a, ev_b in combinations(group, 2):
            conflict = self._compare_pair(topic, ev_a, ev_b)
            if conflict is not None:
                found.append(conflict)

        return found

    def _compare_pair(
        self,
        topic: str,
        ev_a: Evidence,
        ev_b: Evidence,
    ) -> EvidenceConflict | None:
        """
        Сравнить два Evidence на противоречие.

        Возвращает EvidenceConflict или None (нет конфликта).
        """
        data_a = ev_a.supporting_data or {}
        data_b = ev_b.supporting_data or {}

        dir_a = data_a.get("direction")
        dir_b = data_b.get("direction")
        pct_a = data_a.get("change_pct")
        pct_b = data_b.get("change_pct")

        # Случай 1: прямое противоречие направлений
        if (
            dir_a and dir_b
            and dir_a != dir_b
            and dir_a in ("up", "down")
            and dir_b in ("up", "down")
        ):
            return EvidenceConflict(
                id=str(uuid.uuid4()),
                evidence_ids=[ev_a.id, ev_b.id],
                topic=topic,
                description=(
                    f"Противоречие: источник A говорит «{dir_a}» "
                    f"({ev_a.claim[:80]}), "
                    f"источник B говорит «{dir_b}» "
                    f"({ev_b.claim[:80]})"
                ),
                severity=ConflictSeverity.HIGH,
                created_at=time.time(),
                details={
                    "direction_a": dir_a,
                    "direction_b": dir_b,
                    "claim_a": ev_a.claim[:120],
                    "claim_b": ev_b.claim[:120],
                    "confidence_a": ev_a.confidence,
                    "confidence_b": ev_b.confidence,
                },
            )

        # Случай 2: одинаковое направление, но сильно разные проценты
        if (
            dir_a and dir_b
            and dir_a == dir_b
            and pct_a is not None
            and pct_b is not None
        ):
            diff = abs(abs(pct_a) - abs(pct_b))
            if diff >= self.PCT_DIFF_THRESHOLD:
                return EvidenceConflict(
                    id=str(uuid.uuid4()),
                    evidence_ids=[ev_a.id, ev_b.id],
                    topic=topic,
                    description=(
                        f"Расхождение в величине: "
                        f"A = {pct_a:+.1f}%, B = {pct_b:+.1f}% "
                        f"(разница {diff:.1f}пп)"
                    ),
                    severity=ConflictSeverity.MEDIUM,
                    created_at=time.time(),
                    details={
                        "direction": dir_a,
                        "pct_a":     pct_a,
                        "pct_b":     pct_b,
                        "pct_diff":  round(diff, 2),
                        "claim_a":   ev_a.claim[:120],
                        "claim_b":   ev_b.claim[:120],
                    },
                )

        # Случай 3: одинаковые source_url — потенциальный дубликат
        url_a = data_a.get("source_url")
        url_b = data_b.get("source_url")
        if url_a and url_b and url_a == url_b and ev_a.claim != ev_b.claim:
            return EvidenceConflict(
                id=str(uuid.uuid4()),
                evidence_ids=[ev_a.id, ev_b.id],
                topic=topic,
                description=(
                    f"Два разных Evidence из одного URL: {url_a[:80]}"
                ),
                severity=ConflictSeverity.LOW,
                created_at=time.time(),
                details={
                    "source_url": url_a,
                    "claim_a":    ev_a.claim[:120],
                    "claim_b":    ev_b.claim[:120],
                },
            )

        return None
