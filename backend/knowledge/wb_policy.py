"""
wb_policy.py — versioned Wildberries Policy / Offer Knowledge Engine.

Loads official PDF excerpt via wb_ingest. Placeholders never presented as
confirmed ruble amounts. TimeService/date selects effective version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from backend.knowledge.wb_ingest import (
    CABINET_HELP_URL,
    OFFICIAL_PDF_URL,
    load_ingested_rules,
)


@dataclass
class WBPolicyRule:
    rule_id: str
    category: str
    title: str = ""
    violation: str = ""
    description: str = ""
    text_summary: str = ""
    penalty: str | None = None
    penalty_formula: str | None = None
    maximum: str | None = None
    minimum: str | None = None
    sales_models: list[str] = field(default_factory=list)
    offer_clause: str | None = None
    penalty_list_item: str | None = None
    conditions: str | None = None
    exceptions: str | None = None
    financial_impact: str | None = None
    severity: str | None = None
    official_source: str | None = None
    source_url: str | None = None
    offer_version: str | None = None
    source_quality: str = "official_pdf_excerpt"
    effective_from: str = "2022-03-23"
    effective_to: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    source: str = "wb_offer_ingest"
    source_updated_at: str = "2026-04-11"
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.valid_from:
            self.valid_from = self.effective_from
        if self.valid_to is None and self.effective_to:
            self.valid_to = self.effective_to
        if not self.effective_from and self.valid_from:
            self.effective_from = self.valid_from
        if not self.text_summary and self.description:
            self.text_summary = self.description
        if not self.description and self.text_summary:
            self.description = self.text_summary
        if not self.title:
            self.title = self.violation or self.rule_id

    def is_effective_on(self, on: date | None = None) -> bool:
        d = on or date.today()
        start_s = self.valid_from or self.effective_from
        try:
            start = date.fromisoformat(start_s)
        except ValueError:
            start = date.min
        if d < start:
            return False
        end_s = self.valid_to if self.valid_to is not None else self.effective_to
        if end_s:
            try:
                end = date.fromisoformat(end_s)
            except ValueError:
                end = date.max
            if d > end:
                return False
        return True

    def is_placeholder(self) -> bool:
        q = (self.source_quality or "").lower()
        return "placeholder" in q or "cabinet_check" in q or q.startswith("version_slot")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "category": self.category,
            "violation": self.violation,
            "text_summary": self.text_summary,
            "description": self.description,
            "penalty": self.penalty,
            "penalty_formula": self.penalty_formula,
            "maximum": self.maximum,
            "minimum": self.minimum,
            "sales_models": list(self.sales_models),
            "offer_clause": self.offer_clause,
            "penalty_list_item": self.penalty_list_item,
            "conditions": self.conditions,
            "exceptions": self.exceptions,
            "financial_impact": self.financial_impact,
            "severity": self.severity,
            "official_source": self.official_source,
            "source_url": self.source_url,
            "offer_version": self.offer_version,
            "source_quality": self.source_quality,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "source": self.source,
            "source_updated_at": self.source_updated_at,
            "version": self.version,
        }

    @classmethod
    def from_ingest_dict(cls, d: dict[str, Any]) -> "WBPolicyRule":
        return cls(
            rule_id=str(d.get("rule_id")),
            category=str(d.get("category") or "offer"),
            title=str(d.get("title") or ""),
            violation=str(d.get("violation") or ""),
            description=str(d.get("text_summary") or d.get("description") or ""),
            text_summary=str(d.get("text_summary") or d.get("description") or ""),
            penalty=d.get("penalty"),
            penalty_formula=d.get("penalty_formula"),
            maximum=d.get("maximum"),
            minimum=d.get("minimum"),
            sales_models=list(d.get("sales_models") or []),
            offer_clause=d.get("offer_clause"),
            penalty_list_item=d.get("penalty_list_item"),
            conditions=d.get("conditions"),
            exceptions=d.get("exceptions"),
            financial_impact=d.get("financial_impact"),
            severity=d.get("severity"),
            official_source=d.get("official_source"),
            source_url=d.get("source_url"),
            offer_version=d.get("offer_version"),
            source_quality=str(d.get("source_quality") or "official_pdf_excerpt"),
            effective_from=str(d.get("valid_from") or d.get("effective_from") or "2022-03-23"),
            effective_to=d.get("valid_to") if d.get("valid_to") is not None else d.get("effective_to"),
            valid_from=d.get("valid_from") or d.get("effective_from"),
            valid_to=d.get("valid_to") if d.get("valid_to") is not None else d.get("effective_to"),
            version=str(d.get("version") or "1.0"),
            source="wb_offer_ingest",
        )

    def format_answer(self, *, as_of: date | None = None) -> str:
        active = self.is_effective_on(as_of)
        lines = [
            f"**{self.title or self.rule_id}** (`{self.rule_id}` v{self.version})",
            f"Категория: {self.category}",
        ]
        if self.violation and self.violation != "—":
            lines.append(f"Нарушение: {self.violation}")
        lines += ["", self.text_summary or self.description]
        if self.penalty and not self.is_placeholder():
            lines += ["", f"Штраф/неустойка (по ingested тексту): {self.penalty}"]
        elif self.penalty and self.is_placeholder():
            lines += ["", f"Сумма не подтверждена как актуальная: {self.penalty}"]
        if self.penalty_formula:
            lines.append(f"Формула/условие: `{self.penalty_formula}`")
        if self.minimum or self.maximum:
            lines.append(f"Мин: {self.minimum or '—'} · Макс: {self.maximum or '—'}")
        if self.financial_impact:
            lines.append(f"Финансовый эффект: {self.financial_impact}")
        if self.sales_models:
            lines.append("Модели: " + ", ".join(self.sales_models))
        if self.conditions:
            lines.append(f"Условия: {self.conditions}")
        if self.exceptions:
            lines.append(f"Исключения: {self.exceptions}")
        if self.offer_clause:
            lines.append(f"Пункт оферты: {self.offer_clause}")
        if self.penalty_list_item:
            lines.append(f"Перечень: {self.penalty_list_item}")
        lines += [
            "",
            f"Действует с {self.valid_from or self.effective_from}"
            + (f" по {self.valid_to or self.effective_to}" if (self.valid_to or self.effective_to) else " (пока не закрыто в ingest)"),
            f"Источник: {self.official_source or self.source} · {self.source_url or OFFICIAL_PDF_URL}",
            f"quality: {self.source_quality} · offer_version: {self.offer_version or '—'}",
        ]
        if not active:
            lines += ["", "⚠️ Правило **не действует** на выбранную дату — не используйте как актуальное."]
        if self.is_placeholder():
            lines += ["", "⚠️ Требуется сверка с актуальной редакцией в WB Partners."]
        return "\n".join(lines)


class WBPolicyEngine:
    CATEGORIES = (
        "definitions", "offer", "seller_responsibility", "penalties",
        "withholdings", "logistics", "storage", "returns", "orders",
        "supplies", "marking", "product_cards", "prohibited_goods",
        "categories", "documents", "promotion", "payments", "deadlines",
        "restrictions",
    )

    def __init__(self, rules: Iterable[WBPolicyRule] | None = None) -> None:
        self._rules: list[WBPolicyRule] = list(rules or [])
        self.document_meta: dict[str, Any] = {}

    def add(self, rule: WBPolicyRule) -> None:
        self._rules.append(rule)

    def get(self, rule_id: str, *, version: str | None = None, on: date | None = None) -> WBPolicyRule | None:
        matches = [r for r in self._rules if r.rule_id == rule_id]
        if version:
            for r in matches:
                if r.version == version:
                    return r
            return None
        active = [r for r in matches if r.is_effective_on(on)]
        # prefer non-placeholder among active
        preferred = [r for r in active if not r.is_placeholder()] or active or matches
        if not preferred:
            return None
        return sorted(preferred, key=lambda r: (r.valid_from or "", r.version))[-1]

    def rules_effective_on(self, on: date | None = None, *, category: str | None = None) -> list[WBPolicyRule]:
        out = [r for r in self._rules if r.is_effective_on(on) and not r.is_placeholder()]
        # if filtering removes all for a category that only has placeholders, still empty — honesty
        if category:
            out = [r for r in out if r.category == category]
        return out

    def current_rules(self, *, category: str | None = None, on: date | None = None) -> list[WBPolicyRule]:
        return self.rules_effective_on(on, category=category)

    def history(self, rule_id: str) -> list[WBPolicyRule]:
        return sorted(
            [r for r in self._rules if r.rule_id == rule_id],
            key=lambda r: (r.valid_from or "", r.version),
        )

    def search(self, query: str, *, on: date | None = None, limit: int = 5) -> list[WBPolicyRule]:
        q = (query or "").lower().replace("ё", "е")
        stop = {
            "штраф", "штрафы", "оферта", "оферты", "wildberries", "wb",
            "за", "что", "какой", "какая", "может", "начислить", "пункт",
        }
        tokens = [t for t in q.split() if len(t) >= 3 and t not in stop]
        if not tokens:
            return []
        scored: list[tuple[int, WBPolicyRule]] = []
        for r in self.rules_effective_on(on):
            blob = " ".join([
                r.rule_id, r.category, r.title, r.violation, r.text_summary,
                r.penalty or "", r.offer_clause or "", r.penalty_list_item or "",
            ]).lower().replace("ё", "е")
            score = sum(1 for tok in tokens if tok in blob)
            if score >= 1:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    def answer_penalty_question(self, query: str, *, on: date | None = None) -> str:
        hits = self.search(query, on=on, limit=3)
        if hits:
            parts = [
                "### Правила WB (по ingested официальному excerpt)",
                f"_Дата среза: {(on or date.today()).isoformat()}_",
                "",
            ]
            for r in hits:
                parts.append(r.format_answer(as_of=on))
                parts.append("")
            parts.append(
                f"Актуальную редакцию и архив сверяй в кабинете: {CABINET_HELP_URL}"
            )
            parts.append("_Не выдумываю суммы вне ingested текста / Перечня._")
            return "\n".join(parts)

        q = (query or "").lower().replace("ё", "е")
        if any(x in q for x in ("штраф", "удержан", "оферт", "наруш", "блокир")):
            pens = self.rules_effective_on(on, category="penalties")
            if not pens:
                pens = [
                    r for r in self.rules_effective_on(on)
                    if r.category in ("penalties", "withholdings", "marking", "product_cards", "restrictions")
                ]
            lines = [
                "### За что WB может начислить штраф / удержание",
                "",
                "Категории из **офипускаемого ingest** официального PDF excerpt.",
                "Большинство точных ₽ — в «Перечне штрафов» актуальной оферты кабинета; без него не выдумываю.",
                "",
            ]
            seen = set()
            for r in pens:
                if r.rule_id in seen:
                    continue
                seen.add(r.rule_id)
                fin = r.financial_impact or r.penalty or "см. Перечень / кабинет"
                lines.append(
                    f"• **{r.rule_id}** v{r.version}: {r.title or r.violation} "
                    f"(п. {r.offer_clause or '—'}; эффект: {fin})"
                )
            lines += [
                "",
                f"Где проверить актуальность: {CABINET_HELP_URL}",
                f"Ingest PDF: {OFFICIAL_PDF_URL}",
            ]
            return "\n".join(lines)

        return (
            "В структурированной базе нет подходящего актуального пункта на эту дату.\n"
            "Не выдумываю сумму штрафа. Нужен rule_id / категория или сверка с разделом "
            f"«Оферты» в WB Partners ({CABINET_HELP_URL})."
        )


def build_rules_from_ingest() -> tuple[dict[str, Any], list[WBPolicyRule]]:
    doc, rows = load_ingested_rules()
    rules = [WBPolicyRule.from_ingest_dict(r) for r in rows]
    return doc, rules


_DEFAULT_ENGINE: WBPolicyEngine | None = None


def get_default_wb_policy_engine() -> WBPolicyEngine:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None:
        doc, rules = build_rules_from_ingest()
        eng = WBPolicyEngine(rules)
        eng.document_meta = doc.get("document") or {}
        _DEFAULT_ENGINE = eng
    return _DEFAULT_ENGINE


def reset_default_wb_policy_engine() -> WBPolicyEngine:
    global _DEFAULT_ENGINE
    _DEFAULT_ENGINE = None
    return get_default_wb_policy_engine()


# Back-compat name used by older tests
def build_seed_wb_rules() -> list[WBPolicyRule]:
    _, rules = build_rules_from_ingest()
    return rules
