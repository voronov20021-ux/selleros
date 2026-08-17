"""
chat.py — Knowledge / Formula / WB Policy conversational layer.

No Browser. No LLM inventing formulas or penalties.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backend.foundation.formula_engine import FormulaEngine, FormulaStatus
from backend.foundation.outcome_foundation import OutcomeFoundation
from backend.knowledge.base import KnowledgeBase, get_default_knowledge_base
from backend.knowledge.wb_policy import WBPolicyEngine, get_default_wb_policy_engine

_KNOWLEDGE_HINTS = (
    "что такое", "что значит", "как посчитать", "как рассчитать",
    "из чего складывается", "какие расходы", "определени",
    "формула", "unit-эконом", "юнит-эконом", "юнит эконом",
    "что такое лид", "как найти лида", "что такое ctr", "что такое cvr",
    "что такое ip", "маржа", "наценка", "почему я ухожу в минус",
    "объясни", "как новичку", "как опытному",
)

_POLICY_HINTS = (
    "штраф", "оферт", "за что wildberries", "за что wb",
    "пункт оферты", "перечень штраф", "нарушени", "удержан",
    "маркировк", "блокир",
)

_ACTION_CHECK_HINTS = (
    "неделю назад мы", "помоги проверить результат",
    "проверить результат", "проверим результат",
    "сработало ли", "какой эффект после",
    "мы поменяли фото", "после смены фото",
)


def _low(t: str) -> str:
    return (t or "").lower().replace("ё", "е")


def detect_depth(text: str) -> str:
    t = _low(text)
    if "новичк" in t or "простыми словами" in t or "как ребенку" in t:
        return "beginner"
    if "опытн" in t or "для селлера" in t or "глубок" in t or "детально" in t:
        return "expert"
    return "standard"


def is_knowledge_query(text: str) -> bool:
    t = _low(text)
    if any(h in t for h in _POLICY_HINTS) and "что такое оферт" not in t:
        # «что такое оферта» is knowledge; bare penalty questions are policy
        if not any(x in t for x in ("что такое", "что значит", "объясни")):
            return False
    if any(h in t for h in _ACTION_CHECK_HINTS):
        return False
    return any(h in t for h in _KNOWLEDGE_HINTS)


def is_wb_policy_query(text: str) -> bool:
    t = _low(text)
    if "что такое оферт" in t:
        return False
    return any(h in t for h in _POLICY_HINTS)


def is_action_check_query(text: str) -> bool:
    return any(h in _low(text) for h in _ACTION_CHECK_HINTS)


def should_handle_knowledge(text: str, *, has_action_ctx: bool = False) -> bool:
    if is_wb_policy_query(text) or is_knowledge_query(text):
        return True
    if is_action_check_query(text) or (has_action_ctx and "результат" in _low(text)):
        return True
    return False


@dataclass
class KnowledgeReply:
    text: str
    kind: str  # knowledge | formula | policy | action_check | product | insufficient
    used_browser: bool = False
    formula_id: str | None = None
    policy_id: str | None = None
    knowledge_id: str | None = None
    depth: str = "standard"
    meta: dict[str, Any] = field(default_factory=dict)


_TERM_EXTRACT = re.compile(
    r"(?:что такое|что значит|как посчитать|как рассчитать|из чего складывается|объясни(?:\s+мне)?)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)


def _extract_term(text: str) -> str:
    t = _low(text)
    # strip depth phrases
    for p in ("как новичку", "как опытному селлеру", "как опытному", "простыми словами"):
        t = t.replace(p, " ")
    m = _TERM_EXTRACT.search(text or "")
    if m:
        term = m.group(1).strip(" ?!.")
        for p in ("как новичку", "как опытному селлеру", "как опытному"):
            term = re.sub(p, "", term, flags=re.I).strip()
        return term
    for needle in (
        "unit-экономика", "unit economics", "юнит-экономика", "юнит экономика",
        "маржа", "наценка", "ctr", "cvr", "лид", "ip", "воронка", "cac", "roas",
        "комиссия", "логистика", "прибыль", "выручка", "оферта",
    ):
        if needle in t:
            return needle
    return (text or "").strip()


def _g(obj, *names, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        for n in names:
            if n in obj and obj[n] is not None:
                return obj[n]
        return default
    for n in names:
        if hasattr(obj, n) and getattr(obj, n) is not None:
            return getattr(obj, n)
    return default


class KnowledgeChat:
    def __init__(
        self,
        kb: KnowledgeBase | None = None,
        policy: WBPolicyEngine | None = None,
        formulas: FormulaEngine | None = None,
        outcome_foundation: OutcomeFoundation | None = None,
    ) -> None:
        self.kb = kb or get_default_knowledge_base()
        self.policy = policy or get_default_wb_policy_engine()
        self.formulas = formulas or FormulaEngine()
        self.outcomes = outcome_foundation

    def handle(
        self,
        text: str,
        *,
        product=None,
        seller_data=None,
        finance_ctx=None,
        depth: str | None = None,
        as_of: date | None = None,
        action_check_payload: dict[str, Any] | None = None,
    ) -> KnowledgeReply:
        depth = depth or detect_depth(text)
        as_of = as_of or date.today()

        if is_wb_policy_query(text):
            body = self.policy.answer_penalty_question(text, on=as_of)
            return KnowledgeReply(text=body, kind="policy", depth=depth, used_browser=False)

        if is_action_check_query(text):
            return self._action_check(text, action_check_payload)

        t = _low(text)

        # IP ambiguity
        if re.search(r"\bip\b", t) or "что такое айпи" in t or "что такое ip" in t:
            entry = self.kb.get("IP")
            body = entry.format_answer(depth=depth) if entry else ""
            body += (
                "\n\nУточни контекст: **интеллектуальная собственность** "
                "или **IP-адрес/сеть**? Без этого не смешиваю с CTR/CVR."
            )
            return KnowledgeReply(
                text=body.strip(), kind="knowledge", depth=depth,
                knowledge_id=getattr(entry, "knowledge_id", None), used_browser=False,
            )

        # product-linked margin
        if ("маржа" in t) and any(x in t for x in ("моих", "моей", "этом товар", "nike", "артикул", "на мо")):
            return self._product_margin(text, product, seller_data, finance_ctx, depth)

        if "ухожу в минус" in t or "почему я ухожу в минус" in t or "ушел в минус" in t or "ушёл в минус" in t:
            return self._minus_stack(product, seller_data, finance_ctx, depth)

        if "как посчитать cvr" in t or "как рассчитать cvr" in t:
            return self._formula_term("CVR", "F_CVR", depth)
        if "как посчитать ctr" in t or "как рассчитать ctr" in t:
            return self._formula_term("CTR", "F_CTR", depth)

        if "из чего складывается маржа" in t or ("маржа" in t and "складыв" in t):
            entry = self.kb.get("margin")
            r = self.formulas.evaluate("F_MARGIN")
            body = (entry.format_answer(depth=depth) if entry else "") + "\n\n" + self.formulas.explain(r)
            body += "\n\n**Маржа ≠ наценка.** Без profit и revenue число не считаю."
            return KnowledgeReply(
                text=body.strip(), kind="formula", depth=depth,
                formula_id="F_MARGIN", knowledge_id=getattr(entry, "knowledge_id", None),
            )

        if "какие расходы" in t and ("unit" in t or "юнит" in t or "учитывать" in t):
            entry = self.kb.get("unit_economics") or self.kb.get("unit economics")
            lines = [entry.format_answer(depth=depth) if entry else "**Unit economics**", ""]
            lines.append("Учитывать только **известные** расходы (иначе NOT_INCLUDED):")
            for term in ("COGS", "комиссии", "логистика", "хранение", "рекламные_расходы", "возвраты", "налоги", "штраф"):
                e = self.kb.get(term)
                if e:
                    lines.append(f"• **{e.term}** — {e.short_definition}")
            lines.append("")
            lines.append("Не выдумываю комиссию/рекламу/штрафы.")
            return KnowledgeReply(text="\n".join(lines), kind="knowledge", depth=depth)

        if "как найти лида" in t:
            entry = self.kb.get("lead")
            body = entry.format_answer(depth=depth) if entry else ""
            body += (
                "\n\nПрактика: зафиксируй целевое действие → измерь CPL/CPA → "
                "не путай показ/клик/заказ/клиента. Без цифр CPL не выдумываю."
            )
            return KnowledgeReply(text=body.strip(), kind="knowledge", depth=depth,
                                  knowledge_id=getattr(entry, "knowledge_id", None))

        term = _extract_term(text)
        hits = self.kb.search(term, limit=3)
        if not hits:
            return KnowledgeReply(
                text=(
                    "В Knowledge Base нет точного термина.\n"
                    "Не выдумываю определение. Уточни (CTR, маржа, unit-экономика, лид, оферта)."
                ),
                kind="insufficient", depth=depth, used_browser=False,
            )

        primary = hits[0]
        body = primary.format_answer(depth=depth)

        # enrich CTR
        if primary.term.upper() == "CTR" or "ctr" in (primary.term or "").lower():
            body += (
                "\n\n**Что показывает:** привлекательность в выдаче (клик с показа).\n"
                "**Чего НЕ показывает:** качество карточки после клика (это CVR), маржу, прибыль.\n"
                "**Связь с CVR:** низкий CTR → проблема входа; низкий CVR при нормальном CTR → после клика.\n"
                "Бенчмарк «хороший CTR = 5%» без ниши/контекста **не использую**."
            )
        if primary.term.lower() == "lead" or "лид" in primary.aliases:
            body += "\n\n**Различие:** lead ≠ click ≠ order ≠ customer."

        if primary.formula_id and depth != "beginner":
            fr = self.formulas.evaluate(primary.formula_id)
            body += "\n\n" + self.formulas.explain(fr)

        if len(hits) > 1 and depth == "expert":
            body += "\n\nТакже рядом: " + ", ".join(h.term for h in hits[1:])

        # marketplace apply hint
        if depth != "beginner" and primary.category.value in (
            "unit_economics", "economics", "metrics", "marketplace", "marketplaces",
        ):
            body += "\n\nНа маркетплейсе: считай только KNOWN inputs из карточки/seller data; остальное — MISSING."

        return KnowledgeReply(
            text=body,
            kind="knowledge",
            depth=depth,
            knowledge_id=primary.knowledge_id,
            formula_id=primary.formula_id,
            used_browser=False,
        )

    def _formula_term(self, term: str, formula_id: str, depth: str) -> KnowledgeReply:
        entry = self.kb.get(term)
        r = self.formulas.evaluate(formula_id)
        body = (entry.format_answer(depth=depth) if entry else "") + "\n\n" + self.formulas.explain(r)
        return KnowledgeReply(
            text=body.strip(), kind="formula", depth=depth,
            formula_id=formula_id, knowledge_id=getattr(entry, "knowledge_id", None),
        )

    def _product_margin(self, text, product, seller_data, finance_ctx, depth) -> KnowledgeReply:
        price = _g(seller_data, "price") or _g(product, "price")
        cost = _g(seller_data, "cost") or _g(finance_ctx, "purchase_per_unit", "purchase_price", "cost")
        lines = ["### Маржа на твоём товаре", ""]
        title = _g(product, "title", default="")
        art = _g(product, "article") or _g(product, "nm_id")
        if title or art:
            lines.append(f"Товар: {title} (арт. {art})" if art else f"Товар: {title}")
        stack = self.formulas.cost_stack(
            revenue=None,  # need units; show unit path
            cogs=cost,
            logistics=_g(seller_data, "logistics") or _g(finance_ctx, "logistics"),
            commission=_g(seller_data, "commission") or _g(finance_ctx, "commission"),
            storage=_g(seller_data, "storage") or _g(finance_ctx, "storage"),
            returns=_g(seller_data, "returns"),
            advertising=_g(seller_data, "ad_spend") or _g(finance_ctx, "ads", "ad_spend"),
            taxes=_g(finance_ctx, "tax", "taxes"),
            penalties=None,
        )
        if price is not None and cost is not None:
            up = self.formulas.evaluate("F_UNIT_PROFIT", selling_price=price, unit_costs=cost)
            # unit costs incomplete — note
            lines.append(self.formulas.explain(up))
            if up.status is FormulaStatus.KNOWN and up.value is not None and price:
                mu = self.formulas.evaluate("F_MARGIN_UNIT", unit_profit=up.value, selling_price=price)
                lines.append(self.formulas.explain(mu))
                mk = self.formulas.evaluate("F_MARKUP", selling_price=price, cost=cost)
                lines.append(self.formulas.explain(mk))
                lines.append("_Маржа ≠ наценка — оба показаны отдельно._")
        else:
            lines.append("Не хватает цены и/или себестоимости — маржу не считаю.")
        lines.append("")
        lines.append("Статусы расходов:")
        for k, fr in stack.items():
            if k in ("revenue", "profit"):
                continue
            lines.append(f"• {k}: **{fr.status.value}**" + (f" = {fr.value}" if fr.value is not None else ""))
        lines.append("• penalties: **NOT_INCLUDED** — данных о штрафах нет, не выдумываю.")
        return KnowledgeReply(text="\n".join(lines), kind="product", depth=depth, formula_id="F_MARGIN_UNIT")

    def _minus_stack(self, product, seller_data, finance_ctx, depth) -> KnowledgeReply:
        price = _g(seller_data, "price") or _g(product, "price")
        orders = _g(seller_data, "orders") or _g(finance_ctx, "units", "orders")
        revenue = None
        if price is not None and orders is not None:
            revenue = float(price) * float(orders)
        elif _g(seller_data, "revenue") is not None:
            revenue = float(_g(seller_data, "revenue"))
        stack = self.formulas.cost_stack(
            revenue=revenue,
            cogs=_g(seller_data, "cost"),
            logistics=_g(seller_data, "logistics"),
            commission=_g(seller_data, "commission"),
            storage=_g(seller_data, "storage"),
            returns=_g(seller_data, "returns"),
            advertising=_g(seller_data, "ad_spend"),
            taxes=None,
            penalties=None,
        )
        lines = [
            "### Почему можно уйти в минус",
            "",
            "Стек (только известное):",
            "Revenue − COGS − logistics − commission − storage − returns − advertising − taxes − penalties − other",
            "",
        ]
        for name, fr in stack.items():
            if fr.status is FormulaStatus.KNOWN:
                lines.append(f"• {name}: KNOWN = {fr.value}")
            else:
                lines.append(f"• {name}: {fr.status.value}")
        lines += [
            "",
            "Штрафы: **NOT_INCLUDED** — данных нет, не подставляю «примерно 3000 ₽».",
            "Без цифр не ставлю диагноз «виновата реклама» или «виновата цена».",
        ]
        if stack.get("profit") and stack["profit"].status is FormulaStatus.KNOWN:
            lines.append("")
            lines.append(self.formulas.explain(stack["profit"]))
        return KnowledgeReply(text="\n".join(lines), kind="formula", depth=depth, formula_id="F_PROFIT")

    def _action_check(self, text: str, payload: dict[str, Any] | None) -> KnowledgeReply:
        payload = payload or {}
        before = dict(payload.get("baseline_metrics") or {})
        after = dict(payload.get("after_metrics") or {})
        if not before or not after:
            return KnowledgeReply(
                text=(
                    "Для проверки результата нужны baseline и after метрики "
                    "(CTR/CVR/заказы…).\nБез них итог = **INCONCLUSIVE** — не считаю успехом."
                ),
                kind="action_check", used_browser=False,
            )
        foundation = self.outcomes or OutcomeFoundation()
        rec = foundation.evaluate(
            baseline_metrics=before,
            after_metrics=after,
            action_id=payload.get("action_id"),
            action_type=payload.get("action_type"),
            expected_effect=payload.get("expected_effect"),
            baseline_snapshot_id=payload.get("baseline_snapshot_id"),
            after_snapshot_id=payload.get("after_snapshot_id"),
        )
        return KnowledgeReply(
            text=foundation.format_check_reply(rec),
            kind="action_check", used_browser=False,
        )


_DEFAULT_CHAT: KnowledgeChat | None = None


def handle_knowledge_turn(
    text: str,
    *,
    product=None,
    seller_data=None,
    finance_ctx=None,
    depth: str | None = None,
    as_of: date | None = None,
    action_check_payload: dict[str, Any] | None = None,
) -> KnowledgeReply:
    global _DEFAULT_CHAT
    if _DEFAULT_CHAT is None:
        _DEFAULT_CHAT = KnowledgeChat()
    return _DEFAULT_CHAT.handle(
        text,
        product=product,
        seller_data=seller_data,
        finance_ctx=finance_ctx,
        depth=depth,
        as_of=as_of,
        action_check_payload=action_check_payload,
    )
