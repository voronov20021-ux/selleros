"""knowledge — structured Knowledge Base + WB Policy engine (versioned)."""

from backend.knowledge.models import KnowledgeEntry, KnowledgeCategory
from backend.knowledge.base import KnowledgeBase, get_default_knowledge_base, reset_default_knowledge_base
from backend.knowledge.wb_policy import (
    WBPolicyEngine,
    WBPolicyRule,
    get_default_wb_policy_engine,
    reset_default_wb_policy_engine,
)
from backend.knowledge.chat import (
    handle_knowledge_turn,
    is_knowledge_query,
    is_wb_policy_query,
    is_action_check_query,
    should_handle_knowledge,
    detect_depth,
)
from backend.knowledge.learning_log import KnowledgeInteractionLog, build_knowledge_log_from_reply

__all__ = [
    "KnowledgeEntry",
    "KnowledgeCategory",
    "KnowledgeBase",
    "get_default_knowledge_base",
    "reset_default_knowledge_base",
    "WBPolicyEngine",
    "WBPolicyRule",
    "get_default_wb_policy_engine",
    "reset_default_wb_policy_engine",
    "handle_knowledge_turn",
    "is_knowledge_query",
    "is_wb_policy_query",
    "is_action_check_query",
    "should_handle_knowledge",
    "detect_depth",
    "KnowledgeInteractionLog",
    "build_knowledge_log_from_reply",
]
