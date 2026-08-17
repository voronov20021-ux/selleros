"""knowledge models — versioned knowledge entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class KnowledgeCategory(str, Enum):
    ECONOMICS = "economics"
    UNIT_ECONOMICS = "unit_economics"
    MARKETING = "marketing"
    METRICS = "metrics"
    MARKETPLACES = "marketplaces"
    MARKETPLACE = "marketplace"  # alias category for seller-facing
    PROCUREMENT = "procurement"
    LOGISTICS = "logistics"
    FINANCE = "finance"
    WILDBERRIES = "wildberries"


@dataclass
class KnowledgeEntry:
    term: str
    category: KnowledgeCategory
    short_definition: str = ""
    full_definition: str = ""
    # compat alias used by older callers
    definition: str = ""
    formula: str | None = None
    formula_id: str | None = None
    variables: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    common_mistakes: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    knowledge_id: str | None = None
    source: str = "argus_knowledge_v2"
    source_date: str = "2026-04-11"
    version: str = "2.0"
    valid_from: str = "2026-01-01"
    valid_to: str | None = None
    # legacy aliases
    effective_from: str | None = None
    effective_to: str | None = None

    def __post_init__(self) -> None:
        if not self.short_definition and self.definition:
            self.short_definition = self.definition
        if not self.full_definition:
            self.full_definition = self.definition or self.short_definition
        if not self.definition:
            self.definition = self.short_definition or self.full_definition
        if self.effective_from is None:
            self.effective_from = self.valid_from
        if self.effective_to is None:
            self.effective_to = self.valid_to
        if not self.knowledge_id:
            self.knowledge_id = f"kb:{self.category.value}:{self.term.lower().replace(' ', '_')}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "term": self.term,
            "category": self.category.value,
            "short_definition": self.short_definition,
            "full_definition": self.full_definition,
            "definition": self.definition,
            "formula": self.formula,
            "formula_id": self.formula_id,
            "variables": list(self.variables),
            "examples": list(self.examples),
            "common_mistakes": list(self.common_mistakes),
            "related_terms": list(self.related_terms),
            "aliases": list(self.aliases),
            "source": self.source,
            "source_date": self.source_date,
            "version": self.version,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
        }

    def format_answer(self, *, depth: str = "standard") -> str:
        """depth: beginner | standard | expert"""
        lines: list[str] = [f"**{self.term}**", ""]
        lines.append(self.short_definition or self.definition)
        if depth != "beginner" and self.full_definition and self.full_definition != self.short_definition:
            lines += ["", self.full_definition]
        if self.formula:
            lines += ["", f"Формула: `{self.formula}`"]
            if self.formula_id:
                lines.append(f"formula_id: `{self.formula_id}`")
        if self.variables and depth != "beginner":
            lines += ["", "Переменные: " + ", ".join(self.variables)]
        if self.examples:
            lines += ["", "Пример:" if depth != "expert" else "Практика:"]
            n = 1 if depth == "beginner" else 2
            for ex in self.examples[:n]:
                lines.append(f"• {ex}")
        if self.common_mistakes:
            title = "Не путай:" if depth == "beginner" else "Частые ошибки:"
            lines += ["", title]
            n = 2 if depth == "beginner" else 3
            for m in self.common_mistakes[:n]:
                lines.append(f"• {m}")
        if self.related_terms and depth != "beginner":
            lines.append("")
            lines.append("Связано: " + ", ".join(self.related_terms[:8]))
        lines.append("")
        lines.append(
            f"_Источник: {self.source} v{self.version} "
            f"(с {self.valid_from}"
            + (f" по {self.valid_to}" if self.valid_to else "")
            + ")_"
        )
        return "\n".join(lines)
