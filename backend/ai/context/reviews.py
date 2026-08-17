"""
ai/context/reviews.py — Review Intelligence контекст для Argus.

ReviewContextSource:
    product → session reviews / controlled WB fetch
            → ReviewIntelligence
            → компактный ContextBlock (priority=37, ≤1800 символов)

Без фиктивных отзывов. Без выдуманных данных.
"""

from __future__ import annotations

import logging

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent
from backend.intelligence.reviews import ReviewIntelligence, signal_strength_label
from backend.memory.context import make_user_hash

log = logging.getLogger("selleros.ai.context.reviews")

_RELEVANT_INTENTS = frozenset({
    Intent.REVIEWS,
    Intent.PRODUCT_DISCUSSION,
    Intent.GENERAL_QUESTION,
    Intent.MARKETING,
    Intent.PRICING,
    Intent.SELLER_ANALYTICS,
    Intent.COMPETITOR,
    Intent.SOLUTION_RESEARCH,
})

_MAX_BLOCK_CHARS = 1800
_MAX_PROBLEMS = 5
_MAX_ACTIONS = 5


def _reviews_to_payload(raw_reviews) -> list[dict]:
    """Review objects / dicts → payload для ReviewIntelligence."""
    out: list[dict] = []
    for item in raw_reviews or []:
        if item is None:
            continue
        if hasattr(item, "to_ri_dict"):
            out.append(item.to_ri_dict())
            continue
        if isinstance(item, dict):
            text = item.get("text") or item.get("content") or item.get("review_text") or ""
            if not str(text).strip():
                continue
            out.append(item)
            continue
        if isinstance(item, str) and item.strip():
            out.append({"text": item})
    return out


def _format_problem_line(i: int, p, *, with_example: bool = True) -> list[str]:
    """Одна проблема: category + label + strength + freq + severity + dir + P + evidence."""
    strength = signal_strength_label(p)
    prefix = ""
    if p.priority >= 4 or p.confidence < 0.40:
        prefix = "Похоже: "
    freq = getattr(getattr(p, "frequency", None), "value", None) or "?"
    direction = getattr(getattr(p, "direction", None), "value", None) or "?"
    severity = getattr(getattr(p, "severity", None), "value", None) or "?"
    cat = (
        (p.metadata or {}).get("category")
        or getattr(getattr(p, "signal_type", None), "value", None)
        or "?"
    )
    evid = ",".join((p.evidence_ids or [])[:2]) or "—"
    out = [
        f"{i}. [{cat}] {prefix}{p.label} — {strength}; "
        f"freq={freq}; sev={severity}; dir={direction}; P{p.priority}; evidence={evid}"
    ]
    if with_example and p.examples:
        out.append(f"   пример: {p.examples[0][:55]}")
    if getattr(p, "rationale", None):
        out.append(f"   почему: {p.rationale[:80]}")
    return out


def _format_assessment(assessment) -> str:
    """Компактный блок: проблемы + действия + стиль для Argus (≤1800)."""
    header: list[str] = [
        "Что говорят покупатели",
        f"Обработано отзывов: {assessment.processed_count}",
        f"Уверенность: {assessment.confidence:.0%}",
        "Стиль: живо; не выдумывай вне evidence; слабый сигнал → «похоже»/«стоит проверить».",
    ]

    problems = list(getattr(assessment, "problems", None) or [])
    # negative = seller risks; OTHER без negative и positive — не risk
    risk = []
    for p in problems:
        direction = getattr(getattr(p, "direction", None), "value", "") or ""
        stype = getattr(getattr(p, "signal_type", None), "value", "") or ""
        if direction == "negative":
            risk.append(p)
        elif direction == "mixed" and stype not in ("OTHER", "other"):
            risk.append(p)
    pos = [p for p in problems if getattr(p.direction, "value", "") == "positive"]
    focus_risk = [p for p in risk if p.priority <= 3] or risk[:_MAX_PROBLEMS]
    focus_pos = [p for p in pos if p.priority <= 3] or pos[:2]

    body_mid: list[str] = []
    if focus_risk:
        body_mid.append("Проблемы:")
        for i, p in enumerate(focus_risk[:_MAX_PROBLEMS], 1):
            body_mid.extend(_format_problem_line(i, p))

    if focus_pos:
        body_mid.append("Сильные стороны:")
        for i, p in enumerate(focus_pos[:2], 1):
            body_mid.extend(_format_problem_line(i, p, with_example=False))

    actions = list(getattr(assessment, "actions", None) or [])
    main_actions = [a for a in actions if a.priority <= 3]
    weak_actions = [a for a in actions if a.priority >= 4]
    show = main_actions[:_MAX_ACTIONS]
    if len(show) < _MAX_ACTIONS and weak_actions:
        show.append(weak_actions[0])

    action_lines: list[str] = []
    if show:
        action_lines.append("Что сделать:")
        for i, a in enumerate(show, 1):
            evid = ",".join((a.evidence_ids or [])[:2]) or "—"
            action_lines.append(
                f"{i}. {a.title} (P{a.priority}, conf {a.confidence:.0%}, evidence: {evid})"
            )
            if a.rationale:
                action_lines.append(f"   почему: {a.rationale[:90]}")

    if not focus_risk and not focus_pos:
        if assessment.processed_count == 0:
            body_mid.append("Отзывы отсутствуют или не обработаны.")
        else:
            body_mid.append(
                "Recurring patterns не подтверждены — "
                "данных недостаточно для recurring risk; "
                "единичные жалобы ≠ системная проблема."
            )

    # Собрать с приоритетом: header + problems + «Что сделать» всегда в лимите
    def _join(parts: list[str]) -> str:
        return "\n".join(parts)

    text = _join(header + body_mid + action_lines)
    if len(text) <= _MAX_BLOCK_CHARS:
        return text

    # Ужать: без примеров, короче rationale, меньше проблем/действий
    body_mid2: list[str] = []
    if focus_risk:
        body_mid2.append("Проблемы:")
        for i, p in enumerate(focus_risk[:3], 1):
            body_mid2.extend(_format_problem_line(i, p, with_example=False))
    if focus_pos:
        body_mid2.append("Сильные стороны:")
        for i, p in enumerate(focus_pos[:1], 1):
            body_mid2.extend(_format_problem_line(i, p, with_example=False))

    action_lines2: list[str] = []
    if show:
        action_lines2.append("Что сделать:")
        for i, a in enumerate(show[:4], 1):
            evid = ",".join((a.evidence_ids or [])[:1]) or "—"
            action_lines2.append(
                f"{i}. {a.title} (P{a.priority}, evidence: {evid})"
            )
            if a.rationale:
                action_lines2.append(f"   почему: {a.rationale[:60]}")

    text = _join(header + body_mid2 + action_lines2)
    if len(text) > _MAX_BLOCK_CHARS:
        # Последний рубеж: обрезать mid, но сохранить header + хвост «Что сделать»
        budget = _MAX_BLOCK_CHARS - len(_join(header + action_lines2)) - 1
        mid = _join(body_mid2)
        if budget < 40:
            text = _join(header + action_lines2)
        else:
            mid = mid[: max(0, budget - 1)].rstrip() + "…"
            text = _join(header + [mid] + action_lines2)
        if len(text) > _MAX_BLOCK_CHARS:
            text = text[:_MAX_BLOCK_CHARS - 1].rstrip() + "…"
    return text


