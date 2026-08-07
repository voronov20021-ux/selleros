from backend.wb.models import WBProduct


class CardAnalyzer:


    def analyze(self, product: WBProduct):

        problems = []
        recommendations = []


        if product.photos < 5:
            problems.append(
                "Мало фотографий товара"
            )

            recommendations.append(
                "Добавить больше фото: детали, размеры, использование"
            )


        if product.reviews < 100:
            problems.append(
                "Недостаточно отзывов"
            )

            recommendations.append(
                "Стимулировать получение первых 100 отзывов"
            )


        if product.rating < 4.5:
            problems.append(
                "Низкий рейтинг"
            )

            recommendations.append(
                "Работать с качеством товара и отзывами"
            )


        if not problems:
            problems.append(
                "Критичных проблем карточки не найдено"
            )


        return {
            "product": product.name,
            "problems": problems,
            "recommendations": recommendations
        }