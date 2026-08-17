from backend.wb.models import WBProduct


class ScoreCalculator:
    """
    Оценка карточки (card-only).

    Не использует CTR/CVR/рекламу — отсутствие PRIVATE-метрик
    не штрафует score. Breakdown объясняет вклад блоков.
    """

    def calculate(self, product: WBProduct):
        score = 0
        reasons = []
        breakdown: dict[str, int] = {
            "photos": 0,
            "description": 0,
            "rating": 0,
            "feedbacks": 0,
            "characteristics": 0,
        }

        # Фото
        photos = len(product.photos or [])

        if photos >= 8:
            breakdown["photos"] = 20
            reasons.append("✅ Много фотографий")
        elif photos >= 5:
            breakdown["photos"] = 15
            reasons.append("👍 Достаточно фотографий")
        elif photos >= 3:
            breakdown["photos"] = 8
            reasons.append("⚠️ Мало фотографий")
        else:
            reasons.append("❌ Очень мало фотографий")
        score += breakdown["photos"]

        # Описание
        if product.description:
            if len(product.description) > 300:
                breakdown["description"] = 15
                reasons.append("✅ Хорошее описание")
            elif len(product.description) > 100:
                breakdown["description"] = 10
                reasons.append("👍 Есть описание")
            else:
                breakdown["description"] = 5
                reasons.append("⚠️ Короткое описание")
        else:
            reasons.append("❌ Нет описания")
        score += breakdown["description"]

        # Рейтинг — отсутствие данных ≠ штраф «CTR», просто 0 баллов блока
        if product.rating is not None:
            if product.rating >= 4.8:
                breakdown["rating"] = 20
                reasons.append("⭐ Отличный рейтинг")
            elif product.rating >= 4.5:
                breakdown["rating"] = 15
                reasons.append("⭐ Хороший рейтинг")
            elif product.rating >= 4.0:
                breakdown["rating"] = 10
                reasons.append("⚠️ Средний рейтинг")
            else:
                breakdown["rating"] = 3
                reasons.append("⚠️ Низкий рейтинг")
            score += breakdown["rating"]
        else:
            reasons.append("ℹ️ Рейтинг: нет данных (блок не штрафует CTR)")

        # Отзывы (card_feedbacks)
        if product.feedbacks is not None:
            if product.feedbacks >= 1000:
                breakdown["feedbacks"] = 20
                reasons.append("💬 Очень много отзывов")
            elif product.feedbacks >= 300:
                breakdown["feedbacks"] = 15
                reasons.append("💬 Много отзывов")
            elif product.feedbacks >= 50:
                breakdown["feedbacks"] = 8
                reasons.append("💬 Есть отзывы")
            elif product.feedbacks > 0:
                breakdown["feedbacks"] = 3
                reasons.append("💬 Мало отзывов на карточке")
            score += breakdown["feedbacks"]
        else:
            reasons.append("ℹ️ Отзывы на карточке: нет данных")

        # Характеристики
        chars = len(product.characteristics or {})

        if chars >= 20:
            breakdown["characteristics"] = 15
            reasons.append("📋 Хорошо заполнены характеристики")
        elif chars >= 10:
            breakdown["characteristics"] = 10
            reasons.append("📋 Достаточно характеристик")
        elif chars >= 5:
            breakdown["characteristics"] = 5
            reasons.append("📋 Мало характеристик")
        score += breakdown["characteristics"]

        if score > 100:
            score = 100

        return {
            "score": score,
            "reasons": reasons,
            "breakdown": breakdown,
            "scope": "card_only",  # CTR/CVR не входят в score
            "max_points": {
                "photos": 20,
                "description": 15,
                "rating": 20,
                "feedbacks": 20,
                "characteristics": 15,
            },
        }
