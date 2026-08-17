"""
memory/context.py — единый MemoryContext / MemorySnapshot для Argus.

Собирается ДО intent-specific обработки и одинаков для
REVIEWS / PRICING / LOGISTICS / MARKETING / …

Intelligence Layer (Yandex/CostGuard/…) сюда не входит —
только seller/product/conversation/learning scoped memory.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("selleros.memory.context")

#: Сколько последних реплик уходит в prompt как recent_turns.
RECENT_TURNS = 16

#: При превышении — старые реплики сворачиваются в summary.
SUMMARY_OVERFLOW = 20


def make_user_hash(user_id: int) -> str:
    """Стабильный hash продавца без raw Telegram id в промпте."""
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]


@dataclass
class MemoryContext:
    """Снимок памяти текущей seller session для одного AI-запроса."""

    user_hash: str
    current_product_id: int | None = None
    current_product_state: dict[str, Any] = field(default_factory=dict)
    seller_profile: dict[str, Any] = field(default_factory=dict)
    conversation_summary: str = ""
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    relevant_product_history: list[dict[str, Any]] = field(default_factory=list)
    learning_context: str = ""

    def to_prompt_section(self) -> str:
        """Текст для системного промпта (без raw user_id)."""
        lines: list[str] = ["ЕДИНАЯ ПАМЯТЬ SELLER OS", ""]
        lines.append(f"seller_hash: {self.user_hash}")

        if self.current_product_id is not None:
            lines.append(f"current_product_id: {self.current_product_id}")

        state = self.current_product_state
        if state:
            lines.append("")
            lines.append("CURRENT PRODUCT STATE:")
            for key in (
                "title", "brand", "subject", "price", "rating", "reviews",
                "score", "analysis_kind",
            ):
                if key in state and state[key] is not None:
                    lines.append(f"- {key}: {state[key]}")

        if self.seller_profile:
            lines.append("")
            lines.append("SELLER PROFILE:")
            for key, val in self.seller_profile.items():
                if val is not None:
                    lines.append(f"- {key}: {val}")

        if self.conversation_summary:
            lines.append("")
            lines.append("CONVERSATION SUMMARY:")
            lines.append(self.conversation_summary[:1500])

        if self.recent_turns:
            lines.append("")
            lines.append(f"RECENT TURNS ({len(self.recent_turns)}):")
            for turn in self.recent_turns[-8:]:
                role = "Продавец" if turn.get("role") == "user" else "Argus"
                content = (turn.get("content") or "")[:180]
                lines.append(f"- {role}: {content}")

        if self.relevant_product_history:
            lines.append("")
            lines.append("HISTORICAL PRODUCTS (не текущий товар):")
            for item in self.relevant_product_history[:5]:
                lines.append(
                    f"- арт.{item.get('article')}: {item.get('title', '?')} "
                    f"(price={item.get('price')}, rating={item.get('rating')})"
                )

        if self.learning_context:
            lines.append("")
            lines.append("LEARNING CONTEXT:")
            lines.append(self.learning_context[:1200])

        return "\n".join(lines)

    def history_for_api(self, limit: int | None = None) -> list[dict[str, str]]:
        """recent_turns в формате chat-API (одинаково для любого intent)."""
        turns = self.recent_turns
        if limit is not None:
            turns = turns[-limit:]
        return [
            {"role": t["role"], "content": t["content"]}
            for t in turns
            if t.get("role") in ("user", "assistant") and t.get("content")
        ]


def fold_summary(old_summary: str, overflow: list[dict[str, str]]) -> str:
    """
    Детерминированный summary без LLM (без платных API).
    Старые реплики → компактные буллеты.
    """
    parts: list[str] = []
    if old_summary:
        parts.append(old_summary.strip())
    for msg in overflow:
        role = "Продавец" if msg.get("role") == "user" else "Argus"
        content = (msg.get("content") or "").replace("\n", " ").strip()
        if content:
            parts.append(f"- {role}: {content[:160]}")
    text = "\n".join(parts).strip()
    # Жёсткий потолок, чтобы summary не рос бесконечно.
    if len(text) > 2500:
        text = text[-2500:]
        cut = text.find("\n")
        if cut > 0:
            text = text[cut + 1:]
    return text


class MemoryContextBuilder:
    """
    Собирает MemoryContext из SessionService + DialogMemory + MemoryStore.

    Не знает про intents — вызывается до intent-specific ContextBuilder.
    """

    def __init__(self, session, memory_store=None, learning_brain=None):
        self.session = session
        self.store = memory_store
        self.learning_brain = learning_brain

    async def build(
        self,
        user_id: int,
        *,
        recent_turns: list[dict[str, str]] | None = None,
        conversation_summary: str | None = None,
    ) -> MemoryContext:
        product = self.session.get_product(user_id)
        article = getattr(product, "article", None) if product is not None else None
        seller = None
        if hasattr(self.session, "get_seller_data"):
            seller = self.session.get_seller_data(user_id, article=article)

        analysis = self.session.get_analysis(user_id) or {}

        product_state: dict[str, Any] = {}
        if product is not None:
            product_state = {
                "title": getattr(product, "title", None),
                "brand": getattr(product, "brand", None),
                "subject": getattr(product, "subject_name", None),
                "price": (
                    seller.price if seller and seller.price is not None
                    else getattr(product, "price", None)
                ),
                "rating": (
                    seller.rating if seller and seller.rating is not None
                    else getattr(product, "rating", None)
                ),
                "reviews": (
                    seller.feedbacks if seller and seller.feedbacks is not None
                    else getattr(product, "feedbacks", None)
                ),
                "score": analysis.get("score"),
                "analysis_kind": analysis.get("kind"),
                "photos": (
                    int(getattr(product, "photo_count", 0) or 0)
                    or len(getattr(product, "photos", []) or [])
                ),
            }

        seller_profile: dict[str, Any] = {}
        if seller is not None:
            seller_profile = {
                "price": seller.price,
                "rating": seller.rating,
                "reviews": seller.feedbacks,
                "price_source": seller.price_source,
                "rating_source": seller.rating_source,
                "feedbacks_source": seller.feedbacks_source,
                "sales": seller.sales,
                "orders": seller.orders,
                "period": seller.period,
            }

        # Summary: session cache → store → arg
        summary = conversation_summary or ""
        if not summary and hasattr(self.session, "get_conversation_summary"):
            summary = self.session.get_conversation_summary(user_id) or ""
        if not summary and self.store is not None and hasattr(self.store, "get_conversation_summary"):
            try:
                summary = await self.store.get_conversation_summary(
                    user_id, article=article or 0,
                ) or ""
            except Exception as exc:
                log.debug("get_conversation_summary failed: %s", exc)

        turns = list(recent_turns or [])
        if not turns and hasattr(self.session, "get_discussion_messages"):
            if article is not None and self.session.is_discussion_active(user_id, article):
                turns = [
                    {"role": m["role"], "content": m["content"]}
                    for m in self.session.get_discussion_messages(user_id)
                ]

        history_products: list[dict[str, Any]] = []
        if self.store is not None:
            try:
                for rec in await self.store.list_products(user_id):
                    if article is not None and rec.article == article:
                        continue
                    history_products.append({
                        "article": rec.article,
                        "title": rec.title,
                        "price": rec.price,
                        "rating": rec.rating,
                        "reviews": rec.feedbacks,
                    })
            except Exception as exc:
                log.debug("list_products for history failed: %s", exc)

        learning_text = ""
        if self.learning_brain is not None and product is not None:
            category = getattr(product, "subject_name", None) or ""
            if category:
                try:
                    assessment = await self.learning_brain.analyze(
                        category=category, days=90,
                    )
                    if assessment and getattr(assessment, "sample_size", 0) > 0:
                        learning_text = (
                            f"category={category}; "
                            f"sample={assessment.sample_size}; "
                            f"success_rate={assessment.success_rate:.0%}"
                        )
                except Exception as exc:
                    log.debug("learning_brain.analyze failed: %s", exc)

        return MemoryContext(
            user_hash=make_user_hash(user_id),
            current_product_id=article,
            current_product_state=product_state,
            seller_profile=seller_profile,
            conversation_summary=summary,
            recent_turns=turns[-RECENT_TURNS:],
            relevant_product_history=history_products,
            learning_context=learning_text,
        )
