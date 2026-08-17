"""
ai/context/solution_research.py — SOLUTION_RESEARCH контекст для Argus.

Product-scoped: seller + current product + review problem → research query.
Cache HIT → reuse session result, no re-search / no WB re-fetch.
Без выдуманных магазинов/цен.
"""

from __future__ import annotations

import logging

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent
from backend.intelligence.solution_research import (
    extract_problem_from_assessment,
    infer_topic,
    is_solution_choice_query,
    is_solution_research_query,
    research_solutions,
)

log = logging.getLogger("selleros.ai.context.solution_research")

_RELEVANT = frozenset({
    Intent.SOLUTION_RESEARCH,
    Intent.REVIEWS,
    Intent.PRODUCT_DISCUSSION,
    Intent.GENERAL_QUESTION,
    Intent.LOGISTICS,
})


class SolutionResearchContextSource(ContextSource):
    name = "solution_research"
    intents = _RELEVANT
    priority = 36

    def __init__(self, search_service=None, session=None, review_intel=None):
        self._search = search_service
        self.session = session
        self._ri = review_intel
        self.last_result = None

    def _product_problem_ctx(self, user_id: int):
        """Seller + product + cached review problem (no WB fetch)."""
        product = None
        category = None
        title = None
        article = None
        problem_id = None
        problem_label = ""
        evidence_ids: list[str] = []
        problem_type = None

        if self.session is None:
            return product, category, title, article, problem_id, problem_label, evidence_ids, problem_type

        product = self.session.get_product(user_id)
        if product is not None:
            category = getattr(product, "subject_name", None)
            title = getattr(product, "title", None)
            article = getattr(product, "article", None)

        # Prefer last RI assessment cached on session — do NOT re-fetch WB.
        assessment = None
        if hasattr(self.session, "get_review_assessment"):
            try:
                assessment = self.session.get_review_assessment(user_id)
            except Exception:
                assessment = None
        if assessment is None and article is not None and hasattr(self.session, "get_product_reviews"):
            # Analyze in-memory session reviews only (cache HIT path — no HTTP).
            raw = self.session.get_product_reviews(user_id, int(article))
            if raw and self._ri is not None:
                try:
                    payload = []
                    for item in raw:
                        if hasattr(item, "to_ri_dict"):
                            payload.append(item.to_ri_dict())
                        elif isinstance(item, dict):
                            payload.append(item)
                    if payload:
                        assessment = None  # defer — avoid blocking heavy RI here
                        # Lightweight: pull packaging cue from texts without full RI
                        for it in payload[:20]:
                            text = str(it.get("text") or "")
                            low = text.lower().replace("ё", "е")
                            if any(k in low for k in ("упаков", "короб", "мят", "болта")):
                                problem_label = text[:80]
                                evidence_ids = [str(it.get("id") or "")]
                                problem_type = "PACKAGING"
                                break
                except Exception as exc:
                    log.debug("inline review cue failed: %s", exc)

        if assessment is not None:
            problem_id, problem_label, evidence_ids, problem_type = extract_problem_from_assessment(
                assessment,
            )

        return product, category, title, article, problem_id, problem_label, evidence_ids, problem_type

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        text = (request.text or "").strip()
        if not text:
            return None
        if request.intent is not Intent.SOLUTION_RESEARCH:
            if not (is_solution_research_query(text) or is_solution_choice_query(text)):
                return None

        # Cache HIT for choice / compare follow-up — no re-search, no WB fetch.
        cached = None
        if self.session is not None and hasattr(self.session, "get_solution_research"):
            try:
                cached = self.session.get_solution_research(request.user_id)
            except Exception:
                cached = None

        if is_solution_choice_query(text) and cached is not None:
            self.last_result = cached
            if hasattr(cached, "from_cache"):
                cached.from_cache = True
            return ContextBlock(
                title="ПОИСК РЕШЕНИЙ (кэш диалога)",
                body=cached.to_context_block(),
                priority=self.priority,
            )

        (
            _product, category, title, article,
            problem_id, problem_label, evidence_ids, problem_type,
        ) = self._product_problem_ctx(request.user_id)

        try:
            result = await research_solutions(
                text,
                search_service=self._search,
                category=category,
                topic=infer_topic(text),
                product_title=title,
                product_article=int(article) if article is not None else None,
                problem_id=problem_id,
                problem_label=problem_label,
                evidence_ids=evidence_ids,
                problem_type=problem_type,
            )
        except Exception as exc:
            log.warning("SolutionResearchContextSource failed: %s", exc)
            return None

        self.last_result = result
        if self.session is not None and hasattr(self.session, "set_solution_research"):
            try:
                self.session.set_solution_research(request.user_id, result)
            except Exception:
                pass

        return ContextBlock(
            title="ПОИСК РЕШЕНИЙ",
            body=result.to_context_block(),
            priority=self.priority,
        )
