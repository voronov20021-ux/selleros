"""
advisor.py — ARGUS Analytical Brain (Evidence Engine / Actionable Advisor).

Детерминированный слой поверх уже существующих сигналов:
  Review Intelligence, Category Intelligence, карточка, SellerData.

Цепочка для каждого существенного вывода:
  FACT → SIGNAL → CONFIDENCE → DIAGNOSIS → ACTION → PRIORITY → NOT_RECOMMENDED

Разделение утверждений:
  ЧТО МЫ ЗНАЕМ / ЧТО МЫ ПРЕДПОЛАГАЕМ / ЧТО НУЖНО ПРОВЕРИТЬ
  FACT / INFERENCE / RECOMMENDATION / IDEA
  IDEA ≠ FACT; единичный отзыв ≠ systemic; «часто» только при частоте.
  Малая выборка (processed/card <10) + evidence≤2 → candidate/check,
  confidence capped (medium/low), не «критично/системно».

Решение продавца (decision quality):
  🎯 одна ГЛАВНАЯ ПРОБЛЕМА (или честный «системной пока не видно»);
  симптом (цена/CTR/CVR) ≠ причина (фото/описание/размер/качество/упаковка);
  ACTION = действие → почему → эффект → как проверить;
  сильный NOT_RECOMMENDED против бесполезных правок.

Компактный UX — первый экран (10–15с), затем деталь:
  🎯 ГЛАВНАЯ ПРОБЛЕМА (или healthy) → 📊 КЛЮЧЕВЫЕ ЦИФРЫ → 🔥 ЧТО ДЕЛАТЬ
  → 🚫 ЧТО НЕ ТРОГАТЬ → 🔎 ПОЧЕМУ → 📋 ЧТО НУЖНО ПРОВЕРИТЬ
  Suite markers (ГЛАВНЫЙ ВЫВОД / ЧТО НЕ ДЕЛАТЬ / ЧЕГО НЕ ХВАТАЕТ / …) сохраняются.
  → деталь (ЗНАЕМ / ПРЕДПОЛАГАЕМ / ВЕРДИКТ / …)

Не выдумывает факты, %, цены, магазины, причины и результаты.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.wb.provenance import field_provenance_label


class ClaimLayer(str, Enum):
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    RECOMMENDATION = "RECOMMENDATION"
    IDEA = "IDEA"


class FactSource(str, Enum):
    """Слой происхождения факта (не смешивать в выводах)."""

    CARD = "CARD"
    REVIEW = "REVIEW"
    PRIVATE = "PRIVATE"
    RESEARCH = "RESEARCH"


class DiagnosisLocus(str, Enum):
    """
    Где проблема (диагноз).
    TRAFFIC / CONVERSION — только при наличии метрик воронки.
    """

    PRICE = "PRICE"
    CARD = "CARD"
    REVIEWS = "REVIEWS"
    PRODUCT = "PRODUCT"
    PACKAGING = "PACKAGING"
    TRAFFIC = "TRAFFIC"
    CONVERSION = "CONVERSION"
    UNKNOWN = "UNKNOWN"


class CausalRole(str, Enum):
    """
    Симптом ≠ причина.
    Цена / CTR / CVR — симптомы (метрики).
    Фото / описание / размер / качество / упаковка — потенциальные причины.
    """

    SYMPTOM = "SYMPTOM"
    CAUSE = "CAUSE"
    UNKNOWN = "UNKNOWN"


# Категории review-сигналов → потенциальная причина (не симптом воронки).
_CAUSE_CATEGORIES = frozenset({
    "PHOTO_MATCH", "PHOTO", "SIZE", "QUALITY", "PACKAGING",
    "MATERIAL", "DEFECT", "DESCRIPTION", "EXPECTATION",
})
_SYMPTOM_CATEGORIES = frozenset({
    "PRICE", "PRICE_VALUE", "TRAFFIC", "CONVERSION", "CTR", "CVR",
})


def causal_role_for(category: str = "", locus: str = "") -> str:
    """Определить роль сигнала: симптом / причина / неизвестно."""
    cat = (category or "").upper()
    loc = (locus or "").upper()
    if cat in _CAUSE_CATEGORIES or loc in (
        DiagnosisLocus.PRODUCT.value,
        DiagnosisLocus.PACKAGING.value,
        DiagnosisLocus.CARD.value,
    ):
        return CausalRole.CAUSE.value
    if cat in _SYMPTOM_CATEGORIES or loc in (
        DiagnosisLocus.PRICE.value,
        DiagnosisLocus.TRAFFIC.value,
        DiagnosisLocus.CONVERSION.value,
    ):
        return CausalRole.SYMPTOM.value
    return CausalRole.UNKNOWN.value


# Sample-size reliability heuristics (не абсолютная статистика).
_RELIABILITY_BANDS: tuple[tuple[int, str], ...] = (
    (10, "слабая"),
    (50, "предварительная"),
    (200, "заметная"),
    (1000, "устойчивая"),
)


def sample_reliability_label(n: int) -> str:
    """
    Эвристика надёжности выборки отзывов.
    <10 weak, 10–49 preliminary, 50–199 notable, 200+ stable, 1000+ strong.
    """
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return "нет выборки"
    if n < 10:
        return "слабая"
    if n < 50:
        return "предварительная"
    if n < 200:
        return "заметная"
    if n < 1000:
        return "устойчивая"
    return "сильная"


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _numeric_card_bits(product, seller_data) -> list[str]:
    """Реальные цифры карточки/продавца для фраз диагноза (без выдумок)."""
    bits: list[str] = []
    if product is not None:
        price = getattr(product, "price", None)
        rating = getattr(product, "rating", None)
        feedbacks = getattr(product, "feedbacks", None)
        if price is not None:
            bits.append(f"цена {price} ₽")
        if rating is not None:
            bits.append(f"рейтинг {rating}")
        if feedbacks is not None:
            bits.append(f"отзывов на карточке {feedbacks}")
    if seller_data is not None:
        for label, attr, suffix in (
            ("CTR", "ctr", "%"),
            ("CVR", "cvr", "%"),
            ("заказы", "orders", ""),
            ("продажи", "sales", ""),
            ("показы", "impressions", ""),
        ):
            val = getattr(seller_data, attr, None)
            if val is not None:
                bits.append(f"{label}={val}{suffix}" if suffix and attr in ("ctr", "cvr") else f"{label}={val}")
    return bits


def _strip_provenance(text: str) -> str:
    """Убрать тех. provenance из пользовательского текста."""
    s = str(text or "")
    s = re.sub(r"\s*\(Источник:[^)]*\)", "", s)
    s = re.sub(r"\s*·\s*verified[^\n]*", "", s, flags=re.IGNORECASE)
    return s.strip()


def _humanize_main_line(text: str) -> str:
    """Человеческий ярлык диагноза — без DB-шума."""
    s = _strip_provenance(text or "").strip()
    if not s:
        return ""
    s = s.replace("🟢 КАРТОЧКА В НОРМЕ — ", "").replace("🟢 КАРТОЧКА В НОРМЕ", "").strip(" —")
    s = re.sub(r"^\[[A-Z_]+\]\s*", "", s)
    low = s.lower()
    if "прочий сигнал" in low or low in ("other", "[other]"):
        return "Неоднозначные жалобы в отзывах — одна ось пока не выделена"
    return s


def _humanize_why_bullet(text: str) -> str:
    s = _humanize_main_line(text)
    if not s:
        return ""
    low = s.lower()
    if low.startswith("locus="):
        return ""
    if re.match(r"^[A-Z_]{3,20}$", s.strip()):
        return ""
    # убрать шум «факт: не могу оценить CTR…» — это в блок проверки
    if "не могу оценить ctr" in low or "не могу оценить cvr" in low:
        return ""
    if low.startswith("факт:"):
        s = s[5:].strip()
        low = s.lower()
        if _is_empty_metric_fact(s):
            return ""
    return s[:200]


def _fact_for_why(text: str) -> str:
    """Факт для блока ПОЧЕМУ: только полезные известные цифры."""
    raw = _strip_provenance(text or "")
    low = raw.lower()
    if _is_empty_metric_fact(raw):
        return ""
    if any(k in low for k in (
        "публичная цена", "рейтинг карточки", "отзывов на карточке / проанализировано",
        "экономика единицы", "медиана конкурентов",
    )):
        return raw[:120]
    if ("ctr" in low or "cvr" in low) and "нет данных" not in low and "не могу" not in low:
        return raw[:120]
    return ""


def _is_empty_metric_fact(text: str) -> bool:
    low = (text or "").lower()
    if "нет данных" not in low and "не могу оценить" not in low:
        return False
    return any(k in low for k in (
        "ctr", "cvr", "показы", "продажи", "заказы", "марж", "себестоим",
        "реклама", "возврат", "private metrics", "seller_price",
    ))


def _clean_action_text(text: str) -> str:
    s = _strip_provenance(text or "")
    s = re.sub(r"^\[[A-Z_]+\]\s*", "", s)
    s = re.sub(r"\s*\(P\d+(?:,\s*evidence=[^)]*)?\)\s*$", "", s)
    s = re.sub(r"\s*evidence=[A-Za-z0-9_,\-]+\s*", " ", s)
    s = re.sub(r"\bfrequency=\w+\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bsev(?:erity)?=\w+\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(
        r"прочий сигнал из отзывов",
        "жалобы покупателей",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"^\s*(Я бы первым делом|Имеет смысл|Практичный шаг|Сфокусируйся на):\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _soften_why(text: str) -> str:
    s = _strip_provenance(text or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:180]


def _strip_generic_unproven_action(item: "AdvisorItem") -> "AdvisorItem | None":
    """Убрать шаблонные советы без evidence (улучшите фото / добавьте фото)."""
    if item is None:
        return None
    low = (item.text or "").lower()
    evid = list(item.evidence_ids or [])
    meta = item.metadata or {}
    if meta.get("from_cv"):
        return item
    if not evid and any(
        p in low for p in (
            "улучшите фото", "добавьте фото", "сделайте лучшие фото",
            "добавьте характеристик", "увеличьте описание на всякий",
            "заполните характеристик", "заполните больше характеристик",
        )
    ):
        return None
    return item


def _dedupe_missing_bits(bits: list[str]) -> list[str]:
    """Схлопнуть дубли CTR/CVR/показы в одну честную строку."""
    funnel_tokens = ("ctr", "cvr", "показы", "заказы", "продажи")
    funnel_parts: list[str] = []
    other: list[str] = []
    for bit in bits:
        low = bit.lower().strip()
        if not low:
            continue
        is_funnel_list = (
            any(t == low for t in funnel_tokens)
            or (sum(1 for t in funnel_tokens if t in low) >= 2 and len(low) < 80)
        )
        if is_funnel_list:
            for t in funnel_tokens:
                if t in low:
                    label = t.upper() if t in ("ctr", "cvr") else t
                    if label not in funnel_parts:
                        funnel_parts.append(label)
            continue
        if "cv-анализ" in low or "cv анализ" in low:
            cv_line = "детальный CV-анализ не выполнялся"
            if cv_line not in other:
                other.append(cv_line)
            continue
        if bit not in other:
            other.append(bit)
    out: list[str] = []
    if funnel_parts:
        out.append(
            "метрики продавца (" + "/".join(funnel_parts)
            + ") — без них нельзя честно оценить воронку"
        )
    out.extend(other)
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return uniq


def _key_figure_lines(plan: "AdvisorPlan", *, unit=None, market=None) -> list[str]:
    """Только известные цифры. Пустые CTR/CVR/маржа — не пишем как значения."""
    price = rating = photos = chars = None
    reviews_card = reviews_proc = None
    seller_price = None
    ctr_val = cvr_val = None
    extra: list[str] = []
    for f in plan.facts:
        clean = _strip_provenance(f.text)
        low = clean.lower()
        if _is_empty_metric_fact(clean):
            continue
        if low.startswith("публичная цена") and "нет данных" not in low:
            m = re.search(r"([\d]+(?:[.,]\d+)?)", clean)
            if m:
                price = m.group(1)
        elif low.startswith("рейтинг карточки") and "нет данных" not in low:
            m = re.search(r"([\d]+(?:[.,]\d+)?)", clean)
            if m:
                rating = m.group(1)
        elif "отзывов на карточке / проанализировано" in low:
            m = re.search(r"(\d+)\s*/\s*(\d+)", clean)
            if m:
                reviews_card, reviews_proc = m.group(1), m.group(2)
        elif low.startswith("фото в карточке"):
            m = re.search(r"(\d+)", clean)
            if m:
                photos = m.group(1)
        elif low.startswith("характеристик:"):
            m = re.search(r"(\d+)", clean)
            if m:
                chars = m.group(1)
        elif low.startswith("цена продавца") and "нет данных" not in low:
            m = re.search(r"([\d]+(?:[.,]\d+)?)", clean)
            if m:
                seller_price = m.group(1)
        elif re.match(r"^ctr:\s*\d", low):
            ctr_val = clean.split(":", 1)[-1].strip()
        elif re.match(r"^cvr:\s*\d", low):
            cvr_val = clean.split(":", 1)[-1].strip()
        elif low.startswith("показы:") and "нет данных" not in low:
            extra.append(f"• {clean}")
        elif low.startswith("себестоимость:") and "нет данных" not in low:
            extra.append(f"• Себестоимость {clean.split(':', 1)[-1].strip()}")
        elif any(low.startswith(k) for k in ("продажи:", "заказы:")) and "нет данных" not in low:
            extra.append(f"• {clean}")
    rates_known = bool((plan.metadata or {}).get("funnel_rates_known"))
    lines: list[str] = []
    if price is not None:
        lines.append(f"• Цена {price} ₽")
    if seller_price is not None:
        lines.append(f"• Цена продавца {seller_price} ₽")
    if rating is not None:
        lines.append(f"• Рейтинг {rating}")
    if reviews_card is not None:
        if reviews_proc is None or str(reviews_card) == str(reviews_proc):
            lines.append(f"• Отзывы {reviews_card}")
        else:
            lines.append(f"• Отзывы {reviews_card}/{reviews_proc}")
    elif reviews_proc is not None:
        lines.append(f"• Отзывы {reviews_proc}")
    if not rates_known and photos is not None:
        lines.append(f"• Фото: {photos}; качество и соответствие не проверялись")
    if not rates_known and chars is not None:
        lines.append(f"• Характеристики {chars}")
    if ctr_val:
        num = ctr_val.replace("%", "").strip()
        suffix = " — seller-provided" if rates_known or (plan.metadata or {}).get("funnel_rates_known") else ""
        if rates_known:
            lines.append(f"• CTR {num}%{suffix}")
        else:
            lines.append(f"• CTR: {ctr_val}")
    if cvr_val:
        num = cvr_val.replace("%", "").strip()
        if rates_known:
            lines.append(f"• CVR {num}% — seller-provided")
        else:
            lines.append(f"• CVR: {cvr_val}")
    lines.extend(extra)
    has_ctr_line = any("ctr" in ln.lower() for ln in lines)
    has_cvr_line = any("cvr" in ln.lower() for ln in lines)
    if plan.data_needed and ("CTR" in plan.data_needed or "CVR" in plan.data_needed):
        if not has_ctr_line or not has_cvr_line:
            lines.append("• CTR/CVR — нет данных")
    if rates_known:
        has_sales = any("продаж" in ln.lower() or "заказ" in ln.lower() for ln in lines)
        if not has_sales:
            lines.append("• Продажи/заказы — нет данных")
        unit = (plan.metadata or {}).get("unit_economics") if isinstance((plan.metadata or {}).get("unit_economics"), dict) else None
        if unit and unit.get("cost") is not None and not any("себестоим" in ln.lower() for ln in lines):
            lines.append(f"• Себестоимость {unit['cost']:.0f} ₽")
        if unit and (not unit.get("complete")) and any(
            m in (unit.get("missing") or []) for m in ("комиссия", "логистика")
        ):
            if not any("комисси" in ln.lower() for ln in lines):
                lines.append("• Комиссия/логистика — нет данных")
    if unit and unit.get("complete") and unit.get("text"):
        lines.append(f"• {unit['text']}")
    if market and market.get("text") and (market.get("sufficient") or market.get("median")):
        lines.append(f"• {market['text']}")
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        k = ln.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(ln)
    return out[:12] if rates_known else out[:8]


def _human_proof_line(plan: "AdvisorPlan", *, unit=None, market=None) -> str:
    """Одна человеческая строка доказательств вместо DB dump.

    Object-model proof (evidence=/frequency=/ids=/sev=/P1–P4) остаётся в plan;
    на первый экран — только человеческие фрагменты.
    """
    bits: list[str] = []
    proof = (plan.main_problem_proof or "").strip()
    if proof:
        soft = proof
        # strip internal extractor / debug fields from user-facing first screen
        soft = re.sub(
            r"(?:ids|evidence|frequency|freq|sev(?:erity)?|confirmed|candidates|"
            r"выборка|locus|priority)\s*=\s*[^;]+;?\s*",
            "",
            soft,
            flags=re.IGNORECASE,
        )
        soft = re.sub(r"confidence\s*≈\s*[\d.]+%?", "", soft, flags=re.IGNORECASE)
        soft = re.sub(r"confidence\s*=\s*[^;]+;?\s*", "", soft, flags=re.IGNORECASE)
        soft = re.sub(r"\bP[1-5]\b", "", soft)
        soft = re.sub(r"\s*;\s*;", ";", soft)
        soft = re.sub(r"\s{2,}", " ", soft).strip(" ;")
        # keep only human fragments (из N обработанных / цена / рейтинг)
        human_parts: list[str] = []
        for m in re.finditer(
            r"из\s+\d+\s+обработанных(?:\s*\([^)]*\))?|цена\s+[\d.]+\s*₽[^;]*|"
            r"рейтинг\s+[\d.]+|отзывов на карточке\s+\d+",
            soft,
            flags=re.IGNORECASE,
        ):
            human_parts.append(m.group(0).strip(" ;,"))
        if human_parts:
            bits.append("; ".join(human_parts)[:140])
        elif soft and not re.search(r"\w+=\S|confidence\s*≈", soft, flags=re.I):
            bits.append(soft[:140])
    if unit and unit.get("complete") and unit.get("text"):
        bits.append(str(unit["text"])[:100])
    if market and market.get("text") and (market.get("sufficient") or market.get("median")):
        bits.append(str(market["text"])[:100])
    if not bits:
        return ""
    return "Опора: " + "; ".join(bits[:2])


def _extract_competitor_prices(category_context) -> list[float]:
    """Достать реальные цены конкурентов из CI, если они есть. Не выдумывать."""
    if category_context is None:
        return []
    prices: list[float] = []
    events = list(getattr(category_context, "competitor_events", None) or [])
    evidence = list(getattr(category_context, "evidence", None) or [])
    for src in events + evidence:
        if not isinstance(src, dict):
            continue
        for key in ("price", "median", "median_price", "peer_price", "avg_price"):
            v = _safe_float(src.get(key))
            if v is not None and v > 0:
                prices.append(v)
        note = str(src.get("note") or src.get("claim") or "")
        for m in re.finditer(r"(?:median|peer|~|≈)?\s*(\d{3,7})\b", note, re.IGNORECASE):
            try:
                prices.append(float(m.group(1)))
            except ValueError:
                pass
    # dedupe preserving order
    seen: set[float] = set()
    out: list[float] = []
    for p in prices:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out[:12]


def _market_price_compare(our_price: Any, category_context) -> dict[str, Any] | None:
    """Сравнение с медианой конкурентов только при реальных ценах peers."""
    ours = _safe_float(our_price)
    peers = _extract_competitor_prices(category_context)
    if ours is None or not peers:
        return None
    median = sorted(peers)[len(peers) // 2]
    if median <= 0:
        return None
    pct = round((ours - median) / median * 100.0, 1)
    return {
        "our_price": ours,
        "median": median,
        "pct_vs_median": pct,
        "peer_count": len(peers),
        "text": (
            f"цена {ours:.0f} ₽ vs медиана конкурентов {median:.0f} ₽ "
            f"({pct:+.1f}%, n={len(peers)})"
        ),
    }


def _comparison_from_meta(raw: dict) -> Any:
    """Восстановить CompetitorComparison из metadata dict (без ranking scores)."""
    from backend.competitor_intelligence.models import CompetitorComparison, MetricCompare

    def _mc(d) -> MetricCompare:
        d = d if isinstance(d, dict) else {}
        return MetricCompare(
            seller=d.get("seller"),
            competitor_median=d.get("competitor_median"),
            competitor_min=d.get("competitor_min"),
            competitor_max=d.get("competitor_max"),
            difference=d.get("difference"),
            difference_pct=d.get("difference_pct"),
            sample_n=int(d.get("sample_n") or 0),
            sufficient=bool(d.get("sufficient")),
        )

    return CompetitorComparison(
        seller_article=raw.get("seller_article"),
        sample_n=int(raw.get("sample_n") or 0),
        comparable_n=int(raw.get("comparable_n") or 0),
        sufficient_for_market=bool(raw.get("sufficient_for_market")),
        price=_mc(raw.get("price")),
        rating=_mc(raw.get("rating")),
        feedbacks=_mc(raw.get("feedbacks")),
        photos=_mc(raw.get("photos")),
        characteristics=_mc(raw.get("characteristics")),
        price_position=str(raw.get("price_position") or "UNKNOWN"),
        honesty_note=str(raw.get("honesty_note") or ""),
        commercial_n=int(raw.get("commercial_n") or 0),
        inhomogeneous=bool(raw.get("inhomogeneous")),
        evidence_quality=str(raw.get("evidence_quality") or "UNKNOWN"),
    )


def compute_unit_economics(seller_data, product=None) -> dict[str, Any]:
    """
    Вклад / маржа единицы — только из данных продавца.
    Не выдумывает комиссии/логистику.
    """
    if seller_data is None:
        return {
            "complete": False,
            "missing": ["цена", "себестоимость", "комиссия", "логистика"],
            "text": "экономика единицы: нет данных продавца",
        }
    price = _safe_float(getattr(seller_data, "price", None))
    if price is None and product is not None:
        price = _safe_float(getattr(product, "price", None))
    cost = _safe_float(getattr(seller_data, "cost", None))
    commission = _safe_float(getattr(seller_data, "commission", None))
    logistics = _safe_float(getattr(seller_data, "logistics", None))
    ad_spend = _safe_float(getattr(seller_data, "ad_spend", None))
    returns = _safe_float(getattr(seller_data, "returns", None))
    orders = _safe_float(getattr(seller_data, "orders", None))

    missing: list[str] = []
    if price is None:
        missing.append("цена")
    if cost is None:
        missing.append("себестоимость")
    if commission is None:
        missing.append("комиссия")
    if logistics is None:
        missing.append("логистика")

    # ads/returns optional — include if present, else note as optional gap
    optional_missing: list[str] = []
    if ad_spend is None:
        optional_missing.append("реклама/ед.")
    if returns is None:
        optional_missing.append("возвраты")

    if missing:
        out: dict[str, Any] = {
            "complete": False,
            "partial": False,
            "status": "INCOMPLETE",
            "missing": missing,
            "optional_missing": optional_missing,
            "text": "экономика единицы неполная — не хватает: " + ", ".join(missing),
            "contribution": None,
        }
        if price is not None and cost is not None:
            gross = price - cost
            out["partial"] = True
            out["status"] = "PARTIAL"
            out["gross_before_fees"] = gross
            out["price"] = price
            out["cost"] = cost
            out["text"] = (
                f"Известно до комиссии и логистики: {gross:.0f} ₽/ед. до остальных расходов."
            )
            out["honesty"] = "это НЕ прибыль и НЕ маржа"
        return out

    assert price is not None and cost is not None and commission is not None and logistics is not None
    ads_per_unit = 0.0
    if ad_spend is not None and orders is not None and orders > 0:
        ads_per_unit = ad_spend / orders
    elif ad_spend is not None:
        # cannot allocate without orders — keep as known total note only
        ads_per_unit = 0.0

    returns_cost = 0.0
    if returns is not None and orders is not None and orders > 0 and cost is not None:
        # rough: share of units returned * cost (honest heuristic, labeled)
        returns_cost = (returns / orders) * cost

    contribution = price - cost - commission - logistics - ads_per_unit - returns_cost
    margin_pct = round(contribution / price * 100.0, 1) if price else None
    bits = [
        f"цена {price:.0f}",
        f"себест. {cost:.0f}",
        f"комиссия {commission:.0f}",
        f"логистика {logistics:.0f}",
    ]
    if ad_spend is not None and orders is not None and orders > 0:
        bits.append(f"реклама/ед. {ads_per_unit:.0f}")
    elif ad_spend is not None:
        bits.append(f"реклама всего {ad_spend:.0f} (на ед. не распределена — нет заказов)")
    if returns is not None and orders is not None and orders > 0:
        bits.append(f"оценка возвратов/ед. {returns_cost:.0f}")
    text = (
        f"экономика единицы: вклад {contribution:.0f} ₽"
        + (f" ({margin_pct}% маржа)" if margin_pct is not None else "")
        + " = "
        + " − ".join(bits)
    )
    return {
        "complete": True,
        "contribution": contribution,
        "margin_pct": margin_pct,
        "missing": [],
        "optional_missing": optional_missing,
        "text": text,
        "price": price,
        "cost": cost,
        "commission": commission,
        "logistics": logistics,
        "ads_per_unit": ads_per_unit,
        "returns_cost": returns_cost,
    }


# evidence_count ≠ reviews_count: tiny samples cannot prove systemic risk.
_SMALL_SAMPLE_N = 10
_RISE_SAMPLE_N = 40
_SMALL_SAMPLE_EVIDENCE_CAP = 2
_SMALL_SAMPLE_CONF_CAP = 0.55  # medium/low band only


def _confidence_band(confidence: float) -> tuple[str, str]:
    """высокая / средняя / низкая + краткое why."""
    try:
        c = float(confidence or 0.0)
    except (TypeError, ValueError):
        c = 0.0
    if c >= 0.70:
        return "высокая", "сигналы согласованы и опираются на evidence"
    if c >= 0.45:
        return "средняя", "есть сигналы, но не хватает части данных или выборка ограничена"
    return "низкая", "данных мало — выводы предварительные, нужна проверка"


def _funnel_rates_known(seller_data) -> bool:
    if seller_data is None:
        return False
    return (
        getattr(seller_data, "ctr", None) is not None
        and getattr(seller_data, "cvr", None) is not None
    )


def _funnel_orders_known(seller_data) -> bool:
    if seller_data is None:
        return False
    return (
        getattr(seller_data, "orders", None) is not None
        or getattr(seller_data, "sales", None) is not None
    )


def _funnel_has_baseline(seller_data, metric_snapshots=None) -> bool:
    """Historical / target / cohort — without these, no low/high claim."""
    snaps = list(metric_snapshots or [])
    valid_n = 0
    if snaps:
        try:
            from backend.ai.funnel_consistency import validate_snapshot_metrics
            for row in snaps:
                if validate_snapshot_metrics(row).is_ok:
                    valid_n += 1
        except Exception:
            valid_n = len(snaps)
    if valid_n >= 2:
        return True
    if seller_data is None:
        return False
    for attr in ("ctr_target", "cvr_target", "ctr_benchmark", "cvr_benchmark"):
        if getattr(seller_data, attr, None) is not None:
            return True
    return False


def _implied_clicks_note(seller_data) -> str | None:
    """Math from impressions×CTR. Not an observed click count."""
    if seller_data is None:
        return None
    if getattr(seller_data, "clicks", None) is not None:
        return None
    try:
        impr = float(getattr(seller_data, "impressions", None))
        ctr = float(getattr(seller_data, "ctr", None))
    except (TypeError, ValueError):
        return None
    if impr <= 0:
        return None
    n = round(impr * ctr / 100.0)
    return (
        f"≈{n} кликов математически соответствует указанному CTR, "
        "но фактическое clicks не передано."
    )


def _apply_diagnostic_confidence_authority(
    *,
    conf: float,
    processed_n: int,
    card_feedbacks: int | None,
    has_funnel_pair: bool,
    has_sales: bool,
    has_orders: bool,
    commercial_n: int,
    photos_analyzed: bool,
    confirmed_n: int,
    has_recurring: bool,
    unit_complete: bool,
    mp_kind: str,
) -> tuple[float, str, str]:
    """Diagnostic confidence ≠ verified card fields.

    rating=3.0 verified is a FACT; with n=2 and no seller/CI/CV
    the diagnosis is still LOW. Does not rewrite RI evidence caps.
    """
    try:
        conf = float(conf or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    review_n = int(processed_n or 0)
    if card_feedbacks is not None:
        try:
            fb = int(card_feedbacks)
            if review_n <= 0:
                review_n = fb
            else:
                review_n = min(review_n, fb) if fb > 0 else review_n
        except (TypeError, ValueError):
            pass
    has_seller_metrics = bool(has_funnel_pair or has_sales or has_orders)
    has_commercial = int(commercial_n or 0) > 0
    has_cv = bool(photos_analyzed)
    extra_sources = sum(
        1 for flag in (has_funnel_pair, has_commercial, unit_complete, has_cv)
        if flag
    )
    independent = extra_sources
    if confirmed_n > 0 and review_n >= 10:
        independent += 1

    why_bits: list[str] = []
    if review_n < 10:
        why_bits.append("маленькая выборка отзывов")
    if not has_seller_metrics:
        why_bits.append("нет seller metrics")
    if not has_commercial:
        why_bits.append("нет competitor commercial data")
    if not has_cv:
        why_bits.append("CV analysis не выполнялся")
    why = " + ".join(why_bits) if why_bits else "данных для высокой уверенности недостаточно"

    weak_base = (
        review_n < 10
        and not has_seller_metrics
        and not has_commercial
        and not has_cv
    )
    no_cross = not has_seller_metrics and not has_commercial and not has_cv and not unit_complete
    high_ok = (
        confirmed_n > 0
        and has_recurring
        and review_n >= 40
        and independent >= 2
        and mp_kind == "problem"
    )
    if weak_base or (confirmed_n <= 0 and review_n < 10 and no_cross):
        conf = min(conf, 0.40)
        return conf, "низкая", why
    if high_ok and conf >= 0.70:
        return conf, "высокая", "несколько независимых evidence sources согласованы"
    # MEDIUM: sample or a confirmed layer, but not enough cross-source proof
    conf = min(conf, 0.65)
    if conf < 0.45:
        conf = 0.45
    return conf, "средняя", why if no_cross else (
        "есть сигналы, но не хватает cross-source подтверждения"
    )


def _calibrate_review_signal(
    *,
    sample_n: int,
    evidence_count: int,
    freq: str,
    base_weak: bool,
) -> dict[str, Any]:
    """
    Калибровка силы сигнала по выборке и числу evidence.

    Правила (обобщённые, без привязки к артикулам):
      - evidence_count ≤ 1 → не systemic / не recurring
      - sample_n < 10 и evidence_count ≤ 2 → candidate/check; conf capped
      - sample_n < 10 + 2 похожих → recurring *candidate*, не «часто/критично»
      - sample_n ≥ 40 + recurring → уверенность может расти
    """
    try:
        sample_n = int(sample_n or 0)
    except (TypeError, ValueError):
        sample_n = 0
    try:
        evidence_count = max(0, int(evidence_count or 0))
    except (TypeError, ValueError):
        evidence_count = 0
    freq_u = (freq or "").upper()

    weak = bool(base_weak)
    recurring = False
    recurring_candidate = False
    candidate_language = False
    max_confidence = 1.0

    # Единичный evidence никогда не доказывает systemic.
    if evidence_count <= 1:
        weak = True
        recurring = False
        recurring_candidate = False
        if sample_n < _SMALL_SAMPLE_N:
            candidate_language = True
            max_confidence = _SMALL_SAMPLE_CONF_CAP
        else:
            max_confidence = 0.55
        return {
            "weak": weak,
            "recurring": recurring,
            "recurring_candidate": recurring_candidate,
            "candidate_language": candidate_language,
            "max_confidence": max_confidence,
        }

    if sample_n < _SMALL_SAMPLE_N and evidence_count <= _SMALL_SAMPLE_EVIDENCE_CAP:
        # 2 сигнала на крошечной выборке = кандидат на повторяемость, не systemic.
        weak = True
        recurring = False
        recurring_candidate = True
        candidate_language = True
        max_confidence = _SMALL_SAMPLE_CONF_CAP
        return {
            "weak": weak,
            "recurring": recurring,
            "recurring_candidate": recurring_candidate,
            "candidate_language": candidate_language,
            "max_confidence": max_confidence,
        }

    if sample_n < _SMALL_SAMPLE_N:
        # Даже 3+ на n<10: выборка слабая — не «часто/критично/systemic».
        weak = True
        recurring = False
        recurring_candidate = True
        candidate_language = True
        max_confidence = _SMALL_SAMPLE_CONF_CAP
        return {
            "weak": weak,
            "recurring": recurring,
            "recurring_candidate": recurring_candidate,
            "candidate_language": candidate_language,
            "max_confidence": max_confidence,
        }

    # Нормальная выборка (≥10): recurring только без base_weak и при MEDIUM/HIGH.
    if not weak and freq_u in ("HIGH", "MEDIUM") and evidence_count >= 2:
        recurring = True
        if sample_n >= _RISE_SAMPLE_N:
            max_confidence = 0.85
        else:
            # 10–39: recurring допускается, но без высокой уверенности
            max_confidence = 0.65
    elif evidence_count >= 2 and freq_u in ("HIGH", "MEDIUM") and weak:
        recurring_candidate = True
        max_confidence = 0.55
    else:
        weak = True
        max_confidence = min(max_confidence, 0.55)

    return {
        "weak": weak,
        "recurring": recurring,
        "recurring_candidate": recurring_candidate,
        "candidate_language": candidate_language,
        "max_confidence": max_confidence,
    }


_AFFIRM_FREQ_RE = re.compile(r"(?<![нН]е\s)\bчасто\b|(?<![нН]е\s)\bпостоянно\b|(?<![нН]е\s)\bмассово\b", re.IGNORECASE)
_AFFIRM_CRITICAL_RE = re.compile(
    r"(?<![нН]е\s)\bкритичн\w*\b|(?<![нН]е\s)(?<!как\s)\bсистемн\w*\b",
    re.IGNORECASE,
)


def _candidate_safe_label(label: str) -> str:
    """Убрать утвердительные «часто/критично/системно» из label сигнала."""
    cleaned = _AFFIRM_FREQ_RE.sub("иногда", label or "")
    cleaned = _AFFIRM_CRITICAL_RE.sub("заметн", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _card_fact(product, label: str, field: str, unit: str = "") -> "AdvisorItem":
    val = getattr(product, field, None)
    if val is None:
        return AdvisorItem(
            text=f"{label}: нет данных",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.CARD.value, "field": field},
        )
    suffix = f" {unit}" if unit else ""
    prov = field_provenance_label(product, field)
    text = f"{label}: {val}{suffix}"
    if prov:
        text += f" (Источник: {prov})"
    return AdvisorItem(
        text=text,
        layer=ClaimLayer.FACT,
        metadata={"source": FactSource.CARD.value, "field": field, "provenance": prov or ""},
    )


@dataclass
class AdvisorItem:
    """Один пункт Advisor с явным слоем утверждения."""

    text: str
    layer: ClaimLayer
    priority: int | None = None  # 1..4 (P1–P4); None если не применимо
    evidence_ids: list[str] = field(default_factory=list)
    why: str = ""
    category: str = ""
    frequency: str = ""
    severity: str = ""
    direction: str = ""
    examples: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def evidence_tag(self) -> str:
        ids = [str(x) for x in (self.evidence_ids or []) if x][:3]
        return ",".join(ids) if ids else ""


FIRST_SCREEN_MAX_CHARS = 1800
_DETAIL_MARKERS = ("🧠 ВЕРДИКТ", "ЧТО МЫ ЗНАЕМ")


def _format_inconsistent_first_screen(plan: "AdvisorPlan", meta: dict) -> list[str]:
    """Compact seller-facing first screen when funnel input is INCONSISTENT."""
    cons = meta.get("funnel_consistency") if isinstance(meta.get("funnel_consistency"), dict) else {}
    impr = cons.get("impressions")
    ctr = cons.get("ctr")
    derived = cons.get("derived_clicks")
    orders = cons.get("orders")
    cvr = cons.get("cvr")
    check = cons.get("check_line") or "Проверьте клики, заказы и период."
    reason = meta.get("priority_reason") or plan.priority_why or "данные противоречивы"
    blocks: list[str] = []
    blocks.append(
        "🎯 ГЛАВНЫЙ ВЫВОД\n"
        "Данные воронки противоречат друг другу."
    )
    key = ["📊 КЛЮЧЕВЫЕ ЦИФРЫ"]
    if impr is not None:
        key.append(f"• Показы: {impr:.0f}")
    if ctr is not None:
        key.append(f"• CTR: {ctr:g}%")
    if derived is not None:
        key.append(f"• Расчётные клики: ≈{derived:.0f}")
    if orders is not None:
        key.append(f"• Заказы: {orders:.0f}")
    if cvr is not None:
        key.append(f"• CVR: {cvr:g}%")
    blocks.append("\n".join(key))
    blocks.append("⚠️ ПРОВЕРКА\n" + check)
    blocks.append(
        "🔥 ЧТО ДЕЛАТЬ\n🔧 ЧТО ДЕЛАТЬ\n"
        "1. Проверить клики, заказы и период анализа."
    )
    blocks.append(
        "🚫 НЕ ТРОГАТЬ\n🚫 ЧТО НЕ ТРОГАТЬ\n🚫 ЧТО НЕ ДЕЛАТЬ\n"
        "Цена · реклама · карточка"
    )
    blocks.append("⚠️ УВЕРЕННОСТЬ\nНизкая — данные противоречивы.")
    blocks.append(f"🚀 ПРИОРИТЕТ NONE — потому что {reason}")
    return blocks


@dataclass
class AdvisorPlan:
    """Собранный аналитический разбор для продавца."""

    facts: list[AdvisorItem] = field(default_factory=list)
    problems: list[AdvisorItem] = field(default_factory=list)
    strengths: list[AdvisorItem] = field(default_factory=list)
    unproven: list[AdvisorItem] = field(default_factory=list)
    fixes: list[AdvisorItem] = field(default_factory=list)
    add: list[AdvisorItem] = field(default_factory=list)
    grow: list[AdvisorItem] = field(default_factory=list)
    priority: list[AdvisorItem] = field(default_factory=list)
    signals: list[AdvisorItem] = field(default_factory=list)
    known: list[AdvisorItem] = field(default_factory=list)
    assumed: list[AdvisorItem] = field(default_factory=list)
    to_verify: list[AdvisorItem] = field(default_factory=list)
    not_recommended: list[AdvisorItem] = field(default_factory=list)
    why_points: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    bottleneck: str = "неизвестно"  # карточка|цена|доверие|товар|реклама|неизвестно
    diagnosis_locus: str = DiagnosisLocus.UNKNOWN.value
    confidence: float = 0.0
    confidence_label: str = ""  # высокая|средняя|низкая
    confidence_why: str = ""
    do_first: str = ""
    leave_alone: str = ""
    data_needed: str = ""
    main_verdict: str = ""
    diagnosis: str = ""
    priority_tier: str = "P3"  # P1|P2|P3|P4|NONE
    priority_why: str = ""
    sample_reliability: str = ""
    expected_impact: str = ""
    photos_analyzed: bool = False
    # Одна главная проблема (или честный «системной пока не видно»).
    main_problem: str = ""
    main_problem_why: str = ""
    main_problem_proof: str = ""
    main_problem_kind: str = ""  # problem | no_systemic | funnel_symptom
    main_problem_role: str = ""  # CAUSE | SYMPTOM | UNKNOWN

    def has_content(self) -> bool:
        return bool(
            self.facts or self.problems or self.strengths or self.unproven
            or self.fixes or self.add or self.grow or self.priority
            or self.signals or self.known or self.assumed or self.to_verify
            or self.not_recommended or self.bottleneck or self.main_verdict
            or self.main_problem
        )

    def diagnosis_snapshot(self) -> dict[str, Any]:
        """Снимок диагноза для discussion memory / «что решили»."""
        decisions = []
        meta = self.metadata or {}
        for d in list(meta.get("decision_items") or [])[:5]:
            if isinstance(d, dict):
                decisions.append(d)
            else:
                decisions.append({"text": str(d)})
        unit = meta.get("unit_economics") if isinstance(meta.get("unit_economics"), dict) else None
        market = meta.get("market_compare") if isinstance(meta.get("market_compare"), dict) else None
        return {
            "locus": self.diagnosis_locus,
            "bottleneck": self.bottleneck,
            "diagnosis": self.diagnosis,
            "main_verdict": self.main_verdict,
            "main_problem": self.main_problem,
            "main_problem_kind": self.main_problem_kind,
            "main_problem_role": self.main_problem_role,
            "main_problem_why": self.main_problem_why,
            "main_problem_proof": self.main_problem_proof,
            "do_first": self.do_first,
            "leave_alone": self.leave_alone,
            "priority_tier": self.priority_tier,
            "priority_why": self.priority_why,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "confidence_why": self.confidence_why,
            "sample_reliability": self.sample_reliability,
            "data_needed": self.data_needed,
            "expected_impact": self.expected_impact,
            "not_recommended": [x.text for x in self.not_recommended[:5]],
            "actions": [x.text for x in self.fixes[:3]],
            "decisions": decisions,
            "unit_economics": unit,
            "market_compare": market,
            "card_healthy": bool(meta.get("card_healthy")),
            "dynamic_analytics": (
                meta.get("dynamic_analytics")
                if isinstance(meta.get("dynamic_analytics"), dict)
                else None
            ),
        }

    def facts_by_source(self, source: FactSource | str) -> list[AdvisorItem]:
        key = source.value if isinstance(source, FactSource) else str(source)
        return [f for f in self.facts if (f.metadata or {}).get("source") == key]

    def confirmed_problems(self) -> list[AdvisorItem]:
        """❌ ЧТО ПОДТВЕРЖДЕНО — только не-слабые evidence-backed риски."""
        out: list[AdvisorItem] = []
        for p in self.problems:
            if p.layer == ClaimLayer.IDEA:
                continue
            if (p.metadata or {}).get("empty_risks"):
                continue
            if (p.metadata or {}).get("weak"):
                continue
            # OTHER praise/vague ≠ confirmed main-axis risk
            if not _can_be_main_problem(p):
                continue
            out.append(p)
        out.sort(
            key=lambda p: (
                0 if _is_named_risk_type(p.category or "") else 1,
                int(p.priority or 4),
            )
        )
        return out

    def weak_signals(self) -> list[AdvisorItem]:
        return [
            p for p in self.problems
            if (p.metadata or {}).get("weak") and not (p.metadata or {}).get("empty_risks")
        ]

    def format_plain(self, *, max_chars: int | None = None) -> str:
        """
        Компактный доказательный разбор (без Markdown).

        Первый экран (10–15с):
          ГЛАВНАЯ ПРОБЛЕМА / healthy → КЛЮЧЕВЫЕ ЦИФРЫ → ЧТО ДЕЛАТЬ
          → ЧТО НЕ ТРОГАТЬ → ПОЧЕМУ → ЧТО НУЖНО ПРОВЕРИТЬ
        Затем деталь (ВЕРДИКТ / ЗНАЕМ / ПРЕДПОЛАГАЕМ / …).
        Suite markers сохраняются для зелёных тестов.
        """
        blocks: list[str] = ["📈 АРГУС — разбор"]
        meta = self.metadata or {}
        card_healthy = bool(meta.get("card_healthy"))
        unit = meta.get("unit_economics") if isinstance(meta.get("unit_economics"), dict) else None
        market = meta.get("market_compare") if isinstance(meta.get("market_compare"), dict) else None

        # ── FIRST SCREEN ─────────────────────────────────────────────── #
        # 🎯 Lead (suite: ГЛАВНЫЙ ВЫВОД; human: ГЛАВНАЯ ПРОБЛЕМА / healthy)
        missing_funnel = bool(
            self.data_needed
            and ("CTR" in (self.data_needed or "") or "CVR" in (self.data_needed or ""))
        )
        no_sys_line = (
            "Системной проблемы по доступным данным пока не видно."
            if missing_funnel
            else "Системной проблемы пока не видно."
        )
        lead_lines: list[str] = ["🎯 ГЛАВНЫЙ ВЫВОД"]
        if card_healthy and self.main_problem_kind == "no_systemic":
            lead_lines.append("🟢 КАРТОЧКА В НОРМЕ")
            lead_lines.append("🎯 ВЫВОД")
            lead_lines.append("СИСТЕМНОЙ ПРОБЛЕМЫ НЕ ВИЖУ.")
            lead_lines.append(no_sys_line)
            human = _humanize_main_line(self.main_problem)
            if human and "системной проблемы" not in human.lower():
                lead_lines.append(human)
        elif self.main_problem_kind == "problem" and self.main_problem:
            lead_lines.append("🎯 ГЛАВНАЯ ПРОБЛЕМА")
            lead_lines.append(_humanize_main_line(self.main_problem))
            if self.main_problem_role == CausalRole.CAUSE.value:
                lead_lines.append("Это похоже на причину (не симптом CTR/цены).")
            elif self.main_problem_role == CausalRole.SYMPTOM.value:
                lead_lines.append("Это симптом метрики — искать причину в карточке/товаре.")
        elif self.main_problem_kind in ("no_systemic", "funnel_symptom") and self.main_problem:
            lead_lines.append("🎯 ВЫВОД")
            funnel_meas = (meta or {}).get("funnel_interpretation") in (
                "FUNNEL_MEASURABLE",
                "FUNNEL_INSUFFICIENT_FOR_CAUSAL_DIAGNOSIS",
            )
            if self.main_problem_kind == "funnel_symptom" or funnel_meas:
                lead_lines.append("СИСТЕМНОЙ ПРОБЛЕМЫ НЕ ВИЖУ.")
                lead_lines.append("Системной проблемы по доступным данным пока не видно.")
                lead_lines.append("Воронка измерима, но причина потерь пока не установлена.")
            elif self.main_problem_kind == "no_systemic":
                lead_lines.append("СИСТЕМНОЙ ПРОБЛЕМЫ НЕ ВИЖУ.")
                lead_lines.append(no_sys_line)
                rating_cand = meta.get("rating_sample_candidate") if isinstance(meta, dict) else None
                if isinstance(rating_cand, dict) and rating_cand.get("feedbacks") is not None:
                    lead_lines.append("Есть слабый candidate-сигнал:")
                    lead_lines.append(
                        f"рейтинг {rating_cand.get('rating')} при "
                        f"{rating_cand.get('feedbacks')} отзывах."
                    )
                    lead_lines.append("Этого недостаточно для системного вывода.")
                elif self.weak_signals():
                    lead_lines.append(
                        "Есть несколько candidate-сигналов, которые стоит проверить."
                    )
            human = _humanize_main_line(self.main_problem)
            skip_human = bool(
                human and (
                    "системной проблемы" in human.lower()
                    or "низк" in human.lower()
                    or "высок" in human.lower()
                    or "слаб" in human.lower()
                )
            )
            if human and not skip_human:
                if "проблема заключается" not in human.lower():
                    lead_lines.append(human)
        elif self.main_verdict:
            lead_lines.append(_humanize_main_line(self.main_verdict))
        else:
            lead_lines.append(self.diagnosis or "Данных для жёсткого вывода пока мало.")
        # one compact human proof line (не DB dump)
        proof_line = _human_proof_line(
            self,
            unit=unit,
            market=market,
        )
        if proof_line:
            lead_lines.append(proof_line)
        blocks.append("\n".join(lead_lines))

        # 📊 КЛЮЧЕВЫЕ ЦИФРЫ — только известные; CTR/CVR/маржу без данных не пишем
        key_lines = _key_figure_lines(self, unit=unit, market=market)
        for f in self.facts:
            if (f.metadata or {}).get("implied_clicks") and f.text:
                key_lines.append(f"• {f.text}")
            if "для фактического числа заказов нужны clicks" in (f.text or "").lower():
                if f.text and f"• {f.text}" not in key_lines:
                    key_lines.append(f"• {f.text}")
        if key_lines:
            blocks.append("\n".join(["📊 КЛЮЧЕВЫЕ ЦИФРЫ", *key_lines]))

        market_block_txt = ""
        if meta.get("competitor_comparison") or meta.get("competitive_diagnosis"):
            try:
                from backend.competitor_intelligence.comparison import format_market_block
                from backend.competitor_intelligence.models import CompetitorComparison
                raw_cmp = meta.get("competitor_comparison")
                cmp_obj = None
                if isinstance(raw_cmp, CompetitorComparison):
                    cmp_obj = raw_cmp
                elif isinstance(raw_cmp, dict):
                    cmp_obj = _comparison_from_meta(raw_cmp)
                market_block_txt = format_market_block(cmp_obj) or ""
                n_cand = int(getattr(cmp_obj, "sample_n", 0) or 0) if cmp_obj is not None else 0
                comm_n = int(getattr(cmp_obj, "commercial_n", 0) or 0) if cmp_obj is not None else 0
                if n_cand and comm_n <= 0:
                    market_block_txt = (
                        "🏪 РЫНОК\n"
                        f"{n_cand} кандидатов найдены, но commercial fields не подтверждены.\n"
                        "Рыночную позицию не определяю."
                    )
            except Exception:
                if market and market.get("text"):
                    market_block_txt = "🏪 РЫНОК\n" + str(market.get("text"))

        # 📉 ДИНАМИКА / 🔮 ПРОГНОЗ — optional thin layer (no invented history)
        dyn_meta = meta.get("dynamic_analytics") if isinstance(meta.get("dynamic_analytics"), dict) else None
        if dyn_meta is not None:
            try:
                from backend.ai.dynamic_analytics import first_screen_dynamics_blocks
                for blk in first_screen_dynamics_blocks(dyn_meta):
                    if blk:
                        blocks.append(blk)
            except Exception:
                pass

        # 🔥/🔧 ЧТО ДЕЛАТЬ — действие → причина → эффект → проверка
        actions = list(self.fixes)
        funnel_meas = (meta or {}).get("funnel_interpretation") in (
            "FUNNEL_MEASURABLE",
            "FUNNEL_INSUFFICIENT_FOR_CAUSAL_DIAGNOSIS",
        ) or bool((meta or {}).get("funnel_rates_known"))
        rates_known = bool((meta or {}).get("funnel_rates_known"))
        healthy_funnel = "не переписывай" in (self.do_first or "").lower()
        if funnel_meas and self.main_problem_kind in ("no_systemic", "funnel_symptom") and not healthy_funnel:
            actions = [
                AdvisorItem(
                    text="Добавить точное число кликов и заказов / период анализа.",
                    layer=ClaimLayer.RECOMMENDATION,
                    priority=3,
                    why="CTR/CVR уже есть; без clicks/orders causal diagnosis нельзя",
                    metadata={"action_class": "CHECK"},
                ),
                AdvisorItem(
                    text="Накопить historical snapshots.",
                    layer=ClaimLayer.RECOMMENDATION,
                    priority=3,
                    why="без baseline не классифицирую CTR/CVR как low или high",
                    metadata={"action_class": "CHECK"},
                ),
                AdvisorItem(
                    text="После этого оценивать динамику CTR/CVR.",
                    layer=ClaimLayer.RECOMMENDATION,
                    priority=3,
                    why="динамика без истории — догадка",
                    metadata={"action_class": "CHECK"},
                ),
            ]
        elif self.main_problem_kind == "no_systemic":
            gather = (
                "Сейчас не менять карточку автоматически — сначала получить CTR/CVR/заказы."
            )
            first = (self.do_first or "").strip() or gather
            if _is_card_rewrite_text(first):
                first = gather
            actions = [AdvisorItem(
                text=first,
                layer=ClaimLayer.RECOMMENDATION,
                priority=3,
                why="диагноз NO_SYSTEMIC; candidate ≠ confirmed Action",
                metadata={
                    "action_class": "CHECK",
                    "expected_effect": "без подтверждённого диагноза не менять карточку автоматически",
                    "how_to_verify": "снять CTR/CVR/заказы при отсутствии и пересмотреть диагноз",
                },
            )]
        elif self.do_first and "ничего не трогай" in self.do_first.lower():
            actions = [AdvisorItem(
                text="Ничего не трогай в карточке — сначала собери экономику (CTR/CVR/заказы)",
                layer=ClaimLayer.RECOMMENDATION,
                priority=2,
                why=self.do_first,
                metadata={
                    "expected_effect": "без CTR/CVR/заказов эффект любых правок не измерить",
                    "how_to_verify": "снять CTR/CVR/заказы за период и пересмотреть диагноз",
                },
            )] + [a for a in actions if "ничего не трогай" not in a.text.lower()]
        elif card_healthy and self.main_problem_kind == "no_systemic" and not actions:
            actions = [AdvisorItem(
                text="Ничего критичного не менять — мониторить рейтинг/отзывы и экономику",
                layer=ClaimLayer.RECOMMENDATION,
                priority=3,
                why="карточка в норме; правки «на всякий» вредят измеримости",
                metadata={
                    "expected_effect": "сохранить стабильность карточки",
                    "how_to_verify": "рейтинг/жалобы без просадки 2–4 недели; CTR/CVR — если появятся",
                },
            )]
        if self.main_problem_kind == "no_systemic" and not rates_known:
            first_txt = (actions[0].text if actions else self.do_first or "").lower()
            if "ctr" in first_txt and "cvr" in first_txt:
                actions = [AdvisorItem(
                    text="Получить CTR/CVR/заказы.",
                    layer=ClaimLayer.RECOMMENDATION,
                    priority=3,
                    why=(actions[0].why if actions else "") or "диагноз NO_SYSTEMIC",
                    metadata=(actions[0].metadata if actions else None) or {
                        "action_class": "CHECK",
                        "expected_effect": "без подтверждённого диагноза не менять карточку автоматически",
                        "how_to_verify": "снять CTR/CVR/заказы при отсутствии и пересмотреть диагноз",
                    },
                )]
        actions = [_strip_generic_unproven_action(a) for a in actions]
        actions = [a for a in actions if a is not None]
        if actions:
            lines = ["🔥 ЧТО ДЕЛАТЬ", "🔧 ЧТО ДЕЛАТЬ"]
            for i, it in enumerate(actions[:3], 1):
                lines.append(f"{i}. {_clean_action_text(it.text)}")
                if it.why:
                    lines.append(f"   почему: {_soften_why(it.why)}")
                effect = (it.metadata or {}).get("expected_effect") or ""
                if effect:
                    lines.append(f"   эффект: {effect}")
                verify = (it.metadata or {}).get("how_to_verify") or ""
                if verify:
                    lines.append(f"   проверка: {verify}")
            if self.expected_impact and not any(
                (a.metadata or {}).get("expected_effect") for a in actions[:1]
            ):
                lines.append(f"Ожидаемый эффект: {self.expected_impact}")
            blocks.append("\n".join(lines))
        elif self.do_first:
            blocks.append("\n".join([
                "🔥 ЧТО ДЕЛАТЬ",
                "🔧 ЧТО ДЕЛАТЬ",
                f"1. {_clean_action_text(self.do_first)}",
            ]))

        # 🚫 ЧТО НЕ ТРОГАТЬ (+ suite: ЧТО НЕ ДЕЛАТЬ)
        not_rec = list(self.not_recommended)
        if not not_rec and self.leave_alone:
            not_rec = [AdvisorItem(
                text=self.leave_alone,
                layer=ClaimLayer.RECOMMENDATION,
                metadata={"not_recommended": True},
            )]
        if card_healthy and self.main_problem_kind == "no_systemic":
            blob_nr = " ".join(x.text.lower() for x in not_rec)
            if "карточк" not in blob_nr:
                not_rec = [AdvisorItem(
                    text="не переписывать здоровую карточку и не трогать цену/рекламу без сигнала",
                    layer=ClaimLayer.RECOMMENDATION,
                    why="подтверждённых системных рисков нет",
                    metadata={"not_recommended": True},
                )] + not_rec
        if self.main_problem_kind in ("no_systemic", "funnel_symptom"):
            comm_n = 0
            raw_cmp = meta.get("competitor_comparison") if isinstance(meta, dict) else None
            if isinstance(raw_cmp, dict):
                comm_n = int(raw_cmp.get("commercial_n") or 0)
            elif raw_cmp is not None:
                try:
                    comm_n = int(getattr(raw_cmp, "commercial_n", 0) or 0)
                except (TypeError, ValueError):
                    comm_n = 0
            price_nr = (
                "Не менять цену — рыночная позиция неизвестна."
                if comm_n <= 0
                else "Не менять цену без ценностного разрыва и нового evidence."
            )
            not_rec = [
                AdvisorItem(
                    text=price_nr if comm_n <= 0 else "цену — не менять без доказанного диагноза.",
                    layer=ClaimLayer.RECOMMENDATION,
                    why="PRICE_POSITION = UNKNOWN без commercial fields",
                    metadata={"not_recommended": True},
                ),
                AdvisorItem(
                    text="Не менять описание/характеристики автоматически.",
                    layer=ClaimLayer.RECOMMENDATION,
                    why="нет confirmed card diagnosis",
                    metadata={"not_recommended": True},
                ),
                AdvisorItem(
                    text="Не запускать/увеличивать рекламу без funnel evidence.",
                    layer=ClaimLayer.RECOMMENDATION,
                    why="rates без orders/baseline ≠ causal ads action",
                    metadata={"not_recommended": True},
                ),
            ]
        if not_rec:
            lines = ["🚫 ЧТО НЕ ТРОГАТЬ", "🚫 ЧТО НЕ ДЕЛАТЬ"]
            show_n = 3 if self.main_problem_kind in ("no_systemic", "funnel_symptom") else 4
            for i, it in enumerate(not_rec[:show_n], 1):
                why = ""
                if it.why and self.main_problem_kind != "no_systemic":
                    why = f" — {it.why}"
                lines.append(f"{i}. {_clean_action_text(it.text)}{why}")
            blocks.append("\n".join(lines))

        # 🔎 ПОЧЕМУ (+ suite markers)
        why_lines = list(self.why_points)
        if not why_lines:
            if self.diagnosis:
                why_lines.append(self.diagnosis)
            for p in self.confirmed_problems()[:2]:
                why_lines.append(_humanize_main_line(p.text))
            if self.bottleneck and self.bottleneck not in ("неизвестно", "UNKNOWN", "unknown"):
                why_lines.append(f"узкое место: {self.bottleneck}")
        if self.main_problem_why and self.main_problem_kind == "problem":
            why_lines.insert(0, self.main_problem_why[:200])
        decide_lines = [
            "🔎 ПОЧЕМУ",
            "📌 ПОЧЕМУ",
            "📊 ПОЧЕМУ ARGUS ТАК РЕШИЛ",
        ]
        seen_w: set[str] = set()
        for w in why_lines:
            clean = _humanize_why_bullet(w)
            if not clean:
                continue
            key = clean.lower()[:80]
            if key in seen_w:
                continue
            seen_w.add(key)
            decide_lines.append(f"• {clean}")
            if len(decide_lines) >= 8:
                break
        # key numeric facts without provenance noise / empty CTR
        for f in self.facts:
            clean = _fact_for_why(f.text)
            if not clean:
                continue
            key = clean.lower()[:80]
            if key in seen_w:
                continue
            seen_w.add(key)
            decide_lines.append(f"• {clean}")
            if len(decide_lines) >= 10:
                break
        blocks.append("\n".join(decide_lines))

        # 📋 ЧТО НУЖНО ПРОВЕРИТЬ (+ suite: ЧЕГО НЕ ХВАТАЕТ)
        missing_bits: list[str] = []
        if self.data_needed and self.data_needed not in ("—", "-"):
            for part in re.split(r"[,;]", self.data_needed):
                bit = part.strip()
                if bit and bit not in missing_bits:
                    missing_bits.append(bit)
        for it in self.to_verify[:4]:
            if it.text and it.text not in missing_bits:
                missing_bits.append(it.text)
        for g in self.grow:
            low = g.text.lower()
            if "нет данных" in low or "не могу оценить" in low:
                if g.text not in missing_bits:
                    missing_bits.append(g.text)
        if unit and not unit.get("complete") and unit.get("text"):
            missing_bits.append(str(unit["text"]))
        missing_bits = _dedupe_missing_bits(missing_bits)
        if self.main_problem_kind == "no_systemic":
            n_chars_fact = None
            for f in self.facts:
                low = (f.text or "").lower()
                if low.startswith("характеристик:"):
                    m = re.search(r"(\d+)", f.text or "")
                    if m:
                        n_chars_fact = int(m.group(1))
                    break
            compact = [
                "CTR/CVR/заказы",
                "повторяемость review signals",
                "CV-анализ фото — детальный CV-анализ не выполнялся",
            ]
            if n_chars_fact == 0:
                compact.append("Характеристики как IDEA/CHECK")
            else:
                compact.append("economics")
            rest: list[str] = []
            for bit in missing_bits:
                low = bit.lower()
                if any(k in low for k in (
                    "ctr", "cvr", "заказ", "cv-анализ", "фото", "повтор",
                    "экономик", "себестоим", "марж", "показы", "характеристик",
                )):
                    continue
                rest.append(bit)
            missing_bits = compact + rest
        elif (meta or {}).get("funnel_rates_known") or self.main_problem_kind == "funnel_symptom":
            missing_bits = [
                "CTR/CVR baseline",
                "orders",
                "reviews recurring signals",
                "CV-анализ фото — детальный CV-анализ не выполнялся",
                "economics after commission/logistics",
            ]
        if missing_bits:
            lines = [
                "🔎 ЧТО ПРОВЕРИТЬ",
                "📋 ЧТО НУЖНО ПРОВЕРИТЬ",
                "🔎 ЧЕГО НЕ ХВАТАЕТ",
                "📈 ЧТО НУЖНО ДЛЯ СЛЕДУЮЩЕГО ВЫВОДА",
            ]
            for i, bit in enumerate(missing_bits[:5], 1):
                lines.append(f"{i}. {bit}")
            blocks.append("\n".join(lines))

        if market_block_txt:
            blocks.append(market_block_txt)

        unit_block = unit if isinstance(unit, dict) else None
        if unit_block and unit_block.get("partial") and unit_block.get("gross_before_fees") is not None:
            price_u = unit_block.get("price")
            cost_u = unit_block.get("cost")
            gross_u = unit_block.get("gross_before_fees")
            econ_lines = ["🧮 ЭКОНОМИКА"]
            if price_u is not None and cost_u is not None:
                econ_lines.append(
                    f"Цена {price_u:.0f} ₽ − COGS {cost_u:.0f} ₽ = {gross_u:.0f} ₽ "
                    "до комиссии и логистики."
                )
            else:
                econ_lines.append(str(unit_block.get("text") or ""))
            econ_lines.append("Это НЕ прибыль и НЕ маржа.")
            blocks.append("\n".join(econ_lines))

        conf_label = self.confidence_label or _confidence_band(self.confidence)[0]
        conf_why = self.confidence_why or _confidence_band(self.confidence)[1]
        if self.main_verdict or conf_label or self.main_problem:
            lines = ["⚠️ УВЕРЕННОСТЬ", f"{(conf_label or 'средняя').capitalize()}."]
            if conf_why:
                lines.append(f"Почему: {conf_why}")
            blocks.append("\n".join(lines))

        funnel_bad = (
            self.main_problem_kind == "inconsistent"
            or str((meta or {}).get("funnel_status") or "").upper() in (
                "INCONSISTENT", "INVALID",
            )
        )
        if funnel_bad:
            blocks = ["📈 АРГУС — разбор"] + _format_inconsistent_first_screen(self, meta)

        # ── DETAIL (not first screen) ────────────────────────────────── #
        if self.main_verdict:
            blocks.append("🧠 ВЕРДИКТ\n" + self.main_verdict)

        # ЧТО МЫ ЗНАЕМ (FACT + confirmed; never IDEA)
        known_items: list[AdvisorItem] = []
        if self.known:
            known_items = [k for k in self.known if k.layer != ClaimLayer.IDEA]
        else:
            for f in self.facts:
                if f.layer != ClaimLayer.FACT:
                    continue
                if any(
                    k in f.text.lower()
                    for k in (
                        "товар:", "бренд:", "артикул:", "фото", "описание:",
                        "характеристик", "рейтинг", "отзыв", "цена", "score",
                        "обработано", "public_price", "seller_price", "ctr", "cvr",
                        "продаж", "заказ", "экономик",
                    )
                ):
                    # не тащить «нет данных» по CTR/CVR/марже в «знаем» как цифру
                    if _is_empty_metric_fact(f.text):
                        continue
                    known_items.append(f)
            known_items = known_items[:8]
            for p in self.confirmed_problems()[:4]:
                if p.layer != ClaimLayer.IDEA:
                    known_items.append(p)
        known_items = [k for k in known_items if k.layer != ClaimLayer.IDEA]
        known_items = [k for k in known_items if not _is_empty_metric_fact(k.text)]
        if known_items:
            lines = ["ЧТО МЫ ЗНАЕМ"]
            for it in known_items[:10]:
                evid = it.evidence_tag()
                suffix = f" (evidence={evid})" if evid else ""
                freq = f" [{it.frequency}]" if it.frequency else ""
                lines.append(f"• {_strip_provenance(it.text)}{freq}{suffix}")
            blocks.append("\n".join(lines))
        elif any((p.metadata or {}).get("empty_risks") for p in self.problems):
            blocks.append(
                "ЧТО МЫ ЗНАЕМ\n"
                "• Нет подтверждённых системных проблем по обработанным отзывам."
            )

        assumed = list(self.assumed)
        if not assumed:
            assumed = list(self.weak_signals())
            for a in self.add:
                if a.layer == ClaimLayer.IDEA:
                    assumed.append(AdvisorItem(
                        text=a.text,
                        layer=ClaimLayer.IDEA,
                        why=a.why,
                        metadata={"from_add": True},
                    ))
            for u in self.unproven:
                if u.layer in (ClaimLayer.INFERENCE, ClaimLayer.IDEA):
                    assumed.append(u)
        seen_a: set[str] = set()
        assumed_u: list[AdvisorItem] = []
        for it in assumed:
            key = it.text.lower().strip()
            if key in seen_a:
                continue
            seen_a.add(key)
            assumed_u.append(it)
        if assumed_u:
            lines = ["ЧТО МЫ ПРЕДПОЛАГАЕМ"]
            for it in assumed_u[:6]:
                if it.layer == ClaimLayer.IDEA:
                    prefix = "Идея (не факт): "
                elif it.layer == ClaimLayer.INFERENCE or (it.metadata or {}).get("weak"):
                    prefix = "Похоже: "
                else:
                    prefix = ""
                lines.append(f"• {prefix}{_humanize_main_line(it.text)}")
            blocks.append("\n".join(lines))

        to_verify = list(self.to_verify)
        if not to_verify:
            if self.data_needed and self.data_needed not in ("—", "-"):
                to_verify.append(AdvisorItem(
                    text=self.data_needed,
                    layer=ClaimLayer.FACT,
                    metadata={"verify": True},
                ))
            if not self.photos_analyzed and self.facts:
                to_verify.append(AdvisorItem(
                    text="детальный CV-анализ не выполнялся — только счётчик кадров",
                    layer=ClaimLayer.FACT,
                    metadata={"photo_honesty": True},
                ))
        # detail also keeps ЧТО НУЖНО ПРОВЕРИТЬ if first screen already had it —
        # skip duplicate block when identical
        if to_verify and "📋 ЧТО НУЖНО ПРОВЕРИТЬ" not in "\n\n".join(blocks):
            lines = ["ЧТО НУЖНО ПРОВЕРИТЬ"]
            for it in to_verify[:6]:
                lines.append(f"• {it.text}")
            blocks.append("\n".join(lines))

        genuine_strengths = [
            it for it in (self.strengths or [])
            if not _is_complaint_theme_text(it.text)
        ]
        if self.main_problem_kind in ("no_systemic", "funnel_symptom") and not genuine_strengths:
            blocks.append(
                "✅ ЧТО УЖЕ ХОРОШО\nПока ничего отдельно не подтверждаем."
            )
        elif genuine_strengths:
            lines = ["✅ ЧТО УЖЕ ХОРОШО"]
            for i, it in enumerate(genuine_strengths[:4], 1):
                lines.append(f"{i}. {it.text}")
            if card_healthy:
                lines.append("Мониторить: рейтинг, новые жалобы; CTR/CVR — при наличии.")
            blocks.append("\n".join(lines))
        elif self.main_problem_kind in ("no_systemic", "funnel_symptom"):
            blocks.append(
                "✅ ЧТО УЖЕ ХОРОШО\nПока ничего отдельно не подтверждаем."
            )

        if not funnel_bad:
            tier = (self.priority_tier or "").strip() or "NONE"
            reason = (
                (self.metadata or {}).get("priority_reason")
                or self.priority_why
                or ""
            )
            if self.priority and tier != "NONE":
                lines = [f"🚀 ПРИОРИТЕТ {tier}" + (f" — потому что {reason}" if reason else "")]
                for i, it in enumerate(self.priority[:3], 1):
                    p = f"P{it.priority}" if it.priority else tier
                    why = f" — {it.why}" if it.why else ""
                    lines.append(f"{i}. {_clean_action_text(it.text)}{why} ({p})")
                blocks.append("\n".join(lines))
            elif (self.main_verdict or self.fixes or self.do_first or self.main_problem) and reason:
                marker = "🚀 ПРИОРИТЕТ" if tier != "NONE" else "⚠️ ПРИОРИТЕТ"
                blocks.append(f"{marker} {tier} — потому что {reason}")

        text = "\n\n".join(blocks).strip()
        if max_chars is not None and len(text) > max_chars:
            from backend.utils.telegram_split import trim_at_sentence
            text = trim_at_sentence(text, max_chars)
        return text

    def format_first_screen(self) -> str:
        """Seller-facing first screen. Soft cap ~1800, never mid-sentence."""
        full = self.format_plain()
        cut = len(full)
        for marker in _DETAIL_MARKERS:
            idx = full.find(marker)
            if idx > 0:
                cut = min(cut, idx)
        first = full[:cut].rstrip()
        if len(first) <= FIRST_SCREEN_MAX_CHARS:
            return first
        from backend.utils.telegram_split import split_telegram_message
        parts = split_telegram_message(first, limit=FIRST_SCREEN_MAX_CHARS, parse_mode=None)
        return (parts[0] if parts else first).rstrip()

    def format_details(self) -> str:
        full = self.format_plain()
        first = self.format_first_screen()
        rest = full[len(first):].lstrip() if full.startswith(first) else ""
        if rest:
            return rest
        for marker in _DETAIL_MARKERS:
            idx = full.find(marker)
            if idx > 0:
                return full[idx:].strip()
        return ""

    def format_seller_messages(self) -> list[str]:
        """Ordered Telegram parts: first screen, then details. No silent cut."""
        from backend.utils.telegram_split import (
            TELEGRAM_MAX_MESSAGE_LENGTH,
            split_telegram_message,
        )
        first = self.format_first_screen()
        details = self.format_details()
        parts: list[str] = []
        parts.extend(split_telegram_message(first, limit=FIRST_SCREEN_MAX_CHARS, parse_mode=None))
        if details:
            parts.extend(
                split_telegram_message(
                    "Подробнее\n\n" + details,
                    limit=TELEGRAM_MAX_MESSAGE_LENGTH,
                    parse_mode=None,
                )
            )
        return [p for p in parts if p and p.strip()]

    def format_html(self) -> str:
        """HTML-блок для Telegram-отчёта."""
        parts: list[str] = []
        raw = self.format_plain()
        if not raw:
            return ""
        headers = (
            "📈 АРГУС — разбор",
            "🎯 ГЛАВНЫЙ ВЫВОД",
            "🎯 ГЛАВНАЯ ПРОБЛЕМА",
            "🎯 ВЫВОД",
            "🟢 КАРТОЧКА В НОРМЕ",
            "📊 КЛЮЧЕВЫЕ ЦИФРЫ",
            "🏪 РЫНОК",
            "🔥 ЧТО ДЕЛАТЬ",
            "🔧 ЧТО ДЕЛАТЬ",
            "🚫 ЧТО НЕ ТРОГАТЬ",
            "🚫 ЧТО НЕ ДЕЛАТЬ",
            "🔎 ПОЧЕМУ",
            "🔎 ЧТО ПРОВЕРИТЬ",
            "🔎 ЧЕГО НЕ ХВАТАЕТ",
            "📋 ЧТО НУЖНО ПРОВЕРИТЬ",
            "📊 ПОЧЕМУ ARGUS ТАК РЕШИЛ",
            "🧠 ВЕРДИКТ",
            "📌 ПОЧЕМУ",
            "⚠️ ПРОВЕРКА",
            "⚠️ УВЕРЕННОСТЬ",
            "🚫 НЕ ТРОГАТЬ",
            "ЧТО МЫ ЗНАЕМ",
            "ЧТО МЫ ПРЕДПОЛАГАЕМ",
            "ЧТО НУЖНО ПРОВЕРИТЬ",
            "✅ ЧТО УЖЕ ХОРОШО",
            "📈 ЧТО НУЖНО ДЛЯ СЛЕДУЮЩЕГО ВЫВОДА",
        )
        for line in raw.split("\n"):
            if line in headers or line.startswith("🚀 ПРИОРИТЕТ") or line.startswith("⚠️ ПРИОРИТЕТ"):
                parts.append("")
                parts.append(f"<b>{html.escape(line)}</b>")
            else:
                parts.append(html.escape(line))
        body = "\n".join(parts).strip()
        if not body:
            return ""
        return "\n━━━━━━━━━━━━━━━━━━━━\n" + body

    def to_context_block(self, *, max_chars: int = 1600) -> str:
        """Компактный блок для ContextBuilder / SellerBrain."""
        header = [
            "ADVISOR PLAN (ANALYTICAL)",
            "Цепочка: FACT → SIGNAL → CONFIDENCE → DIAGNOSIS → ACTION → PRIORITY → NOT_RECOMMENDED.",
            "Слои: FACT / EVIDENCE / INFERENCE / RECOMMENDATION / IDEA.",
            "Разделение: ЧТО МЫ ЗНАЕМ / ПРЕДПОЛАГАЕМ / НУЖНО ПРОВЕРИТЬ.",
            "Факты разделены: CARD / REVIEW / PRIVATE / RESEARCH + provenance.",
            "IDEA ≠ спрос покупателей. IDEA не в «ЧТО МЫ ЗНАЕМ» как факт спроса.",
            "Единичный отзыв ≠ systemic. «часто» только при подтверждённой частоте.",
            "Не противоречь диагнозу (locus). Не советуй снизить цену без PRICE-риска.",
            "Не выдумывай факты вне evidence. Не выдумывай CTR/CVR/рост рынка.",
            f"locus={self.diagnosis_locus}; bottleneck={self.bottleneck}; "
            f"confidence={self.confidence:.2f} ({self.confidence_label or '—'}); "
            f"priority={self.priority_tier}; reliability={self.sample_reliability or '—'}",
        ]
        body = self.format_plain(max_chars=max_chars - sum(len(h) + 1 for h in header) - 2)
        text = "\n".join(header + ["", body]).strip()
        if len(text) > max_chars:
            from backend.utils.telegram_split import trim_at_sentence
            text = trim_at_sentence(text, max_chars)
        return text


# ── openings (varied, human) ─────────────────────────────────────────────── #

_OPENINGS_PROBLEM = (
    "В отзывах повторяется",
    "Покупатели снова пишут про",
    "Здесь явный риск",
    "Сигнал из отзывов",
)
_OPENINGS_FIX = (
    "Я бы первым делом",
    "Имеет смысл",
    "Практичный шаг",
    "Сфокусируйся на",
)
_OPENINGS_ADD = (
    "Имеет смысл добавить",
    "Карточке не хватает",
    "Стоит дополнить",
)
_OPENINGS_GROW = (
    "Для роста",
    "Если смотреть на спрос",
    "По рынку",
)


def _pick_opening(pool: tuple[str, ...], salt: int) -> str:
    return pool[salt % len(pool)]


def _enum_val(obj: Any) -> str:
    if obj is None:
        return ""
    return str(getattr(obj, "value", obj) or "")


# OTHER ranking: content of evidence beats n/freq alone.
# Praise/neutral/vague OTHER → not main; named concrete risks win; concrete
# negative OTHER can still be ACTION when evidence supports it.
_ADVISOR_PRAISE_MARKERS = (
    "отличн", "хорош", "супер", "класс", "рекоменд", "доволен", "довольн",
    "качественн", "быстр", "удобн", "красив", "нравит", "понрав", "восторг",
    "идеальн", "кайф", "бомб", "огонь", "топ ", "люблю", "прекрасн",
    "замечательн", "шикарн", "спасибо", "респект", "просто бомба",
)
_ADVISOR_CONCRETE_DEFECT_MARKERS = (
    "брак", "сломал", "не работ", "дефект", "порван", "дырк", "трещин",
    "поцарап", "помят", "поврежд", "воня", "запах", "линя", "рассыпал",
    "отклеи", "оторва", "протека", "течёт", "течет", "маломер", "большемер",
    "не как на фото", "не соответств", "вернул", "возврат", "обман",
    "разбит", "скол", "отломал", "остановил", "креплен", "молни", "швы",
    "фурнитур", "не того цвета", "не тот цвет", "пришла не то", "пришло не то",
    "царапин", "рвётся", "рвется", "батаре", "стрелки упал", "краска слет",
    "слетает краск", "под краской",
)
_ADVISOR_VAGUE_OTHER_MARKERS = (
    "не понрав", "не очень", "так себе", "ожидал другого", "не то",
    "странн", "без комментар", "просто так", "норм ",
)
_COMPLAINT_THEME_MARKERS = (
    "царапин", "поцарап", "поврежд", "дефект", "брак", "слом", "трещин",
    "упаков", "ожидан", "не совпал", "несоответств", "не соответств",
    "качество товара", "качеств товара", "комплектац", "непонятн",
)
_NAMED_RISK_TYPES = frozenset({
    "PHOTO_MATCH", "PRODUCT_QUALITY", "QUALITY", "PACKAGING", "SIZE",
    "DAMAGE", "FUNCTIONALITY", "DESCRIPTION_MATCH", "COMPLETENESS",
    "UNPACKING", "LOGISTICS", "DELIVERY", "DESIGN", "APPEARANCE",
    "EXPECTATIONS", "PRICE_VALUE", "SERVICE",
})


def _signal_type_of(problem) -> str:
    return _enum_val(
        getattr(problem, "signal_type", None)
        or getattr(problem, "category", None)
        or (getattr(problem, "metadata", None) or {}).get("category")
    ).upper()


def _problem_text_blob(problem) -> str:
    parts: list[str] = []
    for attr in ("label", "claim", "text", "rationale", "why"):
        v = getattr(problem, attr, None)
        if v:
            parts.append(str(v))
    for ex in list(getattr(problem, "examples", None) or []):
        parts.append(str(ex))
    meta = getattr(problem, "metadata", None) or {}
    for key in ("examples", "claim", "label"):
        val = meta.get(key)
        if val:
            parts.append(str(val))
    return " ".join(parts).lower().replace("ё", "е")


def _other_content_class(problem) -> str:
    """
    Класс содержимого OTHER (и «прочий сигнал»):
      praise | vague | concrete
    Не хардкодит ignore-all-OTHER: concrete negative остаётся возможным ACTION.

    Sense > keywords: direction=positive → praise (даже при «царапины» в label).
    «не/другой/проблема/нет» сами по себе ≠ concrete negative.
    Concrete только из примеров/claim с реальным дефектом, не из taxonomy label.
    """
    direction = _enum_val(getattr(problem, "direction", None)).lower()
    # OTHER + positive — всегда похвала/позитивный контекст, не concrete risk
    if direction == "positive":
        return "praise"

    examples = [str(e) for e in (getattr(problem, "examples", None) or []) if e]
    meta = getattr(problem, "metadata", None) or {}
    if not examples and meta.get("examples"):
        examples = [str(e) for e in (meta.get("examples") or []) if e]
    claim = str(getattr(problem, "claim", None) or "")
    # Evidence-first blob: examples + claim; taxonomy label alone не делает concrete
    evidence_blob = " ".join(examples + ([claim] if claim else [])).lower().replace("ё", "е")
    blob = evidence_blob.strip() or _problem_text_blob(problem)

    # «не понравилось» / «не рекомендую» ≠ praise
    blob_for_praise = blob
    for neg in (
        "не понрав", "не нравит", "не рекоменд", "не довольн", "не доволен",
        "не хорош", "не отличн", "не супер",
    ):
        blob_for_praise = blob_for_praise.replace(neg, " ")
    praise_hits = sum(1 for m in _ADVISOR_PRAISE_MARKERS if m in blob_for_praise)
    defect_hits = sum(1 for m in _ADVISOR_CONCRETE_DEFECT_MARKERS if m in blob)
    vague_hits = sum(1 for m in _ADVISOR_VAGUE_OTHER_MARKERS if m in blob)

    # RI-префикс «похвала» без жалобы/дефекта
    if "похвала" in blob and "жалоб" not in blob and defect_hits == 0:
        return "praise"
    if praise_hits > 0 and defect_hits == 0:
        return "praise"
    if praise_hits > defect_hits:
        return "praise"
    if defect_hits >= 1:
        return "concrete"
    if "прочий сигнал" in blob or vague_hits or not blob.strip():
        return "vague"
    # negative без actionable cause/effect → vague (не главная ось)
    # одиночные «не/другой/проблема/нет» без defect-маркера тоже vague
    return "vague"


def _human_other_diagnosis(problem) -> str:
    """Диагноз OTHER из примеров — без [OTHER] / «прочий сигнал».

    Prefer examples with real defect markers; never lead with praise-like
    snippets just because they are long.
    """
    examples = [str(e).strip() for e in (getattr(problem, "examples", None) or []) if e]
    meta = getattr(problem, "metadata", None) or {}
    if not examples and meta.get("examples"):
        examples = [str(e).strip() for e in (meta.get("examples") or []) if e]

    defect_examples: list[str] = []
    other_examples: list[str] = []
    for ex in examples:
        if not ex:
            continue
        low = ex.lower().replace("ё", "е")
        if "прочий сигнал" in low or low in ("other", "[other]"):
            continue
        if any(m in low for m in _ADVISOR_CONCRETE_DEFECT_MARKERS):
            defect_examples.append(ex)
            continue
        # skip praise-only snippets as diagnosis lead
        praise_hits = sum(1 for m in _ADVISOR_PRAISE_MARKERS if m in low)
        vague_hits = sum(1 for m in _ADVISOR_VAGUE_OTHER_MARKERS if m in low)
        if praise_hits > 0 and not vague_hits:
            continue
        if len(ex) >= 12:
            other_examples.append(ex)

    if defect_examples:
        return defect_examples[0][:90].rstrip(" .")

    claim = str(getattr(problem, "claim", None) or "").strip()
    claim = re.sub(r"^(жалоб[аы]|похвал[аы]|упоминание)\s*\[OTHER\]:\s*", "", claim, flags=re.IGNORECASE)
    claim = re.sub(r"^\[[A-Z_]+\]\s*", "", claim).strip()
    claim_low = claim.lower().replace("ё", "е")
    if claim and "прочий сигнал" not in claim_low and claim_low not in ("other",):
        if any(m in claim_low for m in _ADVISOR_CONCRETE_DEFECT_MARKERS):
            return claim[:90].rstrip(" .")

    if other_examples:
        return other_examples[0][:90].rstrip(" .")

    label = str(getattr(problem, "label", None) or "").strip()
    if label and "прочий сигнал" not in label.lower() and label.lower() not in ("other",):
        return label[:90].rstrip(" .")

    return "конкретные жалобы в отзывах — разобрать формулировки"


def _display_problem_label(problem) -> str:
    """Человеческий ярлык для lead/do_first — OTHER из examples, не taxonomy."""
    stype = _signal_type_of(problem)
    cleaned = _clean_problem_label(getattr(problem, "text", None) or "")
    low = cleaned.lower()
    if (
        stype in ("OTHER", "other")
        or "прочий сигнал" in low
        or (getattr(problem, "category", None) or "").upper() == "OTHER"
    ):
        return _human_other_diagnosis(problem)
    return cleaned or _human_other_diagnosis(problem)


def _is_named_risk_type(stype: str) -> bool:
    return (stype or "").upper() in _NAMED_RISK_TYPES


def _other_rank_penalty(problem) -> int:
    """0 = named risk, 1 = concrete OTHER, 2 = vague/praise OTHER."""
    stype = _signal_type_of(problem)
    if _is_named_risk_type(stype):
        return 0
    if stype in ("OTHER", "other"):
        content = _other_content_class(problem)
        return 1 if content == "concrete" else 2
    return 1


def _can_be_main_problem(problem) -> bool:
    """OTHER praise/vague/positive не может стать 🎯 ГЛАВНАЯ ПРОБЛЕМА."""
    stype = _signal_type_of(problem)
    text_low = _problem_text_blob(problem)
    meta = getattr(problem, "metadata", None) or {}
    direction = _enum_val(getattr(problem, "direction", None)).lower()
    if stype in ("OTHER", "other") or "прочий сигнал" in text_low:
        # OTHER + positive → никогда main / ACTION / severity boost
        if direction == "positive":
            return False
        content = str(meta.get("other_content") or "")
        if not content:
            content = _other_content_class(problem)
        if content in ("praise", "vague"):
            return False
        # concrete OTHER → main только при явном negative
        if direction != "negative":
            return False
        return content == "concrete"
    return True


def _is_advisor_risk(problem) -> bool:
    """
    В «ЧТО ПОДТВЕРЖДЕНО» / сигналы риска — только реальные риски.
    Похвала / OTHER без явного negative — не risk
    (пример: «кайфовый бомбер» → OTHER+positive/mixed ≠ плохо).
    OTHER + praise (по тексту evidence) → не risk даже при кривом direction.
    OTHER + vague → допускается как candidate (weak), не main.
    OTHER + concrete negative → risk при порогах freq/priority.
    """
    direction = _enum_val(getattr(problem, "direction", None)).lower()
    stype = _signal_type_of(problem)

    if stype in ("OTHER", "other") or "прочий сигнал" in _problem_text_blob(problem):
        content = _other_content_class(problem)
        if content == "praise":
            return False
        if direction in ("positive",):
            return False
        # OTHER → risk только при явном negative; mixed/unknown похвалу отсекаем
        if direction != "negative":
            return False
        freq = _enum_val(getattr(problem, "frequency", None)).upper()
        prio = int(getattr(problem, "priority", 4) or 4)
        if freq == "LOW" or prio >= 4:
            return False
        # vague и concrete проходят в pipeline; vague ниже станет weak/candidate
        return True

    if direction in ("positive",):
        return False
    if direction not in ("negative", "mixed"):
        return False
    return True


def _is_complaint_theme_text(text: str) -> bool:
    """Жалобы/дефекты — не «уже хорошо», даже если direction=positive."""
    low = (text or "").lower().replace("ё", "е")
    if not low:
        return False
    if any(m in low for m in _COMPLAINT_THEME_MARKERS):
        return True
    if any(m in low for m in _ADVISOR_CONCRETE_DEFECT_MARKERS):
        return True
    return False


def _is_named_complaint_signal(problem) -> bool:
    stype = _signal_type_of(problem)
    if stype in _NAMED_RISK_TYPES:
        return True
    blob = _problem_text_blob(problem)
    return _is_complaint_theme_text(blob)


def _is_praise_signal(problem) -> bool:
    direction = _enum_val(getattr(problem, "direction", None)).lower()
    if direction == "positive":
        return True
    stype = _signal_type_of(problem)
    if stype in ("OTHER", "other"):
        # контент важнее direction: «кайфовый»/«понравилось» ≠ risk
        return _other_content_class(problem) == "praise"
    return False


# ── builders ─────────────────────────────────────────────────────────────── #


def _build_facts(
    product,
    score_data: dict | None,
    seller_data,
    review_assessment,
) -> list[AdvisorItem]:
    facts: list[AdvisorItem] = []

    if product is not None:
        title = getattr(product, "title", None)
        if title:
            facts.append(AdvisorItem(
                text=f"Товар: {title}",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.CARD.value},
            ))
        brand = getattr(product, "brand", None)
        if brand:
            facts.append(AdvisorItem(
                text=f"Бренд: {brand}",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.CARD.value},
            ))
        article = getattr(product, "article", None)
        if article is not None:
            facts.append(AdvisorItem(
                text=f"Артикул: {article}",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.CARD.value},
            ))

        photos = getattr(product, "photos", None) or []
        n_photos = len(photos) if not isinstance(photos, int) else photos
        facts.append(AdvisorItem(
            text=(
                f"Фото в карточке: {n_photos} "
                "(только счётчик кадров; качество и соответствие не проверялись)"
            ),
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.CARD.value, "photos_analyzed": False},
        ))

        desc = getattr(product, "description", None) or ""
        if desc:
            desc_txt = f"Описание: есть, {len(desc)} символов"
        else:
            desc_txt = "Описание: нет"
        facts.append(
            AdvisorItem(
                text=desc_txt,
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.CARD.value},
            )
        )
        chars = getattr(product, "characteristics", None) or {}
        facts.append(
            AdvisorItem(
                text=f"Характеристик: {len(chars)}",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.CARD.value},
            )
        )

        facts.append(_card_fact(product, "Рейтинг карточки", "rating"))
        facts.append(_card_fact(product, "Отзывов на карточке (card_feedbacks)", "feedbacks"))
        # Публичная цена ≠ «Ваша цена» без seller_price
        facts.append(_card_fact(product, "Публичная цена (public_price)", "price", "₽"))

        # evidence ≤ processed ≤ available (card_feedbacks)
        card_fb = getattr(product, "feedbacks", None)
        if card_fb is not None and review_assessment is not None:
            processed = int(getattr(review_assessment, "processed_count", 0) or 0)
            try:
                available = int(card_fb)
            except (TypeError, ValueError):
                available = None
            if available is not None and processed > available:
                facts.append(AdvisorItem(
                    text=f"Согласование выборки: processed={available} (cap по card_feedbacks={available})",
                    layer=ClaimLayer.FACT,
                    metadata={"source": FactSource.REVIEW.value, "capped_processed": True},
                ))

    if seller_data is not None:
        if getattr(seller_data, "price", None) is not None:
            facts.append(AdvisorItem(
                text=f"Цена продавца (seller_price): {seller_data.price} ₽",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.PRIVATE.value, "field": "seller_price"},
            ))
        else:
            facts.append(AdvisorItem(
                text="Цена продавца (seller_price): нет данных",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.PRIVATE.value, "field": "seller_price"},
            ))
        if getattr(seller_data, "rating", None) is not None:
            facts.append(AdvisorItem(
                text=f"Рейтинг продавца: {seller_data.rating}",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.PRIVATE.value},
            ))
        if getattr(seller_data, "feedbacks", None) is not None:
            facts.append(AdvisorItem(
                text=f"Отзывов (данные продавца): {seller_data.feedbacks}",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.PRIVATE.value},
            ))
        for label, attr in (
            ("CTR", "ctr"),
            ("CVR", "cvr"),
            ("Показы", "impressions"),
            ("Просмотры", "views"),
            ("Продажи", "sales"),
            ("Заказы", "orders"),
            ("Возвраты", "returns"),
            ("Реклама", "ad_spend"),
            ("Себестоимость", "cost"),
        ):
            val = getattr(seller_data, attr, None)
            facts.append(AdvisorItem(
                text=f"{label}: {val if val is not None else 'нет данных'}",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.PRIVATE.value, "field": attr},
            ))
    else:
        facts.append(AdvisorItem(
            text="PRIVATE metrics (CTR/CVR/показы/продажи/заказы): нет данных",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.PRIVATE.value},
        ))
        facts.append(AdvisorItem(
            text="Цена продавца (seller_price): нет данных",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.PRIVATE.value, "field": "seller_price"},
        ))
        facts.append(AdvisorItem(
            text="не могу оценить CTR/CVR без данных продавца",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.PRIVATE.value},
        ))

    if score_data and score_data.get("score") is not None:
        score_txt = f"Score карточки: {score_data['score']}/100"
        breakdown = score_data.get("breakdown")
        if isinstance(breakdown, dict) and breakdown:
            bits = [f"{k}={v}" for k, v in breakdown.items() if v is not None]
            if bits:
                score_txt += f" [{', '.join(bits[:6])}]"
        if score_data.get("scope"):
            score_txt += f" (scope={score_data['scope']})"
        facts.append(AdvisorItem(
            text=score_txt,
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.CARD.value},
        ))

    if review_assessment is not None:
        n = int(getattr(review_assessment, "processed_count", 0) or 0)
        # evidence ≤ processed ≤ available
        available = None
        if product is not None:
            try:
                available = int(getattr(product, "feedbacks", None)) if getattr(product, "feedbacks", None) is not None else None
            except (TypeError, ValueError):
                available = None
        if available is not None and n > available:
            n = available
        card_part = str(available) if available is not None else "нет данных"
        facts.append(AdvisorItem(
            text=f"Отзывов на карточке / Проанализировано: {card_part} / {n}",
            layer=ClaimLayer.FACT,
            metadata={
                "source": FactSource.REVIEW.value,
                "processed": n,
                "available": available,
                "card_vs_processed": True,
            },
        ))
        facts.append(AdvisorItem(
            text=f"Проанализировано отзывов: {n} (processed_reviews={n}"
                 + (f", card_feedbacks={available}" if available is not None else "")
                 + ")",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.REVIEW.value, "processed": n, "available": available},
        ))
        # backwards-compatible alias for older tests
        facts.append(AdvisorItem(
            text=f"Обработано отзывов (processed_reviews): {n}",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.REVIEW.value, "alias": True},
        ))
        rel = sample_reliability_label(n)
        facts.append(AdvisorItem(
            text=f"Надёжность выборки: {rel} (n={n})",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.REVIEW.value, "sample_reliability": rel, "n": n},
        ))
        if n < _SMALL_SAMPLE_N:
            facts.append(AdvisorItem(
                text=f"Выборка n={n} < 10 — сигналы только candidate, не systemic",
                layer=ClaimLayer.FACT,
                metadata={"source": FactSource.REVIEW.value, "small_sample": True},
            ))

    return facts


def _build_strengths(
    product,
    score_data: dict | None,
    review_assessment,
) -> list[AdvisorItem]:
    """✅ ЧТО УЖЕ ХОРОШО — только доказанная похвала, не жалобы и не счётчики."""
    out: list[AdvisorItem] = []

    if review_assessment is not None:
        for p in list(getattr(review_assessment, "problems", None) or []):
            if not _is_praise_signal(p):
                continue
            if _is_named_complaint_signal(p):
                continue
            label = (getattr(p, "label", None) or getattr(p, "claim", None) or "").strip()
            if not label:
                continue
            low = label.lower()
            if "прочий сигнал" in low or low in ("other", "прочее", "прочий"):
                continue
            if _is_complaint_theme_text(label) or _is_complaint_theme_text(_problem_text_blob(p)):
                continue
            out.append(AdvisorItem(
                text=f"Покупатели отмечают: {label}",
                layer=ClaimLayer.FACT,
                evidence_ids=list(getattr(p, "evidence_ids", None) or [])[:4],
                direction=_enum_val(getattr(p, "direction", None)),
                metadata={"praise": True},
            ))

    # dedupe
    seen: set[str] = set()
    uniq: list[AdvisorItem] = []
    for it in out:
        key = it.text.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:6]


def _build_problems(review_assessment) -> list[AdvisorItem]:
    """Сигналы риска: confirmed + weak (weak помечается metadata.weak)."""
    if review_assessment is None:
        return []

    problems_raw = list(getattr(review_assessment, "problems", None) or [])
    risks = [p for p in problems_raw if _is_advisor_risk(p)]
    processed = int(getattr(review_assessment, "processed_count", 0) or 0)
    if not risks:
        if processed > 0:
            return [AdvisorItem(
                text="Нет подтверждённых системных проблем по обработанным отзывам.",
                layer=ClaimLayer.FACT,
                metadata={"empty_risks": True},
            )]
        return []

    def _sort_key(p):
        freq = _enum_val(getattr(p, "frequency", None))
        freq_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(freq, 3)
        # Named concrete risks always rank above OTHER noise/praise/vague.
        type_rank = _other_rank_penalty(p)
        return (
            type_rank,
            freq_rank,
            int(getattr(p, "priority", 4) or 4),
            -float(getattr(p, "confidence", 0) or 0),
        )

    risks = sorted(risks, key=_sort_key)

    out: list[AdvisorItem] = []
    for idx, p in enumerate(risks[:6]):
        freq = _enum_val(getattr(p, "frequency", None))
        sev = _enum_val(getattr(p, "severity", None))
        direction = _enum_val(getattr(p, "direction", None))
        prio = int(getattr(p, "priority", 4) or 4)
        evid = list(getattr(p, "evidence_ids", None) or [])
        # evidence base не больше processed_reviews
        if processed > 0 and len(evid) > processed:
            evid = evid[:processed]
        examples = list(getattr(p, "examples", None) or [])
        label = (getattr(p, "label", None) or getattr(p, "claim", None) or "сигнал из отзывов").strip()
        cat = (
            (getattr(p, "metadata", None) or {}).get("category")
            or _enum_val(getattr(p, "signal_type", None))
        )
        rationale = (getattr(p, "rationale", None) or "").strip()
        count = getattr(p, "count", None)
        # честный знаменатель для упоминаний / evidence_count
        evidence_count = len(evid)
        if processed > 0 and evidence_count > processed:
            evidence_count = processed
            evid = evid[:processed]
        if count is not None and processed > 0:
            try:
                c = int(count)
                if c > processed:
                    c = processed
                if c > evidence_count and evidence_count > 0:
                    c = evidence_count
                freq_note = f"{c} из {processed} обработанных"
            except (TypeError, ValueError):
                freq_note = f"evidence={evidence_count}" if evidence_count else ""
                c = evidence_count
        else:
            freq_note = f"evidence={evidence_count}" if evidence_count else ""
            c = evidence_count

        # base weak from RI fields; then sample/evidence calibration
        base_weak = (
            prio >= 4
            or freq == "LOW"
            or float(getattr(p, "confidence", 0) or 0) < 0.40
        )
        cat_u = str(cat or "").upper()
        other_content = ""
        if cat_u in ("OTHER", "other") or "прочий сигнал" in (label or "").lower():
            other_content = _other_content_class(p)
            # praise/neutral/vague OTHER: lower confidence → candidate/CHECK only
            if other_content in ("praise", "vague"):
                base_weak = True
            # concrete OTHER: humanize label from examples, never lead with «прочий сигнал»
            if other_content == "concrete":
                human = _human_other_diagnosis(p)
                if human and (
                    "прочий сигнал" in (label or "").lower()
                    or not label
                    or label.lower() in ("other",)
                ):
                    label = human
        calib = _calibrate_review_signal(
            sample_n=processed,
            evidence_count=evidence_count,
            freq=freq,
            base_weak=base_weak,
        )
        is_weak = bool(calib["weak"])
        is_recurring = bool(calib["recurring"])
        is_candidate = bool(calib["recurring_candidate"])
        candidate_language = bool(calib["candidate_language"])
        max_conf = float(calib["max_confidence"])
        if other_content in ("praise", "vague"):
            # never confirm vague/praise OTHER as systemic main axis
            is_weak = True
            is_recurring = False
            is_candidate = True
            candidate_language = True
            max_conf = min(max_conf, 0.45)
        if candidate_language or is_candidate or is_weak:
            label = _candidate_safe_label(label)

        if is_weak and evidence_count <= 1:
            layer = ClaimLayer.INFERENCE
            text = (
                f"{_pick_opening(_OPENINGS_PROBLEM, idx)}: {label} — "
                "единичный сигнал-кандидат, не системный риск (нужно проверить)"
            )
        elif candidate_language or is_candidate:
            layer = ClaimLayer.INFERENCE
            opening = _pick_opening(_OPENINGS_PROBLEM, idx)
            text = (
                f"{opening}: {label} — повторяющийся кандидат"
                + (f" ({freq_note})" if freq_note else "")
                + ", не доказано как системное; нужно проверить"
            )
        else:
            layer = ClaimLayer.FACT if evid and not is_weak else ClaimLayer.INFERENCE
            opening = _pick_opening(_OPENINGS_PROBLEM, idx)
            if is_recurring and not is_weak:
                text = f"{opening}: {label}"
            else:
                text = f"Похоже, есть сигнал: {label}"
            if freq_note:
                text = f"{text} ({freq_note})"

        # named categories keep [CAT] for debug; OTHER never as user-facing lead tag
        if cat and cat_u not in ("OTHER", "other"):
            text = f"[{cat}] {text}"

        out.append(AdvisorItem(
            text=text,
            layer=layer,
            priority=prio,
            evidence_ids=evid[:8],
            why=(
                "OTHER без конкретной причины — только CHECK, не главная проблема"
                if other_content in ("praise", "vague")
                else (
                    "малая выборка / мало evidence — только кандидат на проверку"
                    if (candidate_language or is_candidate)
                    else rationale[:160]
                )
            ),
            category=str(cat or ""),
            frequency=freq,
            severity=sev,
            direction=direction,
            examples=[str(e)[:80] for e in examples[:2]],
            metadata={
                "recurring": is_recurring,
                "recurring_candidate": is_candidate,
                "weak": is_weak,
                "evidence_count": evidence_count,
                "mention_count": c if isinstance(c, int) else evidence_count,
                "processed": processed,
                "max_confidence": max_conf,
                "candidate_language": candidate_language,
                "other_content": other_content or None,
            },
        ))
    return out


def _build_fixes(review_assessment, review_recs: list | None = None) -> list[AdvisorItem]:
    """🔧 ЧТО ДЕЛАТЬ — concrete action + why + evidence + priority."""
    out: list[AdvisorItem] = []
    seen: set[str] = set()

    actions = list(getattr(review_assessment, "actions", None) or []) if review_assessment else []
    for idx, a in enumerate(actions):
        title = (getattr(a, "title", None) or "").strip()
        if not title or title.lower() in seen:
            continue
        evid = list(getattr(a, "evidence_ids", None) or [])
        if not evid:
            continue  # no evidence → skip (no fabrication)
        prio = int(getattr(a, "priority", 4) or 4)
        why = (getattr(a, "rationale", None) or "").strip()
        opening = _pick_opening(_OPENINGS_FIX, idx)
        text = f"{opening}: {title}"
        seen.add(title.lower())
        out.append(AdvisorItem(
            text=text,
            layer=ClaimLayer.RECOMMENDATION,
            priority=min(4, max(1, prio)),
            evidence_ids=evid[:8],
            why=why[:160],
            category=_enum_val(getattr(a, "signal_type", None)),
        ))

    for idx, rec in enumerate(review_recs or []):
        title = (getattr(rec, "title", None) or "").strip()
        if not title or title.lower() in seen:
            continue
        rtype = _enum_val(getattr(rec, "type", None))
        if rtype == "MONITOR":
            continue
        evid = list(getattr(rec, "evidence_ids", None) or [])
        if not evid or evid == ["no_recurring_issue"] or evid == ["no_evidence"]:
            continue
        prio = int(getattr(rec, "priority", 4) or 4)
        prio = min(4, max(1, prio))
        action = (getattr(rec, "action", None) or title).strip()
        why = (getattr(rec, "reason", None) or "").strip()
        seen.add(title.lower())
        out.append(AdvisorItem(
            text=f"{_pick_opening(_OPENINGS_FIX, idx + 3)}: {action}",
            layer=ClaimLayer.RECOMMENDATION,
            priority=prio,
            evidence_ids=evid[:8],
            why=why[:160],
        ))

    out.sort(key=lambda x: (x.priority or 4, -len(x.evidence_ids)))
    return out[:4]


def _build_add(product, card_recommendations: list | None = None) -> list[AdvisorItem]:
    """IDEAs from card gaps — только при реальном пробеле, never buyer-demand."""
    out: list[AdvisorItem] = []
    if product is None:
        return out

    photos = getattr(product, "photos", None) or []
    n_photos = len(photos) if not isinstance(photos, int) else int(photos)
    desc = getattr(product, "description", None) or ""
    chars = getattr(product, "characteristics", None) or {}

    # Photo advice only with real product data (no fake CV)
    if n_photos < 8:
        out.append(AdvisorItem(
            text=f"{_pick_opening(_OPENINGS_ADD, 0)} фото — сейчас {n_photos}, лучше 8–12",
            layer=ClaimLayer.IDEA,
            why="Больше кадров обычно помогает клику в ленте; это идея по карточке, не жалоба из отзывов. Визуальный разбор не делался.",
            metadata={"source": "card", "field": "photos"},
        ))
    if n_photos < 5:
        out.append(AdvisorItem(
            text=f"{_pick_opening(_OPENINGS_ADD, 1)} инфографику на первые кадры",
            layer=ClaimLayer.IDEA,
            why="На превью важно сразу показать назначение и выгоду. Кадры не анализировались CV.",
            metadata={"source": "card", "field": "infographic"},
        ))
    if not desc:
        out.append(AdvisorItem(
            text=f"{_pick_opening(_OPENINGS_ADD, 2)} подробное описание",
            layer=ClaimLayer.IDEA,
            why="Сейчас описания нет — покупателю не на чем опереться.",
            metadata={"source": "card", "field": "description"},
        ))
    elif len(desc) < 300:
        out.append(AdvisorItem(
            text=f"{_pick_opening(_OPENINGS_ADD, 0)} более развёрнутое описание (сейчас {len(desc)} симв.)",
            layer=ClaimLayer.IDEA,
            why="Короткий текст часто недораскрывает свойства.",
            metadata={"source": "card", "field": "description"},
        ))
    # Характеристики: только если реально мало (<5). 19 ≠ «добавь характеристики».
    if len(chars) < 5:
        out.append(AdvisorItem(
            text=f"{_pick_opening(_OPENINGS_ADD, 1)} характеристики — заполнено {len(chars)}",
            layer=ClaimLayer.IDEA,
            why="Фильтры и сравнение в выдаче опираются на атрибуты.",
            metadata={"source": "card", "field": "characteristics"},
        ))

    seen = {it.text.lower() for it in out}
    for raw in card_recommendations or []:
        s = str(raw)
        low = s.lower()
        if any(k in low for k in (
            "критичных замечаний нет",
            "выглядит очень хорошо",
            "улучшайте качество",
            "собирайте положительные",
            "увеличьте количество отзывов",
        )):
            continue
        if "фото" in low and n_photos >= 8:
            continue
        if "описан" in low and len(desc) >= 120:
            continue
        if "характеристик" in low and len(chars) >= 5:
            continue
        clean = re.sub(r"^[\W\d_]+", "", s).strip()
        if not clean or clean.lower() in seen:
            continue
        if any(bad in low for bad in ("покупател", "жалоб", "в отзывах")):
            continue
        out.append(AdvisorItem(
            text=f"[IDEA] {clean}",
            layer=ClaimLayer.IDEA,
            why="Идея по заполненности карточки, не сигнал из отзывов.",
            metadata={"source": "card_rec"},
        ))
        seen.add(clean.lower())

    return out[:6]


def _build_grow(
    category_context,
    market_recs: list | None,
    seller_data,
) -> list[AdvisorItem]:
    """Рост — only with data; seasonality via CI; no invented market growth."""
    out: list[AdvisorItem] = []

    if seller_data is not None:
        ctr = getattr(seller_data, "ctr", None)
        cvr = getattr(seller_data, "cvr", None)
        if ctr is None or cvr is None:
            missing = []
            if ctr is None:
                missing.append("CTR")
            if cvr is None:
                missing.append("CVR")
            out.append(AdvisorItem(
                text=f"не могу оценить {', '.join(missing)} и воронку — данных нет",
                layer=ClaimLayer.FACT,
                why="Без показов/CTR/CVR нельзя честно говорить про рекламную эффективность.",
            ))

    if seller_data is not None:
        sales = getattr(seller_data, "sales", None)
        orders = getattr(seller_data, "orders", None)
        period = getattr(seller_data, "period", None) or ""
        if sales is not None or orders is not None:
            bits = []
            if orders is not None:
                bits.append(f"заказов {orders}")
            if sales is not None:
                bits.append(f"продаж {sales}")
            if period:
                bits.append(f"за {period}")
            out.append(AdvisorItem(
                text=f"{_pick_opening(_OPENINGS_GROW, 0)} опирайся на факт: {', '.join(bits)}",
                layer=ClaimLayer.FACT,
                why="Цифры продаж/заказов есть — можно планировать рост от факта, не от догадки.",
            ))

    seasonal = getattr(category_context, "seasonal_signals", None) if category_context else None
    if seasonal:
        try:
            from datetime import datetime
            month = datetime.utcnow().month
            if month in seasonal:
                idx = float(seasonal[month])
                if idx > 1.05:
                    level = "выше нормы"
                    tip = "имеет смысл заранее усилить наличие и видимость"
                elif idx < 0.95:
                    level = "ниже нормы"
                    tip = "не раздувай рекламу в спад без сильной карточки"
                else:
                    level = "около нормы"
                    tip = "сезон не давит — приоритет на конверсию карточки"
                out.append(AdvisorItem(
                    text=f"{_pick_opening(_OPENINGS_GROW, 1)}: сезонность месяца индекс {idx:.2f} ({level})",
                    layer=ClaimLayer.INFERENCE,
                    why=tip,
                    metadata={"season_index": idx, "month": month},
                ))
            else:
                out.append(AdvisorItem(
                    text="По сезонности текущего месяца данных в Category Intelligence нет",
                    layer=ClaimLayer.FACT,
                    why="Не выдумываю сезонность без индекса.",
                ))
        except Exception:
            out.append(AdvisorItem(
                text="Сезонность недоступна — данных недостаточно",
                layer=ClaimLayer.FACT,
            ))
    elif category_context is not None and not seasonal:
        out.append(AdvisorItem(
            text="Сезонность: данных недостаточно — не выдумываю пики спроса",
            layer=ClaimLayer.FACT,
        ))

    if category_context is not None:
        trends = list(getattr(category_context, "trend_signals", None) or [])
        if trends:
            best = max(trends, key=lambda t: float(getattr(t, "confidence", 0) or 0))
            direction = _enum_val(getattr(best, "direction", None))
            conf = float(getattr(best, "confidence", 0) or 0)
            change = getattr(best, "change_pct", None)
            dir_ru = {"up": "рост", "down": "падение", "stable": "стабильно"}.get(direction, direction)
            change_s = f", {change:+.1f}%" if isinstance(change, (int, float)) else ""
            out.append(AdvisorItem(
                text=f"{_pick_opening(_OPENINGS_GROW, 2)}: тренд {dir_ru}{change_s} (confidence {conf:.0%})",
                layer=ClaimLayer.INFERENCE,
                why="Тренд из Category Intelligence / поиска — не абстрактный маркетинг.",
                evidence_ids=list(getattr(best, "evidence_ids", None) or [])[:4],
            ))

    for idx, rec in enumerate(market_recs or []):
        rtype = _enum_val(getattr(rec, "type", None))
        title = (getattr(rec, "title", None) or "").strip()
        action = (getattr(rec, "action", None) or title).strip()
        why = (getattr(rec, "reason", None) or "").strip()
        evid = list(getattr(rec, "evidence_ids", None) or [])
        conf = float(getattr(rec, "confidence", 0) or 0)
        if not action:
            continue
        # Never invent growth without evidence
        if not evid and conf < 0.50:
            continue
        if rtype == "MONITOR" or conf < 0.50:
            out.append(AdvisorItem(
                text=f"Наблюдение: {title or action}",
                layer=ClaimLayer.INFERENCE,
                why=why[:160] or "Данных мало для жёсткого действия.",
                evidence_ids=evid[:4],
                priority=4,
            ))
            continue
        low = action.lower()
        if any(x in low for x in (
            "просто рекламируй", "увеличьте продажи", "запустите маркетинг",
            "рынок растёт", "спрос растёт на",
        )):
            # allow only if evidence present
            if not evid:
                continue
        out.append(AdvisorItem(
            text=f"{_pick_opening(_OPENINGS_GROW, idx)}: {action}",
            layer=ClaimLayer.RECOMMENDATION,
            why=why[:160],
            evidence_ids=evid[:4],
            priority=min(4, max(1, int(getattr(rec, "priority", 3) or 3))),
        ))

    return out[:6]


def auto_action_eligible(
    plan: "AdvisorPlan | Any",
    *,
    explicit_seller_request: bool = False,
) -> bool:
    """
    Auto-Action only if confirmed diagnosis (or unspecified legacy plan).
    Candidate / NO_SYSTEMIC / funnel symptom → IDEA/CHECK, not Action.
    Explicit seller request can still create Action.
    """
    if explicit_seller_request:
        return True
    kind = str(getattr(plan, "main_problem_kind", "") or "")
    if kind in ("no_systemic", "funnel_symptom", "inconsistent"):
        return False
    if str((getattr(plan, "metadata", None) or {}).get("funnel_status") or "").upper() in (
        "INCONSISTENT", "INVALID",
    ):
        return False
    if kind == "problem":
        if hasattr(plan, "confirmed_problems"):
            try:
                return bool(plan.confirmed_problems())
            except Exception:
                return False
        return True
    return True


def _is_card_rewrite_text(text: str) -> bool:
    low = (text or "").lower()
    if "не перепис" in low or low.startswith("не ") or "не менять карточ" in low:
        return False
    return bool(_CARD_REWRITE_RE.search(text or ""))


def _is_generic_card_opt_item(item: "AdvisorItem") -> bool:
    """Generic card fill/expand — not allowed as ACTION/RECOMMENDATION under NO_SYSTEMIC."""
    if item is None:
        return False
    meta = item.metadata or {}
    if meta.get("source") == "eligibility":
        return False
    low = (item.text or "").lower()
    if "можно проверить описание" in low:
        return False
    if meta.get("field") in ("characteristics", "description", "photos", "infographic"):
        return True
    return any(p in low for p in (
        "характеристик — заполнено",
        "заполните характеристик",
        "заполните больше характеристик",
        "более развёрнутое описание",
        "подробное описание",
        "фото — сейчас",
        "инфографик",
        "добавьте больше фото",
    ))


def _build_priority(
    fixes: list[AdvisorItem],
    grow: list[AdvisorItem],
    problems: list[AdvisorItem],
) -> list[AdvisorItem]:
    """Приоритетные действия — max 1–3."""
    candidates: list[AdvisorItem] = []

    for it in fixes:
        if it.evidence_ids and (it.priority or 4) <= 3:
            candidates.append(it)

    for it in grow:
        if it.layer == ClaimLayer.RECOMMENDATION and (it.priority or 3) <= 3:
            candidates.append(it)

    if not candidates and problems:
        top = next(
            (
                p for p in problems
                if not (p.metadata or {}).get("empty_risks")
                and not (p.metadata or {}).get("weak")
                and not (p.metadata or {}).get("recurring_candidate")
                and p.layer != ClaimLayer.IDEA
                and _can_be_main_problem(p)
            ),
            None,
        )
        if top is not None:
            candidates.append(AdvisorItem(
                text=f"Разобрать: {top.text}",
                layer=ClaimLayer.RECOMMENDATION,
                priority=top.priority,
                evidence_ids=list(top.evidence_ids),
                why=top.why or "Самый сильный риск из отзывов — начни с него.",
            ))

    candidates.sort(key=lambda x: (x.priority or 4, -len(x.evidence_ids)))
    top = candidates[:3]
    for i, it in enumerate(top):
        if not it.why:
            if it.priority == 1:
                it.why = "Бьёт по оценке/товару сильнее остального."
            elif it.priority == 2:
                it.why = "Частый сигнал — дешевле закрыть сейчас, чем копить негатив."
            else:
                it.why = "Даст понятный эффект при относительно небольших усилиях."
        top[i] = AdvisorItem(
            text=it.text,
            layer=ClaimLayer.RECOMMENDATION,
            priority=it.priority,
            evidence_ids=list(it.evidence_ids),
            why=it.why,
            category=it.category,
            frequency=it.frequency,
            severity=it.severity,
            examples=list(it.examples),
        )
    return top


def _infer_bottleneck(
    product,
    score_data: dict | None,
    seller_data,
    problems: list[AdvisorItem],
    add: list[AdvisorItem],
) -> tuple[str, float, str, str, str, str]:
    """
    Узкое место + diagnosis locus.
    Возвращает (bottleneck, confidence, do_first, leave_alone, data_needed, locus).

    TRAFFIC / CONVERSION — только при наличии CTR/CVR (или показов+заказов).
    Без метрик воронки locus ≠ TRAFFIC/CONVERSION.
    """
    photos = getattr(product, "photos", None) or [] if product else []
    n_photos = len(photos) if not isinstance(photos, int) else int(photos)
    desc = (getattr(product, "description", None) or "") if product else ""
    chars = (getattr(product, "characteristics", None) or {}) if product else {}
    rating = getattr(product, "rating", None) if product else None
    feedbacks = getattr(product, "feedbacks", None) if product else None
    price = getattr(product, "price", None) if product else None
    score = (score_data or {}).get("score")

    real_risks = [
        p for p in problems
        if p.layer != ClaimLayer.IDEA
        and not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and _can_be_main_problem(p)
    ]
    candidate_risks = [
        p for p in problems
        if p.layer != ClaimLayer.IDEA
        and not (p.metadata or {}).get("empty_risks")
        and (
            (p.metadata or {}).get("weak")
            or (p.metadata or {}).get("recurring_candidate")
        )
    ]
    has_packaging_risk = any(
        "PACKAGING" in (p.category or "").upper()
        or "упаков" in (p.text or "").lower()
        for p in real_risks
    )
    has_product_risk = any(
        any(k in (p.category or "").upper() for k in (
            "QUALITY", "SIZE", "DEFECT", "PACKAGING", "MATERIAL", "PHOTO_MATCH",
        ))
        for p in real_risks
    )

    ctr = getattr(seller_data, "ctr", None) if seller_data else None
    cvr = getattr(seller_data, "cvr", None) if seller_data else None
    impressions = getattr(seller_data, "impressions", None) if seller_data else None
    orders = getattr(seller_data, "orders", None) if seller_data else None
    has_funnel_metrics = ctr is not None or cvr is not None
    has_traffic_pair = impressions is not None and (ctr is not None or orders is not None)

    data_needed_bits: list[str] = []
    if ctr is None:
        data_needed_bits.append("CTR")
    if cvr is None:
        data_needed_bits.append("CVR")
    if impressions is None:
        data_needed_bits.append("показы")
    if orders is None:
        data_needed_bits.append("заказы")
    if _funnel_rates_known(seller_data) and orders is None:
        data_needed_bits.append("клики")
        data_needed_bits.append("historical snapshots")

    card_healthy = (
        n_photos >= 8
        and len(desc) >= 300
        and len(chars) >= 5
        and (isinstance(score, (int, float)) and score >= 75 or score is None)
        and not real_risks
        and (rating is None or float(rating) >= 4.5)
    )

    if has_packaging_risk:
        top = next(
            (p for p in real_risks if "PACKAGING" in (p.category or "").upper()),
            real_risks[0],
        )
        action = _concrete_action_from_problem(top)
        return (
            "товар",
            min(0.85, 0.55 + 0.1 * len(real_risks)),
            (action.text if action else _display_problem_label(top))[:120],
            "рекламу и цену — пока не закрыт сигнал из отзывов",
            ", ".join(data_needed_bits[:3]) or "—",
            DiagnosisLocus.PACKAGING.value,
        )

    if has_product_risk:
        top = real_risks[0]
        action = _concrete_action_from_problem(top)
        return (
            "товар",
            min(0.85, 0.55 + 0.1 * len(real_risks)),
            (action.text if action else _display_problem_label(top))[:120],
            "рекламу и цену — пока не закрыт сигнал из отзывов",
            ", ".join(data_needed_bits[:3]) or "—",
            DiagnosisLocus.PRODUCT.value,
        )

    n_fb = 0
    try:
        n_fb = int(feedbacks) if feedbacks is not None else 0
    except (TypeError, ValueError):
        n_fb = 0
    # Verified low rating ≠ confirmed REVIEWS diagnosis. Tiny sample → candidate only.
    # Funnel rates already known → don't preempt CTR/CVR interpretation with rating.
    if (
        rating is not None and float(rating) < 4.5 and n_fb >= 10
        and not has_funnel_metrics
    ):
        if candidate_risks and not real_risks:
            cand = _clean_problem_label(candidate_risks[0].text)[:80]
            return (
                "доверие",
                0.50,
                f"рейтинг низкий, системной причины нет — проверить кандидата: {cand}",
                "масштабную рекламу и правки «на всякий случай»",
                ", ".join(data_needed_bits[:3]) or "—",
                DiagnosisLocus.REVIEWS.value,
            )
        return (
            "доверие",
            0.55,
            "рейтинг ниже 4.5 — проверить повторяемость жалоб, не делать auto-action",
            "масштабную рекламу",
            ", ".join(data_needed_bits[:3]) or "—",
            DiagnosisLocus.UNKNOWN.value,
        )

    if n_photos < 5 or not desc or len(desc) < 120:
        first = add[0].text[:120] if add else "дотянуть фото/описание/характеристики"
        return (
            "карточка",
            0.65,
            first,
            "рекламу до базовой готовности карточки",
            ", ".join(data_needed_bits[:3]) or "—",
            DiagnosisLocus.CARD.value,
        )

    # Здоровая карточка + нет экономики → ничего не трогай, собери метрики
    if card_healthy and not has_funnel_metrics:
        return (
            "неизвестно",
            0.55,
            "ничего не трогай в карточке — сначала CTR/CVR/заказы",
            "правки карточки и ставки вслепую",
            ", ".join(data_needed_bits[:4]) or "CTR, CVR, показы, заказы",
            DiagnosisLocus.UNKNOWN.value,
        )

    # Есть CTR/CVR → можно говорить про TRAFFIC / CONVERSION
    if has_funnel_metrics:
        try:
            ctr_f = float(ctr) if ctr is not None else None
            cvr_f = float(cvr) if cvr is not None else None
        except (TypeError, ValueError):
            ctr_f, cvr_f = None, None

        # CTR высокий + CVR высокий → не переписывать карточку вслепую
        if (
            ctr_f is not None and cvr_f is not None
            and ctr_f >= 2.0 and cvr_f >= 5.0
        ):
            return (
                "неизвестно",
                0.60,
                "воронка CTR/CVR выглядит здоровой — не переписывай карточку вслепую",
                "бессмысленные правки карточки при высоких CTR и CVR",
                ", ".join(
                    x for x in (
                        "остатки" if orders is None else "",
                        "себестоимость/маржа",
                        "цена конкурентов",
                    ) if x
                ) or "остатки, маржа (если есть cost+fees)",
                DiagnosisLocus.UNKNOWN.value,
            )

        no_bench = not _funnel_has_baseline(seller_data)
        no_orders = orders is None
        next_step = (
            "Добавить точное число кликов и заказов / период анализа — "
            "без этого причину потерь после клика не установить."
        )

        # Оба ниже внутренних порогов — OBSERVATION, не «оба низкие» без baseline
        if (
            ctr_f is not None and cvr_f is not None
            and ctr_f < 2.0 and cvr_f < 5.0
        ):
            first = (
                next_step
                if no_bench
                else (
                    f"CTR={ctr_f} и CVR={cvr_f} оба слабые — "
                    "сначала привлекательность выдачи, затем конверсия после клика"
                )
            )
            return (
                "неизвестно",
                0.62,
                first,
                "чинить только одно звено вслепую или лить трафик",
                ", ".join(data_needed_bits[:4]) or "—",
                DiagnosisLocus.TRAFFIC.value,
            )

        # CTR ниже порога → гипотеза привлекательности; без baseline не писать «низкий»
        if ctr_f is not None and ctr_f < 2.0 and (cvr_f is None or cvr_f >= 5.0):
            conf = 0.55 if cvr_f is None else 0.65
            if no_bench:
                first = (
                    f"CTR: {ctr_f}%. Без baseline/benchmark не утверждаю, "
                    "что показатель низкий или высокий. "
                    + (next_step if no_orders else "Проверить historical snapshots.")
                )
            elif cvr_f is None:
                first = (
                    f"CTR={ctr_f} низкий, CVR неизвестен — "
                    "гипотеза привлекательности выдачи; конверсию не утверждать"
                )
            else:
                first = (
                    f"CTR={ctr_f} низкий при CVR={cvr_f} — "
                    "сначала креатив/превью; конверсия после клика пока не главный сигнал"
                )
            return (
                "реклама",
                conf,
                first,
                "менять цену без данных; утверждать что фото бьёт CTR без метрики CTR",
                ", ".join(data_needed_bits[:4]) or ("CVR" if cvr_f is None else "—"),
                DiagnosisLocus.TRAFFIC.value,
            )

        # CTR известен + CVR ниже порога → OBSERVATION после клика, не «CVR низкий»
        if cvr_f is not None and cvr_f < 5.0:
            after_click = ctr_f is not None and ctr_f >= 2.0
            if no_bench:
                first = next_step
            elif after_click:
                first = (
                    f"CTR={ctr_f} нормальный/высокий, CVR={cvr_f} низкий → "
                    "симптом после клика: проверить карточку/цену/отзывы/соответствие"
                )
            elif ctr_f is None:
                first = (
                    f"CVR={cvr_f} низкий, CTR нет — симптом после клика; "
                    "проблему клика/CTR не утверждать"
                )
            else:
                first = f"разобрать конверсию карточки (CVR={cvr_f}) — оффер/доверие/цена"
            return (
                "неизвестно",
                0.65 if after_click else 0.60,
                first,
                "наращивать трафик вслепую",
                ", ".join(data_needed_bits[:4]) or "—",
                DiagnosisLocus.CONVERSION.value,
            )

        if has_traffic_pair:
            return (
                "неизвестно",
                0.50,
                (
                    _display_problem_label(real_risks[0])[:120]
                    if real_risks
                    else (add[0].text[:120] if add else "удерживать текущий фокус")
                ),
                "менять всё сразу",
                ", ".join(data_needed_bits[:2]) or "—",
                DiagnosisLocus.UNKNOWN.value,
            )

    # ad_spend без метрик ≠ TRAFFIC-диагноз
    if getattr(seller_data, "ad_spend", None) is not None if seller_data else False:
        if not has_funnel_metrics:
            return (
                "неизвестно",
                0.55,
                "снять CTR/CVR/показы за период — иначе не оценить рекламу",
                "повышение ставки вслепую",
                ", ".join(data_needed_bits[:4]) or "CTR, CVR, показы",
                DiagnosisLocus.UNKNOWN.value,
            )

    if price is None:
        return (
            "цена",
            0.50,
            "указать актуальную цену карточки",
            "выводы по марже без себестоимости",
            "цена, себестоимость, комиссия",
            DiagnosisLocus.PRICE.value,
        )

    # PRICE locus only with price-risk signal from reviews
    price_risk = any(
        "PRICE" in (p.category or "").upper() or "цен" in (p.text or "").lower()
        for p in real_risks
    )
    if price_risk:
        return (
            "цена",
            0.60,
            _display_problem_label(real_risks[0])[:120] if real_risks else "разобрать цену/ценность",
            "менять контент карточки вместо ценности/цены",
            "себестоимость, комиссия, цена конкурентов",
            DiagnosisLocus.PRICE.value,
        )

    if not has_funnel_metrics:
        return (
            "неизвестно",
            0.40,
            (
                _display_problem_label(real_risks[0])[:120]
                if real_risks
                else (
                    "Сейчас не менять карточку автоматически — сначала получить CTR/CVR/заказы."
                )
            ),
            "сильные заявления про CTR/CVR без цифр",
            ", ".join(data_needed_bits[:4]) or "CTR, CVR",
            DiagnosisLocus.UNKNOWN.value,
        )

    return (
        "неизвестно",
        0.45,
        (
            _display_problem_label(real_risks[0])[:120]
            if real_risks
            else (add[0].text[:120] if add else "удерживать текущий фокус")
        ),
        "менять всё сразу",
        ", ".join(data_needed_bits[:2]) or "—",
        DiagnosisLocus.UNKNOWN.value,
    )


_BOTTLENECK_NOT = {
    "товар": "не цена и не реклама",
    "карточка": "не товар и не цена",
    "доверие": "не реклама до закрытия причины оценки",
    "цена": "не контент карточки",
    "реклама": "не обязательно товар",
    "неизвестно": "системная проблема пока не доказана",
}

_LOCUS_NOT = {
    DiagnosisLocus.PRODUCT.value: "не цена и не трафик",
    DiagnosisLocus.PACKAGING.value: "не цена и не CTR",
    DiagnosisLocus.CARD.value: "не товар и не цена",
    DiagnosisLocus.REVIEWS.value: "не реклама до закрытия причины оценки",
    DiagnosisLocus.PRICE.value: "не контент карточки",
    DiagnosisLocus.TRAFFIC.value: "не обязательно товар",
    DiagnosisLocus.CONVERSION.value: "не обязательно ставка рекламы",
    DiagnosisLocus.UNKNOWN.value: "системная проблема пока не доказана",
}

_PRICE_CUT_RE = re.compile(
    r"сниз(ить|ь|ать)?\s+цен|уменьш\w*\s+цен|сделать\s+дешевле|скин?ь?\s+цен|"
    r"понизь?\s+цен|резать\s+цен|дроп\w*\s+цен|discount\s+price",
    re.IGNORECASE,
)

_ADS_PUSH_RE = re.compile(
    r"запуст\w*\s+реклам|увелич\w*\s+реклам|увелич\w*\s+ставк|поднят\w*\s+ставк|"
    r"просто\s+рекламир|больше\s+трафика|льйте?\s+бюджет|залейте?\s+реклам",
    re.IGNORECASE,
)

_PHOTO_BAD_RE = re.compile(
    r"фото\s+плох|плохие?\s+фото|качество\s+фото\s+низк|на\s+фото\s+видно\s+дефект|"
    r"компьютерн\w*\s+зрени|cv[\-\s]?анализ",
    re.IGNORECASE,
)

_OVERPRICED_RE = re.compile(
    r"цена\s+завышен|завышенн\w*\s+цен|дорого\s+относительно\s+рынка|"
    r"дороже\s+конкурент|overpriced",
    re.IGNORECASE,
)

_FREQ_CLAIM_RE = re.compile(r"\bчасто\b|\bпостоянно\b|\bмассово\b", re.IGNORECASE)

_CARD_REWRITE_RE = re.compile(
    r"описан|характеристик|назван|заголов|перепис\w*\s+карточ",
    re.IGNORECASE,
)

# Конкретные действия по категории причины (не «улучшите карточку»).
_CONCRETE_ACTIONS: dict[str, dict[str, str]] = {
    "PHOTO_MATCH": {
        "action": (
            "Заменить/переснять первое фото так, чтобы покупатель сразу видел "
            "реальный цвет и форму товара"
        ),
        "effect": "снизить риск ошибочного ожидания товара и связанных возвратов/негатива",
        "verify": "повторный разбор отзывов после накопления новых данных (фото/ожидание)",
    },
    "PHOTO": {
        "action": (
            "Заменить/переснять первое фото так, чтобы покупатель сразу видел "
            "реальный цвет и форму товара"
        ),
        "effect": "снизить риск ошибочного ожидания товара и связанных возвратов/негатива",
        "verify": "повторный разбор отзывов после накопления новых данных (фото/ожидание)",
    },
    "PACKAGING": {
        "action": "Усилить упаковку (доп. защита углов/вкладыш) и проверить типовую схему отгрузки",
        "effect": "снизить повторные жалобы на повреждение при доставке",
        "verify": "доля жалоб на упаковку в новых отзывах после смены схемы",
    },
    "QUALITY": {
        "action": "Разобрать конкретный дефект качества из отзывов и проверить партию/поставщика",
        "effect": "снизить давление на рейтинг от повторяющихся жалоб на качество",
        "verify": "динамика негатива по качеству на новой выборке отзывов",
    },
    "SIZE": {
        "action": "Уточнить размерную сетку и добавить замеры в карточку (не гадать по одному отзыву)",
        "effect": "меньше ошибок размера и возвратов из-за ожидания",
        "verify": "доля size-жалоб в новых отзывах после правки сетки",
    },
    "MATERIAL": {
        "action": "Уточнить материал в описании/характеристиках по факту товара (без маркетинговых обещаний)",
        "effect": "снизить mismatch ожидания по составу/фактуре",
        "verify": "повторяемость жалоб на материал в новых отзывах",
    },
    "DEFECT": {
        "action": "Проверить ОТК/партию по конкретному дефекту из evidence и отсечь брак",
        "effect": "меньше повторяющихся дефектов в отзывах",
        "verify": "частота дефект-сигналов после смены партии",
    },
    "PRICE_VALUE": {
        "action": "Сверить публичную цену с конкурентами/ценностью оффера — не резать цену вслепую",
        "effect": "ясность, есть ли реальный PRICE-разрыв или это шум отзывов",
        "verify": "наличие рыночного бенчмарка + реакция на цену в новых отзывах",
    },
}


def _clean_problem_label(text: str) -> str:
    """Убрать префиксы/открытия из текста сигнала для главной проблемы."""
    t = text or ""
    t = re.sub(r"^\[[^\]]+\]\s*", "", t)
    t = re.sub(
        r"^(В отзывах повторяется|Покупатели снова пишут про|Здесь явный риск|"
        r"Сигнал из отзывов|Похоже, есть сигнал):\s*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\s*\([^)]*обработан[^)]*\)\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*—\s*(единичный|повторяющийся).*$", "", t, flags=re.IGNORECASE)
    return t.strip() or (text or "").strip()


def _concrete_action_from_problem(problem: AdvisorItem) -> AdvisorItem | None:
    """Собрать конкретное ACTION из подтверждённой причины."""
    cat = (problem.category or "").upper()
    # OTHER praise/positive never generates ACTION
    if not _can_be_main_problem(problem):
        return None
    spec = _CONCRETE_ACTIONS.get(cat)
    if not spec:
        # generic cause-backed action — still concrete enough
        label = _display_problem_label(problem)
        if not label:
            return None
        spec = {
            "action": f"Закрыть подтверждённую причину: {label[:100]}",
            "effect": "снизить повтор тех же жалоб",
            "verify": "повторяемость той же темы в новых отзывах",
        }
    evid = list(problem.evidence_ids or [])
    why = (
        f"подтверждённый сигнал: {_display_problem_label(problem)[:120]}"
    )
    return AdvisorItem(
        text=spec["action"],
        layer=ClaimLayer.RECOMMENDATION,
        priority=min(2, int(problem.priority or 2)),
        evidence_ids=evid[:8],
        why=why,
        category=cat,
        metadata={
            "expected_effect": spec["effect"],
            "how_to_verify": spec["verify"],
            "from_confirmed_problem": True,
            "causal_role": CausalRole.CAUSE.value,
        },
    )


def _enrich_fixes_from_problems(
    fixes: list[AdvisorItem],
    problems: list[AdvisorItem],
) -> list[AdvisorItem]:
    """
    Если есть confirmed cause, но fixes пусты/нерелевантны — добавить concrete ACTION.
    Не дублировать уже существующие действия по категории.
    """
    confirmed = [
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and p.layer != ClaimLayer.IDEA
        and _can_be_main_problem(p)
    ]
    confirmed.sort(
        key=lambda p: (
            0 if _is_named_risk_type(p.category or "") else 1,
            int(p.priority or 4),
        )
    )
    if not confirmed:
        return fixes

    existing_cats = {(f.category or "").upper() for f in fixes if f.category}
    existing_blob = " ".join(f.text.lower() for f in fixes)
    out = list(fixes)
    for p in confirmed[:2]:
        cat = (p.category or "").upper()
        role = causal_role_for(cat)
        if role != CausalRole.CAUSE.value and cat not in _CONCRETE_ACTIONS:
            continue
        if cat and cat in existing_cats:
            continue
        generated = _concrete_action_from_problem(p)
        if generated is None:
            continue
        # avoid near-duplicates by keyword
        key = generated.text.lower()[:40]
        if key in existing_blob:
            continue
        out.insert(0, generated)
        existing_cats.add(cat)
        existing_blob += " " + generated.text.lower()
    # Enrich existing fixes missing effect/verify
    for f in out:
        meta = dict(f.metadata or {})
        if not meta.get("expected_effect"):
            cat = (f.category or "").upper()
            spec = _CONCRETE_ACTIONS.get(cat)
            if spec:
                meta["expected_effect"] = spec["effect"]
                meta.setdefault("how_to_verify", spec["verify"])
                f.metadata = meta
            elif f.why and not meta.get("how_to_verify"):
                meta["expected_effect"] = meta.get("expected_effect") or "снизить повтор того же риска"
                meta["how_to_verify"] = "повторный разбор отзывов после изменения"
                f.metadata = meta
    out.sort(key=lambda x: (x.priority or 4, -len(x.evidence_ids)))
    return out[:4]


def _build_main_problem_block(
    *,
    problems: list[AdvisorItem],
    locus: str,
    bottleneck: str,
    do_first: str,
    sample_reliability: str,
    confidence: float,
    seller_data,
    leave_alone_mode: bool,
    product=None,
    unit_economics: dict | None = None,
    market_compare: dict | None = None,
    card_healthy: bool = False,
) -> tuple[str, str, str, str, str]:
    """
    Returns (title, why, proof, kind, role).
    kind: problem | no_systemic | funnel_symptom
    """
    confirmed = [
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and p.layer != ClaimLayer.IDEA
        and _can_be_main_problem(p)
    ]
    # Named concrete risks always win over OTHER for the main axis.
    confirmed.sort(
        key=lambda p: (
            0 if _is_named_risk_type(p.category or "") else 1,
            int(p.priority or 4),
            -int((p.metadata or {}).get("evidence_count") or len(p.evidence_ids or [])),
        )
    )
    candidates = [
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and (
            (p.metadata or {}).get("weak")
            or (p.metadata or {}).get("recurring_candidate")
        )
        and p.layer != ClaimLayer.IDEA
    ]
    num_bits = _numeric_card_bits(product, seller_data)
    num_phrase = (", ".join(num_bits[:4])) if num_bits else ""

    if confirmed:
        top = confirmed[0]
        label = _display_problem_label(top)
        role = causal_role_for(top.category, locus)
        evid_n = int((top.metadata or {}).get("evidence_count") or len(top.evidence_ids or []))
        processed = int((top.metadata or {}).get("processed") or 0)
        why = (
            f"Это единственная ось: подтверждённый сигнал из отзывов по теме «{label}». "
            f"Другие риски слабее или не доказаны — не распылять правки."
        )
        if num_phrase:
            why += f" Контекст карточки: {num_phrase}."
        if role == CausalRole.CAUSE.value:
            why += " Это потенциальная причина (не симптом CTR/цены)."
        proof_bits = [
            f"evidence={evid_n}",
            f"frequency={top.frequency or '—'}",
            f"confidence≈{confidence:.0%}",
        ]
        if processed:
            proof_bits.append(f"из {processed} обработанных (не из feedbacks_card целиком)")
        if sample_reliability:
            proof_bits.append(f"выборка={sample_reliability}")
        if top.evidence_ids:
            proof_bits.append(f"ids={','.join(str(x) for x in top.evidence_ids[:3])}")
        if num_phrase:
            proof_bits.append(num_phrase)
        return label, why, "; ".join(proof_bits), "problem", role

    # Funnel symptom (metrics present) — не выдавать как «главную причину товара»
    has_ctr = seller_data is not None and getattr(seller_data, "ctr", None) is not None
    has_cvr = seller_data is not None and getattr(seller_data, "cvr", None) is not None
    if locus in (DiagnosisLocus.TRAFFIC.value, DiagnosisLocus.CONVERSION.value) and (has_ctr or has_cvr):
        ctr = getattr(seller_data, "ctr", None) if seller_data else None
        cvr = getattr(seller_data, "cvr", None) if seller_data else None
        try:
            ctr_f = float(ctr) if ctr is not None else None
            cvr_f = float(cvr) if cvr is not None else None
        except (TypeError, ValueError):
            ctr_f, cvr_f = None, None
        if ctr_f is not None and cvr_f is not None and ctr_f < 2.0 and cvr_f < 5.0:
            title = (
                "Системной проблемы по доступным данным пока не видно. "
                "Воронка измерима, но причина потерь пока не установлена."
            )
            why = (
                "CTR и CVR известны как OBSERVATION. "
                "Без baseline/benchmark не утверждаю, что показатели низкие. "
                "Заказы/история отсутствуют — causal diagnosis нельзя."
            )
            proof = f"CTR={ctr_f}, CVR={cvr_f}" + (f"; {num_phrase}" if num_phrase else "")
        elif locus == DiagnosisLocus.TRAFFIC.value:
            title = (
                "Системной проблемы по доступным данным пока не видно. "
                "Воронка измерима, но причина потерь пока не установлена."
            )
            why = (
                f"CTR: {ctr}%. Без baseline/benchmark не утверждаю, "
                "что показатель низкий или высокий.\n"
                "Причина (превью/креатив/оффер) не доказана.\n"
                "Нельзя утверждать проблему конверсии без CVR."
                if not has_cvr
                else (
                    f"CTR: {ctr}%. CVR: {cvr}%. Без baseline не классифицирую как низкий/высокий.\n"
                    "Сначала historical snapshots и заказы, не правки вслепую."
                )
            )
            proof = f"CTR={ctr}" + (f", CVR={cvr}" if cvr is not None else ", CVR=нет данных")
            if num_phrase:
                proof += f"; {num_phrase}"
        else:
            title = (
                "Системной проблемы по доступным данным пока не видно. "
                "Воронка измерима, но причина потерь пока не установлена."
            )
            why = (
                f"CVR: {cvr}%. Без baseline/benchmark не утверждаю, что показатель низкий.\n"
                "Нельзя утверждать проблему клика/CTR без метрики CTR.\n"
                "Для фактического числа заказов нужны clicks + orders."
                if not has_ctr
                else (
                    f"CTR: {ctr}%. CVR: {cvr}%. Без baseline не утверждаю low/high. "
                    "Воронка измерима; причина потерь после клика не установлена. "
                    "Для фактического числа заказов нужны clicks + orders."
                )
            )
            proof = (f"CTR={ctr}, " if ctr is not None else "CTR=нет данных, ") + f"CVR={cvr}"
            if num_phrase:
                proof += f"; {num_phrase}"
        return title, why, proof, "funnel_symptom", CausalRole.SYMPTOM.value

    # Leave-alone / healthy / insufficient
    cand_lines = []
    for c in candidates[:3]:
        cand_lines.append(f"- {_clean_problem_label(c.text)[:90]} — candidate")
    if leave_alone_mode or card_healthy or (not candidates and not confirmed):
        if card_healthy or leave_alone_mode:
            title = (
                "🟢 КАРТОЧКА В НОРМЕ — системной проблемы по доступным данным пока не видно."
                if not (has_ctr or has_cvr)
                else "🟢 КАРТОЧКА В НОРМЕ — системной проблемы пока не видно."
            )
            why = (
                "Ничего критичного менять не надо — подтверждённых системных рисков нет.\n"
                + (f"Цифры: {num_phrase}.\n" if num_phrase else "")
                + ("Что есть:\n" + "\n".join(cand_lines) + "\n" if cand_lines else "")
                + "Не менять: структуру карточки, цену и рекламу без нового сигнала.\n"
                + "Мониторить: рейтинг, новые жалобы, CTR/CVR/заказы при наличии."
            )
        else:
            title = (
                "Системной проблемы по доступным данным пока не видно."
                if not (has_ctr or has_cvr)
                else "Системной проблемы пока не видно."
            )
            why = (
                "Ничего критичного менять не надо — подтверждённых системных рисков нет.\n"
                + (f"Цифры: {num_phrase}.\n" if num_phrase else "")
                + ("Что есть:\n" + "\n".join(cand_lines) + "\n" if cand_lines else "")
                + "Что проверить: CTR/CVR/заказы (экономика), затем новые отзывы."
            )
        if market_compare and market_compare.get("text"):
            why += f"\nРынок: {market_compare['text']}."
        elif num_bits and any("цена" in b for b in num_bits) and not market_compare:
            why += "\nЦена сама по себе не доказывает проблему продаж — нет цен конкурентов."
        if unit_economics:
            if unit_economics.get("complete"):
                why += f"\n{unit_economics.get('text')}."
            elif unit_economics.get("text"):
                why += f"\n{unit_economics.get('text')}."
        proof = f"confirmed=0; confidence≈{confidence:.0%}"
        if num_phrase:
            proof += f"; {num_phrase}"
        return title, why, proof, "no_systemic", CausalRole.UNKNOWN.value

    title = (
        "Системной проблемы по доступным данным пока не видно."
        if not (has_ctr or has_cvr)
        else "Системной проблемы пока не видно."
    )
    why = (
        "Проблемы пока не доказаны — ничего критичного менять не надо.\n"
        + (f"Цифры: {num_phrase}.\n" if num_phrase else "")
        + "Что есть:\n" + ("\n".join(cand_lines) if cand_lines else "- слабые сигналы без systemic-порога")
        + "\nЧто проверить: дополнительные отзывы на повторяемость; не делать масштабных правок."
    )
    proof = f"candidates={len(candidates)}; confirmed=0; confidence≈{confidence:.0%}"
    return title, why, proof, "no_systemic", CausalRole.UNKNOWN.value


def _build_diagnosis(bottleneck: str, problems: list[AdvisorItem], leave_alone: str, locus: str = "") -> str:
    confirmed = [
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and p.layer != ClaimLayer.IDEA
        and _can_be_main_problem(p)
    ]
    confirmed.sort(
        key=lambda p: (
            0 if _is_named_risk_type(p.category or "") else 1,
            int(p.priority or 4),
        )
    )
    not_part = _LOCUS_NOT.get(locus) or _BOTTLENECK_NOT.get(bottleneck, "не доказана")

    # Funnel loci: симптом метрики, не «чинить всё» и не ложный «системно не доказано» без оговорок.
    if locus == DiagnosisLocus.TRAFFIC.value and not confirmed:
        return "симптом: CTR (привлекательность выдачи) — гипотеза воронки, не финальная причина товара"
    if locus == DiagnosisLocus.CONVERSION.value and not confirmed:
        return "симптом: CVR (после клика) — гипотеза воронки; причина карточки/цены/доверия не доказана"

    if (bottleneck == "неизвестно" or locus == DiagnosisLocus.UNKNOWN.value) and not confirmed:
        return "системная проблема не доказана — сначала не хватает данных, а не «чинить всё»"
    if confirmed:
        label = confirmed[0].category or "сигнал"
        role = causal_role_for(label, locus)
        role_note = ", потенциальная причина" if role == CausalRole.CAUSE.value else ""
        head = locus or bottleneck
        return f"{head} ({label}{role_note}), {not_part}"
    head = locus or bottleneck
    return f"{head}, {not_part}"


def _build_main_verdict(
    *,
    bottleneck: str,
    diagnosis: str,
    problems: list[AdvisorItem],
    sample_reliability: str,
    do_first: str,
    confidence: float,
    locus: str = "",
) -> str:
    confirmed = [
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and _can_be_main_problem(p)
    ]
    confirmed.sort(
        key=lambda p: (
            0 if _is_named_risk_type(p.category or "") else 1,
            int(p.priority or 4),
        )
    )
    loc_u = (locus or "").upper()
    bn = (bottleneck or "").strip().lower()
    if not confirmed and (bn in ("неизвестно", "unknown", "") or loc_u in ("UNKNOWN", "")):
        bits = ["Боттлнек пока не определён."]
    elif not confirmed and (bn == "карточка" or loc_u == "CARD"):
        bits = ["Боттлнек пока не определён. Область для проверки — карточка."]
    else:
        bits = [f"Узкое место — {bottleneck}" + (f" / {locus}" if locus else "") + "."]
    if confirmed:
        bits.append(f"Главный подтверждённый сигнал: {confirmed[0].text[:90]}.")
    else:
        bits.append("Системных подтверждённых рисков по отзывам нет — ничего критичного не доказано.")
    if sample_reliability:
        bits.append(f"Надёжность выборки: {sample_reliability}.")
    if do_first:
        if "ничего не трогай" in do_first.lower():
            bits.append("Карточка выглядит здоровой — ничего критичного, ничего не трогай, пока нет экономики.")
        else:
            bits.append(f"Первый шаг: {do_first[:100]}.")
    bits.append(f"Уверенность разбора: {confidence:.0%}.")
    return " ".join(bits)


def _actions_consistent_with_diagnosis(
    fixes: list[AdvisorItem],
    grow: list[AdvisorItem],
    priority: list[AdvisorItem],
    locus: str,
    problems: list[AdvisorItem],
    *,
    photos_analyzed: bool = False,
    has_market_price_evidence: bool = False,
    has_funnel_metrics: bool = False,
) -> tuple[list[AdvisorItem], list[AdvisorItem], list[AdvisorItem]]:
    """
    Не противоречить диагнозу:
    - без PRICE-локуса / price-risk — не советовать «снизить цену»
    - без TRAFFIC — не пушить рекламу универсально
    - без image analysis — не утверждать «фото плохие»
    - без рынка/конкурентов — не утверждать «цена завышена»
    - «часто» без HIGH frequency — режем
    """
    price_proven = locus == DiagnosisLocus.PRICE.value or any(
        "PRICE" in (p.category or "").upper()
        for p in problems
        if not (p.metadata or {}).get("weak") and not (p.metadata or {}).get("empty_risks")
    )
    recurring_ok = any((p.metadata or {}).get("recurring") for p in problems)

    def _ok(it: AdvisorItem) -> bool:
        blob = f"{it.text} {it.why}".lower()
        if _PRICE_CUT_RE.search(blob) and not price_proven:
            return False
        if locus not in (DiagnosisLocus.TRAFFIC.value, DiagnosisLocus.CONVERSION.value):
            if re.search(r"ctr\s*[:=]?\s*\d|cvr\s*[:=]?\s*\d|конверси\w+\s+\d+%", blob):
                return False
        if _ADS_PUSH_RE.search(blob) and locus != DiagnosisLocus.TRAFFIC.value and not has_funnel_metrics:
            return False
        if _PHOTO_BAD_RE.search(blob) and not photos_analyzed:
            return False
        if _OVERPRICED_RE.search(blob) and not has_market_price_evidence and not price_proven:
            return False
        # «часто» только при подтверждённом recurring (не candidate)
        if _FREQ_CLAIM_RE.search(blob) and not recurring_ok:
            return False
        return True

    return (
        [f for f in fixes if _ok(f)],
        [g for g in grow if _ok(g)],
        [p for p in priority if _ok(p)],
    )


def _priority_tier(
    fixes: list[AdvisorItem],
    problems: list[AdvisorItem],
    bottleneck: str,
) -> str:
    """Legacy numeric collapse. Prefer _resolve_priority_authority."""
    tier, _reason = _resolve_priority_authority(
        mp_kind="problem",
        problems=problems,
        fixes=fixes,
        bottleneck=bottleneck,
        card_healthy=False,
        funnel_inconsistent=False,
    )
    return tier


def _resolve_priority_authority(
    *,
    mp_kind: str,
    problems: list[AdvisorItem],
    fixes: list[AdvisorItem],
    bottleneck: str,
    card_healthy: bool,
    funnel_inconsistent: bool,
) -> tuple[str, str]:
    """Deterministic priority. Never emit a tier without a reason."""
    if funnel_inconsistent or mp_kind == "inconsistent":
        return "NONE", "данные воронки противоречивы — действие нельзя назначать"
    confirmed = [
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and p.layer != ClaimLayer.IDEA
    ]
    if mp_kind == "problem" and confirmed:
        severe = bottleneck in ("товар",) or any(
            any(k in (p.category or "").upper() for k in (
                "PACKAGING", "QUALITY", "DAMAGE", "DEFECT",
            ))
            for p in confirmed
        )
        best = 4
        for it in list(fixes) + confirmed:
            if it.priority:
                best = min(best, int(it.priority))
        if bottleneck == "товар" and confirmed:
            best = min(best, 1)
        if severe or best <= 1:
            return "P1", "подтверждённый критичный риск / операционный блок"
        if best <= 2:
            return "P2", "подтверждённая проблема с влиянием на бизнес"
        return "P2", "подтверждённая проблема с влиянием на бизнес"
    if mp_kind == "no_systemic" and card_healthy and not confirmed:
        return "NONE", "NO_ACTION — системной проблемы нет"
    if mp_kind in ("no_systemic", "funnel_symptom"):
        return "P3", "candidate/check — нужны данные или проверка сигнала"
    if not confirmed and not fixes:
        return "P4", "IDEA / мониторинг / низкий риск"
    return "P3", "candidate/check"


def _apply_no_systemic_policy(
    *,
    do_first: str,
    fixes: list[AdvisorItem],
    priority: list[AdvisorItem],
    add: list[AdvisorItem],
    problems: list[AdvisorItem],
    product,
    seller_data,
) -> tuple[str, list[AdvisorItem], list[AdvisorItem], list[AdvisorItem], str]:
    """NO_SYSTEMIC: candidate ≠ Action. Description/chars → IDEA/CHECK."""
    has_ctr = seller_data is not None and getattr(seller_data, "ctr", None) is not None
    has_cvr = seller_data is not None and getattr(seller_data, "cvr", None) is not None
    gather = (
        "Сейчас не менять карточку автоматически — сначала получить CTR/CVR/заказы."
    )
    if not has_ctr or not has_cvr:
        do_first = gather
    elif "не переписывай" in (do_first or "").lower():
        pass
    elif _funnel_rates_known(seller_data) and not _funnel_orders_known(seller_data):
        do_first = (
            "Добавить точное число кликов и заказов / период анализа — "
            "без этого причину потерь после клика не установить."
        )
    elif _is_card_rewrite_text(do_first):
        do_first = gather

    fixes = []  # no automatic card Action under NO_SYSTEMIC

    priority = [
        p for p in priority
        if not _is_card_rewrite_text(p.text or "")
        and "разобрать:" not in (p.text or "").lower()
    ]
    idea_text = (
        "Можно проверить описание и характеристики, но это пока не подтверждённая проблема"
    )
    add = [a for a in add if not _is_generic_card_opt_item(a)]
    chars = getattr(product, "characteristics", None) or {} if product is not None else {}
    n_chars = len(chars) if isinstance(chars, dict) else 0
    desc = (getattr(product, "description", None) or "") if product is not None else ""
    desc_cand = any(
        "DESCR" in (p.category or "").upper() or "описан" in (p.text or "").lower()
        for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and (
            (p.metadata or {}).get("weak")
            or (p.metadata or {}).get("recurring_candidate")
        )
    )
    extra_ideas: list[AdvisorItem] = []
    if n_chars == 0:
        extra_ideas.append(AdvisorItem(
            text=(
                "Характеристики: 0. Можно проверить как IDEA/CHECK, "
                "но влияние на продажи не доказано"
            ),
            layer=ClaimLayer.IDEA,
            why="count=0 ≠ sales impact; IDEA/CHECK, not Action",
            metadata={
                "action_class": "IDEA",
                "source": "eligibility",
            },
        ))
    need_desc_idea = desc_cand or not desc or (0 < n_chars < 5)
    if need_desc_idea:
        extra_ideas.append(AdvisorItem(
            text=idea_text,
            layer=ClaimLayer.IDEA,
            why="candidate/check ≠ confirmed diagnosis",
            metadata={"action_class": "IDEA", "source": "eligibility"},
        ))
    for idea in reversed(extra_ideas):
        if not any((idea.text or "")[:40].lower() in (a.text or "").lower() for a in add):
            add = [idea] + list(add)
    return do_first, fixes, priority, add, "P3"


def _expected_impact(bottleneck: str, fixes: list[AdvisorItem], sample_reliability: str, *, has_funnel: bool = False) -> str:
    if fixes:
        for f in fixes:
            effect = (f.metadata or {}).get("expected_effect")
            if effect:
                return str(effect)
    if not fixes and "ничего" not in bottleneck:
        if bottleneck == "неизвестно" and not has_funnel:
            return "без CTR/CVR/заказов эффект любых правок не измерить"
        return ""
    if sample_reliability in ("слабая", "нет выборки"):
        return "эффект предварительный — выборка отзывов слабая"
    mapping = {
        "товар": "снижение повторных жалоб и давления на рейтинг",
        "карточка": "рост кликабельности и понимания оффера",
        "доверие": "меньше отсева на этапе решения о покупке",
        "цена": "ясность маржи и позиции в выдаче — после цифр",
        "реклама": "управляемый ДРР вместо ставки вслепую",
        "неизвестно": (
            "сначала измеримость воронки, потом масштаб"
            if not has_funnel
            else "проверить гипотезу воронки точечно, без правок «всего»"
        ),
    }
    return mapping.get(bottleneck, "")


def _build_unproven(
    problems: list[AdvisorItem],
    grow: list[AdvisorItem],
    seller_data,
    category_context,
) -> list[AdvisorItem]:
    """⚠️ Не доказано: слабые сигналы, отсутствие экономики, нет роста рынка без evidence."""
    out: list[AdvisorItem] = []
    for p in problems:
        if (p.metadata or {}).get("weak"):
            out.append(AdvisorItem(
                text=p.text,
                layer=ClaimLayer.INFERENCE,
                why="Слабый/единичный сигнал ≠ recurring риск",
                evidence_ids=list(p.evidence_ids),
                metadata={"weak": True},
            ))
    if seller_data is None or getattr(seller_data, "ctr", None) is None:
        out.append(AdvisorItem(
            text="Проблема в рекламе/CTR не доказана — цифр воронки нет",
            layer=ClaimLayer.FACT,
        ))
    if seller_data is None or getattr(seller_data, "cvr", None) is None:
        out.append(AdvisorItem(
            text="Проблема в конверсии (CVR) не доказана — данных нет",
            layer=ClaimLayer.FACT,
        ))
    # Без CI/market evidence — не утверждаем рост рынка
    has_market_ev = False
    if category_context is not None:
        trends = list(getattr(category_context, "trend_signals", None) or [])
        seasonal = getattr(category_context, "seasonal_signals", None) or {}
        has_market_ev = bool(trends or seasonal)
    if not has_market_ev:
        # only note if grow tried to talk market without evidence
        for g in grow:
            if g.layer == ClaimLayer.RECOMMENDATION and not g.evidence_ids:
                out.append(AdvisorItem(
                    text="Рост рынка/спроса не доказан — нет Yandex/CI evidence",
                    layer=ClaimLayer.FACT,
                ))
                break
    # dedupe
    seen: set[str] = set()
    uniq: list[AdvisorItem] = []
    for it in out:
        key = it.text.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:6]


def _build_signals(problems: list[AdvisorItem]) -> list[AdvisorItem]:
    """SIGNAL layer: recurring → strong; weak → marked."""
    out: list[AdvisorItem] = []
    for p in problems:
        if (p.metadata or {}).get("empty_risks"):
            continue
        if p.layer == ClaimLayer.IDEA:
            continue
        meta = dict(p.metadata or {})
        meta["signal"] = True
        out.append(AdvisorItem(
            text=p.text,
            layer=ClaimLayer.INFERENCE if meta.get("weak") else ClaimLayer.FACT,
            priority=p.priority,
            evidence_ids=list(p.evidence_ids),
            why=p.why or ("recurring сигнал" if meta.get("recurring") else "слабый сигнал"),
            category=p.category,
            frequency=p.frequency,
            severity=p.severity,
            examples=list(p.examples),
            metadata=meta,
        ))
    return out[:8]


def _build_known_assumed_verify(
    facts: list[AdvisorItem],
    problems: list[AdvisorItem],
    add: list[AdvisorItem],
    unproven: list[AdvisorItem],
    data_needed: str,
    photos_analyzed: bool,
) -> tuple[list[AdvisorItem], list[AdvisorItem], list[AdvisorItem]]:
    known: list[AdvisorItem] = []
    # Priority facts first (reviews card/processed, unit economics, market)
    for f in facts:
        if f.layer != ClaimLayer.FACT:
            continue
        meta = f.metadata or {}
        if meta.get("card_vs_processed") or meta.get("unit_economics") is not None or meta.get("market_compare"):
            known.append(f)
    for f in facts:
        if f.layer != ClaimLayer.FACT:
            continue
        if f in known:
            continue
        if any(
            k in f.text.lower()
            for k in (
                "товар:", "бренд:", "артикул:", "фото", "описание:",
                "характеристик", "рейтинг", "отзыв", "цена", "score",
                "обработано", "public_price", "seller_price", "ctr", "cvr",
                "продаж", "заказ", "private", "экономик", "проанализир",
            )
        ):
            known.append(f)
    for p in problems:
        if (p.metadata or {}).get("empty_risks"):
            continue
        if (p.metadata or {}).get("weak"):
            continue
        if p.layer == ClaimLayer.IDEA:
            continue
        known.append(p)

    assumed: list[AdvisorItem] = []
    for p in problems:
        if (p.metadata or {}).get("weak") or (p.metadata or {}).get("recurring_candidate"):
            why = (
                "повторяющийся кандидат на малой выборке ≠ systemic"
                if (p.metadata or {}).get("recurring_candidate")
                else "единичный/слабый сигнал ≠ systemic"
            )
            assumed.append(AdvisorItem(
                text=p.text,
                layer=ClaimLayer.INFERENCE,
                why=why,
                evidence_ids=list(p.evidence_ids),
                metadata={
                    "weak": True,
                    "recurring_candidate": bool((p.metadata or {}).get("recurring_candidate")),
                },
            ))
    for a in add:
        if a.layer == ClaimLayer.IDEA:
            assumed.append(AdvisorItem(
                text=a.text,
                layer=ClaimLayer.IDEA,
                why=a.why or "IDEA по карточке, не жалоба покупателей",
                metadata={"from_add": True},
            ))
    for u in unproven:
        if u.layer in (ClaimLayer.INFERENCE, ClaimLayer.IDEA):
            assumed.append(u)

    to_verify: list[AdvisorItem] = []
    if data_needed and data_needed not in ("—", "-"):
        to_verify.append(AdvisorItem(
            text=data_needed,
            layer=ClaimLayer.FACT,
            metadata={"verify": True},
        ))
    if not photos_analyzed:
        to_verify.append(AdvisorItem(
            text="детальный CV-анализ не выполнялся — реальные фото/соответствие не разбирались",
            layer=ClaimLayer.FACT,
            metadata={"photo_honesty": True},
        ))
    # weak / candidate review themes → check more reviews
    if any(
        (p.metadata or {}).get("weak") or (p.metadata or {}).get("recurring_candidate")
        for p in problems
    ):
        to_verify.append(AdvisorItem(
            text="дополнительные отзывы и повторяемость формулировок (сейчас сигнал-кандидат)",
            layer=ClaimLayer.FACT,
            metadata={"verify": True},
        ))

    def _dedupe(items: list[AdvisorItem], limit: int) -> list[AdvisorItem]:
        seen: set[str] = set()
        out: list[AdvisorItem] = []
        for it in items:
            key = it.text.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            if len(out) >= limit:
                break
        return out

    return _dedupe(known, 14), _dedupe(assumed, 8), _dedupe(to_verify, 6)


def _build_not_recommended(
    *,
    locus: str,
    leave_alone: str,
    do_first: str,
    seller_data,
    problems: list[AdvisorItem],
    photos_analyzed: bool,
    has_market_price_evidence: bool = False,
    competitor_comparison=None,
    unit_economics: dict | None = None,
    competitive_diagnosis=None,
    main_problem_kind: str = "",
) -> list[AdvisorItem]:
    """NOT_RECOMMENDED: что сейчас не делать."""
    out: list[AdvisorItem] = []
    has_ctr = seller_data is not None and getattr(seller_data, "ctr", None) is not None
    has_cvr = seller_data is not None and getattr(seller_data, "cvr", None) is not None
    confirmed = [
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and p.layer != ClaimLayer.IDEA
    ]
    weak = [
        p for p in problems
        if (p.metadata or {}).get("weak") and not (p.metadata or {}).get("empty_risks")
    ]
    price_proven = locus == DiagnosisLocus.PRICE.value or any(
        "PRICE" in (p.category or "").upper()
        for p in confirmed
    )
    photo_proven = any(
        "PHOTO" in (p.category or "").upper()
        for p in confirmed
    )
    packaging_proven = any(
        "PACKAGING" in (p.category or "").upper()
        for p in confirmed
    ) or locus == DiagnosisLocus.PACKAGING.value

    if leave_alone:
        out.append(AdvisorItem(
            text=leave_alone,
            layer=ClaimLayer.RECOMMENDATION,
            why="не связано с текущим диагнозом или данных недостаточно",
            metadata={"not_recommended": True},
        ))

    if not has_ctr and not has_cvr:
        out.append(AdvisorItem(
            text="не запускать/увеличивать рекламу без CTR/CVR",
            layer=ClaimLayer.RECOMMENDATION,
            why="нет метрик воронки — TRAFFIC/CONVERSION не доказаны",
            metadata={"not_recommended": True},
        ))
    elif has_ctr and not has_cvr and locus == DiagnosisLocus.TRAFFIC.value:
        out.append(AdvisorItem(
            text="не утверждать проблему конверсии (CVR) — метрики нет",
            layer=ClaimLayer.RECOMMENDATION,
            why="есть только CTR; CVR неизвестен",
            metadata={"not_recommended": True},
        ))
    elif has_cvr and not has_ctr and locus == DiagnosisLocus.CONVERSION.value:
        out.append(AdvisorItem(
            text="не утверждать проблему клика/CTR — метрики нет",
            layer=ClaimLayer.RECOMMENDATION,
            why="есть только CVR; CTR неизвестен",
            metadata={"not_recommended": True},
        ))

    if not price_proven:
        if has_market_price_evidence:
            out.append(AdvisorItem(
                text="не менять цену без ценностного разрыва и проверки юнит-экономики",
                layer=ClaimLayer.RECOMMENDATION,
                why="есть рыночная выборка, но PRICE не доказан как причина",
                metadata={"not_recommended": True},
            ))
        else:
            out.append(AdvisorItem(
                text="не менять цену без данных о конкурентах/ценностном разрыве",
                layer=ClaimLayer.RECOMMENDATION,
                why="PRICE-риск не подтверждён и нет рыночного бенчмарка",
                metadata={"not_recommended": True},
            ))

    if main_problem_kind == "no_systemic":
        out.append(AdvisorItem(
            text="не переписывать описание как будто найден подтверждённый дефект",
            layer=ClaimLayer.RECOMMENDATION,
            why="диагноз NO_SYSTEMIC; description mismatch только candidate",
            metadata={"not_recommended": True},
        ))

    if photo_proven:
        out.append(AdvisorItem(
            text="не менять описание как главное действие — проблема доказанно в фото/соответствии",
            layer=ClaimLayer.RECOMMENDATION,
            why="главная ось — PHOTO_MATCH; описание сейчас отвлекает",
            metadata={"not_recommended": True},
        ))
        out.append(AdvisorItem(
            text="не снижать цену вместо исправления фото",
            layer=ClaimLayer.RECOMMENDATION,
            why="цена — симптом/шум, пока не закрыто визуальное соответствие",
            metadata={"not_recommended": True},
        ))

    if packaging_proven:
        out.append(AdvisorItem(
            text="не менять цену/рекламу вместо упаковки",
            layer=ClaimLayer.RECOMMENDATION,
            why="подтверждён PACKAGING — сначала логистика/защита",
            metadata={"not_recommended": True},
        ))

    if weak and not confirmed:
        out.append(AdvisorItem(
            text="не считать единичный/слабый отзыв системной проблемой",
            layer=ClaimLayer.RECOMMENDATION,
            why="candidate/check ≠ recurring systemic",
            metadata={"not_recommended": True},
        ))

    if not photos_analyzed:
        out.append(AdvisorItem(
            text="не утверждать, что фото плохие / видны дефекты / «улучшите фото» без CV",
            layer=ClaimLayer.RECOMMENDATION,
            why="качество и соответствие фото не проверялись",
            metadata={"not_recommended": True},
        ))
        out.append(AdvisorItem(
            text="не утверждать, что фото бьёт по CTR, если CTR нет",
            layer=ClaimLayer.RECOMMENDATION,
            why="без CTR нельзя связать превью с кликабельностью как факт",
            metadata={"not_recommended": True},
        ))

    if "не переписывай карточку" in (do_first or "").lower() or (
        has_ctr and has_cvr
        and float(getattr(seller_data, "ctr", 0) or 0) >= 2.0
        and float(getattr(seller_data, "cvr", 0) or 0) >= 5.0
    ):
        out.append(AdvisorItem(
            text="не переписывать карточку вслепую при здоровой воронке",
            layer=ClaimLayer.RECOMMENDATION,
            why="CTR и CVR высокие — искать другие ограничения по данным",
            metadata={"not_recommended": True},
        ))

    if "ничего не трогай" in (do_first or "").lower():
        out.append(AdvisorItem(
            text="не править карточку без экономики",
            layer=ClaimLayer.RECOMMENDATION,
            why="карточка выглядит здоровой; подтверждения критичного риска нет",
            metadata={"not_recommended": True},
        ))

    diag = competitive_diagnosis
    kind = ""
    do_not_cut = False
    extra_nr: list[str] = []
    if diag is not None:
        kind = getattr(diag, "kind", None) or (diag.get("kind") if isinstance(diag, dict) else "") or ""
        do_not_cut = bool(
            getattr(diag, "do_not_cut_price", False)
            if not isinstance(diag, dict)
            else diag.get("do_not_cut_price")
        )
        extra_nr = list(
            getattr(diag, "not_recommended", None)
            if not isinstance(diag, dict)
            else diag.get("not_recommended")
            or []
        )
    if do_not_cut or kind == "unit_econ_block":
        out.append(AdvisorItem(
            text="цену не снижать до конкурентного уровня без изменения себестоимости",
            layer=ClaimLayer.RECOMMENDATION,
            why="конкуренты дешевле, но вклад единицы уйдёт в минус",
            metadata={"not_recommended": True, "unit_econ_block": True},
        ))
    if kind == "trust_not_price":
        out.append(AdvisorItem(
            text="не снижать цену вместо исправления доверия/карточки",
            layer=ClaimLayer.RECOMMENDATION,
            why="цена в рынке, слабое конкурентное преимущество по доверию",
            metadata={"not_recommended": True},
        ))
    if kind == "insufficient":
        out.append(AdvisorItem(
            text="не писать «ты дороже рынка» по 1–2 слабым результатам",
            layer=ClaimLayer.RECOMMENDATION,
            why="выборки недостаточно для рыночного сравнения",
            metadata={"not_recommended": True},
        ))
        out.append(AdvisorItem(
            text="не утверждать, что конкуренты получают больше продаж",
            layer=ClaimLayer.RECOMMENDATION,
            why="продаж конкурентов в источниках нет",
            metadata={"not_recommended": True},
        ))
    for nr in extra_nr[:3]:
        if nr:
            out.append(AdvisorItem(
                text=str(nr),
                layer=ClaimLayer.RECOMMENDATION,
                why="competitor evidence",
                metadata={"not_recommended": True},
            ))

    seen: set[str] = set()
    uniq: list[AdvisorItem] = []
    for it in out:
        key = it.text.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:9]


def _apply_competitive_overlay(
    *,
    mp_title: str,
    mp_why: str,
    mp_kind: str,
    do_first: str,
    leave_alone: str,
    competitive_diagnosis,
    problems: list,
    card_healthy: bool,
) -> tuple[str, str, str, str, str]:
    """Дополнить главную проблему competitor evidence. Не перебивает confirmed RI."""
    diag = competitive_diagnosis
    if diag is None:
        return mp_title, mp_why, mp_kind, do_first, leave_alone
    kind = getattr(diag, "kind", None) or (diag.get("kind") if isinstance(diag, dict) else "") or ""
    insight = getattr(diag, "insight", None) or (diag.get("insight") if isinstance(diag, dict) else "") or ""
    hint = getattr(diag, "action_hint", None) or (diag.get("action_hint") if isinstance(diag, dict) else None)
    photo_proven = any(
        "PHOTO" in str(getattr(p, "category", "") or "").upper()
        for p in (problems or [])
        if not (getattr(p, "metadata", None) or {}).get("weak")
        and not (getattr(p, "metadata", None) or {}).get("empty_risks")
    )
    if kind == "trust_not_price":
        if mp_kind in ("no_systemic", "funnel_symptom") and insight:
            mp_title = insight
            mp_kind = "problem"
        elif insight and insight not in (mp_why or ""):
            mp_why = (mp_why + " " + insight).strip()
        if photo_proven:
            leave_alone = "цену — сначала закрыть доверие/фото"
        elif hint:
            do_first = hint
        return mp_title, mp_why, mp_kind, do_first, leave_alone
    if kind == "unit_econ_block" and insight:
        if insight not in (mp_why or ""):
            mp_why = (mp_why + " " + insight).strip()
        if mp_kind in ("no_systemic", "funnel_symptom", "") or "системной" in (mp_title or "").lower():
            mp_title = insight
            mp_kind = "problem"
        if hint:
            do_first = hint
        return mp_title, mp_why, mp_kind, do_first, leave_alone
    if kind in ("funnel_entry", "funnel_after_click", "price_candidate") and mp_kind in (
        "no_systemic", "funnel_symptom",
    ):
        if insight and insight not in (mp_why or ""):
            mp_why = (mp_why + " " + insight).strip()
        return mp_title, mp_why, mp_kind, do_first, leave_alone
    if kind == "no_action" and (card_healthy or mp_kind == "no_systemic"):
        if insight and "системной" not in (mp_title or "").lower():
            mp_title = mp_title or "Системной проблемы пока не видно."
        return mp_title, mp_why, "no_systemic", do_first, leave_alone
    if kind == "insufficient" and insight and insight not in (mp_why or ""):
        mp_why = (mp_why + " " + insight).strip()
    quality = getattr(diag, "kind", None)
    if quality == "insufficient":
        leave_alone = leave_alone or "цену — данных по рынку недостаточно"
    return mp_title, mp_why, mp_kind, do_first, leave_alone


def _build_why_points(
    *,
    diagnosis: str,
    problems: list[AdvisorItem],
    facts: list[AdvisorItem],
    bottleneck: str,
    seller_data,
) -> list[str]:
    points: list[str] = []
    if diagnosis:
        points.append(diagnosis)
    for p in problems:
        if (p.metadata or {}).get("empty_risks") or (p.metadata or {}).get("weak"):
            continue
        if p.layer == ClaimLayer.IDEA:
            continue
        points.append(f"сигнал: {p.text[:100]}")
        if len(points) >= 4:
            break
    # key card facts
    for key in ("рейтинг", "отзыв", "public_price", "ctr", "cvr"):
        for f in facts:
            if key in f.text.lower() and "нет данных" not in f.text.lower():
                points.append(f"факт: {f.text[:90]}")
                break
        if len(points) >= 6:
            break
    if seller_data is not None:
        ctr = getattr(seller_data, "ctr", None)
        cvr = getattr(seller_data, "cvr", None)
        if ctr is not None and cvr is not None:
            points.append(f"факт воронки: CTR={ctr}, CVR={cvr}")
        elif ctr is not None:
            points.append(f"факт воронки: CTR={ctr}, CVR нет")
    if bottleneck and bottleneck != "неизвестно" and f"узкое место: {bottleneck}" not in points:
        points.append(f"узкое место: {bottleneck}")
    # dedupe
    seen: set[str] = set()
    out: list[str] = []
    for p in points:
        k = p.lower().strip()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out[:6]


def build_advisor_plan(
    product=None,
    score_data: dict | None = None,
    seller_data=None,
    review_assessment=None,
    category_context=None,
    card_recommendations: list | None = None,
    market_recommendations: list | None = None,
    review_recommendations: list | None = None,
    decisions: list | None = None,
    metric_snapshots: list | None = None,
    competitor_comparison=None,
    competitive_diagnosis=None,
) -> AdvisorPlan:
    """
    Собрать AdvisorPlan из уже существующих структур.
    Не ходит в сеть и не вызывает LLM.
    """
    # Cap processed by available card_feedbacks before building
    assessment = review_assessment
    if assessment is not None and product is not None:
        try:
            available = getattr(product, "feedbacks", None)
            processed = int(getattr(assessment, "processed_count", 0) or 0)
            if available is not None and processed > int(available):
                # soft-cap in metadata only; do not mutate caller's object deeply
                from dataclasses import replace as _dc_replace
                try:
                    assessment = _dc_replace(assessment, processed_count=int(available))
                except Exception:
                    pass
        except (TypeError, ValueError):
            pass

    facts = _build_facts(product, score_data, seller_data, assessment)
    problems = _build_problems(assessment)
    problems = [p for p in problems if p.layer != ClaimLayer.IDEA]
    strengths = _build_strengths(product, score_data, assessment)
    fixes = _build_fixes(assessment, review_recommendations)
    add = _build_add(product, card_recommendations)
    grow = _build_grow(category_context, market_recommendations, seller_data)
    priority = _build_priority(fixes, grow, problems)
    bottleneck, conf, do_first, leave_alone, data_needed, locus = _infer_bottleneck(
        product, score_data, seller_data, problems, add,
    )

    has_funnel = bool(
        seller_data is not None
        and (
            getattr(seller_data, "ctr", None) is not None
            or getattr(seller_data, "cvr", None) is not None
        )
    )
    has_market_price = bool(
        category_context is not None
        and (
            getattr(category_context, "competitor_events", None)
            or getattr(category_context, "evidence", None)
        )
    )
    our_price = None
    if seller_data is not None and getattr(seller_data, "price", None) is not None:
        our_price = getattr(seller_data, "price", None)
    elif product is not None:
        our_price = getattr(product, "price", None)
    market_compare = _market_price_compare(our_price, category_context) if has_market_price else None
    if competitor_comparison is not None:
        try:
            from backend.competitor_intelligence.comparison import to_advisor_market_dict
            cmp_dict = to_advisor_market_dict(competitor_comparison)
            if cmp_dict:
                market_compare = cmp_dict
        except Exception:
            pass
    if market_compare is None and our_price is not None and not has_market_price:
        # explicit honesty: alone price ≠ sales problem
        market_compare = None
    unit_econ = compute_unit_economics(seller_data, product)

    from backend.ai.funnel_consistency import validate_seller_funnel
    funnel_consistency = validate_seller_funnel(seller_data)
    funnel_inconsistent = not funnel_consistency.is_ok

    # Thin funnel layer: attach diagnosis for chat/report consumers (does not
    # rewrite bottleneck / RI ranking — only metadata enrichment).
    funnel_diag_meta: dict[str, Any] | None = None
    if funnel_inconsistent:
        funnel_diag_meta = {
            "case": "INCONSISTENT",
            "locus": DiagnosisLocus.UNKNOWN.value,
            "action_class": "NONE",
            **funnel_consistency.to_dict(),
        }
    else:
        try:
            from backend.ai.funnel_economics import (
                card_signals_from_product,
                compute_funnel_metrics,
                diagnose_funnel,
                review_signals_from_risks,
            )
            _fm = compute_funnel_metrics(seller_data=seller_data)
            _card = card_signals_from_product(product, market_compare=market_compare)
            _revs = review_signals_from_risks(problems)
            _fd = diagnose_funnel(_fm, unit=unit_econ, card=_card, reviews=_revs, product=product)
            funnel_diag_meta = _fd.to_dict()
        except Exception:
            funnel_diag_meta = None

    # Thin Dynamic Analytics layer (history/trends) — metadata only.
    dynamics_meta: dict[str, Any] | None = None
    try:
        from backend.ai.dynamic_analytics import (
            attach_dynamics_metadata,
            build_series_from_snapshots,
        )
        pts = build_series_from_snapshots(metric_snapshots or [])
        dynamics_meta = attach_dynamics_metadata(
            pts,
            period="7d",
            seller_data=seller_data,
            product=product,
            card_healthy=False,  # refined after card_healthy computed below
        )
    except Exception:
        dynamics_meta = None

    # Card healthy flag (same heuristic as bottleneck leave-alone path)
    photos = getattr(product, "photos", None) or [] if product else []
    n_photos = len(photos) if not isinstance(photos, int) else int(photos)
    desc = (getattr(product, "description", None) or "") if product else ""
    chars = (getattr(product, "characteristics", None) or {}) if product else {}
    rating = getattr(product, "rating", None) if product else None
    score = (score_data or {}).get("score")
    real_risks_pre = [
        p for p in problems
        if p.layer != ClaimLayer.IDEA
        and not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
    ]
    card_healthy = (
        n_photos >= 8
        and len(desc) >= 300
        and len(chars) >= 5
        and (isinstance(score, (int, float)) and score >= 75 or score is None)
        and not real_risks_pre
        and (rating is None or float(rating) >= 4.5)
    )

    if competitive_diagnosis is None and competitor_comparison is not None:
        try:
            from backend.competitor_intelligence.reasoning import diagnose_competitive
            competitive_diagnosis = diagnose_competitive(
                competitor_comparison,
                review_assessment=assessment,
                funnel_diag=funnel_diag_meta,
                unit_econ=unit_econ,
                seller_data=seller_data,
                card_healthy=bool(card_healthy and not real_risks_pre),
            )
        except Exception:
            competitive_diagnosis = None

    # Refine dynamics with card_healthy (still metadata-only).
    if dynamics_meta is not None and (metric_snapshots or card_healthy):
        try:
            from backend.ai.dynamic_analytics import (
                attach_dynamics_metadata,
                build_series_from_snapshots,
            )
            pts = build_series_from_snapshots(metric_snapshots or [])
            dynamics_meta = attach_dynamics_metadata(
                pts,
                period="7d",
                seller_data=seller_data,
                product=product,
                card_healthy=bool(card_healthy and not real_risks_pre),
            )
        except Exception:
            pass

    # Unit economics / market facts
    unit_econ_override: dict[str, Any] | None = None
    if unit_econ.get("complete"):
        facts.append(AdvisorItem(
            text=str(unit_econ["text"]),
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.PRIVATE.value, "unit_economics": True},
        ))
        # Allow diagnosis «экономика единицы» when complete and no stronger product risk
        if locus == DiagnosisLocus.UNKNOWN.value and not real_risks_pre:
            try:
                if float(unit_econ.get("margin_pct") or 0) < 15:
                    unit_econ_override = {
                        "diagnosis": (
                            f"экономика единицы: маржа {unit_econ.get('margin_pct')}% "
                            f"(вклад {unit_econ.get('contribution')} ₽) — узкое место в юнит-экономике"
                        ),
                        "bottleneck": "цена",
                        "locus": DiagnosisLocus.PRICE.value,
                        "do_first": (
                            f"разобрать юнит-экономику: вклад {unit_econ.get('contribution')} ₽ "
                            f"({unit_econ.get('margin_pct')}% маржа) — не резать цену вслепую"
                        ),
                    }
            except (TypeError, ValueError):
                pass
    elif seller_data is not None and unit_econ.get("text"):
        facts.append(AdvisorItem(
            text=str(unit_econ["text"]),
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.PRIVATE.value, "unit_economics": False},
        ))
    note = _implied_clicks_note(seller_data)
    if note:
        facts.append(AdvisorItem(
            text=note,
            layer=ClaimLayer.INFERENCE,
            metadata={
                "source": FactSource.PRIVATE.value,
                "derived": True,
                "not_observed": True,
                "implied_clicks": True,
            },
        ))
        facts.append(AdvisorItem(
            text="Для фактического числа заказов нужны clicks + orders.",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.PRIVATE.value, "not_observed": True},
        ))
    if (
        _funnel_rates_known(seller_data)
        and not _funnel_has_baseline(seller_data, metric_snapshots)
    ):
        ctr_v = getattr(seller_data, "ctr", None)
        cvr_v = getattr(seller_data, "cvr", None)
        facts.append(AdvisorItem(
            text=(
                f"Наблюдение по CTR {ctr_v}%: без baseline/benchmark не утверждаю, "
                "что показатель низкий или высокий."
            ),
            layer=ClaimLayer.INFERENCE,
            metadata={"funnel_observation": True, "not_problem": True},
        ))
        facts.append(AdvisorItem(
            text=(
                f"Наблюдение по CVR {cvr_v}%: без baseline/benchmark не утверждаю, "
                "что показатель низкий."
            ),
            layer=ClaimLayer.INFERENCE,
            metadata={"funnel_observation": True, "not_problem": True},
        ))
    if market_compare and market_compare.get("text"):
        facts.append(AdvisorItem(
            text=f"Сравнение с рынком: {market_compare['text']}",
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.RESEARCH.value, "market_compare": True},
        ))
    elif our_price is not None and not market_compare:
        facts.append(AdvisorItem(
            text=(
                f"Публичная/seller цена {our_price} ₽ есть; цен конкурентов нет — "
                "цена сама по себе не доказывает проблему продаж"
            ),
            layer=ClaimLayer.FACT,
            metadata={"source": FactSource.CARD.value, "price_alone": True},
        ))

    # Consistency: actions must not contradict diagnosis
    fixes, grow, priority = _actions_consistent_with_diagnosis(
        fixes, grow, priority, locus, problems,
        photos_analyzed=False,
        has_market_price_evidence=bool(market_compare) or has_market_price,
        has_funnel_metrics=has_funnel,
    )
    # Confirmed cause → concrete ACTION (action→why→effect→verify)
    fixes = _enrich_fixes_from_problems(fixes, problems)
    if fixes and any((f.metadata or {}).get("from_confirmed_problem") for f in fixes):
        do_first = fixes[0].text[:160]
        # Rebuild priority with concrete action first
        priority = _build_priority(fixes, grow, problems)
        unit_econ_override = None  # product cause wins over thin margin

    # Healthy / leave-alone: drop template card IDEAs from action path
    if "ничего не трогай" in (do_first or "").lower():
        add = []
        priority = [AdvisorItem(
            text="Ничего не трогай в карточке — собери CTR/CVR/заказы",
            layer=ClaimLayer.RECOMMENDATION,
            priority=2,
            why="Карточка выглядит здоровой; без экономики правки — гадание",
            metadata={
                "expected_effect": "без CTR/CVR/заказов эффект любых правок не измерить",
                "how_to_verify": "снять CTR/CVR/заказы за период и пересмотреть диагноз",
            },
        )] + [p for p in priority if "ничего не трогай" not in p.text.lower()][:2]

    processed_n = 0
    if assessment is not None:
        processed_n = int(getattr(assessment, "processed_count", 0) or 0)
        # Cap for sample reliability display
        if product is not None:
            try:
                available = getattr(product, "feedbacks", None)
                if available is not None and processed_n > int(available):
                    processed_n = int(available)
            except (TypeError, ValueError):
                pass
    sample_rel = sample_reliability_label(processed_n) if assessment is not None else ""
    if unit_econ_override:
        bottleneck = unit_econ_override["bottleneck"]
        locus = unit_econ_override["locus"]
        do_first = unit_econ_override["do_first"]
        diagnosis = unit_econ_override["diagnosis"]
    else:
        diagnosis = _build_diagnosis(bottleneck, problems, leave_alone, locus=locus)

    # Cap plan confidence by small-sample / low-evidence calibration.
    # Weak/candidate caps must not drag down a confirmed recurring signal.
    confirmed_caps = [
        float((p.metadata or {}).get("max_confidence", 1.0) or 1.0)
        for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
    ]
    candidate_caps = [
        float((p.metadata or {}).get("max_confidence", 1.0) or 1.0)
        for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and (
            (p.metadata or {}).get("weak")
            or (p.metadata or {}).get("recurring_candidate")
        )
    ]
    if confirmed_caps:
        conf = min(conf, min(confirmed_caps))
    elif candidate_caps:
        conf = min(conf, min(candidate_caps))
    small_sample = processed_n < _SMALL_SAMPLE_N
    has_candidate = any(
        (p.metadata or {}).get("recurring_candidate")
        or (p.metadata or {}).get("candidate_language")
        or (
            (p.metadata or {}).get("weak")
            and int((p.metadata or {}).get("evidence_count", 0) or 0)
            <= _SMALL_SAMPLE_EVIDENCE_CAP
        )
        for p in problems
        if not (p.metadata or {}).get("empty_risks")
    )
    # Explicit rule: processed/card <10 and evidence_count<=2 → band ≤ medium
    if small_sample and any(
        int((p.metadata or {}).get("evidence_count", 0) or 0) <= _SMALL_SAMPLE_EVIDENCE_CAP
        for p in problems
        if not (p.metadata or {}).get("empty_risks")
    ):
        conf = min(conf, _SMALL_SAMPLE_CONF_CAP)

    conf_label, conf_why_base = _confidence_band(conf)
    if small_sample and has_candidate:
        # never «высокая» on tiny sample with ≤2 evidence
        if conf_label == "высокая":
            conf_label = "средняя"
        conf_why = (
            f"{conf_why_base}; малая выборка (n={processed_n}) — "
            "только кандидат/проверка, не systemic"
        )
    elif sample_rel in ("слабая", "нет выборки"):
        conf_label = "низкая" if conf < 0.70 else conf_label
        if conf_label == "высокая":
            conf_label = "средняя"
        conf_why = f"{conf_why_base}; выборка отзывов: {sample_rel}"
    elif sample_rel:
        conf_why = f"{conf_why_base}; выборка: {sample_rel}"
    else:
        conf_why = conf_why_base
    if not has_funnel and locus not in (
        DiagnosisLocus.PACKAGING.value,
        DiagnosisLocus.PRODUCT.value,
        DiagnosisLocus.CARD.value,
        DiagnosisLocus.REVIEWS.value,
        DiagnosisLocus.PRICE.value,
    ):
        conf_why += "; без CTR/CVR нельзя измерить влияние на продажи"

    main_verdict = _build_main_verdict(
        bottleneck=bottleneck,
        diagnosis=diagnosis,
        problems=problems,
        sample_reliability=sample_rel,
        do_first=do_first,
        confidence=conf,
        locus=locus,
    )
    leave_alone_mode = "ничего не трогай" in (do_first or "").lower()
    mp_title, mp_why, mp_proof, mp_kind, mp_role = _build_main_problem_block(
        problems=problems,
        locus=locus,
        bottleneck=bottleneck,
        do_first=do_first,
        sample_reliability=sample_rel,
        confidence=conf,
        seller_data=seller_data,
        leave_alone_mode=leave_alone_mode,
        product=product,
        unit_economics=unit_econ,
        market_compare=market_compare,
        card_healthy=card_healthy and not real_risks_pre,
    )
    if competitive_diagnosis is not None:
        mp_title, mp_why, mp_kind, do_first, leave_alone = _apply_competitive_overlay(
            mp_title=mp_title,
            mp_why=mp_why,
            mp_kind=mp_kind,
            do_first=do_first,
            leave_alone=leave_alone,
            competitive_diagnosis=competitive_diagnosis,
            problems=problems,
            card_healthy=bool(card_healthy and not real_risks_pre),
        )
    # no_systemic: candidate ≠ Action; description/chars → IDEA/CHECK
    if mp_kind == "no_systemic":
        do_first, fixes, priority, add, tier = _apply_no_systemic_policy(
            do_first=do_first,
            fixes=fixes,
            priority=priority,
            add=add,
            problems=problems,
            product=product,
            seller_data=seller_data,
        )
        bottleneck = "неизвестно"
        locus = DiagnosisLocus.UNKNOWN.value
        if "готовности карточки" in (leave_alone or "").lower():
            leave_alone = "правки карточки, цену и рекламу без нового evidence"
        diagnosis = _build_diagnosis(bottleneck, problems, leave_alone, locus=locus)
        main_verdict = _build_main_verdict(
            bottleneck=bottleneck,
            diagnosis=diagnosis,
            problems=problems,
            sample_reliability=sample_rel,
            do_first=do_first,
            confidence=conf,
            locus=locus,
        )
    elif mp_kind == "funnel_symptom" and do_first:
        _df = do_first.lower()
        if ("описан" in _df and any(k in _df for k in ("добав", "разверн", "развёрн"))) or (
            "характеристик" in _df and "добав" in _df
        ):
            do_first = (
                "Сначала собрать CTR/CVR/заказы (без них нельзя измерить эффект правок); "
                "расширение описания — идея, не подтверждённый диагноз"
            )
            fixes = [
                f for f in fixes
                if not (
                    "описан" in (f.text or "").lower()
                    and any(k in (f.text or "").lower() for k in ("добав", "разверн", "развёрн"))
                )
            ]
            main_verdict = _build_main_verdict(
                bottleneck=bottleneck,
                diagnosis=diagnosis,
                problems=problems,
                sample_reliability=sample_rel,
                do_first=do_first,
                confidence=conf,
                locus=locus,
            )
    if funnel_inconsistent:
        mp_kind = "inconsistent"
        mp_title = "Данные воронки противоречат друг другу."
        mp_why = funnel_consistency.human_message or mp_title
        mp_proof = funnel_consistency.check_line or "Проверьте клики, заказы и период."
        mp_role = CausalRole.UNKNOWN.value
        bottleneck = "неизвестно"
        locus = DiagnosisLocus.UNKNOWN.value
        conf = min(float(conf or 0.0), 0.35)
        conf_label = "низкая"
        conf_why = "данные противоречивы"
        do_first = "Проверить клики, заказы и период анализа."
        leave_alone = "цену, рекламу и карточку"
        data_needed = "проверить клики / заказы / период"
        fixes = []
        priority = []
        diagnosis = mp_title
        main_verdict = funnel_consistency.human_message or mp_title
    if mp_kind != "no_systemic":
        tier, _pr = _resolve_priority_authority(
            mp_kind=mp_kind,
            problems=problems,
            fixes=fixes,
            bottleneck=bottleneck,
            card_healthy=bool(card_healthy and not real_risks_pre),
            funnel_inconsistent=funnel_inconsistent,
        )

    card_fb = None
    if product is not None and getattr(product, "feedbacks", None) is not None:
        try:
            card_fb = int(getattr(product, "feedbacks"))
        except (TypeError, ValueError):
            card_fb = None
    comm_n = 0
    if competitor_comparison is not None:
        try:
            comm_n = int(getattr(competitor_comparison, "commercial_n", 0) or 0)
        except (TypeError, ValueError):
            comm_n = 0
    has_funnel_pair = bool(
        seller_data is not None
        and getattr(seller_data, "ctr", None) is not None
        and getattr(seller_data, "cvr", None) is not None
    )
    has_sales = seller_data is not None and getattr(seller_data, "sales", None) is not None
    has_orders = seller_data is not None and getattr(seller_data, "orders", None) is not None
    confirmed_n = len([
        p for p in problems
        if not (p.metadata or {}).get("empty_risks")
        and not (p.metadata or {}).get("weak")
        and p.layer != ClaimLayer.IDEA
        and _can_be_main_problem(p)
    ])
    has_recurring = any((p.metadata or {}).get("recurring") for p in problems)
    unit_complete = bool(isinstance(unit_econ, dict) and unit_econ.get("complete"))
    conf, conf_label, conf_why = _apply_diagnostic_confidence_authority(
        conf=conf,
        processed_n=processed_n,
        card_feedbacks=card_fb,
        has_funnel_pair=has_funnel_pair,
        has_sales=has_sales,
        has_orders=has_orders,
        commercial_n=comm_n,
        photos_analyzed=False,
        confirmed_n=confirmed_n,
        has_recurring=has_recurring,
        unit_complete=unit_complete,
        mp_kind=mp_kind,
    )
    price_position = "UNKNOWN"
    if competitor_comparison is not None and comm_n > 0:
        price_position = str(
            getattr(competitor_comparison, "price_position", None) or "UNKNOWN"
        )
    rating_sample_candidate = None
    rating_candidate = None
    if product is not None:
        try:
            rt = getattr(product, "rating", None)
            fb = getattr(product, "feedbacks", None)
            if rt is not None and float(rt) < 4.5:
                if fb is not None and int(fb) < 10:
                    rating_sample_candidate = {
                        "rating": float(rt),
                        "feedbacks": int(fb),
                    }
                else:
                    rating_candidate = {"rating": float(rt)}
        except (TypeError, ValueError):
            rating_sample_candidate = None
            rating_candidate = None
    if comm_n <= 0 and product is not None and getattr(product, "price", None) is not None:
        facts.append(AdvisorItem(
            text=(
                f"Цена: {getattr(product, 'price')} ₽. Рыночная позиция не определена — "
                "commercial fields конкурентов не подтверждены"
            ),
            layer=ClaimLayer.FACT,
            metadata={"price_position": "UNKNOWN", "source": FactSource.CARD.value},
        ))

    impact = _expected_impact(bottleneck, fixes, sample_rel, has_funnel=has_funnel)
    unproven = _build_unproven(problems, grow, seller_data, category_context)
    signals = _build_signals(problems)
    known, assumed, to_verify = _build_known_assumed_verify(
        facts, problems, add, unproven, data_needed, photos_analyzed=False,
    )
    assumed_keys = {a.text.lower().strip() for a in assumed}
    if rating_sample_candidate:
        rt = rating_sample_candidate["rating"]
        fb = rating_sample_candidate["feedbacks"]
        cand_txt = (
            f"Слабый сигнал: рейтинг {rt} при {fb} отзывах; "
            "выборка слишком мала для системного вывода"
        )
        if cand_txt.lower() not in assumed_keys:
            assumed.append(AdvisorItem(
                text=cand_txt,
                layer=ClaimLayer.INFERENCE,
                why="verified rating ≠ diagnostic confidence",
                metadata={
                    "weak": True,
                    "rating_sample_candidate": True,
                    "action_class": "CHECK",
                },
            ))
            assumed_keys.add(cand_txt.lower())
    if rating_candidate and mp_kind in ("no_systemic", "funnel_symptom"):
        rt = rating_candidate["rating"]
        cand_txt = f"CANDIDATE: рейтинг {rt}; системность причины не доказана."
        if cand_txt.lower() not in assumed_keys:
            assumed.append(AdvisorItem(
                text=cand_txt,
                layer=ClaimLayer.INFERENCE,
                why="средний рейтинг ≠ confirmed PROBLEM",
                metadata={"weak": True, "rating_candidate": True, "action_class": "CHECK"},
            ))
            assumed_keys.add(cand_txt.lower())
    kept_strengths: list[AdvisorItem] = []
    for s in strengths:
        if _is_complaint_theme_text(s.text):
            core = re.sub(r"^покупатели отмечают:\s*", "", s.text, flags=re.I).strip()
            if core and core.lower() not in assumed_keys:
                assumed.append(AdvisorItem(
                    text=core,
                    layer=ClaimLayer.INFERENCE,
                    why="кандидат из отзывов ≠ «уже хорошо»",
                    evidence_ids=list(s.evidence_ids),
                    metadata={"weak": True, "from_false_strength": True},
                ))
                assumed_keys.add(core.lower())
            continue
        kept_strengths.append(s)
    strengths = kept_strengths
    if mp_kind in ("no_systemic", "funnel_symptom") and assessment is not None:
        for p in list(getattr(assessment, "problems", None) or []):
            if not (
                _is_named_complaint_signal(p)
                or _is_complaint_theme_text(_problem_text_blob(p))
            ):
                continue
            label = (getattr(p, "label", None) or getattr(p, "claim", None) or "").strip()
            if not label or label.lower() in assumed_keys:
                continue
            assumed.append(AdvisorItem(
                text=label,
                layer=ClaimLayer.INFERENCE,
                why="candidate review signal — проверить повторяемость",
                evidence_ids=list(getattr(p, "evidence_ids", None) or [])[:4],
                metadata={"weak": True, "review_candidate": True},
            ))
            assumed_keys.add(label.lower())
    not_recommended = _build_not_recommended(
        locus=locus,
        leave_alone=leave_alone,
        do_first=do_first,
        seller_data=seller_data,
        problems=problems,
        photos_analyzed=False,
        has_market_price_evidence=bool(market_compare) or has_market_price,
        competitor_comparison=competitor_comparison,
        unit_economics=unit_econ,
        competitive_diagnosis=competitive_diagnosis,
        main_problem_kind=mp_kind,
    )
    why_points = _build_why_points(
        diagnosis=diagnosis,
        problems=problems,
        facts=facts,
        bottleneck=bottleneck,
        seller_data=seller_data,
    )
    # Symptom vs cause note in why
    if mp_role == CausalRole.CAUSE.value and mp_kind == "problem":
        why_points = [f"главная причина: {mp_title}"] + [
            w for w in why_points if "главная причина" not in w.lower()
        ]
    elif mp_kind == "funnel_symptom":
        why_points = [f"симптом воронки (не причина товара): locus={locus}"] + why_points
    why_points = why_points[:6]
    if funnel_inconsistent:
        why_points = [
            "данные воронки противоречат друг другу — causal diagnosis нельзя",
            funnel_consistency.check_line or "проверьте клики, заказы и период",
        ]

    # Healthy card + missing economics → explicit leave-alone action
    if (not funnel_inconsistent) and "ничего не трогай" in (do_first or "").lower():
        if not any("ничего не трогай" in (p.text or "").lower() for p in priority):
            priority = [AdvisorItem(
                text="Ничего не трогай в карточке — собери CTR/CVR/заказы",
                layer=ClaimLayer.RECOMMENDATION,
                priority=3,
                why="Карточка выглядит здоровой; без экономики правки — гадание",
                metadata={"action_class": "CHECK"},
            )] + priority[:2]
        if mp_kind == "no_systemic":
            tier = "P3"
        else:
            tier = "P2"

    decision_items: list[dict[str, Any]] = []
    if decisions:
        for d in decisions[:5]:
            status = _enum_val(getattr(d, "status", None)) or str(getattr(d, "status", ""))
            topic = getattr(d, "topic", None) or ""
            selected = getattr(d, "selected_solution_id", None)
            choice = getattr(d, "seller_choice", None) or getattr(d, "choice", None)
            item = {
                "topic": topic,
                "status": status,
                "selected_solution_id": selected,
                "seller_choice": choice,
            }
            decision_items.append(item)
            if selected or status:
                facts.append(AdvisorItem(
                    text=f"Решение по «{topic or 'теме'}»: status={status}"
                         + (f", selected={selected}" if selected else "")
                         + (f", choice={choice}" if choice else ""),
                    layer=ClaimLayer.FACT,
                    evidence_ids=list(getattr(d, "evidence_ids", None) or [])[:4],
                    metadata={"source": FactSource.RESEARCH.value, "decision": True},
                ))

    rates_known = _funnel_rates_known(seller_data)
    orders_known = _funnel_orders_known(seller_data)
    has_baseline = _funnel_has_baseline(seller_data, metric_snapshots)
    funnel_interpretation = None
    if funnel_inconsistent:
        funnel_interpretation = "INCONSISTENT"
        conf_label = "низкая"
        conf = min(float(conf or 0.0), 0.35)
        conf_why = "данные противоречивы"
    elif rates_known and mp_kind in ("no_systemic", "funnel_symptom"):
        funnel_interpretation = (
            "FUNNEL_MEASURABLE"
            if rates_known
            else "FUNNEL_INSUFFICIENT_FOR_CAUSAL_DIAGNOSIS"
        )
        if not orders_known or not has_baseline:
            funnel_interpretation = "FUNNEL_MEASURABLE"
    if (not funnel_inconsistent) and rates_known and not orders_known and not has_baseline:
        conf_why = (
            "rates известны, но нет historical/benchmark/order evidence"
        )
        if conf_label == "высокая":
            conf_label = "средняя"
            conf = min(conf, 0.65)

    healthy_now = bool(card_healthy and not real_risks_pre)
    tier, priority_why = _resolve_priority_authority(
        mp_kind=mp_kind,
        problems=problems,
        fixes=fixes,
        bottleneck=bottleneck,
        card_healthy=healthy_now,
        funnel_inconsistent=funnel_inconsistent,
    )

    meta: dict[str, Any] = {
        "has_reviews": assessment is not None,
        "has_category": category_context is not None,
        "decisions": len(decisions or []),
        "decision_items": decision_items,
        "bottleneck": bottleneck,
        "diagnosis_locus": locus,
        "confidence": conf,
        "confidence_label": conf_label,
        "priority_tier": tier,
        "priority_reason": priority_why,
        "sample_reliability": sample_rel,
        "photos_analyzed": False,
        "card_healthy": healthy_now,
        "auto_action_eligible": mp_kind == "problem" and not funnel_inconsistent,
        "price_position": price_position,
        "rating_sample_candidate": rating_sample_candidate,
        "rating_candidate": rating_candidate,
        "funnel_rates_known": rates_known,
        "funnel_orders_known": orders_known,
        "funnel_has_baseline": has_baseline,
        "funnel_interpretation": funnel_interpretation,
        "funnel_status": (
            funnel_consistency.status if funnel_inconsistent else "CONSISTENT"
        ),
        "funnel_consistency": funnel_consistency.to_dict(),
        "funnel_causal_status": (
            None if funnel_inconsistent else (
                "FUNNEL_INSUFFICIENT_FOR_CAUSAL_DIAGNOSIS"
                if rates_known and (not orders_known or not has_baseline)
                else None
            )
        ),
        "diagnostic_confidence_policy": conf_label,
        "primary_action_class": (
            "NONE" if funnel_inconsistent or tier == "NONE"
            else ("ACTION" if mp_kind == "problem"
                  else ("CHECK" if mp_kind in ("no_systemic", "funnel_symptom") else "CHECK"))
        ),
        "unit_economics": unit_econ,
        "market_compare": market_compare,
        "funnel_diagnosis": funnel_diag_meta,
        "dynamic_analytics": dynamics_meta,
        "competitor_comparison": (
            competitor_comparison.as_dict()
            if competitor_comparison is not None and hasattr(competitor_comparison, "as_dict")
            else competitor_comparison
        ),
        "competitive_diagnosis": (
            competitive_diagnosis.as_dict()
            if competitive_diagnosis is not None and hasattr(competitive_diagnosis, "as_dict")
            else competitive_diagnosis
        ),
        "chain": "FACT→SIGNAL→CONFIDENCE→DIAGNOSIS→ACTION→PRIORITY→NOT_RECOMMENDED",
    }

    plan = AdvisorPlan(
        facts=facts,
        problems=problems,
        strengths=strengths,
        unproven=unproven,
        fixes=fixes,
        add=add,
        grow=grow,
        priority=priority,
        signals=signals,
        known=known,
        assumed=assumed,
        to_verify=to_verify,
        not_recommended=not_recommended,
        why_points=why_points,
        metadata=meta,
        bottleneck=bottleneck,
        diagnosis_locus=locus,
        confidence=conf,
        confidence_label=conf_label,
        confidence_why=conf_why,
        do_first=do_first,
        leave_alone=leave_alone,
        data_needed=data_needed,
        main_verdict=main_verdict,
        diagnosis=diagnosis,
        priority_tier=tier,
        priority_why=priority_why,
        sample_reliability=sample_rel,
        expected_impact=impact,
        photos_analyzed=False,
        main_problem=mp_title,
        main_problem_why=mp_why,
        main_problem_proof=mp_proof,
        main_problem_kind=mp_kind,
        main_problem_role=mp_role,
    )
    meta["diagnosis_snapshot"] = plan.diagnosis_snapshot()
    return plan


#: Быстрые вопросы для Telegram / multi-turn (тот же Advisor context).
ADVISOR_QUICK_PROMPTS: dict[str, str] = {
    "detail": "Разобери товар подробнее: главный вывод, где проблема, что подтверждено, что делать и приоритет.",
    "fix": "Как исправить главные проблемы этого товара? Дай конкретные шаги с приоритетом.",
    "add": "Что добавить в карточку? Только идеи по данным карточки, без выдуманных жалоб покупателей.",
    "grow": "Как увеличить продажи по этому товару? Только если есть данные или сезонность, без воды.",
}


def advisor_focus_section(key: str) -> str | None:
    """Какой блок подчеркнуть для quick-action."""
    return {
        "detail": None,
        "fix": "fixes",
        "add": "add",
        "grow": "grow",
    }.get(key)


def format_advisor_focus(plan: AdvisorPlan, focus: str | None) -> str:
    """Ответ на quick-action: полный план или один блок + приоритет."""
    if not focus:
        return plan.format_plain()
    subset = AdvisorPlan(
        facts=plan.facts[:4] if focus != "add" else [],
        problems=plan.problems if focus == "fixes" else [],
        strengths=plan.strengths[:3] if focus == "fixes" else [],
        unproven=plan.unproven if focus == "fixes" else [],
        fixes=plan.fixes if focus == "fixes" else [],
        add=plan.add if focus == "add" else [],
        grow=plan.grow if focus == "grow" else [],
        priority=plan.priority,
        signals=plan.signals if focus == "fixes" else [],
        known=plan.known if focus == "fixes" else [],
        assumed=plan.assumed if focus == "fixes" else [],
        to_verify=plan.to_verify if focus == "fixes" else [],
        not_recommended=plan.not_recommended if focus == "fixes" else [],
        why_points=plan.why_points if focus == "fixes" else [],
        metadata=plan.metadata,
        bottleneck=plan.bottleneck,
        diagnosis_locus=plan.diagnosis_locus,
        confidence=plan.confidence,
        confidence_label=plan.confidence_label,
        confidence_why=plan.confidence_why,
        do_first=plan.do_first,
        leave_alone=plan.leave_alone,
        data_needed=plan.data_needed,
        main_verdict=plan.main_verdict if focus == "fixes" else "",
        diagnosis=plan.diagnosis if focus == "fixes" else "",
        priority_tier=plan.priority_tier,
        sample_reliability=plan.sample_reliability,
        expected_impact=plan.expected_impact,
        photos_analyzed=plan.photos_analyzed,
    )
    text = subset.format_plain()
    return text or plan.format_plain()
