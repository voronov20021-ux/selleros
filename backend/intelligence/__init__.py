"""
backend/intelligence — Intelligence Layer для Argus.

Публичное API модуля. Learning Loop v1 изолирован и не подключён к Argus.
"""

from backend.intelligence.category_intelligence import CategoryContext, CategoryIntelligence
from backend.intelligence.cost_guard import GuardResult, GuardStatus, YandexCostGuard
from backend.intelligence.event_sources.base import EventSourceAdapter
from backend.intelligence.event_sources.yandex_news import YandexNewsAdapter
from backend.intelligence.evidence.aggregator import AggregatedEvidence, EvidenceAggregator
from backend.intelligence.evidence.category import CategoryResolver
from backend.intelligence.evidence.conflicts import (
    ConflictDetector,
    ConflictSeverity,
    EvidenceConflict,
)
from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.evidence.signals import SignalExtractor, SignalType
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.learning import (
    ActionOutcome,
    LearningAssessment,
    LearningSignal,
    LearningSignalType,
    OutcomeDirection,
)
from backend.intelligence.learning_brain import LearningBrain
from backend.intelligence.learning_brain_interface import (
    LearningBrainProvider,
    RuleBasedLearningBrainProvider,
)
from backend.intelligence.learning_integration import OutcomeLearningIntegrator
from backend.intelligence.outcome_tracker import OutcomeTracker
from backend.intelligence.outcomes import (
    MetricAnalysis,
    RecommendationOutcome,
    OutcomeDirection as RecOutcomeDirection,
)
from backend.intelligence.market_event_engine import MarketEventEngine
from backend.intelligence.models import (
    DataSource,
    Evidence,
    EvidenceType,
    ItemType,
    KnowledgeItem,
    MarketEvent,
    ProblemDirection,
    ReviewAssessment,
    ReviewIssue,
    ReviewSentiment,
    ReviewSignal,
    ReviewSignalType,
    SeasonalityRecord,
    SellerAction,
    SellerObservation,
    SellerProblem,
    SignalFrequency,
    SignalSeverity,
    SourceType,
    TrendRecord,
)
from backend.intelligence.reviews import ReviewIntelligence
from backend.intelligence.search_service import SearchService
from backend.intelligence.seasonality_engine import SeasonalityEngine
from backend.intelligence.solution_research import (
    DecisionRecord,
    DecisionStatus,
    SolutionOption,
    SolutionResearchResult,
    research_solutions,
)
from backend.intelligence.sources.registry import SourceRegistry
from backend.intelligence.sources.wordstat import WordstatAdapter
from backend.intelligence.sources.yandex_search import YandexSearchAdapter
from backend.intelligence.store import IntelligenceStore
from backend.intelligence.trend_engine import TrendEngine

__all__ = [
    # store
    "IntelligenceStore",
    "IIntelligenceStore",
    # engine
    "EvidenceEngine",
    "TrendEngine",
    "SeasonalityEngine",
    "MarketEventEngine",
    "CategoryIntelligence",
    "CategoryContext",
    "YandexCostGuard",
    "GuardResult",
    "GuardStatus",
    # learning loop
    "LearningBrain",
    "LearningBrainProvider",
    "RuleBasedLearningBrainProvider",
    "ActionOutcome",
    "LearningSignal",
    "LearningAssessment",
    "LearningSignalType",
    "OutcomeDirection",
    "OutcomeLearningIntegrator",
    # outcome tracker
    "OutcomeTracker",
    "RecommendationOutcome",
    "MetricAnalysis",
    "RecOutcomeDirection",
    # event sources
    "EventSourceAdapter",
    "YandexNewsAdapter",
    # sources
    "SourceRegistry",
    "WordstatAdapter",
    "YandexSearchAdapter",
    # service
    "SearchService",
    # evidence v2
    "SignalExtractor",
    "SignalType",
    "CategoryResolver",
    "ConflictDetector",
    "EvidenceConflict",
    "ConflictSeverity",
    "EvidenceAggregator",
    "AggregatedEvidence",
    # models
    "DataSource",
    "KnowledgeItem",
    "Evidence",
    "EvidenceType",
    "ItemType",
    "SourceType",
    "SellerObservation",
    "SeasonalityRecord",
    "TrendRecord",
    "MarketEvent",
    "ReviewIntelligence",
    "ReviewAssessment",
    "ReviewIssue",
    "ReviewSignal",
    "ReviewSignalType",
    "ReviewSentiment",
    "SellerProblem",
    "SellerAction",
    "SignalFrequency",
    "SignalSeverity",
    "ProblemDirection",
    "SolutionResearchResult",
    "SolutionOption",
    "DecisionRecord",
    "DecisionStatus",
    "research_solutions",
]
