"""
finance_planner.py — ARGUS Financial / Procurement Planning.

Детерминированный слой: resolve товара, парсинг цифр продавца,
честные расчёты (без выдуманных веса/комиссии/рекламы/налога),
память допущений в сессии, адаптивная глубина ответа.

Funnel + unit economics (CTR/CVR locus) живёт в funnel_economics.py
и переиспользует calculate() / FinancialContext / resolve_product отсюда.

Не трогает Browser / WB Engine / SFP.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Iterable


# --------------------------------------------------------------------------- #
# Intent markers
# --------------------------------------------------------------------------- #

_FINANCE_MARKERS = (
    "закуп", "заказат", "заказать", "заказатьс", "закупит", "закупк",
    "себестоим", "маржинальн", "маржа", "наценк",
    "безубыточн", "окупаем", "точка безубыт",
    "сколько выйдет", "сколько все выйдет", "сколько всё выйдет",
    "сколько это будет стоить", "сколько будет стоить",
    "стоит ли закуп", "стоит ли заказ",
    "закупочн", "цена закупки", "цена закуп",
    "полная себестоим", "unit economics", "юнит эконом",
    "₽/кг", "руб/кг", "за кг",
)

#: Слабые маркеры — только вместе с закупочным/цифровым контекстом.
_FINANCE_WEAK = (
    "посчитай", "посчитать", "рассчитай", "рассчитать",
    "логистик", "доставка", "парти",
)

_FINANCE_FOLLOWUP = (
    "а если", "если заказать", "а заказать", "а взять",
    "а сколько", "тогда сколько", "пересчитай", "пересчитать",
)

_WEIGHT_ASK = ("вес одной", "пар в 1 кг", "пар на 1 кг", "сколько пар", "вес пары", "вес единицы")
_DEEP_MARKERS = (
    "полностью", "стоит ли", "что дальше", "план", "закупаться",
    "маржинальн", "безубыточ", "сценари", "окупаем",
)
_SHORT_MARKERS = ("а если", "пересчитай", "а заказать", "а взять")


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def is_finance_query(text: str) -> bool:
    """Явный запрос закупки / себестоимости / маржи / сценария."""
    low = _norm(text)
    if not low:
        return False
    # «где купить упаковку» — solution research, не finance
    if any(m in low for m in ("где купить", "где найти", "где заказать", "найди упаков")):
        if not any(m in low for m in ("закупк", "себестоим", "марж", "сколько выйдет", "сколько будет стоить")):
            return False
    if any(m in low for m in _FINANCE_MARKERS):
        return True
    # weak: нужен ещё вес/цена/закуп-контекст
    if any(m in low for m in _FINANCE_WEAK):
        if any(m in low for m in ("кг", "закуп", "себестоим", "марж", "партия", "₽", "руб", "комисси")):
            return True
        if re.search(r"\d", low) and any(m in low for m in ("доставк", "закуп", "парти", "кг")):
            return True
    return False


def is_finance_followup(text: str, *, has_ctx: bool) -> bool:
    """Короткий follow-up при уже открытом financial context."""
    if not has_ctx:
        return False
    low = _norm(text)
    if not low:
        return False
    if is_finance_query(text):
        return True
    if any(m in low for m in _FINANCE_FOLLOWUP):
        return True
    # чистое число / «80 кг» / «300 руб»
    if re.fullmatch(r"[\d\s.,]+(?:\s*(?:кг|шт|пар|руб|р|₽|%)?)?", low):
        return True
    if _extract_any_finance_number(low):
        return True
    return False


def reply_depth(text: str) -> str:
    """short | deep — глубина ответа."""
    low = _norm(text)
    if any(m in low for m in _SHORT_MARKERS) and not any(m in low for m in _DEEP_MARKERS):
        return "short"
    if any(m in low for m in _DEEP_MARKERS):
        return "deep"
    if is_finance_query(text) and len(low) > 80:
        return "deep"
    return "normal"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

class KnowledgeLayer(str, Enum):
    KNOWN = "ЗНАЕМ"
    ASSUMED = "ПРЕДПОЛАГАЕМ"
    UNKNOWN = "НЕ ЗНАЕМ"


@dataclass
class ProductCandidate:
    article: int
    title: str = ""
    brand: str = ""
    price: float | None = None
    subject: str = ""
    source: str = "context"  # context | memory | session


@dataclass
class FinancialContext:
    """Память финансовых допущений в рамках обсуждения."""

    article: int | None = None
    product_title: str | None = None
    product_brand: str | None = None
    card_price: float | None = None  # цена карточки (KNOWN)

    purchase_price: float | None = None       # ₽/шт
    delivery_per_kg: float | None = None      # ₽/кг
    weight_kg: float | None = None
    units: float | None = None                # штук / пар
    unit_weight_kg: float | None = None       # кг на 1 шт
    units_per_kg: float | None = None         # шт на 1 кг
    sell_price: float | None = None           # ₽/шт
    commission_pct: float | None = None       # % от выручки
    packaging_per_unit: float | None = None   # ₽/шт
    ads_total: float | None = None            # ₽ на партию
    ads_per_unit: float | None = None         # ₽/шт
    returns_pct: float | None = None          # %
    tax_pct: float | None = None              # %

    #: откуда пришло каждое поле: user | card | seller | assumed
    provenance: dict[str, str] = field(default_factory=dict)

    def known(self, key: str) -> bool:
        return getattr(self, key, None) is not None

    def set_field(self, key: str, value: float | int | None, *, source: str = "user") -> None:
        if value is None:
            return
        if not hasattr(self, key):
            return
        setattr(self, key, float(value))
        self.provenance[key] = source

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FinancialContext":
        if not data:
            return cls()
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        ctx = cls(**kwargs)
        if not isinstance(ctx.provenance, dict):
            ctx.provenance = {}
        return ctx

    def merge_from_card(
        self,
        *,
        article: int | None = None,
        title: str | None = None,
        brand: str | None = None,
        price: float | None = None,
        seller_cost: float | None = None,
        seller_commission: float | None = None,
        seller_ads: float | None = None,
        seller_logistics: float | None = None,
    ) -> None:
        if article is not None:
            self.article = int(article)
        if title:
            self.product_title = title
        if brand:
            self.product_brand = brand
        if price is not None and self.sell_price is None:
            self.set_field("sell_price", float(price), source="card")
            self.card_price = float(price)
        elif price is not None:
            self.card_price = float(price)
        if seller_cost is not None and self.purchase_price is None:
            self.set_field("purchase_price", float(seller_cost), source="seller")
        if seller_commission is not None and self.commission_pct is None:
            # seller commission часто в ₽/шт — если < 100 трактуем как %, иначе ₽→% позже не угадываем
            val = float(seller_commission)
            if 0 < val <= 100:
                self.set_field("commission_pct", val, source="seller")
        if seller_ads is not None and self.ads_total is None and self.ads_per_unit is None:
            self.set_field("ads_total", float(seller_ads), source="seller")
        if seller_logistics is not None and self.delivery_per_kg is None:
            # logistics из seller — ₽/шт обычно; не мапим в ₽/кг молча
            pass


# --------------------------------------------------------------------------- #
# Product resolve
# --------------------------------------------------------------------------- #

_STOP = frozenset({
    "мои", "мой", "моя", "мое", "моё", "те", "эти", "этот", "эта", "это",
    "тот", "та", "те", "товар", "товара", "карточка", "карточку",
    "хочу", "снова", "заказать", "закупиться", "закончились", "белые",
    "черные", "чёрные", "новый", "новая",
})


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Zа-яёА-ЯЁ0-9]+", _norm(text)) if len(t) > 1]


def resolve_product(
    text: str,
    candidates: Iterable[ProductCandidate],
    *,
    current: ProductCandidate | None = None,
) -> tuple[ProductCandidate | None, list[ProductCandidate], str | None]:
    """
    Resolve product from name/article/price hints.

    Returns: (unique_match | None, ambiguous_list, clarify_message | None)
    """
    items = list(candidates)
    low = _norm(text)

    # 1) explicit article
    art_m = re.search(r"(?:артикул|арт\.?|товар)?\s*(\d{6,})", low)
    if art_m:
        art = int(art_m.group(1))
        for c in items:
            if int(c.article) == art:
                return c, [], None
        if current and int(current.article) == art:
            return current, [], None
        return None, [], f"Товар {art} не найден в известных карточках. Пришлите ссылку или уточните название."

    # 2) price hint «за 3800» / «товар за 2990»
    price_m = re.search(r"(?:за|по)\s+(\d[\d\s]{2,})\s*(?:₽|руб|р\.?)?", low)
    price_hits: list[ProductCandidate] = []
    if price_m:
        try:
            target = float(price_m.group(1).replace(" ", ""))
        except ValueError:
            target = None
        if target is not None:
            for c in items:
                if c.price is not None and abs(float(c.price) - target) <= 1:
                    price_hits.append(c)
            if len(price_hits) == 1:
                return price_hits[0], [], None
            if len(price_hits) > 1:
                return None, price_hits, _clarify(price_hits)

    # 3) title / brand token match
    toks = [t for t in _tokens(text) if t not in _STOP and not t.isdigit()]
    # keep meaningful product words
    scored: list[tuple[int, ProductCandidate]] = []
    for c in items:
        blob = _norm(f"{c.title} {c.brand} {c.subject}")
        score = 0
        for t in toks:
            if len(t) < 3:
                continue
            if t in blob or any(w.startswith(t) or t.startswith(w) for w in blob.split()):
                score += 2 if t in _norm(c.brand) or t in ("nike", "adidas", "puma") else 1
        # category words
        for hint, keys in (
            ("кроссов", ("кроссов", "кед", "sneaker")),
            ("бомбер", ("бомбер", "куртк")),
            ("очк", ("очк", "glasses")),
        ):
            if hint in low and any(k in blob for k in keys):
                score += 2
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: (-x[0], x[1].title))
    if scored:
        best = scored[0][0]
        top = [c for s, c in scored if s == best]
        if len(top) == 1:
            return top[0], [], None
        if len(top) > 1:
            return None, top, _clarify(top)

    # 4) contextual reference + current product
    refs = ("эти", "те", "этот", "эта", "тот", "та", "них", "него", "нее", "неё", "кроссов", "товар")
    if current is not None and any(r in low for r in refs):
        # if only one candidate of matching category — prefer current
        return current, [], None

    if current is not None and not toks:
        return current, [], None

    if len(items) == 1:
        return items[0], [], None

    if current is not None and is_finance_query(text):
        return current, [], None

    # Dynamics / trend questions without a new product name → sticky current
    if current is not None and not scored:
        try:
            from backend.ai.dynamic_analytics import is_dynamics_query
            if is_dynamics_query(text):
                return current, [], None
        except Exception:
            pass

    return None, [], None


def _clarify(items: list[ProductCandidate]) -> str:
    lines = ["У тебя несколько похожих товаров. Какой имеешь в виду?"]
    for c in items[:5]:
        price = f"{_fmt_money(c.price)} ₽" if c.price is not None else "цена н/д"
        title = (c.title or "без названия")[:60]
        lines.append(f"• арт.{c.article}: {title} — {price}")
    return "\n".join(lines)


def candidates_from_session(
    *,
    current_product=None,
    history: list[dict[str, Any]] | None = None,
    memory_products: list[Any] | None = None,
) -> list[ProductCandidate]:
    """Собрать кандидатов из текущего товара + history + memory list."""
    out: dict[int, ProductCandidate] = {}

    def _add(c: ProductCandidate) -> None:
        if c.article in out:
            prev = out[c.article]
            if not prev.title and c.title:
                prev.title = c.title
            if prev.price is None and c.price is not None:
                prev.price = c.price
            if not prev.brand and c.brand:
                prev.brand = c.brand
        else:
            out[c.article] = c

    if current_product is not None:
        art = getattr(current_product, "article", None)
        if art is not None:
            _add(ProductCandidate(
                article=int(art),
                title=str(getattr(current_product, "title", "") or ""),
                brand=str(getattr(current_product, "brand", "") or ""),
                price=_as_float(getattr(current_product, "price", None)),
                subject=str(getattr(current_product, "subject_name", "") or ""),
                source="session",
            ))

    for item in history or []:
        art = item.get("article")
        if art is None:
            continue
        _add(ProductCandidate(
            article=int(art),
            title=str(item.get("title") or ""),
            brand=str(item.get("brand") or ""),
            price=_as_float(item.get("price")),
            source="history",
        ))

    for rec in memory_products or []:
        art = getattr(rec, "article", None)
        if art is None and isinstance(rec, dict):
            art = rec.get("article")
        if art is None:
            continue
        if isinstance(rec, dict):
            _add(ProductCandidate(
                article=int(art),
                title=str(rec.get("title") or ""),
                brand=str(rec.get("brand") or ""),
                price=_as_float(rec.get("price")),
                source="memory",
            ))
        else:
            _add(ProductCandidate(
                article=int(art),
                title=str(getattr(rec, "title", "") or ""),
                brand="",
                price=_as_float(getattr(rec, "price", None)),
                source="memory",
            ))

    return list(out.values())


# --------------------------------------------------------------------------- #
# Number extraction
# --------------------------------------------------------------------------- #

def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(s: str) -> float | None:
    s = (s or "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


_NUM = r"(\d+(?:[.,]\d+)?)"


def extract_finance_fields(text: str) -> dict[str, float]:
    """Извлечь финансовые поля из реплики продавца (только явно сказанное)."""
    low = _norm(text)
    found: dict[str, float] = {}

    # --- delivery ₽/кг (до «веса партии», чтобы «за 1 кг» не стал weight) ---
    for pat in (
        r"(?:доставк\w*|логистик\w*)\s*[—\-:]?\s*"
        rf"{_NUM}\s*(?:₽|руб|р\.?)?\s*(?:/|за)\s*(?:\d+\s*)?кг",
        rf"{_NUM}\s*(?:₽|руб|р\.?)?\s*(?:/|за)\s*(?:\d+\s*)?кг",
    ):
        m = re.search(pat, low)
        if m:
            val = _num(m.group(1))
            if val is not None:
                found["delivery_per_kg"] = val
                break

    # --- purchase ---
    for pat in (
        r"(?:закупк\w*|закупочн\w*\s*цен\w*|цена\s*закупк\w*)\s*[—\-:]?\s*"
        rf"{_NUM}\s*(?:₽|руб|р\.?)?(?:\s*(?:за\s*)?(?:шт|штук|пар))?",
        rf"закупк\w*\s*[—\-:]?\s*{_NUM}",
    ):
        m = re.search(pat, low)
        if m:
            val = _num(m.group(1))
            if val is not None:
                found["purchase_price"] = val
                break
    if "purchase_price" not in found:
        m = re.search(
            rf"{_NUM}\s*(?:₽|руб|р\.?)?\s*(?:за\s*)?(?:шт|штук|пар\w*)",
            low,
        )
        if m and any(w in low for w in ("закуп", "себестоим")):
            found["purchase_price"] = _num(m.group(1))  # type: ignore

    # --- batch weight kg (не «за 1 кг» у ставки доставки) ---
    # вырезаем куски вида «N ₽ за 1 кг» / «N/кг» чтобы не спутать с объёмом
    weight_src = re.sub(
        rf"{_NUM}\s*(?:₽|руб|р\.?)?\s*(?:/|за)\s*(?:\d+\s*)?кг",
        " ",
        low,
    )
    for pat in (
        rf"(?:заказать|заказ\w*|взять|объем|объём|вес\s*парти|хочу)\s*{_NUM}\s*кг",
        rf"(?:а\s+если|если)\s*(?:заказать|взять)?\s*{_NUM}\s*кг",
        rf"{_NUM}\s*кг",
    ):
        m = re.search(pat, weight_src)
        if m:
            val = _num(m.group(1))
            if val is not None:
                found["weight_kg"] = val
                break

    patterns: list[tuple[str, str]] = [
        (rf"(?:продаж\w*|цена\s*продаж\w*|продавать\s*по)\s*[—\-:]?\s*{_NUM}",
         "sell_price"),
        (rf"(?:комисси\w*|комиссия\s*wb|комиссия\s*мп)\s*[—\-:]?\s*{_NUM}\s*%?",
         "commission_pct"),
        (rf"комисси\w*\s*{_NUM}\s*%",
         "commission_pct"),
        (rf"(?:упаковк\w*)\s*[—\-:]?\s*{_NUM}",
         "packaging_per_unit"),
        (rf"(?:реклам\w*|ads|дrr|дрр)\s*[—\-:]?\s*{_NUM}",
         "ads_total"),
        (rf"(?:возврат\w*)\s*[—\-:]?\s*{_NUM}\s*%",
         "returns_pct"),
        (rf"(?:налог\w*|ндс|усн)\s*[—\-:]?\s*{_NUM}\s*%?",
         "tax_pct"),
        (rf"(?:пар\w*|штук\w*|единиц\w*)\s*(?:в|на)?\s*1?\s*кг\s*[—\-:=]?\s*{_NUM}",
         "units_per_kg"),
        (rf"{_NUM}\s*(?:пар\w*|шт)\s*(?:в|на)\s*1?\s*кг",
         "units_per_kg"),
        (rf"(?:вес\s*(?:одной|1|единиц\w*|пар\w*))\s*[—\-:]?\s*{_NUM}\s*(?:кг|г)?",
         "unit_weight_raw"),
        (rf"(?:количеств\w*|штук\w*)\s*[—\-:]?\s*{_NUM}",
         "units"),
        (rf"{_NUM}\s*(?:шт|штук)(?!\s*(?:в|на)\s*1?\s*кг)",
         "units"),
        # «100 пар» как количество партии — только если НЕ «N пар в кг»
        (rf"{_NUM}\s*пар\w*(?!\s*(?:в|на)\s*1?\s*кг)",
         "units_pairs"),
    ]

    for pat, key in patterns:
        m = re.search(pat, low)
        if not m:
            continue
        val = _num(m.group(1))
        if val is None:
            continue
        if key == "unit_weight_raw":
            span = m.group(0)
            if "г" in span and "кг" not in span:
                found["unit_weight_kg"] = val / 1000.0
            elif val > 5 and "кг" not in span:
                found["unit_weight_kg"] = val / 1000.0
            else:
                found["unit_weight_kg"] = val
            continue
        if key == "units_pairs":
            # не путать с units_per_kg
            if "units_per_kg" in found:
                continue
            if re.search(rf"{_NUM}\s*пар\w*\s*(?:в|на)\s*1?\s*кг", low):
                continue
            if "units" not in found:
                found["units"] = val
            continue
        if key not in found:
            found[key] = val

    if "sell_price" not in found:
        m = re.search(rf"цен[аыуе]\s*(?:продаж\w*)?\s*[—\-:]?\s*{_NUM}", low)
        if m and "закуп" not in low[max(0, m.start() - 20):m.start()]:
            found["sell_price"] = _num(m.group(1))  # type: ignore

    return {k: v for k, v in found.items() if v is not None}


def _extract_any_finance_number(low: str) -> bool:
    return bool(extract_finance_fields(low)) or bool(re.search(rf"{_NUM}\s*кг", low))


def apply_extracted(ctx: FinancialContext, extracted: dict[str, float]) -> list[str]:
    """Применить извлечённые поля; вернуть список обновлённых ключей."""
    updated: list[str] = []
    mapping = {
        "purchase_price": "purchase_price",
        "delivery_per_kg": "delivery_per_kg",
        "weight_kg": "weight_kg",
        "units": "units",
        "unit_weight_kg": "unit_weight_kg",
        "units_per_kg": "units_per_kg",
        "sell_price": "sell_price",
        "commission_pct": "commission_pct",
        "packaging_per_unit": "packaging_per_unit",
        "ads_total": "ads_total",
        "returns_pct": "returns_pct",
        "tax_pct": "tax_pct",
    }
    for src, dst in mapping.items():
        if src in extracted:
            ctx.set_field(dst, extracted[src], source="user")
            updated.append(dst)
    return updated


# --------------------------------------------------------------------------- #
# Calculations
# --------------------------------------------------------------------------- #

@dataclass
class FinanceCalc:
    units: float | None = None
    purchase_total: float | None = None
    logistics_total: float | None = None
    packaging_total: float | None = None
    cogs_known_total: float | None = None
    revenue: float | None = None
    gross_profit: float | None = None          # revenue - purchase - logistics - packaging
    after_commission: float | None = None
    after_ads: float | None = None
    net_profit: float | None = None
    margin_pct: float | None = None            # profit / revenue
    markup_pct: float | None = None            # profit / cogs
    unit_cogs: float | None = None
    missing: list[str] = field(default_factory=list)
    not_included: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    can_break_even: bool = False
    min_sell_price: float | None = None
    units_to_breakeven: float | None = None
    max_ads: float | None = None
    max_purchase: float | None = None


def resolve_units(ctx: FinancialContext) -> tuple[float | None, list[str]]:
    notes: list[str] = []
    # Явный вес партии + шт/кг / вес единицы надёжнее, чем «2» из «2 пары в кг».
    if ctx.weight_kg is not None and ctx.units_per_kg is not None:
        u = float(ctx.weight_kg) * float(ctx.units_per_kg)
        notes.append(
            f"Количество единиц = {ctx.weight_kg} кг × {ctx.units_per_kg} шт/кг "
            f"= {_fmt_num(u)} — по данным продавца."
        )
        return u, notes
    if ctx.weight_kg is not None and ctx.unit_weight_kg is not None and ctx.unit_weight_kg > 0:
        u = float(ctx.weight_kg) / float(ctx.unit_weight_kg)
        notes.append(
            f"Количество единиц = {ctx.weight_kg} кг / {ctx.unit_weight_kg} кг "
            f"= {_fmt_num(u)}."
        )
        return u, notes
    if ctx.units is not None:
        return float(ctx.units), notes
    return None, notes


def calculate(ctx: FinancialContext) -> FinanceCalc:
    calc = FinanceCalc()
    units, notes = resolve_units(ctx)
    calc.units = units
    calc.notes.extend(notes)

    # Logistics — не требует units
    if ctx.weight_kg is not None and ctx.delivery_per_kg is not None:
        calc.logistics_total = float(ctx.weight_kg) * float(ctx.delivery_per_kg)
    elif ctx.weight_kg is not None and ctx.delivery_per_kg is None:
        calc.missing.append("ставка доставки ₽/кг")
    elif ctx.delivery_per_kg is not None and ctx.weight_kg is None:
        calc.missing.append("вес партии (кг)")

    # Purchase
    if ctx.purchase_price is not None and units is not None:
        calc.purchase_total = float(ctx.purchase_price) * float(units)
    elif ctx.purchase_price is not None and ctx.weight_kg is not None and units is None:
        calc.missing.append("вес одной единицы или количество единиц в 1 кг")
        calc.notes.append(
            "Чтобы точно посчитать закупку на "
            f"{_fmt_num(ctx.weight_kg)} кг, нужен вес одной единицы или количество в 1 кг."
        )
    elif ctx.purchase_price is None:
        if ctx.weight_kg is not None or units is not None:
            calc.missing.append("цена закупки ₽/шт")

    # Packaging
    if ctx.packaging_per_unit is not None and units is not None:
        calc.packaging_total = float(ctx.packaging_per_unit) * float(units)
    elif ctx.packaging_per_unit is None:
        calc.not_included.append("упаковка")

    # Commission / ads / tax / returns — never invent
    if ctx.commission_pct is None:
        calc.not_included.append("комиссия маркетплейса")
    if ctx.ads_total is None and ctx.ads_per_unit is None:
        calc.not_included.append("реклама")
    if ctx.tax_pct is None:
        calc.not_included.append("налоги")
    if ctx.returns_pct is None:
        calc.not_included.append("возвраты")

    # COGS from known components only
    parts = [p for p in (calc.purchase_total, calc.logistics_total, calc.packaging_total) if p is not None]
    if parts:
        calc.cogs_known_total = sum(parts)
        if units:
            calc.unit_cogs = calc.cogs_known_total / float(units)

    # Revenue
    if ctx.sell_price is not None and units is not None:
        calc.revenue = float(ctx.sell_price) * float(units)
    elif ctx.sell_price is None:
        calc.missing.append("цена продажи")

    # Profits — only with known pieces
    if calc.revenue is not None and calc.purchase_total is not None:
        base_cost = calc.purchase_total
        if calc.logistics_total is not None:
            base_cost += calc.logistics_total
        if calc.packaging_total is not None:
            base_cost += calc.packaging_total
        calc.gross_profit = calc.revenue - base_cost

        if ctx.commission_pct is not None:
            commission_rub = calc.revenue * float(ctx.commission_pct) / 100.0
            calc.after_commission = calc.gross_profit - commission_rub
        else:
            calc.notes.append(
                "Точную прибыль после комиссии пока не считаю — не хватает комиссии."
            )

        profit_for_margin = calc.after_commission if calc.after_commission is not None else calc.gross_profit

        ads_rub = None
        if ctx.ads_total is not None:
            ads_rub = float(ctx.ads_total)
        elif ctx.ads_per_unit is not None and units is not None:
            ads_rub = float(ctx.ads_per_unit) * float(units)

        if ads_rub is not None and profit_for_margin is not None:
            calc.after_ads = profit_for_margin - ads_rub
            profit_for_margin = calc.after_ads
        elif ads_rub is None:
            calc.notes.append(
                "Точную чистую маржинальность пока не считаю — не хватает рекламы/налога."
                if ctx.tax_pct is None else
                "Реклама не включена в расчёт."
            )

        if ctx.tax_pct is not None and profit_for_margin is not None and calc.revenue is not None:
            tax_rub = calc.revenue * float(ctx.tax_pct) / 100.0
            calc.net_profit = profit_for_margin - tax_rub
        elif ctx.returns_pct is not None and profit_for_margin is not None and calc.revenue is not None:
            # returns as revenue haircut only if given
            ret = calc.revenue * float(ctx.returns_pct) / 100.0
            calc.net_profit = profit_for_margin - ret

        # Margin vs markup — label correctly; use best available profit
        profit = calc.net_profit
        if profit is None:
            profit = calc.after_ads
        if profit is None:
            profit = calc.after_commission
        if profit is None:
            profit = calc.gross_profit

        if profit is not None and calc.revenue and calc.revenue > 0:
            calc.margin_pct = profit / calc.revenue * 100.0
        if profit is not None and calc.cogs_known_total and calc.cogs_known_total > 0:
            calc.markup_pct = profit / calc.cogs_known_total * 100.0

    # Break-even when enough data
    if calc.unit_cogs is not None:
        unit_extra = 0.0
        if ctx.commission_pct is not None and ctx.sell_price is not None:
            # iterative-ish: min price covers unit_cogs / (1 - commission%)
            denom = 1.0 - float(ctx.commission_pct) / 100.0
            if denom > 0:
                calc.min_sell_price = calc.unit_cogs / denom
                calc.can_break_even = True
        elif ctx.commission_pct is None:
            calc.min_sell_price = calc.unit_cogs
            calc.can_break_even = True
            calc.notes.append(
                "Минимальная цена ниже покрывает только известную себестоимость "
                "(без комиссии/рекламы/налога)."
            )

        if ctx.sell_price is not None and calc.unit_cogs is not None:
            unit_contrib = float(ctx.sell_price) - float(calc.unit_cogs)
            if ctx.commission_pct is not None:
                unit_contrib -= float(ctx.sell_price) * float(ctx.commission_pct) / 100.0
            batch_fixed = 0.0
            # logistics already in unit_cogs if units known; for batch recovery use purchase+logistics
            if calc.purchase_total is not None and calc.logistics_total is not None:
                batch_cost = calc.purchase_total + calc.logistics_total + (calc.packaging_total or 0)
                if unit_contrib > 0:
                    calc.units_to_breakeven = batch_cost / (
                        float(ctx.sell_price)
                        - float(ctx.purchase_price or 0)
                        - (float(ctx.packaging_per_unit or 0))
                        - (float(ctx.sell_price) * float(ctx.commission_pct or 0) / 100.0)
                    ) if (ctx.purchase_price is not None) else None
            if unit_contrib > 0 and calc.gross_profit is not None:
                calc.max_ads = max(0.0, calc.gross_profit - (
                    calc.revenue * float(ctx.commission_pct) / 100.0 if ctx.commission_pct and calc.revenue else 0
                ))
            if ctx.sell_price is not None:
                # max purchase: sell - logistics_per_unit - commission - packaging
                log_pu = None
                if calc.logistics_total is not None and units:
                    log_pu = calc.logistics_total / float(units)
                room = float(ctx.sell_price)
                if ctx.commission_pct is not None:
                    room *= (1.0 - float(ctx.commission_pct) / 100.0)
                if log_pu is not None:
                    room -= log_pu
                if ctx.packaging_per_unit is not None:
                    room -= float(ctx.packaging_per_unit)
                calc.max_purchase = max(0.0, room)

    return calc


def scenario_rows(ctx: FinancialContext, calc: FinanceCalc) -> list[dict[str, Any]]:
    """Сценарии только при известной цене продажи (или явных сценариях). Без выдуманных рыночных цен."""
    if ctx.sell_price is None or calc.units is None or calc.cogs_known_total is None:
        return []
    base = float(ctx.sell_price)
    # relative scenarios around KNOWN sell price — labeled as assumptions
    tiers = [
        ("Осторожный", base * 0.9),
        ("Базовый", base),
        ("Оптимистичный", base * 1.1),
    ]
    rows = []
    unit_cogs = calc.cogs_known_total / float(calc.units) if calc.units else None
    for name, price in tiers:
        if unit_cogs is None:
            break
        rev = price * float(calc.units)
        profit = rev - float(calc.cogs_known_total)
        if ctx.commission_pct is not None:
            profit -= rev * float(ctx.commission_pct) / 100.0
        margin = (profit / rev * 100.0) if rev else None
        rows.append({
            "name": name,
            "sell_price": price,
            "cogs": calc.cogs_known_total,
            "profit": profit,
            "margin_pct": margin,
            "assumed": name != "Базовый",
        })
    return rows


def operational_plan(ctx: FinancialContext, calc: FinanceCalc, *, demand_proven: bool = False) -> list[str]:
    steps = [
        "Проверить актуальную цену конкурентов по этой карточке (не выдумываю цифры — сверьте вручную).",
        "Зафиксировать минимальную прибыльную цену по известной себестоимости.",
    ]
    if ctx.weight_kg is not None and not demand_proven:
        test_kg = max(5.0, min(float(ctx.weight_kg) * 0.3, 15.0))
        steps.append(
            f"Я бы не заказывал сразу {_fmt_num(ctx.weight_kg)} кг. "
            f"Сначала тестировал бы {_fmt_num(test_kg)}–{_fmt_num(min(float(ctx.weight_kg), 15))} кг — "
            "фактический CVR и скорость продаж пока неизвестны."
        )
    else:
        steps.append("Определить тестовый объём закупки (небольшая первая партия).")
    steps.extend([
        "Проверить спрос после появления партии на витрине.",
        "Запустить небольшую первую партию и отследить продажи.",
        "После появления метрик — CTR/CVR/заказы (сейчас их нет в расчёте).",
        "Только после подтверждения спроса увеличивать закупку.",
    ])
    if calc.min_sell_price is not None:
        steps.insert(
            1,
            f"Цена ниже {_fmt_money(calc.min_sell_price)} ₽ уже делает модель убыточной "
            "по известным затратам.",
        )
    return steps


# --------------------------------------------------------------------------- #
# Reply formatting
# --------------------------------------------------------------------------- #

def _fmt_money(v: float | None) -> str:
    if v is None:
        return "н/д"
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v)):,}".replace(",", " ")
    return f"{v:,.2f}".replace(",", " ")


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "н/д"
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "н/д"
    return f"{v:.1f}%".replace(".0%", "%")


def layer_blocks(ctx: FinancialContext, calc: FinanceCalc) -> tuple[list[str], list[str], list[str]]:
    known: list[str] = []
    assumed: list[str] = []
    unknown: list[str] = []

    def add(label: str, key: str, fmt: str) -> None:
        val = getattr(ctx, key, None)
        if val is None:
            unknown.append(label)
            return
        src = ctx.provenance.get(key, "user")
        line = f"{label}: {fmt}"
        if src in ("card", "seller", "user"):
            known.append(line)
        elif src == "assumed":
            assumed.append(line)
        else:
            known.append(line)

    if ctx.product_title:
        known.append(f"товар: {ctx.product_title}" + (f" (арт.{ctx.article})" if ctx.article else ""))
    if ctx.card_price is not None:
        known.append(f"цена карточки: {_fmt_money(ctx.card_price)} ₽")

    add("закупка", "purchase_price", f"{_fmt_money(ctx.purchase_price)} ₽/шт")
    add("доставка", "delivery_per_kg", f"{_fmt_money(ctx.delivery_per_kg)} ₽/кг")
    add("объём", "weight_kg", f"{_fmt_num(ctx.weight_kg)} кг")
    if calc.units is not None:
        known.append(f"единицы (расчёт): {_fmt_num(calc.units)} шт")
    elif ctx.units is not None:
        add("единицы", "units", f"{_fmt_num(ctx.units)} шт")
    else:
        unknown.append("единицы")
    add("вес единицы", "unit_weight_kg", f"{_fmt_num(ctx.unit_weight_kg)} кг")
    add("шт/кг", "units_per_kg", f"{_fmt_num(ctx.units_per_kg)}")
    add("цена продажи", "sell_price", f"{_fmt_money(ctx.sell_price)} ₽")
    add("комиссия", "commission_pct", f"{_fmt_num(ctx.commission_pct)}%")
    add("упаковка", "packaging_per_unit", f"{_fmt_money(ctx.packaging_per_unit)} ₽/шт")
    add("реклама", "ads_total", f"{_fmt_money(ctx.ads_total)} ₽")
    add("налог", "tax_pct", f"{_fmt_num(ctx.tax_pct)}%")
    add("возвраты", "returns_pct", f"{_fmt_num(ctx.returns_pct)}%")

    for m in calc.missing:
        if m not in " ".join(unknown):
            unknown.append(m)

    return known, assumed, unknown


def format_reply(
    ctx: FinancialContext,
    calc: FinanceCalc,
    *,
    text: str,
    clarify: str | None = None,
    depth: str | None = None,
    demand_proven: bool = False,
) -> str:
    if clarify:
        return clarify

    depth = depth or reply_depth(text)
    known, assumed, unknown = layer_blocks(ctx, calc)

    # --- SHORT ---
    if depth == "short":
        parts: list[str] = []
        if calc.logistics_total is not None:
            parts.append(
                f"При {_fmt_num(ctx.weight_kg)} кг × {_fmt_money(ctx.delivery_per_kg)} ₽/кг "
                f"доставка = {_fmt_money(calc.logistics_total)} ₽."
            )
        if calc.purchase_total is not None:
            parts.append(f"Закупка партии: {_fmt_money(calc.purchase_total)} ₽.")
        elif ctx.purchase_price is not None and ctx.weight_kg is not None and calc.units is None:
            parts.append(
                f"Закупка {_fmt_money(ctx.purchase_price)} ₽/шт сохранена. "
                f"Для {_fmt_num(ctx.weight_kg)} кг нужен вес единицы или шт/кг — "
                "без этого сумму закупки не считаю."
            )
        if calc.cogs_known_total is not None:
            parts.append(f"Известная себестоимость партии: {_fmt_money(calc.cogs_known_total)} ₽.")
        if not parts:
            parts.append("Принял новые вводные. Напиши, что пересчитать.")
        # keep purchase remembered visibly
        if ctx.purchase_price is not None:
            parts.append(f"(закупка в контексте: {_fmt_money(ctx.purchase_price)} ₽/шт)")
        return "\n".join(parts)

    # --- NORMAL / DEEP ---
    lines: list[str] = []

    # Verdict
    lines.append("🎯 ВЫВОД")
    lines.append("")
    if calc.logistics_total is not None and calc.purchase_total is None and calc.units is None:
        lines.append(
            f"При {_fmt_num(ctx.weight_kg)} кг доставка составит {_fmt_money(calc.logistics_total)} ₽."
        )
        lines.append(
            "Но полную стоимость партии пока нельзя точно посчитать: "
            "не хватает веса единицы / количества в 1 кг."
        )
    elif calc.cogs_known_total is not None and calc.margin_pct is not None:
        lines.append(
            f"По известным данным партия ≈ {_fmt_money(calc.cogs_known_total)} ₽ себестоимости; "
            f"маржа (прибыль/выручка) ≈ {_fmt_pct(calc.margin_pct)}, "
            f"наценка (прибыль/себестоимость) ≈ {_fmt_pct(calc.markup_pct)}."
        )
        lines.append("Маржа ≠ наценка — цифры подписаны отдельно.")
    elif calc.cogs_known_total is not None:
        lines.append(
            f"Известная себестоимость партии: {_fmt_money(calc.cogs_known_total)} ₽ "
            "(только компоненты, которые вы дали)."
        )
        if ctx.sell_price is None:
            lines.append("Для маржинальности нужна цена продажи.")
        elif ctx.commission_pct is None:
            lines.append(
                "Точную чистую маржинальность пока не считаю — не хватает комиссии/рекламы/налога."
            )
    else:
        lines.append("Пока собрал вводные. Ниже — что уже известно и чего не хватает.")

    lines.append("")
    lines.append("📊 ЗНАЕМ")
    if known:
        for k in known:
            lines.append(f"• {k}")
    else:
        lines.append("• (пока пусто)")

    if assumed:
        lines.append("")
        lines.append("📎 ПРЕДПОЛАГАЕМ")
        for a in assumed:
            lines.append(f"• {a}")

    lines.append("")
    lines.append("❗ НЕ ЗНАЕМ / НЕ ВКЛЮЧЕНО")
    shown_u = []
    for u in unknown:
        if u not in shown_u:
            shown_u.append(u)
            lines.append(f"• {u}")
    for n in calc.not_included:
        label = f"{n} — не включено в расчёт"
        if label not in "\n".join(lines):
            lines.append(f"• {label}")
    if not shown_u and not calc.not_included:
        lines.append("• критичных пробелов нет для текущего среза")

    lines.append("")
    lines.append("💰 ЧТО МОЖНО ПОСЧИТАТЬ УЖЕ")
    computed = False
    if calc.logistics_total is not None:
        lines.append(
            f"• Логистика: {_fmt_num(ctx.weight_kg)} × {_fmt_money(ctx.delivery_per_kg)} "
            f"= {_fmt_money(calc.logistics_total)} ₽"
        )
        computed = True
    if calc.purchase_total is not None:
        lines.append(
            f"• Закупка: {_fmt_money(ctx.purchase_price)} × {_fmt_num(calc.units)} "
            f"= {_fmt_money(calc.purchase_total)} ₽"
        )
        computed = True
    if calc.packaging_total is not None:
        lines.append(f"• Упаковка: {_fmt_money(calc.packaging_total)} ₽")
        computed = True
    if calc.cogs_known_total is not None:
        lines.append(f"• Известная себестоимость: {_fmt_money(calc.cogs_known_total)} ₽")
        computed = True
    if calc.revenue is not None:
        lines.append(f"• Выручка: {_fmt_money(calc.revenue)} ₽")
        computed = True
    if calc.gross_profit is not None:
        lines.append(f"• Валовая прибыль (по известным затратам): {_fmt_money(calc.gross_profit)} ₽")
        computed = True
    if calc.after_commission is not None:
        lines.append(f"• После комиссии: {_fmt_money(calc.after_commission)} ₽")
        computed = True
    if calc.after_ads is not None:
        lines.append(f"• После рекламы: {_fmt_money(calc.after_ads)} ₽")
        computed = True
    if calc.net_profit is not None:
        lines.append(f"• Чистая (по известным полям): {_fmt_money(calc.net_profit)} ₽")
        computed = True
    if calc.margin_pct is not None:
        lines.append(f"• Маржа (прибыль / выручка): {_fmt_pct(calc.margin_pct)}")
        computed = True
    if calc.markup_pct is not None:
        lines.append(f"• Наценка (прибыль / себестоимость): {_fmt_pct(calc.markup_pct)}")
        computed = True
    if not computed:
        lines.append("• Пока недостаточно связанных цифр для формулы.")

    # Break-even
    if calc.can_break_even and depth in ("normal", "deep"):
        lines.append("")
        lines.append("📉 ТОЧКА БЕЗУБЫТОЧНОСТИ")
        if calc.min_sell_price is not None:
            lines.append(
                f"• Мин. цена продажи ≈ {_fmt_money(calc.min_sell_price)} ₽ "
                "(по известной себестоимости"
                + (f", с комиссией { _fmt_num(ctx.commission_pct) }%" if ctx.commission_pct is not None else ", без комиссии")
                + ")."
            )
        if calc.max_purchase is not None:
            lines.append(f"• Макс. закупочная цена ≈ {_fmt_money(calc.max_purchase)} ₽/шт")
        if calc.max_ads is not None:
            lines.append(f"• Допустимый рекламный бюджет партии ≈ {_fmt_money(calc.max_ads)} ₽")
        if calc.units_to_breakeven is not None:
            lines.append(f"• Единиц для окупаемости партии ≈ {_fmt_num(calc.units_to_breakeven)}")

    # Scenarios
    rows = scenario_rows(ctx, calc) if depth == "deep" else []
    if rows:
        lines.append("")
        lines.append("📑 СЦЕНАРИИ (вокруг известной цены продажи, не рыночные выдумки)")
        lines.append("Сценарий | Цена | Себестоимость | Прибыль | Маржа")
        for r in rows:
            tag = " (допущение ±10%)" if r["assumed"] else ""
            lines.append(
                f"{r['name']}{tag} | {_fmt_money(r['sell_price'])} | "
                f"{_fmt_money(r['cogs'])} | {_fmt_money(r['profit'])} | {_fmt_pct(r['margin_pct'])}"
            )

    # Plan
    if depth == "deep":
        lines.append("")
        lines.append("🔧 ДАЛЬШЕ (operational plan)")
        for i, step in enumerate(operational_plan(ctx, calc, demand_proven=demand_proven), 1):
            lines.append(f"{i}. {step}")
    else:
        lines.append("")
        lines.append("🔧 ДАЛЬШЕ")
        need = []
        if calc.units is None and ctx.weight_kg is not None:
            need.append("вес одной единицы или шт/кг")
        if ctx.sell_price is None:
            need.append("цену продажи")
        if ctx.commission_pct is None and ctx.sell_price is not None:
            need.append("комиссию WB (%)")
        if need:
            lines.append("Дай: " + ", ".join(need) + " — досчитаю полную экономику и мин. прибыльную цену.")
        else:
            lines.append("Могу углубить: «посчитай полностью и скажи, стоит ли закупаться».")

    # Honesty footer
    lines.append("")
    lines.append(
        "Честность: не выдумываю вес, комиссию, налог, рекламу, возвраты, "
        "шт/кг и цены конкурентов."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

@dataclass
class FinancePlanResult:
    text: str
    ctx: FinancialContext
    calc: FinanceCalc
    product: ProductCandidate | None = None
    handled: bool = True


def handle_finance_turn(
    text: str,
    *,
    ctx: FinancialContext | None = None,
    candidates: list[ProductCandidate] | None = None,
    current: ProductCandidate | None = None,
    demand_proven: bool = False,
) -> FinancePlanResult:
    """
    Один ход финансового диалога: resolve → extract → calc → format.
    """
    ctx = ctx or FinancialContext()
    cands = list(candidates or [])
    if current and all(c.article != current.article for c in cands):
        cands.insert(0, current)

    product, ambiguous, clarify = resolve_product(text, cands, current=current)
    if clarify and ambiguous:
        return FinancePlanResult(text=clarify, ctx=ctx, calc=FinanceCalc(), product=None)

    if product is not None:
        ctx.merge_from_card(
            article=product.article,
            title=product.title,
            brand=product.brand,
            price=product.price,
        )
        # sell_price from card only if still empty — already in merge_from_card
    elif current is not None and (ctx.article is None or ctx.article == current.article):
        ctx.merge_from_card(
            article=current.article,
            title=current.title,
            brand=current.brand,
            price=current.price,
        )

    extracted = extract_finance_fields(text)
    apply_extracted(ctx, extracted)

    calc = calculate(ctx)
    depth = reply_depth(text)
    reply = format_reply(
        ctx, calc, text=text, clarify=None, depth=depth, demand_proven=demand_proven,
    )
    return FinancePlanResult(text=reply, ctx=ctx, calc=calc, product=product or current)


def should_handle_finance(text: str, *, has_ctx: bool = False) -> bool:
    return is_finance_query(text) or is_finance_followup(text, has_ctx=has_ctx)
