from backend.wb.models import WBProduct


class RecommendationGenerator:

    def generate(self, product: WBProduct):

        recommendations = []

        # Фото
        if len(product.photos) < 8:
            recommendations.append(
                "📸 Добавьте больше фотографий товара (желательно 8–12)."
            )

        if len(product.photos) < 5:
            recommendations.append(
                "🎨 Сделайте инфографику на первых изображениях."
            )

        # Описание
        if not product.description:
            recommendations.append(
                "📝 Добавьте подробное описание товара."
            )

        elif len(product.description) < 300:
            recommendations.append(
                "📝 Сделайте описание более подробным."
            )

        # Характеристики
        if len(product.characteristics) < 15:
            recommendations.append(
                "📋 Заполните больше характеристик товара."
            )

        # Рейтинг
        if product.rating is not None and product.rating < 4.7:
            recommendations.append(
                "⭐ Улучшайте качество товара и собирайте положительные отзывы."
            )

        # Отзывы
        if product.feedbacks is not None and product.feedbacks < 100:
            recommendations.append(
                "💬 Увеличьте количество отзывов через акции и программу отзывов."
            )

        # Цена
        if product.price is None:
            recommendations.append(
                "💰 Не удалось определить цену. Проверьте API или BrowserProvider."
            )

        if not recommendations:
            recommendations.append(
                "✅ Карточка выглядит очень хорошо. Критичных замечаний нет."
            )

        return recommendations