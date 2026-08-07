import html

from backend.config import AI_NAME

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
        return f"• {label}: не указано"

    suffix = f" {unit}" if unit else ""
    source_label = _SOURCE_LABELS.get(source or "")
    source_part = f" (Источник: {source_label})" if source_label else ""

    return f"• {label}: {value}{suffix}{source_part}"


def verdict_for(score: int) -> str:
    """Единый вердикт по Score — используется в карточке и истории."""
    if score >= 90:
        return "🟢 Отличная карточка"
    if score >= 75:
        return "🟢 Хорошая карточка"
    if score >= 60:
        return "🟡 Требует доработки"
    return "🔴 Нужна оптимизация"


class ReportBuilder:

    def build_card(
        self,
        product,
        score_data: dict,
        recommendations: list,
        ai_comment: str | None = None,
    ) -> str:
        """
        Красивая карточка анализа для Telegram (parse_mode=HTML).

        Структура:
            шапка -> цена/рейтинг -> описание ->
            плюсы -> минусы -> советы Seller AI -> итог
        """

        score = score_data["score"]
        reasons = score_data["reasons"]

        pluses = [r for r in reasons if r.startswith(_POSITIVE)]
        minuses = [r for r in reasons if r.startswith(_NEGATIVE)]

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

        # --- Советы Seller AI ---
        if recommendations:
            lines.append("")
            lines.append(f"<b>Советы {AI_NAME}</b>")
            for recommendation in recommendations:
                lines.append(f"• {recommendation}")

        # --- Живой комментарий Seller AI (если AI доступен) ---
        if ai_comment:
            lines.append("")
            lines.append(f"<b>Разбор от {AI_NAME}</b>")
            lines.append(html.escape(ai_comment.strip()))

        # --- Итог ---
        lines.append("")
        lines.append(f"<b>{verdict_for(score)}.</b>")

        return "\n".join(lines)

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

    def build_preliminary(self, product, score_data: dict, recommendations: list) -> str:
        """
        «🤖 Предварительный анализ» — строится ТОЛЬКО по данным карточки
        (контент): название, описание, характеристики, фото. Цена/рейтинг/
        отзывы сюда не подставляются, даже если где-то есть — предварительный
        анализ намеренно не претендует на оценку эффективности продаж.

        Если price/rating/feedbacks отсутствуют — честно перечисляем это
        как то, что нужно для точного анализа, вместо того чтобы молчать
        или показывать 0.
        """
        score = score_data["score"]
        reasons = score_data["reasons"]

        pluses = [r for r in reasons if r.startswith(_POSITIVE)]
        minuses = [r for r in reasons if r.startswith(_NEGATIVE)]

        # Совет про цену из RecommendationGenerator упоминает
        # API/BrowserProvider — техническая формулировка, которая тут не
        # нужна: ниже даём собственный явный блок про нехватку seller-данных.
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

        lines.append("<b>Данные карточки</b>")
        lines.append(f"📝 Описание: {'есть' if product.description else 'нет'}")
        lines.append(f"🖼 Фото: {len(product.photos)}")
        lines.append(f"⚙️ Характеристики: {len(product.characteristics)}")

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
            missing.append("• количество отзывов")

        if missing:
            lines.append("")
            lines.append("⚠️ <b>Для точного анализа не хватает данных продавца:</b>")
            lines.extend(missing)
            lines.append("")
            lines.append("Нажмите «📊 Точный анализ», чтобы указать их.")

        return "\n".join(lines)

    def build_full_with_sections(
        self,
        product,
        seller_data,
        score_data: dict,
        recommendations: list,
        ai_comment: str | None = None,
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

        lines = []

        lines.append("📈 <b>Полный анализ</b>")
        lines.append("")

        # --- 📦 Данные карточки (WBProduct) ---
        lines.append("📦 <b>Данные карточки</b>")
        lines.append(f"• Название: {html.escape(product.title or 'Без названия')}")
        if product.brand:
            lines.append(f"• Бренд: {html.escape(product.brand)}")
        lines.append(f"• Артикул: <code>{product.article}</code>")
        lines.append(f"• Описание: {'есть' if product.description else 'нет'}")
        lines.append(f"• Фото: {len(product.photos)}")
        lines.append(f"• Характеристики: {len(product.characteristics)}")

        # --- 📊 Данные продавца (SellerData) ---
        lines.append("")
        lines.append("📊 <b>Данные продавца</b>")
        lines.append(_seller_line("💰 Цена", seller_data.price, "₽", seller_data.price_source))
        lines.append(_seller_line("⭐ Рейтинг", seller_data.rating, "", seller_data.rating_source))
        lines.append(_seller_line("💬 Отзывов", seller_data.feedbacks, "", seller_data.feedbacks_source))

        if seller_data.sales is not None:
            lines.append(_seller_line("📈 Продажи", seller_data.sales, "", seller_data.sales_source))
        if seller_data.orders is not None:
            lines.append(_seller_line("📦 Заказы", seller_data.orders, "", seller_data.orders_source))
        if seller_data.period:
            lines.append(f"• Период: {html.escape(seller_data.period)}")

        # --- 🧠 Анализ ---
        lines.append("")
        lines.append("🧠 <b>Анализ</b>")
        lines.append(f"Оценка {AI_NAME}: <b>{score}/100</b> · {verdict_for(score)}")

        # --- ⚠️ Проблемы ---
        if minuses:
            lines.append("")
            lines.append("⚠️ <b>Проблемы</b>")
            lines.extend(minuses)

        # --- 💡 Рекомендации ---
        if recommendations:
            lines.append("")
            lines.append("💡 <b>Рекомендации</b>")
            for recommendation in recommendations:
                lines.append(f"• {recommendation}")

        if ai_comment:
            lines.append("")
            lines.append(f"<b>Разбор от {AI_NAME}</b>")
            lines.append(html.escape(ai_comment.strip()))

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