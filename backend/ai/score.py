from backend.wb.models import WBProduct


class ScoreCalculator:

    def calculate(self, product: WBProduct):

        score = 0

        reasons = []

        # Фото
        photos = len(product.photos)

        if photos >= 8:
            score += 20
            reasons.append("✅ Много фотографий")
        elif photos >= 5:
            score += 15
            reasons.append("👍 Достаточно фотографий")
        elif photos >= 3:
            score += 8
            reasons.append("⚠️ Мало фотографий")
        else:
            reasons.append("❌ Очень мало фотографий")

        # Описание
        if product.description:
            if len(product.description) > 300:
                score += 15
                reasons.append("✅ Хорошее описание")
            elif len(product.description) > 100:
                score += 10
                reasons.append("👍 Есть описание")
            else:
                score += 5
                reasons.append("⚠️ Короткое описание")
        else:
            reasons.append("❌ Нет описания")

        # Рейтинг
        if product.rating:

            if product.rating >= 4.8:
                score += 20
                reasons.append("⭐ Отличный рейтинг")

            elif product.rating >= 4.5:
                score += 15
                reasons.append("⭐ Хороший рейтинг")

            elif product.rating >= 4.0:
                score += 10
                reasons.append("⚠️ Средний рейтинг")

        # Отзывы
        if product.feedbacks:

            if product.feedbacks >= 1000:
                score += 20
                reasons.append("💬 Очень много отзывов")

            elif product.feedbacks >= 300:
                score += 15
                reasons.append("💬 Много отзывов")

            elif product.feedbacks >= 50:
                score += 8
                reasons.append("💬 Есть отзывы")

        # Характеристики
        chars = len(product.characteristics)

        if chars >= 20:
            score += 15
            reasons.append("📋 Хорошо заполнены характеристики")

        elif chars >= 10:
            score += 10
            reasons.append("📋 Достаточно характеристик")

        elif chars >= 5:
            score += 5
            reasons.append("📋 Мало характеристик")

        if score > 100:
            score = 100

        return {
            "score": score,
            "reasons": reasons
        }