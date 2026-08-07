"""
product.py — контекст последнего разобранного товара.

Отвечает за требование «Seller AI обязан помнить название, цену,
рейтинг, отзывы, Score, плюсы, минусы и рекомендации».
"""

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.config import AI_NAME


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

        lines = [
            f"Название: {product.title or 'не определено'}",
            f"Бренд: {product.brand or 'не указан'}",
            f"Артикул: {product.article}",
        ]

        if product.price:
            price_line = f"Цена: {product.price} руб."
            if product.old_price and product.old_price > product.price:
                price_line += f" (до скидки {product.old_price} руб., -{product.discount or 0}%)"
            lines.append(price_line)
        else:
            lines.append("Цена: нет данных")

        lines.append(f"Рейтинг: {product.rating or 'нет данных'}")
        lines.append(f"Отзывов: {product.feedbacks or 0}")
        lines.append(f"Фотографий: {len(product.photos)}")
        lines.append(f"Характеристик заполнено: {len(product.characteristics)}")
        lines.append(f"Длина описания: {len(product.description or '')} символов")

        total_qty = getattr(product, "total_qty", 0)
        if total_qty:
            warehouses = len(getattr(product, "warehouses", []) or [])
            lines.append(f"Остаток: {total_qty} шт на {warehouses} складах")

        if analysis:
            lines.append("")
            lines.append(f"Оценка {AI_NAME}: {analysis.get('score', '?')}/100")

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

        return ContextBlock(
            title="ТОВАР В РАБОТЕ (продавец разбирал его последним)",
            body="\n".join(lines),
            priority=10,
        )


def _clean(text: str) -> str:
    """Убираем эмодзи из начала строки — модели они в контексте не нужны."""
    return text.lstrip("✅👍⭐💬📋🔥⚠️❌📸🎨📝💰🚀🛠📦 ").strip()
