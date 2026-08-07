class AIRecommendations:

    def analyze(self, product: dict):

        recommendations = []

        price = product.get("salePriceU", 0) / 100
        rating = product.get("reviewRating", 0)
        feedbacks = product.get("feedbacks", 0)

        if rating < 4.6:
            recommendations.append(
                "⭐ Низкий рейтинг. Проверь качество товара и отзывы."
            )

        if feedbacks < 30:
            recommendations.append(
                "💬 Мало отзывов. Запусти сбор отзывов."
            )

        if price > 50000:
            recommendations.append(
                "💰 Высокая цена. Сравни с конкурентами."
            )

        if not recommendations:
            recommendations.append(
                "✅ Карточка выглядит хорошо."
            )

        return recommendations