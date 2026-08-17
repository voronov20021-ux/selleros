"""
learning_log.py — schema for future Knowledge learning examples.

trainable=false. No auto-learning / ML on this stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.foundation.time_service import get_time_service


@dataclass
class KnowledgeInteractionLog:
    knowledge_id: str | None = None
    question: str = ""
    answer: str = ""
    source: str = "knowledge_chat"
    formula_id: str | None = None
    policy_id: str | None = None
    seller_context: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    outcome: str | None = None
    confidence: float = 0.0
    created_at: float | None = None
    trainable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "question": self.question,
            "answer": self.answer,
            "source": self.source,
            "formula_id": self.formula_id,
            "policy_id": self.policy_id,
            "seller_context": dict(self.seller_context),
            "action": dict(self.action),
            "outcome": self.outcome,
            "confidence": self.confidence,
            "created_at": self.created_at if self.created_at is not None else get_time_service().timestamp(),
            "trainable": False,
            "auto_learning": False,
        }


def build_knowledge_log_from_reply(question: str, reply) -> KnowledgeInteractionLog:
    return KnowledgeInteractionLog(
        knowledge_id=getattr(reply, "knowledge_id", None),
        question=question,
        answer=getattr(reply, "text", "") or "",
        source=getattr(reply, "kind", "knowledge_chat"),
        formula_id=getattr(reply, "formula_id", None),
        policy_id=getattr(reply, "policy_id", None),
        trainable=False,
    )
