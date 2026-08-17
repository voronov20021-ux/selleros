import html
import re

from backend.ai.recommendations import Recommendation, RecommendationType
from backend.config import AI_NAME
from backend.wb.provenance import field_provenance_label

_MAX_MARKET_RECS = 5          # максимум рекомендаций в блоке
_MIN_SHOW_CONF   = 0.50       # показываем только confidence >= порога

# Понятные пользователю источники вместо технического UUID
_SOURCE_NAMES = {
    "yandex_wordstat": "Яндекс Wordstat",
    "yandex_search":   "Яндекс Поиск",
    "manual":          "Данные команды",
    "seller":          "Данные продавца",
}
_TYPE_ICONS = {
    RecommendationType.PRICE:       "💰",
    RecommendationType.ADVERTISING: "📢",
    RecommendationType.CONTENT:     "📝",
    RecommendationType.STOCK:       "📦",
    RecommendationType.MARKET:      "🌐",
    RecommendationType.MONITOR:     "🔭",
}

# Эмодзи, с которых начинаются «плюсы» и «минусы» в reasons.
_POSITIVE = ("✅", "👍", "⭐", "💬", "📋", "🔥")
_NEGATIVE = ("⚠️", "❌")

#: Подпись источника значения seller-данных — см. build_full_with_sections().
_SOURCE_LABELS = {
    "user": "👤 указано продавцом",
    "api": "🔑 Seller API",
}


def _seller_line(label: str, value, unit: str, source: str | None) -> str:
    """Одна строка блока «Данные продавца»: значение + честный источник."""
    if value is None:
        return f"• {label}: нет данных"

    suffix = f" {unit}" if unit else ""
    source_label = _SOURCE_LABELS.get(source or "")
    source_part = f" (Источник: {source_label})" if source_label else ""

    return f"• {label}: {value}{suffix}{source_part}"


def _card_commercial_line(product, label: str, field: str, unit: str = "") -> str:
    """CARD DATA строка с provenance; None → «нет данных»."""
    val = getattr(product, field, None)
    if val is None:
        return f"• {label}: нет данных"
    suffix = f" {unit}" if unit else ""
    prov = field_provenance_label(product, field)
    if prov:
        return f"• {label}: {val}{suffix}\n  Источник: {prov}"
    return f"• {label}: {val}{suffix}"


_GENERIC_CARD_REC_MARKERS = (
    "заполните характеристик",
    "добавьте подробное описание",
    "сделайте описание более",
    "добавьте больше фотографий",
    "инфографик",
)


def _is_generic_card_recommendation(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _GENERIC_CARD_REC_MARKERS)


def verdict_for(
    score: int,
    *,
    diagnosis_kind: str | None = None,
    funnel_complete: bool = False,
) -> str:
    """Вердикт по Score. При NO_SYSTEMIC не красим как «нужна оптимизация»."""
    if (diagnosis_kind or "") == "no_systemic":
        if not funnel_complete:
            return "Полная оценка ограничена: нет CTR/CVR/заказов"
        return "Системной проблемы не видно"
    if score >= 90:
        return "🟢 Отличная карточка"
    if score >= 75:
        return "🟢 Хорошая карточка"
    if score >= 60:
        return "🟡 Требует доработки"
    return "🔴 Нужна оптимизация"


def sanitize_no_systemic_comment(text: str) -> str:
    """Убрать causal claims про продажи/доверие/фото-плюс из LLM-обёртки."""
    if not text:
        return text
    s = text
    s = re.sub(
        r"системн\w* проблем\w*(?:\s+\w+){0,4}\s+с\s+продаж\w*\s+не\s+наблюда\w*",
        "Системной проблемы по доступным данным пока не видно",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"[^.!?\n]*свидетельств\w*\s+о\s+высоком(?:\s+уровне)?\s+довери[^.!?\n]*[.!?]?",
        "Рейтинг: факт карточки, не оценка доверия.",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\d+\s*фотограф\w*\s*[—\-–]\s*плюс",
        "фото: счётчик кадров; качество и соответствие не проверялись",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"⚠️?\s*низкий рейтинг\.?",
        "Слабый сигнал: рейтинг при малой выборке — не системный вывод.",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"[^.!?\n]*выглядит\s+адекватн[^.!?\n]*[.!?]?",
        "Рыночная позиция не определена — commercial fields конкурентов не подтверждены.",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"[^.!?\n]*негативно\s+сказыва[^.!?\n]*[.!?]?",
        "Характеристики: можно проверить как IDEA/CHECK, влияние на продажи не доказано.",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"[^.!?\n]*\bесть\s+спрос\b[^.!?\n]*[.!?]?",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"[^.!?\n]*cvr\s*[\d.]+%\s*говорит о том[^.!?\n]*[.!?]?",
        "CVR известен как observation; без baseline не утверждаю, что он низкий.",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"cvr\s*низк\w*",
        "CVR без baseline не классифицирую",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r" {2,}", " ", s)
    return s.strip()