class ReviewContextSource(ContextSource):
    """
    review_intel — ReviewIntelligence | None
    session — SessionService
    store — IntelligenceStore | None
    reviews_service — WBReviewsService | None (controlled fetch on miss)
    """

    name = "reviews"
    intents = _RELEVANT_INTENTS
    priority = 37

    def __init__(self, review_intel=None, session=None, store=None, reviews_service=None):
        self._ri = review_intel
        self.session = session
        self.store = store
        self._reviews_svc = reviews_service

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        if self.session is None:
            return None
        try:
            product = self.session.get_product(request.user_id)
            if product is None:
                return None

            category = getattr(product, "subject_name", None) or None
            article_raw = getattr(product, "article", None)
            article = str(article_raw) if article_raw is not None else None
            article_id = int(article_raw) if article_raw is not None else None
            user_hash = make_user_hash(request.user_id)

            assessment = None
            raw_reviews = None

            # 1) Session reviews (реальные, без fixture)
            if hasattr(self.session, "get_product_reviews") and article_id is not None:
                raw_reviews = self.session.get_product_reviews(request.user_id, article_id)

            # 2) Controlled fetch on session miss
            if raw_reviews is None and self._reviews_svc is not None and article_id is not None:
                try:
                    raw_reviews = await self._reviews_svc.load_into_session(
                        self.session, request.user_id, product,
                    )
                except Exception as exc:
                    log.warning("ReviewContextSource fetch reviews failed: %s", exc)
                    raw_reviews = []

            extra = getattr(request, "extra", None) or {}
            # extra.reviews — только явный DI (тесты); production path не подставляет fixture
            if raw_reviews is None and extra.get("reviews") is not None:
                raw_reviews = extra.get("reviews")

            payload = _reviews_to_payload(raw_reviews) if raw_reviews else []

            if payload and self._ri is not None:
                assessment = await self._ri.analyze(
                    payload,
                    category=category,
                    article=article,
                    user_hash=user_hash,
                    persist=True,
                )
            elif self.store is not None and hasattr(self.store, "get_review_assessment"):
                assessment = await self.store.get_review_assessment(
                    user_hash=user_hash,
                    category=category,
                    article=article,
                )
                if assessment is not None and not assessment.problems and assessment.issues:
                    from backend.intelligence.reviews import (
                        build_seller_actions,
                        build_seller_problems,
                    )
                    assessment.problems = build_seller_problems(
                        assessment.issues, assessment.signals,
                    )
                    assessment.actions = build_seller_actions(assessment.problems)

            if assessment is None or (
                assessment.processed_count == 0
                and not assessment.issues
                and not assessment.signals
            ):
                return None

            body = _format_assessment(assessment)
            if not body.strip():
                return None

            if len(body) > _MAX_BLOCK_CHARS:
                body = body[:_MAX_BLOCK_CHARS - 1].rstrip() + "…"

            return ContextBlock(
                title=f"ОТЗЫВЫ ТОВАРА: {category or article or '?'}",
                body=body,
                priority=self.priority,
            )
        except Exception as exc:
            log.warning("ReviewContextSource failed: %s", exc)
            return None
