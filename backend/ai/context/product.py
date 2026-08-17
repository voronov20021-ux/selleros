"""
product.py — контекст последнего разобранного товара.

CARD DATA и SELLER/PRIVATE явно разделены.
public_price ≠ seller_price. Отсутствующее = «нет данных».
"""

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.config import AI_NAME
from backend.wb.provenance import field_provenance_label


class ProductContextSource(ContextSource):

    name = "product"
    # Полезен всегда: почти любой вопрос продавца — про его товар.
    intents = frozenset()

    def __init__(self, session):
        self.session = session

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        product = self.session.get_product(request.user_id)

        if product is None:
            return None

        analysis = self.session.get_analysis(request.user_id) or {}
        get_seller = getattr(self.session, "get_seller_data", None)
        seller = (
            get_seller(request.user_id, article=product.article)
            if callable(get_seller)
            else None
        )

        lines = [
            "=== CARD DATA ===",
            f"Название: {product.title or 'не определено'}",
            f"Бренд: {product.brand or 'не указан'}",
            f"Артикул: {product.article}",
        ]

        def _card_line(label: str, field: str, unit: str = "") -> str:
            val = getattr(product, field, None)
            if val is None:
                return f"{label}: нет данных"
            suffix = f" {unit}" if unit else ""
            prov = field_provenance_label(product, field)
            if prov:
                return f"{label}: {val}{suffix} (Источник: {prov})"
            return f"{label}: {val}{suffix}"

        price_line = _card_line("Цена карточки (public_price)", "price", "руб.")
        if product.price is not None and product.old_price and product.old_price > product.price:
            price_line += f" (до скидки {product.old_price} руб., -{product.discount or 0}%)"
        lines.append(price_line)
        lines.append(_card_line("Рейтинг карточки", "rating"))
        lines.append(_card_line("Отзывов на карточке (card_feedbacks)", "feedbacks"))
        lines.append(f"Фотографий: {len(product.photos)}")
        lines.append(f"Характеристик заполнено: {len(product.characteristics)}")
        lines.append(f"Длина описания: {len(product.description or '')} символов")

        total_qty = getattr(product, "total_qty", 0)
        if total_qty:
            warehouses = len(getattr(product, "warehouses", []) or [])
            lines.append(f"Остаток: {total_qty} шт на {warehouses} складах")

        lines.append("")
        lines.append("=== SELLER / PRIVATE ANALYTICS ===")
        if seller is not None:
            lines.append(
                f"Цена продавца (seller_price): {seller.price} руб."
                if seller.price is not None
                else "Цена продавца (seller_price): нет данных"
            )
            lines.append(
                f"Рейтинг продавца: {seller.rating}"
                if seller.rating is not None
                else "Рейтинг продавца: нет данных"
            )
            lines.append(
                f"Отзывов продавца: {seller.feedbacks}"
                if seller.feedbacks is not None
                else "Отзывов продавца: нет данных"
            )
            for label, attr in (
                ("CTR", "ctr"),
                ("CVR", "cvr"),
                ("Продажи", "sales"),
                ("Заказы", "orders"),
                ("Возвраты", "returns"),
                ("Реклама", "ad_spend"),
                ("Себестоимость", "cost"),
                ("Комиссия", "commission"),
                ("Логистика", "logistics"),
                ("Хранение", "storage"),
            ):
                val = getattr(seller, attr, None)
                lines.append(f"{label}: {val if val is not None else 'нет данных'}")
            if seller.period:
                lines.append(f"Период: {seller.period}")
        else:
            lines.append("Цена продавца (seller_price): нет данных")
            lines.append("CTR: нет данных")
            lines.append("CVR: нет данных")
            lines.append("Продажи: нет данных")
            lines.append("Заказы: нет данных")
            lines.append("Возвраты: нет данных")
            lines.append("Реклама: нет данных")

        if analysis:
            lines.append("")
            lines.append("=== ANALYSIS ===")
            lines.append(f"Оценка {AI_NAME}: {analysis.get('score', '?')}/100")
            if analysis.get("kind"):
                lines.append(f"Тип анализа: {analysis.get('kind')}")

            reasons = analysis.get("reasons") or []
            pluses = [r for r in reasons if r.startswith(("✅", "👍", "⭐", "💬", "📋", "🔥"))]
            minuses = [r for r in reasons if r.startswith(("⚠️", "❌"))]

            if pluses:
                lines.append("Сильные стороны: " + "; ".join(_clean(p) for p in pluses))
            if minuses:
                lines.append("Слабые места: " + "; ".join(_clean(m) for m in minuses))

            recommendations = analysis.get("recommendations") or []
            if recommendations:
                lines.append("Что уже рекомендовано:")
                for item in recommendations[:6]:
                    lines.append(f"- {_clean(item)}")

        get_pc = getattr(self.session, "get_product_context_prompt", None)
        if callable(get_pc):
            extra = get_pc(request.user_id)
            if isinstance(extra, str) and extra.strip():
                lines.append("")
                lines.append(extra.strip())

        get_cc = getattr(self.session, "get_competitor_context_prompt", None)
        if callable(get_cc):
            comp_extra = get_cc(request.user_id)
            if isinstance(comp_extra, str) and comp_extra.strip():
                lines.append("")
                lines.append(comp_extra.strip())

        is_active = getattr(self.session, "is_discussion_active", None)
        get_msgs = getattr(self.session, "get_discussion_messages", None)
        if callable(is_active) and is_active(request.user_id, product.article) and callable(get_msgs):
            msgs = get_msgs(request.user_id) or []
            if msgs:
                lines.append("")
                lines.append("ПОСЛЕДНИЕ РЕПЛИКИ ОБСУЖДЕНИЯ:")
                for msg in msgs[-6:]:
                    role = "Продавец" if msg.get("role") == "user" else AI_NAME
                    content = (msg.get("content") or "")[:200]
                    lines.append(f"- {role}: {content}")

        return ContextBlock(
            title="ТОВАР В РАБОТЕ (продавец разбирал его последним)",
            body="\n".join(lines),
            priority=10,
        )


def _clean(text: str) -> str:
    """Убираем эмодзи из начала строки — модели они в контексте не нужны."""
    return text.lstrip("✅👍⭐💬📋🔥⚠️❌📸🎨📝💰🚀🛠📦 ").strip()
