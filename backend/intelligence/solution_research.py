"""
solution_research.py — SOLUTION_RESEARCH + Decision Memory v1 для Argus.

Использует SearchService (Yandex) если доступен.
НЕ выдумывает магазины/цены/ссылки/сток/рейтинги.
Если поиска нет — критерии + типы решений + честное сообщение.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("selleros.intelligence.solution_research")

# ─── intent markers (robust recognition) ─────────────────────────────────── #

_BUY_MARKERS = (
    "где купить", "где найти", "где взять", "где заказать",
    "что купить", "что заказать", "чем заменить",
    "какой лучше", "какая лучше", "какие лучше", "какой взять",
    "сравни", "сравн", "дешевле", "красивее", "красивые дешев", "красивые дешёв",
    "сколько стоит", "какой размер", "какого размера",
    "наполнитель", "коробк", "упаков", "фиксатор", "ложемент",
    "посоветуй поставщик", "найди упаков", "варианты упаков",
    "купить короб", "купить упаков", "поставщик упаков",
)

_CHOICE_MARKERS = (
    "какую из", "какой бы выбрал", "что бы выбрал", "ты бы выбрал",
    "какую взять", "что взять", "какой вариант", "сравни вариант",
    "какой лучше из", "что лучше из", "между ними",
)

_CONFIRM_MARKERS = (
    "беру", "беру её", "беру ее", "беру эту", "выбираю", "возьму",
    "остановился на", "берём", "берем", "беру первый", "беру второй",
    "беру третий", "беру 1", "беру 2", "беру 3",
)

_RECALL_MARKERS = (
    "что мы решили", "что решили", "какое решение", "что выбрали",
    "что решили по", "на чём остановились", "на чем остановились",
    "почему именно", "почему этот", "почему эту", "почему выбрали",
)

_IMPLEMENT_MARKERS = (
    "внедрил", "внедрила", "внедряю", "сделал", "сделала", "реализовал",
    "купил и поставил", "уже купил", "implement", "записать внедрение",
)

_ORDINAL_MAP = {
    "перв": 1, "1": 1, "один": 1, "a": 1, "а": 1,
    "втор": 2, "2": 2, "два": 2, "b": 2, "б": 2,
    "трет": 3, "3": 3, "три": 3, "c": 3, "в": 3,
    "четв": 4, "4": 4, "d": 4,
    "пят": 5, "5": 5, "e": 5,
}


# ─── Decision status ─────────────────────────────────────────────────────── #

class DecisionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    SELECTED = "SELECTED"
    IMPLEMENTED = "IMPLEMENTED"
    REJECTED = "REJECTED"
    TESTING = "TESTING"
    COMPLETED = "COMPLETED"


# ─── Packaging / problem → solution taxonomy (extensible) ────────────────── #

#: problem_type → list of solution type descriptors
SOLUTION_TAXONOMY: dict[str, list[dict[str, str]]] = {
    "PACKAGING": [
        {
            "type": "formament",
            "title": "Ложемент / фиксатор",
            "description": "Жёсткая фиксация товара внутри коробки, чтобы не болтался",
        },
        {
            "type": "foam",
            "title": "Пена / вспененный наполнитель",
            "description": "Амортизация ударов при доставке",
        },
        {
            "type": "flap_box",
            "title": "Коробка с клапанами (гофро)",
            "description": "Прочная внешняя тара под размер товара",
        },
        {
            "type": "branded_box",
            "title": "Брендированная коробка",
            "description": "Внешний вид + защита; важно для unboxing",
        },
        {
            "type": "insert",
            "title": "Вкладыш / картонный insert",
            "description": "Разделение и фиксация без тяжёлого наполнителя",
        },
        {
            "type": "void_fill",
            "title": "Наполнитель пустот (крафт/пузырьки)",
            "description": "Заполняет свободное пространство, снижает болтанку",
        },
    ],
    "DAMAGE": [
        {
            "type": "foam",
            "title": "Защитная пена",
            "description": "Снижает риск повреждения при транспортировке",
        },
        {
            "type": "flap_box",
            "title": "Усиленная гофрокоробка",
            "description": "Жёсткая внешняя защита",
        },
        {
            "type": "formament",
            "title": "Ложемент под форму",
            "description": "Товар не смещается и не бьётся о стенки",
        },
    ],
    "SIZE": [
        {
            "type": "size_chart",
            "title": "Обновить таблицу размеров",
            "description": "Сверить замеры с реальным товаром и карточкой",
        },
        {
            "type": "fit_guide",
            "title": "Гайд по посадке в описании",
            "description": "Снижает возвраты из-за «маломерит/большемерит»",
        },
    ],
    "PRODUCT_QUALITY": [
        {
            "type": "supplier_swap",
            "title": "Смена/проверка поставщика комплектующих",
            "description": "Устранить системный брак по evidence из отзывов",
        },
        {
            "type": "qc_check",
            "title": "Входящий контроль качества",
            "description": "Отсечь брак до отгрузки на WB",
        },
    ],
    "DEFAULT": [
        {
            "type": "criteria_based",
            "title": "Подбор по критериям под проблему",
            "description": "Без выдуманных магазинов — сначала критерии и тип решения",
        },
    ],
}


def solution_types_for_problem(problem_type: str | None) -> list[dict[str, str]]:
    key = (problem_type or "DEFAULT").upper()
    if key in ("PACKAGING", "УПАКОВКА"):
        key = "PACKAGING"
    elif key in ("DAMAGE", "ПОВРЕЖДЕНИЕ"):
        key = "DAMAGE"
    elif key in ("SIZE", "РАЗМЕР"):
        key = "SIZE"
    elif key in ("PRODUCT_QUALITY", "QUALITY", "КАЧЕСТВО"):
        key = "PRODUCT_QUALITY"
    return list(SOLUTION_TAXONOMY.get(key) or SOLUTION_TAXONOMY["DEFAULT"])


# ─── models ──────────────────────────────────────────────────────────────── #

@dataclass
class SolutionOption:
    """Вариант решения (v1). label сохранён для совместимости с RI v2."""

    id: str
    title: str
    type: str = "criteria_based"
    description: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    estimated_cost: float | None = None
    cost_source: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    research_source_ids: list[str] = field(default_factory=list)
    fit_score: float = 0.0
    confidence: float = 0.0
    problem_id: str | None = None
    label: str = ""  # A/B/C — UI + legacy
    snippet: str = ""
    source_url: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SolutionOption":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        payload = {k: v for k, v in (data or {}).items() if k in known}
        if "id" not in payload or not payload["id"]:
            payload["id"] = str(uuid.uuid4())
        if "title" not in payload:
            payload["title"] = "Вариант"
        return cls(**payload)


@dataclass
class SolutionResearchResult:
    query: str
    available: bool
    options: list[SolutionOption] = field(default_factory=list)
    preferred_label: str | None = None
    preferred_option_id: str | None = None
    preferred_reason: str = ""
    criteria: list[str] = field(default_factory=list)
    honest_message: str | None = None
    topic: str = "упаковка"
    problem_id: str | None = None
    problem_label: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    product_article: int | None = None
    solution_types: list[dict[str, str]] = field(default_factory=list)
    compare_table: str = ""
    from_cache: bool = False

    def to_context_block(self, *, max_chars: int = 1600) -> str:
        lines: list[str] = [
            "SOLUTION RESEARCH",
            f"Запрос: {self.query}",
            f"Тема: {self.topic}",
        ]
        if self.problem_label:
            lines.append(f"Проблема из отзывов: {self.problem_label}")
        if self.evidence_ids:
            lines.append(f"evidence: {','.join(self.evidence_ids[:5])}")
        if not self.available:
            lines.append(
                self.honest_message
                or "Актуальные предложения недоступны — не выдумываю магазины и цены."
            )
            if self.solution_types:
                lines.append("Типы решений (без магазинов/цен):")
                for st in self.solution_types[:6]:
                    lines.append(f" - [{st.get('type')}] {st.get('title')}: {st.get('description', '')[:80]}")
            if self.criteria:
                lines.append("Критерии выбора:")
                for c in self.criteria:
                    lines.append(f" - {c}")
        else:
            lines.append("Варианты (только из поиска, без выдуманных цен):")
            if self.compare_table:
                lines.append(self.compare_table)
            else:
                for opt in self.options:
                    url = f" | {opt.source_url}" if opt.source_url else ""
                    cost = (
                        f" | ~{opt.estimated_cost:.0f}₽ ({opt.cost_source})"
                        if opt.estimated_cost is not None and opt.cost_source
                        else " | цена неизвестна"
                    )
                    lines.append(f" {opt.label}) {opt.title}{cost}{url}")
                    if opt.snippet:
                        lines.append(f"    {opt.snippet[:160]}")
                    for n in (opt.notes or opt.pros)[:3]:
                        lines.append(f"    • {n}")
            if self.preferred_label:
                lines.append(
                    f"Я бы выбрал {self.preferred_label}: {self.preferred_reason}"
                )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return text

    def options_json(self) -> str:
        return json.dumps([o.to_dict() for o in self.options], ensure_ascii=False)


@dataclass
class DecisionRecord:
    """Persistent decision memory: seller_id + product_article scoped."""

    id: int | None
    seller_id: int
    product_article: int
    topic: str
    problem_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    recommendation: str = ""
    solution_options: list[dict] = field(default_factory=list)
    selected_solution_id: str | None = None
    seller_comment: str | None = None
    status: DecisionStatus = DecisionStatus.PROPOSED
    problem: str = ""
    seller_question: str = ""
    action: str | None = None
    outcome: str | None = None
    outcome_tracker_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def selected_option(self) -> dict | None:
        if not self.selected_solution_id:
            return None
        for opt in self.solution_options or []:
            if str(opt.get("id")) == str(self.selected_solution_id):
                return opt
            if str(opt.get("label", "")).upper() == str(self.selected_solution_id).upper():
                return opt
        return None


# ─── detectors ───────────────────────────────────────────────────────────── #

def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def is_solution_research_query(text: str) -> bool:
    low = _norm(text)
    if not low:
        return False
    return any(m.replace("ё", "е") in low for m in _BUY_MARKERS)


def is_solution_choice_query(text: str) -> bool:
    low = _norm(text)
    return any(m.replace("ё", "е") in low for m in _CHOICE_MARKERS)


def is_seller_confirm_choice(text: str) -> bool:
    low = _norm(text)
    if not low or len(low) > 100:
        return False
    return any(m.replace("ё", "е") in low for m in _CONFIRM_MARKERS)


def is_decision_recall_query(text: str) -> bool:
    low = _norm(text)
    return any(m.replace("ё", "е") in low for m in _RECALL_MARKERS)


def is_implement_query(text: str) -> bool:
    low = _norm(text)
    if not low or len(low) > 120:
        return False
    return any(m.replace("ё", "е") in low for m in _IMPLEMENT_MARKERS)


def parse_choice_index(text: str) -> int | None:
    """«Беру второй» / «беру 2» / «вариант B» → 1-based index or None."""
    low = _norm(text)
    if not low:
        return None
    m = re.search(
        r"(?:беру|выбираю|возьму|вариант|номер)\s*"
        r"(перв\w*|втор\w*|трет\w*|четв\w*|пят\w*|[1-5]|[a-eа-д])",
        low,
    )
    if m:
        token = m.group(1)
        for key, idx in _ORDINAL_MAP.items():
            if token.startswith(key) or token == key:
                return idx
    # bare "2" / "B" in short confirms
    if len(low) <= 24:
        m2 = re.search(r"\b([1-5]|[a-e])\b", low)
        if m2:
            tok = m2.group(1)
            return _ORDINAL_MAP.get(tok)
    return None


def infer_topic(text: str) -> str:
    low = _norm(text)
    if any(k in low for k in ("упаков", "короб", "пакет", "наполнител", "ложемент", "пен")):
        return "упаковка"
    if "размер" in low:
        return "размер"
    if "фото" in low:
        return "фото"
    if any(k in low for k in ("качеств", "брак")):
        return "качество"
    return "решение"


def infer_problem_type(text: str, *, signal_type: str | None = None) -> str:
    if signal_type:
        st = signal_type.upper()
        if "PACK" in st or "УПАК" in st:
            return "PACKAGING"
        if "DAMAGE" in st or "ПОВРЕЖ" in st:
            return "DAMAGE"
        if "SIZE" in st or "РАЗМЕР" in st:
            return "SIZE"
        if "QUALITY" in st or "КАЧЕСТВ" in st:
            return "PRODUCT_QUALITY"
    low = _norm(text)
    if any(k in low for k in ("упаков", "короб", "мят", "болта", "наполнител")):
        return "PACKAGING"
    if any(k in low for k in ("поврежд", "разбит", "тресну", "скол")):
        return "DAMAGE"
    if "размер" in low or "маломер" in low:
        return "SIZE"
    if any(k in low for k in ("качеств", "брак")):
        return "PRODUCT_QUALITY"
    return "DEFAULT"


def default_selection_criteria(topic: str = "упаковка") -> list[str]:
    if topic == "упаковка":
        return [
            "Цена за единицу и партия (MOQ) — без выдуманных цифр, сверять у поставщика",
            "Размер под ваш товар + запас на защиту",
            "Материал: прочность vs вес/стоимость доставки",
            "Внешний вид (если unboxing важен покупателю)",
            "Срок и регион доставки до вашего склада",
            "Насколько закрывает проблему из отзывов (фиксация / защита / внешний вид)",
        ]
    return [
        "Цена / партия (MOQ)",
        "Соответствие проблеме из отзывов",
        "Срок поставки",
        "Качество / материал",
        "Риск возврата при несоответствии",
    ]


# ─── product-scoped query ────────────────────────────────────────────────── #

def build_research_query(
    user_text: str,
    *,
    product_title: str | None = None,
    product_category: str | None = None,
    problem_label: str | None = None,
    topic: str | None = None,
) -> str:
    """Seller + product + review problem → research query (not from zero)."""
    parts: list[str] = []
    topic = topic or infer_topic(user_text)
    if problem_label:
        parts.append(problem_label[:80])
    elif topic == "упаковка":
        parts.append("упаковка коробка защита товара")
    low = _norm(user_text)
    # Keep actionable user intent words
    for marker in (
        "красивые", "дешевые", "дешёвые", "бренд", "опт",
        "наполнитель", "ложемент", "пена", "гофро",
    ):
        if marker.replace("ё", "е") in low and marker not in " ".join(parts).lower():
            parts.append(marker.replace("ё", "е"))
    if product_category:
        parts.append(str(product_category)[:40])
    if product_title:
        # short product cue — not full marketing title dump
        words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", product_title) if len(w) > 2][:4]
        if words:
            parts.append(" ".join(words))
    if not parts:
        parts.append(user_text.strip()[:120] or "решение для товара")
    return " ".join(parts)[:200]


def extract_problem_from_assessment(assessment) -> tuple[str | None, str, list[str], str]:
    """
    Returns (problem_id, problem_label, evidence_ids, problem_type).
    """
    problems = list(getattr(assessment, "problems", None) or [])
    risk = [
        p for p in problems
        if getattr(getattr(p, "direction", None), "value", "") in ("negative", "mixed", "")
        or getattr(p, "priority", 5) <= 3
    ]
    pool = risk or problems
    if not pool:
        return None, "", [], "DEFAULT"
    # prefer packaging-like
    chosen = pool[0]
    for p in pool:
        cat = (
            (getattr(p, "metadata", None) or {}).get("category")
            or getattr(getattr(p, "signal_type", None), "value", "")
            or ""
        )
        if "PACK" in str(cat).upper() or "УПАК" in str(cat).upper():
            chosen = p
            break
    pid = getattr(chosen, "id", None) or getattr(chosen, "problem_id", None)
    label = getattr(chosen, "label", None) or getattr(chosen, "title", "") or "проблема из отзывов"
    evid = list(getattr(chosen, "evidence_ids", None) or [])[:8]
    st = (
        (getattr(chosen, "metadata", None) or {}).get("category")
        or getattr(getattr(chosen, "signal_type", None), "value", None)
    )
    ptype = infer_problem_type(str(label), signal_type=str(st) if st else None)
    return (str(pid) if pid else None), str(label), evid, ptype


# ─── search item helpers ─────────────────────────────────────────────────── #

def _item_title(item) -> str:
    for attr in ("title", "claim", "content", "text"):
        val = getattr(item, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()[:120]
    meta = getattr(item, "metadata", None) or {}
    if isinstance(meta, dict):
        for key in ("title", "name", "snippet"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:120]
    return "Вариант из поиска"


def _item_snippet(item) -> str:
    for attr in ("snippet", "summary", "content", "claim"):
        val = getattr(item, attr, None)
        if isinstance(val, str) and val.strip():
            return re.sub(r"\s+", " ", val.strip())[:200]
    return ""


def _item_url(item) -> str | None:
    for attr in ("source_url", "url"):
        val = getattr(item, attr, None)
        if isinstance(val, str) and val.startswith("http"):
            return val
    meta = getattr(item, "metadata", None) or {}
    if isinstance(meta, dict):
        for key in ("url", "source_url", "link"):
            val = meta.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


def _item_id(item, fallback: str) -> str:
    for attr in ("id", "evidence_id", "source_id"):
        val = getattr(item, attr, None)
        if val is not None and str(val).strip():
            return str(val)
    return fallback


def _extract_cost(text: str) -> tuple[float | None, str | None]:
    """Parse cost ONLY if explicitly present in snippet — never invent."""
    if not text:
        return None, None
    m = re.search(
        r"(?:от\s+)?(\d[\d\s]{1,8})\s*(?:₽|руб\.?|рублей)",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None, None
    raw = m.group(1).replace(" ", "").replace("\xa0", "")
    try:
        val = float(raw)
    except ValueError:
        return None, None
    if val <= 0 or val > 10_000_000:
        return None, None
    return val, "search_snippet"


def _compare_notes(title: str, snippet: str, topic: str) -> list[str]:
    blob = f"{title} {snippet}".lower()
    notes: list[str] = []
    if any(k in blob for k in ("короб", "упаков", "пакет", "гофр")):
        notes.append("Похоже на упаковку / коробки")
    if any(k in blob for k in ("опт", "парти", "moq", "от ")):
        notes.append("Возможен опт / партия — уточнить MOQ у продавца")
    if any(k in blob for k in ("достав", "самовывоз", "склад")):
        notes.append("Упоминается доставка/склад — сверить регион")
    if any(k in blob for k in ("картон", "гофро", "микрогофр", "крафт")):
        notes.append("Указан материал — сверить прочность под товар")
    if topic == "упаковка" and any(k in blob for k in ("красив", "дизайн", "печат", "бренд")):
        notes.append("Акцент на внешний вид — полезно для unboxing")
    if not any(k in blob for k in ("₽", "руб", "цена", "стоимость")):
        notes.append("Цена в выдаче не указана — не выдумываю")
    return notes[:4]


def _infer_option_type(title: str, snippet: str, taxonomy: list[dict[str, str]]) -> str:
    blob = f"{title} {snippet}".lower()
    mapping = [
        (("ложемент", "фиксатор", "holder"), "formament"),
        (("пен", "вспенен", "foam"), "foam"),
        (("бренд", "печат", "логотип"), "branded_box"),
        (("вклад", "insert"), "insert"),
        (("пузыр", "наполнител", "крафт-бумаг"), "void_fill"),
        (("короб", "гофр", "упаков"), "flap_box"),
    ]
    for keys, typ in mapping:
        if any(k in blob for k in keys):
            return typ
    if taxonomy:
        return taxonomy[0].get("type", "criteria_based")
    return "criteria_based"


def _fit_score(title: str, snippet: str, problem_label: str, topic: str) -> float:
    blob = f"{title} {snippet}".lower()
    score = 0.35
    if topic == "упаковка" and any(k in blob for k in ("короб", "упаков", "гофр", "пен", "ложемент")):
        score += 0.35
    for token in re.findall(r"[а-яa-z]{4,}", _norm(problem_label))[:6]:
        if token in blob:
            score += 0.05
    return max(0.0, min(0.95, score))


def build_compare_table(options: list[SolutionOption], *, problem_label: str = "") -> str:
    """Real-data compare table — unknown fields stay «н/д», never fabricated."""
    if not options:
        return ""
    lines = ["Сравнение (только факты из поиска):"]
    if problem_label:
        lines.append(f"Проблема: {problem_label}")
    lines.append("| # | Тип | Название | Цена | Источник цены | Fit |")
    lines.append("|---|-----|----------|------|---------------|-----|")
    for opt in options:
        cost = f"{opt.estimated_cost:.0f}₽" if opt.estimated_cost is not None else "н/д"
        src = opt.cost_source or "н/д"
        lines.append(
            f"| {opt.label} | {opt.type} | {opt.title[:40]} | {cost} | {src} | "
            f"{opt.fit_score:.2f} |"
        )
    return "\n".join(lines)


def pick_preferred(
    options: list[SolutionOption],
    *,
    topic: str,
    problem_label: str = "",
) -> tuple[SolutionOption | None, str]:
    if not options:
        return None, ""
    ranked = sorted(options, key=lambda o: (o.fit_score, o.confidence), reverse=True)
    preferred = ranked[0]
    reason_parts = [
        f"из доступной выдачи ближе к задаче «{topic}»",
        f"(«{preferred.title[:60]}», fit={preferred.fit_score:.2f})",
    ]
    if problem_label:
        reason_parts.append(f"и связан с проблемой «{problem_label[:60]}»")
    reason_parts.append("Цену/MOQ уточните у поставщика — в выдаче я их не выдумываю.")
    return preferred, " ".join(reason_parts)


# ─── core research ───────────────────────────────────────────────────────── #

async def research_solutions(
    query: str,
    *,
    search_service=None,
    category: str | None = None,
    topic: str | None = None,
    product_title: str | None = None,
    product_article: int | None = None,
    problem_id: str | None = None,
    problem_label: str = "",
    evidence_ids: list[str] | None = None,
    problem_type: str | None = None,
) -> SolutionResearchResult:
    """
    Solution research через SearchService если есть.
    CostGuard boundary остаётся внутри SearchService.
    """
    topic = topic or infer_topic(query)
    ptype = problem_type or infer_problem_type(
        f"{query} {problem_label}", signal_type=None,
    )
    taxonomy = solution_types_for_problem(ptype)
    criteria = default_selection_criteria(topic)
    research_query = build_research_query(
        query,
        product_title=product_title,
        product_category=category,
        problem_label=problem_label or None,
        topic=topic,
    )
    result = SolutionResearchResult(
        query=research_query,
        available=False,
        criteria=criteria,
        topic=topic,
        problem_id=problem_id,
        problem_label=problem_label or "",
        evidence_ids=list(evidence_ids or []),
        product_article=product_article,
        solution_types=taxonomy,
    )

    if search_service is None:
        result.honest_message = (
            "Актуальные предложения недоступны — не буду выдумывать магазины, "
            "цены, ссылки и наличие. Ниже типы решений и критерии выбора."
        )
        # Type-only options (no fake shops)
        labels = "ABCDE"
        for i, st in enumerate(taxonomy[:5]):
            result.options.append(SolutionOption(
                id=f"type-{st.get('type', i)}",
                label=labels[i],
                title=st.get("title") or st.get("type") or f"Тип {i+1}",
                type=st.get("type") or "criteria_based",
                description=st.get("description") or "",
                pros=["Закрывает класс проблемы", "Можно искать у любого поставщика"],
                cons=["Конкретный магазин/цена неизвестны — поиск недоступен"],
                estimated_cost=None,
                cost_source=None,
                evidence_ids=list(evidence_ids or []),
                research_source_ids=[],
                fit_score=0.55 - i * 0.05,
                confidence=0.35,
                problem_id=problem_id,
                notes=["Тип решения, не магазин"],
            ))
        if result.options:
            preferred, reason = pick_preferred(
                result.options, topic=topic, problem_label=problem_label,
            )
            if preferred:
                result.preferred_label = preferred.label
                result.preferred_option_id = preferred.id
                result.preferred_reason = (
                    f"как ТИП решения (не магазин): {reason}"
                )
        return result

    try:
        items = await search_service.search_and_store(
            query=research_query,
            category=category,
        )
    except Exception as exc:
        log.warning("solution research search failed: %s", exc)
        result.honest_message = (
            "Поиск не удался — актуальные предложения недоступны. "
            "Честно, без выдуманных вариантов. Ориентируйтесь на типы и критерии."
        )
        return result

    if not items:
        result.honest_message = (
            "Актуальные предложения недоступны (лимит/кэш/пусто). "
            "Не выдумываю магазины. Используйте типы решений и критерии."
        )
        return result

    labels = "ABCDEFG"
    options: list[SolutionOption] = []
    for i, item in enumerate(items[:5]):
        title = _item_title(item)
        snippet = _item_snippet(item)
        cost, cost_src = _extract_cost(f"{title} {snippet}")
        src_id = _item_id(item, f"search-{i}")
        opt_type = _infer_option_type(title, snippet, taxonomy)
        notes = _compare_notes(title, snippet, topic)
        fit = _fit_score(title, snippet, problem_label, topic)
        options.append(SolutionOption(
            id=str(uuid.uuid4()),
            label=labels[i],
            title=title,
            type=opt_type,
            description=snippet[:200],
            pros=notes[:2],
            cons=["Уточнить MOQ/срок у продавца"] if cost is None else [],
            estimated_cost=cost,
            cost_source=cost_src,
            evidence_ids=list(evidence_ids or []),
            research_source_ids=[src_id],
            fit_score=fit,
            confidence=0.55 if cost is not None else 0.4,
            problem_id=problem_id,
            snippet=snippet,
            source_url=_item_url(item),
            notes=notes,
        ))

    result.available = True
    result.options = options
    result.compare_table = build_compare_table(options, problem_label=problem_label)
    preferred, reason = pick_preferred(
        options, topic=topic, problem_label=problem_label,
    )
    if preferred:
        result.preferred_label = preferred.label
        result.preferred_option_id = preferred.id
        result.preferred_reason = reason
    return result


def format_choice_reply(result: SolutionResearchResult) -> str:
    if not result.options:
        lines = [
            "Честно: актуальные предложения недоступны, поэтому не выбираю «из воздуха».",
            "Типы решений и критерии:",
        ]
        for st in (result.solution_types or [])[:5]:
            lines.append(f"• [{st.get('type')}] {st.get('title')}")
        for c in result.criteria:
            lines.append(f"• {c}")
        return "\n".join(lines)

    lines: list[str] = []
    if result.problem_label:
        lines.append(f"Проблема: {result.problem_label}")
    if result.compare_table and result.available:
        lines.append(result.compare_table)
    else:
        lines.append("Варианты:")
        for opt in result.options:
            cost = (
                f" ~{opt.estimated_cost:.0f}₽"
                if opt.estimated_cost is not None else " цена н/д"
            )
            lines.append(f"{opt.label}) [{opt.type}] {opt.title}{cost}")
            for n in (opt.notes or opt.pros)[:2]:
                lines.append(f"   — {n}")
    if result.preferred_label:
        lines.append(
            f"\nЯ бы выбрал {result.preferred_label}, потому что {result.preferred_reason}"
        )
    if not result.available and result.honest_message:
        lines.insert(0, result.honest_message)
    return "\n".join(lines)


def format_why_selected(record: DecisionRecord) -> str:
    opt = record.selected_option()
    lines = [f"Почему именно этот вариант (тема «{record.topic}»):"]
    if opt:
        lines.append(f"• Выбран: {opt.get('label', '')} {opt.get('title', '')}".strip())
        if opt.get("type"):
            lines.append(f"• Тип: {opt['type']}")
        if record.problem:
            lines.append(f"• Связь с проблемой: {record.problem}")
        evid = record.evidence_ids or opt.get("evidence_ids") or []
        if evid:
            lines.append(f"• Evidence: {', '.join(map(str, evid[:5]))}")
        if opt.get("fit_score") is not None:
            lines.append(f"• Fit score: {opt.get('fit_score')}")
        reason = record.recommendation or ""
        if reason:
            lines.append(f"• Обоснование: {reason}")
    else:
        lines.append(record.recommendation or "Выбор зафиксирован, детали варианта не сохранились.")
    return "\n".join(lines)
