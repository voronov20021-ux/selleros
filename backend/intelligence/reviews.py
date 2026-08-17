"""
reviews.py — Review Intelligence v2.

Анализ реальных отзывов товара:
    нормализация → дедуп → cap MAX_REVIEW_TEXTS → signal type/sentiment
    → grouping → SellerProblem (category/issue/freq/severity/…) → ReviewAssessment

Без HTTP. Без выдуманных отзывов/цифр/%.
Seller isolation через user_hash.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from collections import defaultdict

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    ProblemDirection,
    ReviewAssessment,
    ReviewIssue,
    ReviewSentiment,
    ReviewSignal,
    ReviewSignalType,
    SellerAction,
    SellerProblem,
    SignalFrequency,
    SignalSeverity,
)

log = logging.getLogger("selleros.intelligence.reviews")

try:
    from backend.config import MAX_REVIEW_TEXTS as _MAX_REVIEW_TEXTS
except Exception:  # pragma: no cover
    _MAX_REVIEW_TEXTS = 60

#: Минимум отзывов в группе для recurring issue (не один слабый отзыв).
_MIN_RECURRING_COUNT = 2
#: Минимальная доля группы среди валидных отзывов.
_MIN_RECURRING_RATIO = 0.10
#: Минимальный confidence группы.
_MIN_ISSUE_CONFIDENCE = 0.40

_POSITIVE_MARKERS = (
    "отличн", "хорош", "супер", "класс", "рекоменд", "доволен", "довольн",
    "качественн", "быстр", "удобн", "красив", "нравит", "восторг", "идеальн",
    "кайф", "бомб", "огонь", "топ ", "люблю", "прекрасн", "замечательн",
)
_NEGATIVE_MARKERS = (
    "плох", "ужас", "брак", "сломал", "не работ", "разочаров", "обман",
    "не советую", "не рекоменду", "ужасн", "отвратительн", "грязн", "дешев",
    "вернул", "возврат", "жалоб", "маломер", "большемер", "не подош",
    "дорого", "переплат", "помят", "поврежд", "поцарап", "скол", "опоздал",
    "ожидал", "не соответств", "не хвата",
)

#: v2 expandable categories + keywords. Новые категории можно дописать сюда.
_TYPE_KEYWORDS: dict[ReviewSignalType, tuple[str, ...]] = {
    ReviewSignalType.PACKAGING: (
        "упаковк", "коробк", "пакет", "пленк", "плёнк", "замотан", "пузырчат",
        "болта", "фиксац", "защитн слой", "мятая короб",
    ),
    ReviewSignalType.UNPACKING: (
        "распаков", "открыл короб", "достал из", "разбирал упаков",
        "трудно открыть", "скотч", "заклеен",
    ),
    ReviewSignalType.COMPLETENESS: (
        "комплект", "не хвата", "недокомплект", "без инструкц", "инструкц",
        "в наборе нет", "положили не все", "забыли положить",
    ),
    ReviewSignalType.PRODUCT_QUALITY: (
        "качеств", "материал", "прочн", "надежн", "надёжн", "брак", "дефект",
        "износ", "рассыпал", "потрескал", "дешев материал",
    ),
    ReviewSignalType.QUALITY: (  # back-compat alias
        "ткан",
    ),
    ReviewSignalType.FUNCTIONALITY: (
        "не работ", "функци", "кнопк", "заряд", "батаре", "ошибк", "глючит",
        "перестал", "не включа", "не заряж",
    ),
    ReviewSignalType.PHOTO_MATCH: (
        "не как на фото", "не как на картинк", "фото врёт", "на фото друг",
        "отличается от фото", "цвет не как", "на фото выглядел",
    ),
    ReviewSignalType.DESCRIPTION_MATCH: (
        "не соответств описан", "в описании написано", "обманули описан",
        "характеристик не совпад", "в карточке указано", "описание врёт",
    ),
    ReviewSignalType.SIZE: (
        "размер", "маломер", "большемер", "мал ", "велик", "не подошел размер",
        "не подошёл размер", "таблица размеров", "см ",
    ),
    ReviewSignalType.DESIGN: (
        "дизайн", "внешн вид", "выгляд", "эстети", "стильн", "некрасив",
        "цвет ", "расцветк",
    ),
    ReviewSignalType.APPEARANCE: (
        "внешн", "отличаетс",
    ),
    ReviewSignalType.DAMAGE: (
        "поврежд", "помят", "разбит", "трещин", "скол", "дырк", "порван",
        "поцарапан", "деформир",
    ),
    ReviewSignalType.LOGISTICS: (
        "доставк", "привез", "срок достав", "курьер", "логист", "опоздал",
        "ждал посыл", "пункт выдач",
    ),
    ReviewSignalType.DELIVERY: (
        "транспортн компан",
    ),
    ReviewSignalType.EXPECTATIONS: (
        "ожидал", "думал что", "рассчитывал", "не то что ждал",
        "разочаровани", "ожидани",
    ),
    ReviewSignalType.PRICE_VALUE: (
        "цена", "дорого", "дешево", "дешёво", "за свои деньги", "соотношен",
        "переплат", "стоит своих",
    ),
    ReviewSignalType.SERVICE: (
        "продавц", "поддержк", "ответ", "сервис", "магазин", "консультант",
    ),
}

_DETECT_PRIORITY = [
    ReviewSignalType.DAMAGE,
    ReviewSignalType.PACKAGING,
    ReviewSignalType.UNPACKING,
    ReviewSignalType.COMPLETENESS,
    ReviewSignalType.FUNCTIONALITY,
    ReviewSignalType.PHOTO_MATCH,
    ReviewSignalType.DESCRIPTION_MATCH,
    ReviewSignalType.SIZE,
    ReviewSignalType.PRODUCT_QUALITY,
    ReviewSignalType.DESIGN,
    ReviewSignalType.EXPECTATIONS,
    ReviewSignalType.LOGISTICS,
    ReviewSignalType.PRICE_VALUE,
    ReviewSignalType.SERVICE,
    ReviewSignalType.QUALITY,
    ReviewSignalType.APPEARANCE,
    ReviewSignalType.DELIVERY,
    ReviewSignalType.OTHER,
]


def normalize_review_text(text: str) -> str:
    """Нормализация текста отзыва для сравнения/дедупа."""
    if not text:
        return ""
    t = text.lower().strip()
    t = t.replace("ё", "е")
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^\w\s\-.,!?%]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _text_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_review_text(text).encode("utf-8")).hexdigest()[:24]


def _detect_sentiment(text: str) -> ReviewSentiment:
    low = text.lower()
    pos = sum(1 for m in _POSITIVE_MARKERS if m in low)
    neg = sum(1 for m in _NEGATIVE_MARKERS if m in low)
    if pos == 0 and neg == 0:
        return ReviewSentiment.UNKNOWN
    if pos > neg:
        return ReviewSentiment.POSITIVE
    if neg > pos:
        return ReviewSentiment.NEGATIVE
    return ReviewSentiment.NEUTRAL


def _detect_signal_type(text: str) -> ReviewSignalType:
    low = text.lower()
    scores: list[tuple[int, ReviewSignalType]] = []
    for stype, kws in _TYPE_KEYWORDS.items():
        hit = sum(1 for kw in kws if kw in low)
        if hit:
            scores.append((hit, stype))
    if not scores:
        return ReviewSignalType.OTHER
    scores.sort(key=lambda x: x[0], reverse=True)
    best_hit = scores[0][0]
    top = [s for h, s in scores if h == best_hit]
    for pref in _DETECT_PRIORITY:
        if pref in top:
            return pref
    return scores[0][1]


def _extract_claim(text: str, signal_type: ReviewSignalType, sentiment: ReviewSentiment) -> str:
    """Короткий claim из реального текста — без выдуманных фактов."""
    norm = normalize_review_text(text)
    if not norm:
        return "пустой отзыв"
    chunk = re.split(r"[.!?]", norm)[0].strip() or norm
    chunk = chunk[:120].rstrip()
    label = {
        ReviewSentiment.POSITIVE: "похвала",
        ReviewSentiment.NEGATIVE: "жалоба",
        ReviewSentiment.NEUTRAL: "замечание",
        ReviewSentiment.UNKNOWN: "упоминание",
    }[sentiment]
    return f"{label} [{signal_type.value}]: {chunk}"


def _signal_confidence(text: str, sentiment: ReviewSentiment, signal_type: ReviewSignalType) -> float:
    """Confidence зависит от качества исходных данных, не от выдумок."""
    base = 0.35
    n = len(normalize_review_text(text))
    if n >= 40:
        base += 0.15
    if n >= 100:
        base += 0.10
    if sentiment != ReviewSentiment.UNKNOWN:
        base += 0.10
    if signal_type != ReviewSignalType.OTHER:
        base += 0.15
    return round(min(0.85, base), 4)


def _relevance_score(row: dict) -> float:
    """Для cap ≤ MAX_REVIEW_TEXTS: длиннее + ясный тип/sentiment выше."""
    text = row.get("text") or ""
    sentiment = _detect_sentiment(row.get("norm") or text)
    stype = _detect_signal_type(row.get("norm") or text)
    score = float(len(row.get("norm") or ""))
    if sentiment != ReviewSentiment.UNKNOWN:
        score += 40
    if stype != ReviewSignalType.OTHER:
        score += 60
    if sentiment == ReviewSentiment.NEGATIVE:
        score += 20
    return score


class ReviewIntelligence:
    """
    Движок анализа отзывов.

        assessment = await ri.analyze(reviews, category=..., article=..., user_hash=...)
    """

    def __init__(self, store: IIntelligenceStore | None = None) -> None:
        self._store = store

    async def analyze(
        self,
        reviews: list[dict] | None,
        *,
        category: str | None = None,
        article: str | None = None,
        user_hash: str | None = None,
        persist: bool = True,
        max_texts: int | None = None,
    ) -> ReviewAssessment:
        """
        reviews — список dict с ключами:
            text / content / review_text (обязателен текст)
            id / review_id (опционально)
            source_url / url (опционально)

        ≥ max_texts → берём наиболее релевантные; < max_texts → все;
        0 → graceful empty. Без fixture.
        """
        now = time.time()
        cap = int(max_texts) if max_texts is not None else int(_MAX_REVIEW_TEXTS)
        cap = max(1, cap)
        try:
            raw_list = list(reviews or [])
        except Exception:
            raw_list = []

        # 1. Normalize + filter empty/malformed
        prepared: list[dict] = []
        for item in raw_list:
            if not isinstance(item, dict):
                if isinstance(item, str):
                    item = {"text": item}
                else:
                    continue
            text = (
                item.get("text")
                or item.get("content")
                or item.get("review_text")
                or ""
            )
            if not isinstance(text, str):
                continue
            norm = normalize_review_text(text)
            if len(norm) < 3:
                continue
            prepared.append({
                "text": text,
                "norm": norm,
                "fp": _text_fingerprint(text),
                "review_id": item.get("review_id") or item.get("id"),
                "source_url": item.get("source_url") or item.get("url"),
            })

        # 2. Dedup by fingerprint
        seen_fp: set[str] = set()
        unique: list[dict] = []
        for row in prepared:
            if row["fp"] in seen_fp:
                continue
            seen_fp.add(row["fp"])
            unique.append(row)

        # 3. Cap to most relevant MAX_REVIEW_TEXTS (no truncation to old 40)
        if len(unique) > cap:
            unique = sorted(unique, key=_relevance_score, reverse=True)[:cap]

        # 4. Extract signals
        signals: list[ReviewSignal] = []
        for row in unique:
            sentiment = _detect_sentiment(row["norm"])
            stype = _detect_signal_type(row["norm"])
            claim = _extract_claim(row["text"], stype, sentiment)
            conf = _signal_confidence(row["text"], sentiment, stype)
            rid = str(row["review_id"]) if row["review_id"] is not None else row["fp"]
            signals.append(ReviewSignal(
                id=str(uuid.uuid4()),
                category=category,
                signal_type=stype,
                sentiment=sentiment,
                claim=claim,
                confidence=conf,
                source_ids=[rid],
                user_hash=user_hash,
                article=str(article) if article is not None else None,
                source_url=row["source_url"],
                review_id=str(row["review_id"]) if row["review_id"] is not None else None,
                created_at=now,
                metadata={"fingerprint": row["fp"], "norm": row["norm"]},
            ))

        # 5. Group into issues
        issues = self._group_issues(
            signals, category=category, article=article, user_hash=user_hash, now=now,
        )

        total = len(unique)
        overall = 0.0
        if issues:
            overall = sum(i.confidence for i in issues) / len(issues)
        elif signals:
            overall = sum(s.confidence for s in signals) / len(signals) * 0.6

        problems = build_seller_problems(issues, signals)
        actions = build_seller_actions(problems)

        assessment = ReviewAssessment(
            category=category,
            article=str(article) if article is not None else None,
            user_hash=user_hash,
            processed_count=total,
            signals=signals,
            issues=issues,
            problems=problems,
            actions=actions,
            confidence=round(min(0.90, overall), 4),
            generated_at=now,
            metadata={
                "input_count": len(raw_list),
                "unique_count": total,
                "max_texts": cap,
            },
        )

        if persist and self._store is not None and user_hash:
            try:
                await self._persist(assessment)
            except Exception as exc:
                log.warning("ReviewIntelligence persist failed: %s", exc)

        return assessment

    def _group_issues(
        self,
        signals: list[ReviewSignal],
        *,
        category: str | None,
        article: str | None,
        user_hash: str | None,
        now: float,
    ) -> list[ReviewIssue]:
        if not signals:
            return []

        groups: dict[tuple, list[ReviewSignal]] = defaultdict(list)
        for sig in signals:
            key = (sig.signal_type, sig.sentiment)
            groups[key].append(sig)

        total = len(signals)
        issues: list[ReviewIssue] = []
        for (stype, sentiment), members in groups.items():
            count = len(members)
            ratio = count / total if total else 0.0
            avg_conf = sum(m.confidence for m in members) / count
            # Recurring: real repetition only (не один слабый отзыв)
            is_recurring = (
                count >= _MIN_RECURRING_COUNT
                and ratio >= _MIN_RECURRING_RATIO
                and avg_conf >= _MIN_ISSUE_CONFIDENCE
            )
            if is_recurring:
                issue_conf = min(0.90, avg_conf + 0.05 * (count - 1))
            else:
                issue_conf = min(avg_conf, 0.39)

            source_ids: list[str] = []
            seen: set[str] = set()
            for m in members:
                for sid in m.source_ids:
                    if sid not in seen:
                        seen.add(sid)
                        source_ids.append(sid)

            best = max(members, key=lambda m: m.confidence)
            blob = " ".join(
                (m.claim or "") + " " + str((m.metadata or {}).get("norm", ""))
                for m in members
            )
            themes = _detect_themes(blob)
            issues.append(ReviewIssue(
                id=str(uuid.uuid4()),
                category=category,
                signal_type=stype,
                sentiment=sentiment,
                claim=best.claim,
                count=count,
                ratio=round(ratio, 4),
                confidence=round(issue_conf, 4),
                source_ids=source_ids,
                user_hash=user_hash,
                article=str(article) if article is not None else None,
                created_at=now,
                metadata={
                    "recurring": is_recurring,
                    "group_size": count,
                    "themes": themes,
                    "examples": [
                        m.claim[:100] for m in sorted(
                            members, key=lambda x: x.confidence, reverse=True
                        )[:3]
                    ],
                },
            ))

        issues.sort(key=lambda i: (i.count, i.confidence), reverse=True)
        return issues

    async def _persist(self, assessment: ReviewAssessment) -> None:
        assert self._store is not None
        for sig in assessment.signals:
            await self._store.save_review_signal(sig)
        for issue in assessment.issues:
            await self._store.save_review_issue(issue)

    @staticmethod
    def recurring_issues(assessment: ReviewAssessment) -> list[ReviewIssue]:
        """Только recurring (порог count/ratio/confidence)."""
        return [
            i for i in assessment.issues
            if i.count >= _MIN_RECURRING_COUNT
            and i.ratio >= _MIN_RECURRING_RATIO
            and i.confidence >= _MIN_ISSUE_CONFIDENCE
        ]


# ────────────────────────────────── seller problems / actions ───────────── #


_THEME_LABELS: dict[str, str] = {
    "damaged_box": "повреждённая упаковка",
    "rattling": "товар болтается внутри коробки",
    "scratches": "царапины / повреждения товара",
    "unclear_kit": "непонятная комплектация",
    "size_fit": "проблема с размером",
    "photo_mismatch": "несоответствие фото и товара",
    "description_mismatch": "несоответствие описанию",
    "function_fail": "проблемы с работой товара",
    "unpacking": "сложная / неприятная распаковка",
    "design": "дизайн / внешний вид",
    "expectations": "ожидания не совпали",
    "quality": "качество товара",
    "product_quality": "качество товара",
    "delivery": "доставка",
    "logistics": "логистика (из отзывов)",
    "price_value": "цена / ценность",
    "service": "сервис продавца",
    "packaging": "упаковка",
    "damage": "повреждения",
    "appearance": "внешний вид",
    "completeness": "комплектация",
    "other": "прочий сигнал из отзывов",
}


def _detect_themes(text: str) -> list[str]:
    low = (text or "").lower()
    themes: list[str] = []
    if any(k in low for k in ("помят", "поврежд", "рван", "вскрыт", "мят")):
        themes.append("damaged_box")
    if any(k in low for k in ("болта", "свободн", "внутри короб", "не зафиксир")):
        themes.append("rattling")
    if any(k in low for k in ("царап", "скол", "поцарап")):
        themes.append("scratches")
    if any(k in low for k in ("комплект", "инструкц", "непонятн", "не хвата")):
        themes.append("unclear_kit")
    if any(k in low for k in ("размер", "маломер", "большемер")):
        themes.append("size_fit")
    if any(k in low for k in ("фото", "не как на", "отличаетс", "цвет")):
        themes.append("photo_mismatch")
    if any(k in low for k in ("описан", "в карточке", "характеристик")):
        themes.append("description_mismatch")
    if any(k in low for k in ("не работ", "не включа", "глючит", "перестал")):
        themes.append("function_fail")
    if any(k in low for k in ("распаков", "открыл короб", "скотч")):
        themes.append("unpacking")
    if any(k in low for k in ("ожидал", "думал что", "не то что ждал")):
        themes.append("expectations")
    if any(k in low for k in ("дизайн", "некрасив", "стильн")):
        themes.append("design")
    return themes


def _frequency_for_issue(issue: ReviewIssue) -> SignalFrequency:
    if issue.count >= 4 or (issue.count >= 3 and issue.ratio >= 0.25):
        return SignalFrequency.HIGH
    if (
        issue.count >= _MIN_RECURRING_COUNT
        and issue.ratio >= _MIN_RECURRING_RATIO
        and issue.confidence >= _MIN_ISSUE_CONFIDENCE
    ):
        return SignalFrequency.MEDIUM
    return SignalFrequency.LOW


def _direction_for_issue(issue: ReviewIssue) -> ProblemDirection:
    sent = getattr(issue.sentiment, "value", str(issue.sentiment))
    if sent == "POSITIVE":
        return ProblemDirection.POSITIVE
    if sent == "NEGATIVE":
        return ProblemDirection.NEGATIVE
    # OTHER / UNKNOWN / NEUTRAL без явного негатива — не mixed-risk
    st = getattr(issue.signal_type, "value", str(issue.signal_type))
    if st == "OTHER" and sent in ("UNKNOWN", "NEUTRAL", "POSITIVE"):
        return ProblemDirection.POSITIVE
    return ProblemDirection.MIXED


def _priority_for_issue(issue: ReviewIssue, freq: SignalFrequency) -> int:
    """P1–P4. Слабые / единичные → P4. Без выдуманных %."""
    st = getattr(issue.signal_type, "value", str(issue.signal_type))
    recurring = bool((issue.metadata or {}).get("recurring"))
    if not recurring or freq == SignalFrequency.LOW or issue.confidence < _MIN_ISSUE_CONFIDENCE:
        return 4
    if st in ("PRODUCT_QUALITY", "QUALITY", "DAMAGE", "FUNCTIONALITY"):
        return 1
    if st in ("PACKAGING", "UNPACKING", "COMPLETENESS") or "unclear_kit" in (
        issue.metadata or {}
    ).get("themes", []):
        return 2
    if st in (
        "PHOTO_MATCH", "DESCRIPTION_MATCH", "APPEARANCE", "SIZE", "DESIGN",
        "SERVICE", "PRICE_VALUE", "LOGISTICS", "DELIVERY", "EXPECTATIONS",
    ):
        return 3
    return 3


def _severity_for(priority: int, freq: SignalFrequency, recurring: bool) -> SignalSeverity:
    if not recurring or priority >= 4:
        return SignalSeverity.LOW
    if priority == 1 and freq == SignalFrequency.HIGH:
        return SignalSeverity.CRITICAL
    if priority <= 2:
        return SignalSeverity.HIGH
    if priority == 3:
        return SignalSeverity.MEDIUM
    return SignalSeverity.LOW


def _problem_label(issue: ReviewIssue) -> str:
    themes = list((issue.metadata or {}).get("themes") or [])
    for t in themes:
        if t in _THEME_LABELS:
            return _THEME_LABELS[t]
    st = getattr(issue.signal_type, "value", str(issue.signal_type))
    key = {
        "QUALITY": "product_quality",
        "PRODUCT_QUALITY": "product_quality",
        "PACKAGING": "packaging",
        "UNPACKING": "unpacking",
        "COMPLETENESS": "completeness",
        "DAMAGE": "damage",
        "DELIVERY": "delivery",
        "LOGISTICS": "logistics",
        "SIZE": "size_fit",
        "APPEARANCE": "appearance",
        "PHOTO_MATCH": "photo_mismatch",
        "DESCRIPTION_MATCH": "description_mismatch",
        "DESIGN": "design",
        "FUNCTIONALITY": "function_fail",
        "EXPECTATIONS": "expectations",
        "PRICE_VALUE": "price_value",
        "SERVICE": "service",
    }.get(st, "other")
    return _THEME_LABELS[key]


def _clean_example(claim: str) -> str:
    """Короткий пример без PII — берём claim как есть, обрезаем."""
    t = (claim or "").strip()
    t = re.sub(
        r"^(жалоба|похвала|замечание|упоминание)\s*\[[A-Z_]+\]:\s*",
        "",
        t,
        flags=re.I,
    )
    return t[:90].rstrip()


def _problem_rationale(issue: ReviewIssue, freq: SignalFrequency, recurring: bool) -> str:
    label = _problem_label(issue)
    if not recurring:
        return (
            f"Единичный/слабый сигнал «{label}» "
            f"(n={issue.count}) — не recurring risk."
        )
    return (
        f"Повторяется в отзывах: «{label}» "
        f"(n={issue.count}, freq={freq.value}). Без выдуманных процентов."
    )


def build_seller_problems(
    issues: list[ReviewIssue],
    signals: list[ReviewSignal] | None = None,
) -> list[SellerProblem]:
    """Преобразовать ReviewIssue → нормализованные SellerProblem."""
    problems: list[SellerProblem] = []
    for issue in issues or []:
        freq = _frequency_for_issue(issue)
        direction = _direction_for_issue(issue)
        priority = _priority_for_issue(issue, freq)
        recurring = bool((issue.metadata or {}).get("recurring"))
        severity = _severity_for(priority, freq, recurring)
        examples = [
            _clean_example(e)
            for e in list((issue.metadata or {}).get("examples") or [])
            if e
        ]
        if not examples and issue.claim:
            examples = [_clean_example(issue.claim)]
        problems.append(SellerProblem(
            id=issue.id or str(uuid.uuid4()),
            label=_problem_label(issue),
            frequency=freq,
            confidence=float(issue.confidence),
            direction=direction,
            priority=priority,
            signal_type=issue.signal_type,
            evidence_ids=list(issue.source_ids or []),
            examples=examples[:3],
            claim=issue.claim or "",
            count=int(issue.count or 0),
            severity=severity,
            rationale=_problem_rationale(issue, freq, recurring),
            metadata={
                "recurring": recurring,
                "themes": list((issue.metadata or {}).get("themes") or []),
                "ratio": issue.ratio,
                "category": getattr(issue.signal_type, "value", str(issue.signal_type)),
            },
        ))
    problems.sort(key=lambda p: (p.priority, -p.confidence, -p.count))
    return problems


def _actions_for_problem(problem: SellerProblem) -> list[tuple[str, str]]:
    """(title, rationale) без выдуманных цифр."""
    themes = set((problem.metadata or {}).get("themes") or [])
    st = getattr(problem.signal_type, "value", str(problem.signal_type))
    label = problem.label
    out: list[tuple[str, str]] = []

    if "damaged_box" in themes or st in ("PACKAGING", "DAMAGE"):
        if "damaged_box" in themes or "упаков" in label or st == "PACKAGING":
            out.append((
                "Усилить упаковку",
                f"По отзывам повторяется сигнал «{label}» — сначала убрать причину повреждений.",
            ))
            out.append((
                "Добавить защитный слой",
                "Защитный слой снижает риск повреждений при перевозке.",
            ))
            out.append((
                "Проверить фиксацию товара внутри коробки",
                "Если товар смещается в коробке, риск повреждений растёт.",
            ))
    if "rattling" in themes:
        out.append((
            "Добавить внутренний фиксатор / вставку",
            "В отзывах есть сигнал, что товар болтается внутри.",
        ))
        out.append((
            "Уменьшить свободное пространство",
            "Меньше люфта — меньше ударов и царапин по дороге.",
        ))
    if "scratches" in themes or st == "DAMAGE":
        out.append((
            "Добавить защитную плёнку/пакет",
            "Сигнал о царапинах/повреждениях — защитить поверхность до отгрузки.",
        ))
        out.append((
            "Разделить товар и элементы упаковки",
            "Жёсткие элементы упаковки не должны тереться о товар.",
        ))
    if st == "UNPACKING" or "unpacking" in themes:
        out.append((
            "Упростить распаковку",
            f"Сигнал «{label}» — сделать вскрытие понятным и аккуратным.",
        ))
    if "unclear_kit" in themes or st == "COMPLETENESS":
        out.append((
            "Добавить инструкцию по комплектации",
            "Покупателям неочевидно, что входит в набор.",
        ))
        out.append((
            "Разложить элементы по отдельным секциям",
            "Понятная раскладка снижает путаницу при получении.",
        ))
    if st == "FUNCTIONALITY" or "function_fail" in themes:
        out.append((
            "Проверить качество и характеристики партии",
            f"Повторяется сигнал «{label}» — стоит проверить товар до масштабирования.",
        ))
    if st in ("PHOTO_MATCH", "APPEARANCE") or "photo_mismatch" in themes:
        out.append((
            "Сверить фото с реальным товаром",
            "Сигнал о внешнем виде — фото и товар должны совпадать.",
        ))
    if st == "DESCRIPTION_MATCH" or "description_mismatch" in themes:
        out.append((
            "Сверить описание и характеристики с фактом",
            "Покупатели видят расхождение карточки и товара.",
        ))
    if st == "DESIGN" or "design" in themes:
        out.append((
            "Уточнить визуал в карточке",
            "Сигнал по дизайну — честно показать реальный вид.",
        ))
    if st == "SIZE" or "size_fit" in themes:
        out.append((
            "Добавить точные размеры / таблицу размеров",
            "Повторяющиеся замечания по размеру — уточнить замеры в карточке.",
        ))
    if st == "EXPECTATIONS" or "expectations" in themes:
        out.append((
            "Снизить разрыв ожиданий в карточке",
            "Честно описать, что получит покупатель — меньше разочарований.",
        ))
    if st == "PRICE_VALUE":
        out.append((
            "Усилить perceived value в карточке",
            "Сигнал о цене/ценности — без назначения новой цены усилить аргументы в описании.",
        ))
    if st in ("PRODUCT_QUALITY", "QUALITY") and not out:
        out.append((
            "Разобрать жалобы на качество",
            f"Повторяется «{label}» — проверить партию и свойства в карточке.",
        ))
    if st in ("LOGISTICS", "DELIVERY"):
        out.append((
            "Проверить схему отгрузки и сроки",
            "Сигнал по доставке из отзывов; часть может быть на стороне маркетплейса.",
        ))
    if st == "SERVICE":
        out.append((
            "Улучшить ответы покупателям",
            "Сигнал по сервису — быстрее и конкретнее отрабатывать вопросы.",
        ))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for title, rationale in out:
        if title in seen:
            continue
        seen.add(title)
        unique.append((title, rationale))
    return unique


def build_seller_actions(problems: list[SellerProblem]) -> list[SellerAction]:
    """SellerProblem → конкретные SellerAction с evidence_ids."""
    actions: list[SellerAction] = []
    for problem in problems or []:
        if problem.direction == ProblemDirection.POSITIVE:
            continue
        pairs = _actions_for_problem(problem)
        if problem.priority >= 4 or problem.frequency == SignalFrequency.LOW:
            if not pairs:
                continue
            title, rationale = pairs[0]
            actions.append(SellerAction(
                id=str(uuid.uuid4()),
                title=title,
                rationale=(
                    f"Есть слабый сигнал «{problem.label}». "
                    f"Стоит проверить, не утверждать как факт. {rationale}"
                ),
                confidence=min(problem.confidence, 0.39),
                priority=4,
                evidence_ids=list(problem.evidence_ids or []),
                problem_id=problem.id,
                signal_type=problem.signal_type,
                metadata={"cautious": True, "frequency": problem.frequency.value},
            ))
            continue

        for title, rationale in pairs:
            conf = problem.confidence
            if problem.frequency == SignalFrequency.HIGH:
                conf = min(0.90, conf + 0.05)
            actions.append(SellerAction(
                id=str(uuid.uuid4()),
                title=title,
                rationale=rationale,
                confidence=round(conf, 4),
                priority=problem.priority,
                evidence_ids=list(problem.evidence_ids or []),
                problem_id=problem.id,
                signal_type=problem.signal_type,
                metadata={
                    "frequency": problem.frequency.value,
                    "label": problem.label,
                    "severity": getattr(problem.severity, "value", str(problem.severity)),
                },
            ))

    actions.sort(key=lambda a: (a.priority, -a.confidence))
    return actions


def signal_strength_label(problem: SellerProblem) -> str:
    if problem.priority >= 4 or problem.frequency == SignalFrequency.LOW:
        return "слабый сигнал"
    if problem.frequency == SignalFrequency.HIGH and problem.confidence >= 0.55:
        return "сильный сигнал"
    return "средний сигнал"


async def record_review_action_for_learning(
    outcome_tracker,
    action: SellerAction,
    *,
    user_hash: str,
    category: str,
    article: str | None = None,
) -> object | None:
    """
    В Learning/OutcomeTracker — только структурированный сигнал.
    Не сохраняет raw user_id и не пишет каждый ответ Argus.
    """
    if outcome_tracker is None or not user_hash:
        return None
    if not action.evidence_ids:
        return None
    try:
        return await outcome_tracker.record_recommendation(
            user_hash=user_hash,
            category=category,
            article=article,
            recommendation_type="REVIEW_ACTION",
            recommendation_action=action.title,
            recommendation_confidence=float(action.confidence),
            evidence_ids=list(action.evidence_ids),
        )
    except Exception as exc:
        log.warning("record_review_action_for_learning failed: %s", exc)
        return None
