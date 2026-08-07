"""
DailyPlanner — раздел «📈 Что сделать сегодня».

Seller AI формирует список действий на день
по последнему проанализированному товару.

Пока — на правилах поверх Score (по плану этапа 3).
Когда подключим живой AI и Seller API (заказы, остатки, CTR) —
план станет умнее, но экран и интерфейс не изменятся.
"""


class DailyPlanner:

    def build_plan(self, product, analysis: dict | None) -> list[str]:
        """Список конкретных действий на сегодня."""

        if product is None or analysis is None:
            return []

        actions: list[str] = []
        score = analysis.get("score", 0)

        # --- Контент карточки ---
        if len(product.photos) < 8:
            actions.append("📸 Улучшить фото: довести до 8–12 снимков, добавить инфографику")

        if not product.description or len(product.description) < 300:
            actions.append("📝 Переписать описание: подробно, с ключевыми словами")

        if len(product.characteristics) < 15:
            actions.append("📋 Дозаполнить характеристики — это влияет на поиск")

        # --- Репутация ---
        if product.rating and product.rating < 4.7:
            actions.append("💬 Ответить на негативные отзывы и разобрать причины")

        if product.feedbacks is not None and product.feedbacks < 100:
            actions.append("⭐ Собрать отзывы: подключить «Отзывы за баллы»")

        # --- Цена ---
        if product.price and not product.discount:
            actions.append("💰 Проверить цену: скидки нет — карточка проигрывает в выдаче")

        # --- Остатки ---
        if getattr(product, "total_qty", 0) and product.total_qty < 20:
            actions.append(f"📦 Пополнить остатки: на складах всего {product.total_qty} шт")

        # --- Реклама ---
        if score >= 60:
            actions.append("🚀 Запустить рекламу: карточка готова принимать трафик")
        else:
            actions.append("🛠 Сначала доработать карточку, потом включать рекламу")

        return actions