def _filter_score_reasons_display(
    pluses: list[str],
    minuses: list[str],
    *,
    product=None,
    advisor_plan=None,
) -> tuple[list[str], list[str]]:
    """Display-only: score formula unchanged. Hide quality judgments without evidence."""
    kind = ""
    if advisor_plan is not None:
        kind = str(getattr(advisor_plan, "main_problem_kind", "") or "")
    n_fb = 0
    if product is not None and getattr(product, "feedbacks", None) is not None:
        try:
            n_fb = int(getattr(product, "feedbacks"))
        except (TypeError, ValueError):
            n_fb = 0
    photos_analyzed = bool(getattr(advisor_plan, "photos_analyzed", False)) if advisor_plan else False
    if not photos_analyzed:
        pluses = [
            p for p in pluses
            if not re.search(r"много фотографий|достаточно фотографий", p, re.I)
        ]
    if kind in ("no_systemic", "funnel_symptom"):
        minuses = [
            m for m in minuses
            if "низкий рейтинг" not in m.lower() and "средний рейтинг" not in m.lower()
        ]
    return pluses, minuses


class ReportBuilder:

    # ──────────────────────────── market block ───────────────────────── #

    @staticmethod
    def build_market_block(market_recs: list[Recommendation]) -> str:
        """
        Компактный HTML-блок «🎯 Действия для роста» из market recommendations.

        Показывает только рекомендации с confidence >= _MIN_SHOW_CONF (макс. 5).
        MONITOR-рекомендации — отдельным разделом «👁 Наблюдения».
        Не показывает технические UUID — только понятный тип/источник.
        Возвращает пустую строку, если нечего показывать.
        """
        if not market_recs:
            return ""

        # Разделяем на конкретные действия и наблюдения
        actions = [
            r for r in market_recs
            if r.type != RecommendationType.MONITOR
            and r.confidence >= _MIN_SHOW_CONF
        ][:_MAX_MARKET_RECS]

        monitors = [
            r for r in market_recs
            if r.type == RecommendationType.MONITOR
            and r.confidence >= _MIN_SHOW_CONF
        ][:_MAX_MARKET_RECS]

        # Если совсем пусто — не выводим блок
        if not actions and not monitors:
            return ""

        lines: list[str] = []
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🎯 <b>Действия для роста</b>")

        for idx, rec in enumerate(actions, 1):
            icon = _TYPE_ICONS.get(rec.type, "•")
            title = html.escape(rec.title)
            reason = html.escape(rec.reason)
            action = html.escape(rec.action)
            conf_pct = int(rec.confidence * 100)
            lines.append("")
            lines.append(f"{idx}. {icon} <b>{title}</b>")
            lines.append(f"   Почему: {reason}")
            lines.append(f"   Действие: {action}")
            lines.append(f"   Уверенность: {conf_pct}%")

        if monitors:
            lines.append("")
            lines.append("👁 <b>Наблюдения</b>")
            for rec in monitors:
                icon = _TYPE_ICONS.get(rec.type, "🔭")
                title = html.escape(rec.title)
                action = html.escape(rec.action)
                lines.append(f"• {title}: {action}")

        return "\n".join(lines)

    # ──────────────────────────── card build ─────────────────────────── #

    def build_card(
        self,
        product,
        score_data: dict,
        recommendations: list,
        ai_comment: str | None = None,
        market_recs: list[Recommendation] | None = None,
        advisor_plan=None,
    ) -> str:
        """
        Красивая карточка анализа для Telegram (parse_mode=HTML).

        Структура:
            шапка -> цена/рейтинг -> описание ->
            плюсы -> минусы -> советы Seller AI -> Advisor -> итог
        """

        score = score_data["score"]
        reasons = score_data["reasons"]

        pluses = [r for r in reasons if r.startswith(_POSITIVE)]
        minuses = [r for r in reasons if r.startswith(_NEGATIVE)]
        pluses, minuses = _filter_score_reasons_display(
            pluses, minuses, product=product, advisor_plan=advisor_plan,
        )

        # Не дублировать шаблонные советы, если Advisor сказал «ничего не трогай»
        leave_alone = False
        if advisor_plan is not None:
            do_first = (getattr(advisor_plan, "do_first", None) or "").lower()
            leave_alone = "ничего не трогай" in do_first or "ничего критичного" in (
                getattr(advisor_plan, "main_verdict", None) or ""
            ).lower()
        recs_to_show = [] if leave_alone else list(recommendations or [])

        lines = []

        # --- Шапка ---
        title = html.escape(product.title or "Без названия")
        lines.append(f"📦 <b>{title}</b>")

        meta = []
        if product.brand:
            meta.append(html.escape(product.brand))
        meta.append(f"арт. <code>{product.article}</code>")
        lines.append("🏷 " + " · ".join(meta))

        # WB Engine честно помечает, если живых источников не было
        # и он отдал последний известный снимок из памяти ARGUS.
        if getattr(product, "source", "live") == "history":
            lines.append("🕐 <i>WB сейчас недоступен — данные могут быть устаревшими</i>")

        lines.append("")

        # --- Цена ---
        if product.price:
            price_line = f"💰 <b>{product.price} ₽</b>"
            if product.old_price and product.old_price > product.price:
                price_line += f"  <s>{product.old_price} ₽</s>"
            if product.discount:
                price_line += f"  (−{product.discount}%)"
            lines.append(price_line)

        # --- Рейтинг и отзывы ---
        rating_parts = []
        if product.rating:
            rating_parts.append(f"⭐ {product.rating}")
        if product.feedbacks:
            rating_parts.append(f"💬 {product.feedbacks} отзывов")
        if rating_parts:
            lines.append(" · ".join(rating_parts))

        # --- Описание (коротко) ---
        if product.description:
            preview = product.description.strip()[:250]
            if len(product.description) > 250:
                preview += "…"
            lines.append("")
            lines.append(f"📝 <i>{html.escape(preview)}</i>")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🧠 <b>{AI_NAME} · оценка {score}/100</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        # --- Плюсы ---
        if pluses:
            lines.append("")
            lines.append("<b>Плюсы</b>")
            lines.extend(pluses)

        # --- Минусы ---
        if minuses:
            lines.append("")
            lines.append("<b>Минусы</b>")
            lines.extend(minuses)

        # Score breakdown (если есть) — без CTR
        breakdown = score_data.get("breakdown") if isinstance(score_data, dict) else None
        if breakdown and isinstance(breakdown, dict):
            bits = [f"{k}:{v}" for k, v in breakdown.items()]
            if bits:
                lines.append("")
                lines.append(
                    "📊 Score breakdown (card_only): " + ", ".join(bits)
                )

        # --- Советы Seller AI ---
        if recs_to_show:
            lines.append("")
            lines.append(f"<b>Советы {AI_NAME}</b>")
            for recommendation in recs_to_show:
                lines.append(f"• {recommendation}")

        # --- Живой комментарий Seller AI (если AI доступен) ---
        if ai_comment:
            lines.append("")
            lines.append(f"<b>Разбор от {AI_NAME}</b>")
            lines.append(html.escape(ai_comment.strip()))

        # --- Actionable Advisor ---
        advisor_block = self.build_advisor_block(advisor_plan)
        if advisor_block:
            lines.append(advisor_block)

        # --- Market Recommendations (Intelligence Layer) ---
        if market_recs:
            market_block = self.build_market_block(market_recs)
            if market_block:
                lines.append(market_block)

        # --- Итог ---
        lines.append("")
        lines.append(f"<b>{verdict_for(score)}.</b>")

        return "\n".join(lines)

    @staticmethod
    def build_advisor_block(advisor_plan) -> str:
        """HTML-блок Actionable Advisor; пусто если плана нет."""
        if advisor_plan is None:
            return ""
        if hasattr(advisor_plan, "has_content") and not advisor_plan.has_content():
            return ""
        if hasattr(advisor_plan, "format_html"):
            return advisor_plan.format_html() or ""
        return ""

    def build_full_report(self, product, analysis: dict) -> str:
        """«📊 Полный отчёт» — все данные карточки одним экраном."""

        lines = []

        title = html.escape(product.title or "Без названия")
        lines.append("📊 <b>Полный отчёт</b>")
        lines.append("")
        lines.append(f"📦 <b>{title}</b>")
        lines.append(f'🔗 <a href="{product.url}">Открыть на Wildberries</a>')
        lines.append("")

        # --- Идентификаторы ---
        lines.append("<b>Карточка</b>")
        lines.append(f"• Артикул: <code>{product.article}</code>")
        if product.brand:
            lines.append(f"• Бренд: {html.escape(product.brand)}")
        if product.supplier:
            lines.append(f"• Продавец: {html.escape(product.supplier)}")
        if product.supplier_id:
            lines.append(f"• ID продавца: <code>{product.supplier_id}</code>")

        # --- Коммерция ---
        lines.append("")
        lines.append("<b>Цена</b>")
        lines.append(f"• Текущая: {product.price or '—'} ₽")
        if product.old_price:
            lines.append(f"• До скидки: {product.old_price} ₽")
        if product.discount:
            lines.append(f"• Скидка: {product.discount}%")

        # --- Репутация ---
        lines.append("")
        lines.append("<b>Репутация</b>")
        lines.append(f"• Рейтинг: {product.rating or '—'}")
        lines.append(f"• Отзывов: {product.feedbacks or 0}")

        # --- Контент ---
        lines.append("")
        lines.append("<b>Контент</b>")
        lines.append(f"• Фотографий: {len(product.photos)}")
        lines.append(f"• Характеристик: {len(product.characteristics)}")
        description_length = len(product.description or "")
        lines.append(f"• Описание: {description_length} символов")

        # --- Логистика ---
        total_qty = getattr(product, "total_qty", 0)
        warehouses = getattr(product, "warehouses", [])
        sizes = getattr(product, "sizes", [])
        if total_qty or sizes:
            lines.append("")
            lines.append("<b>Наличие</b>")
            lines.append(f"• Остаток: {total_qty} шт")
            if warehouses:
                lines.append(f"• Складов: {len(warehouses)}")
            if sizes:
                lines.append(f"• Размеров: {len(sizes)}")

        # --- Характеристики (первые 15) ---
        if product.characteristics:
            lines.append("")
            lines.append("<b>Характеристики</b>")
            for name, value in list(product.characteristics.items())[:15]:
                lines.append(f"• {html.escape(str(name))}: {html.escape(str(value))}")

        # --- Оценка ---
        score = analysis.get("score", 0)
        lines.append("")
        lines.append(f"🧠 <b>{AI_NAME} · {score}/100 · {verdict_for(score)}</b>")

        return "\n".join(lines)

    def build_caption(self, product, score: int) -> str:
        """Короткая подпись под фото (лимит Telegram — 1024 символа)."""

        parts = [f"📦 <b>{html.escape(product.title or 'Без названия')}</b>"]

        if product.price:
            parts.append(f"💰 {product.price} ₽")

        line = []
        if product.rating:
            line.append(f"⭐ {product.rating}")
        if product.feedbacks:
            line.append(f"💬 {product.feedbacks}")
        line.append(f"🧠 {score}/100")
        parts.append(" · ".join(line))

        return "\n".join(parts)[:1000]

    def build_preliminary(
        self,
        product,
        score_data: dict,
        recommendations: list,
        market_recs: list[Recommendation] | None = None,
    ) -> str:
        """
        «🤖 Предварительный анализ» — данные карточки WB (контент +
        публичные price/rating/feedbacks, если уже получены).

        SellerData сюда не подмешивается. Если публичные поля есть —
        показываем их. Если нет — честно просим указать вручную.
        """
        score = score_data["score"]
        reasons = score_data["reasons"]

        pluses = [r for r in reasons if r.startswith(_POSITIVE)]
        minuses = [r for r in reasons if r.startswith(_NEGATIVE)]
        pluses, minuses = _filter_score_reasons_display(
            pluses, minuses, product=product, advisor_plan=None,
        )

        # Совет про цену из RecommendationGenerator упоминает
        # API/BrowserProvider — техническая формулировка; ниже свой блок.
        content_recommendations = [r for r in recommendations if not r.startswith("💰")]

        lines = []

        title = html.escape(product.title or "Без названия")
        lines.append(f"📦 <b>{title}</b>")

        meta = []
        if product.brand:
            meta.append(html.escape(product.brand))
        meta.append(f"арт. <code>{product.article}</code>")
        lines.append("🏷 " + " · ".join(meta))
        lines.append("")

        lines.append("<b>Данные карточки (CARD)</b>")
        lines.append(f"📝 Описание: {'есть' if product.description else 'нет'}")
        lines.append(f"🖼 Фото: {len(product.photos)}")
        lines.append(f"⚙️ Характеристики: {len(product.characteristics)}")
        if product.price is not None:
            lines.append(_card_commercial_line(product, "Публичная цена", "price", "₽").lstrip("• "))
        if product.rating is not None:
            lines.append(_card_commercial_line(product, "Рейтинг", "rating").lstrip("• "))
        if product.feedbacks is not None:
            lines.append(
                _card_commercial_line(product, "Отзывов на карточке", "feedbacks").lstrip("• ")
            )

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🧠 <b>{AI_NAME} · предварительная оценка {score}/100</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        if pluses:
            lines.append("")
            lines.append("<b>Плюсы</b>")
            lines.extend(pluses)

        if minuses:
            lines.append("")
            lines.append("<b>Минусы</b>")
            lines.extend(minuses)

        if content_recommendations:
            lines.append("")
            lines.append(f"<b>Советы {AI_NAME}</b>")
            for recommendation in content_recommendations:
                lines.append(f"• {recommendation}")

        missing = []
        if product.price is None:
            missing.append("• текущая цена")
        if product.rating is None:
            missing.append("• средняя оценка")
        if product.feedbacks is None:
            missing.append("• количество отзывов на карточке")

        if missing:
            lines.append("")
            lines.append(
                "⚠️ <b>Публичная карточка не отдала:</b>"
            )
            lines.extend(missing)
            lines.append("")
            lines.append(
                "Нажмите «📊 Точный анализ», чтобы указать недостающее вручную "
                "(это будет данные продавца, отдельно от карточки)."
            )
        else:
            lines.append("")
            lines.append(
                "✅ Публичные цена/рейтинг/отзывы карточки получены автоматически."
            )

        # --- Market Recommendations ---
        if market_recs:
            market_block = self.build_market_block(market_recs)
            if market_block:
                lines.append(market_block)

        return "\n".join(lines)

    def build_full_with_sections(
        self,
        product,
        seller_data,
        score_data: dict,
        recommendations: list,
        ai_comment: str | None = None,
        market_recs: list[Recommendation] | None = None,
        advisor_plan=None,
    ) -> str:
        """
        «📈 Полный анализ» — карточка (WBProduct) + данные продавца
        (SellerData) явно разделены на два блока, каждая seller-цифра
        подписана источником ("👤 указано продавцом" / "🔑 Seller API").

        Никогда не смешивает то, что реально спарсено с WB, с тем, что
        ввёл продавец вручную или отдал Seller API — и не придумывает
        отсутствующие значения.
        """
        score = score_data["score"]
        reasons = score_data["reasons"]
        minuses = [r for r in reasons if r.startswith(_NEGATIVE)]
        _, minuses = _filter_score_reasons_display(
            [], minuses, product=product, advisor_plan=advisor_plan,
        )

        lines = []

        lines.append("📈 <b>Полный анализ</b>")
        lines.append("")

        # --- 📦 Карточка (CARD / PUBLIC) ---
        lines.append("📦 <b>Карточка</b>")
        lines.append(f"• Название: {html.escape(product.title or 'Без названия')}")
        if product.brand:
            lines.append(f"• Бренд: {html.escape(product.brand)}")
        lines.append(f"• Артикул: <code>{product.article}</code>")
        lines.append(f"• Описание: {'есть' if product.description else 'нет'}")
        lines.append(f"• Фото: {len(product.photos)}")
        lines.append(f"• Характеристики: {len(product.characteristics)}")
        lines.append(_card_commercial_line(product, "Публичная цена", "price", "₽"))
        lines.append(_card_commercial_line(product, "Рейтинг карточки", "rating"))
        lines.append(_card_commercial_line(product, "Отзывов на карточке (card_feedbacks)", "feedbacks"))

        # --- 👤 Данные продавца (gap-fill / seller commercial only) ---
        lines.append("")
        lines.append("👤 <b>Данные продавца</b>")
        if seller_data is None:
            lines.append("• не указаны")
        else:
            has_seller_commercial = any(
                getattr(seller_data, f, None) is not None
                for f in ("price", "rating", "feedbacks")
            )
            if not has_seller_commercial:
                lines.append("• не указаны (публичная карточка используется как есть)")
            else:
                lines.append(_seller_line("💰 Цена продавца", seller_data.price, "₽", seller_data.price_source))
                lines.append(_seller_line("⭐ Рейтинг продавца", seller_data.rating, "", seller_data.rating_source))
                lines.append(_seller_line("💬 Отзывов продавца", seller_data.feedbacks, "", seller_data.feedbacks_source))

        # --- 📈 Бизнес-метрики (PRIVATE) ---
        lines.append("")
        lines.append("📈 <b>Бизнес-метрики</b>")
        if seller_data is None or not getattr(seller_data, "has_any_private_metrics", lambda: False)():
            lines.append("• CTR: нет данных")
            lines.append("• CVR: нет данных")
            lines.append("• Показы: нет данных")
            lines.append("• Просмотры: нет данных")
            lines.append("• Продажи: нет данных")
            lines.append("• Заказы: нет данных")
            lines.append("• не могу оценить CTR/CVR/воронку без этих цифр")
        else:
            lines.append(_seller_line("CTR", getattr(seller_data, "ctr", None), "", getattr(seller_data, "ctr_source", None)))
            lines.append(_seller_line("CVR", getattr(seller_data, "cvr", None), "", getattr(seller_data, "cvr_source", None)))
            lines.append(_seller_line("Показы", getattr(seller_data, "impressions", None), "", getattr(seller_data, "impressions_source", None)))
            lines.append(_seller_line("Просмотры", getattr(seller_data, "views", None), "", getattr(seller_data, "views_source", None)))
            lines.append(_seller_line("📈 Продажи", seller_data.sales, "", seller_data.sales_source))
            lines.append(_seller_line("📦 Заказы", seller_data.orders, "", seller_data.orders_source))
            lines.append(_seller_line("↩️ Возвраты", getattr(seller_data, "returns", None), "", getattr(seller_data, "returns_source", None)))
            lines.append(_seller_line("📢 Реклама", getattr(seller_data, "ad_spend", None), "₽", getattr(seller_data, "ad_spend_source", None)))
            lines.append(_seller_line("🧮 Себестоимость", getattr(seller_data, "cost", None), "₽", getattr(seller_data, "cost_source", None)))
            lines.append(_seller_line("💳 Комиссия", getattr(seller_data, "commission", None), "", getattr(seller_data, "commission_source", None)))
            lines.append(_seller_line("🚚 Логистика", getattr(seller_data, "logistics", None), "₽", getattr(seller_data, "logistics_source", None)))
            lines.append(_seller_line("🏬 Хранение", getattr(seller_data, "storage", None), "₽", getattr(seller_data, "storage_source", None)))
            if seller_data.period:
                lines.append(f"• Период: {html.escape(seller_data.period)}")
            if getattr(seller_data, "ctr", None) is None or getattr(seller_data, "cvr", None) is None:
                lines.append("• не могу оценить CTR/CVR полностью — части данных нет")

        # --- 🧠 Анализ ---
        lines.append("")
        lines.append("🧠 <b>Анализ</b>")
        kind = getattr(advisor_plan, "main_problem_kind", None) if advisor_plan else None
        funnel_complete = bool(
            seller_data is not None
            and getattr(seller_data, "ctr", None) is not None
            and getattr(seller_data, "cvr", None) is not None
        )
        lines.append(
            f"Оценка {AI_NAME}: <b>{score}/100</b> · "
            f"{verdict_for(score, diagnosis_kind=kind, funnel_complete=funnel_complete)}"
        )

        # --- ⚠️ Проблемы ---
        if minuses:
            lines.append("")
            lines.append("⚠️ <b>Проблемы</b>")
            lines.extend(minuses)

        # --- 💡 Рекомендации (NO_SYSTEMIC: no generic card optimization) ---
        recs = list(recommendations or [])
        if kind == "no_systemic":
            recs = [r for r in recs if not _is_generic_card_recommendation(str(r))]
        if recs:
            lines.append("")
            lines.append("💡 <b>Рекомендации</b>")
            for recommendation in recs:
                lines.append(f"• {recommendation}")

        if ai_comment:
            comment = ai_comment.strip()
            if kind == "no_systemic":
                comment = sanitize_no_systemic_comment(comment)
            lines.append("")
            lines.append(f"<b>Разбор от {AI_NAME}</b>")
            lines.append(html.escape(comment))

        # --- Actionable Advisor ---
        advisor_block = self.build_advisor_block(advisor_plan)
        if advisor_block:
            lines.append(advisor_block)

        # --- Market Recommendations ---
        if market_recs:
            market_block = self.build_market_block(market_recs)
            if market_block:
                lines.append(market_block)

        return "\n".join(lines)

    def build(
        self,
        product,
        score_data: dict,
        recommendations: list,
        ai_comment: str | None = None,
    ):

        score = score_data["score"]
        reasons = score_data["reasons"]

        report = []

        title = html.escape(product.title or "Без названия")
        report.append(f"📦 <b>{title}</b>")
        report.append("")

        if product.brand:
            report.append(f"🏷 Бренд: {html.escape(product.brand)}")

        if product.price:
            report.append(f"💰 Цена: {product.price} ₽")

        if product.old_price:
            report.append(f"💸 Старая цена: {product.old_price} ₽")

        if product.discount:
            report.append(f"🔥 Скидка: {product.discount}%")

        if product.rating:
            report.append(f"⭐ Рейтинг: {product.rating}")

        if product.feedbacks:
            report.append(f"💬 Отзывов: {product.feedbacks}")

        report.append("")
        report.append("━━━━━━━━━━━━━━━━━━━━━━")
        report.append("")
        report.append(f"🤖 <b>AI Score: {score}/100</b>")
        report.append("")

        report.append("<b>Что хорошо:</b>")

        if reasons:
            for reason in reasons:
                report.append(reason)
        else:
            report.append("Нет данных.")

        report.append("")
        report.append("<b>Что улучшить:</b>")

        for recommendation in recommendations:
            report.append(f"• {recommendation}")

        if ai_comment:
            report.append("")
            report.append("━━━━━━━━━━━━━━━━━━━━━━")
            report.append("")
            report.append(f"🧠 <b>Мнение {AI_NAME}:</b>")
            report.append("")
            # Экранируем: AI может вернуть символы < > &,
            # которые сломают parse_mode="HTML" в Telegram.
            report.append(html.escape(ai_comment.strip()))

        report.append("")
        report.append("━━━━━━━━━━━━━━━━━━━━━━")
        report.append("")

        if score >= 90:
            report.append("🟢 Отличная карточка.")

        elif score >= 75:
            report.append("🟢 Хорошая карточка.")

        elif score >= 60:
            report.append("🟡 Карточка требует доработки.")

        else:
            report.append("🔴 Карточка нуждается в серьёзной оптимизации.")

        return "\n".join(report)