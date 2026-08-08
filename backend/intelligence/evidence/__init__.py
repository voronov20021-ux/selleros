"""
intelligence/evidence — обработка и извлечение знаний (Evidence Engine v2).

Публичное API:
    EvidenceEngine      — ingestion, normalize, deduplicate, signals, retrieve, decay
    SignalExtractor     — rule-based извлечение сигналов из KnowledgeItem
    SignalType          — перечисление типов сигналов
    CategoryResolver    — определение WB-категории из текста
    ConflictDetector    — обнаружение противоречий между Evidence
    EvidenceConflict    — dataclass конфликта
    ConflictSeverity    — уровень серьёзности конфликта
    EvidenceAggregator  — агрегация похожих Evidence в консенсус
    AggregatedEvidence  — результат агрегации
"""

from backend.intelligence.evidence.aggregator import AggregatedEvidence, EvidenceAggregator
from backend.intelligence.evidence.category import CategoryResolver
from backend.intelligence.evidence.conflicts import (
    ConflictDetector,
    ConflictSeverity,
    EvidenceConflict,
)
from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.evidence.signals import SignalExtractor, SignalType

__all__ = [
    "EvidenceEngine",
    "SignalExtractor",
    "SignalType",
    "CategoryResolver",
    "ConflictDetector",
    "EvidenceConflict",
    "ConflictSeverity",
    "EvidenceAggregator",
    "AggregatedEvidence",
]
