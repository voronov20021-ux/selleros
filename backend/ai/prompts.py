"""
prompts.py — промпты разовых задач (не диалога).

Диалог с продавцом живёт в backend/ai/brain.py и собирает промпт
из personality.py + context/. Здесь остаётся то, что вызывается
одним запросом без истории: разбор карточки после анализа.

Правило проекта:
    Промпты НЕ пишутся внутри хендлеров или сервисов.
"""

from backend.ai.intents import Intent
from backend.ai.personality import build_system
from backend.config import AI_NAME


def build_product_analysis_prompt(product) -> str:
    """Промпт анализа карточки. product — WBProduct."""

    characteristics = "\n".join(
        f"- {name}: {value}"
        for name, value in list(product.characteristics.items())[:15]
    ) or "нет данных"

    description = (product.description or "нет описания")[:1500]

    return f"""Ты — {AI_NAME}, эксперт по продажам на маркетплейсе Wildberries.
Продавец прислал свою карточку товара. Помоги ему увеличить продажи.

ДАННЫЕ КАРТОЧКИ

Название: {product.title or "нет"}
Бренд: {product.brand or "нет"}
Цена: {product.price or "нет данных"} руб.
Старая цена: {product.old_price or "нет данных"} руб.
Рейтинг: {product.rating or "нет данных"}
Отзывов: {product.feedbacks or 0}
Фотографий: {len(product.photos)}

Характеристики:
{characteristics}

Описание:
{description}

ЗАДАНИЕ

Дай краткий экспертный разбор строго в таком формате:

1. Сильные стороны (2-3 пункта)
2. Слабые места (2-3 пункта)
3. Конкретные действия для роста продаж (3-4 пункта, каждый начинай с глагола)

ПРАВИЛА ОТВЕТА

- Пиши по-русски, коротко и по делу, без воды.
- Не используй Markdown (звёздочки, решётки) — только обычный текст,
  нумерацию и тире.
- Весь ответ — не длиннее 1200 символов.
- Не выдумывай данные, которых нет в карточке.
"""


def build_full_analysis_prompt(product, seller_data) -> str:
    """
    Промпт «📈 Полного анализа» — карточка (product) + данные продавца
    (seller_data) явно разделены на два раздела, CARD DATA и SELLER DATA.

    Важно: AI не должен сам догадываться о цене/рейтинге/отзывах.
    Если seller_data.price/rating/feedbacks is None — модель получает
    буквально "unavailable", а не отсутствие строки и не 0 — так модель
    не может ни придумать число, ни спутать "нет данных" с "ноль".
    """

    characteristics = "\n".join(
        f"- {name}: {value}"
        for name, value in list(product.characteristics.items())[:15]
    ) or "нет данных"

    description = (product.description or "нет описания")[:1500]

    price = "unavailable" if seller_data.price is None else f"{seller_data.price} руб."
    rating = "unavailable" if seller_data.rating is None else str(seller_data.rating)
    feedbacks = "unavailable" if seller_data.feedbacks is None else str(seller_data.feedbacks)
    sales = "unavailable" if seller_data.sales is None else str(seller_data.sales)
    orders = "unavailable" if seller_data.orders is None else str(seller_data.orders)
    period = "unavailable" if not seller_data.period else seller_data.period

    return f"""Ты — {AI_NAME}, эксперт по продажам на маркетплейсе Wildberries.
Продавец прислал карточку товара и данные о продавце. Помоги ему увеличить продажи.

CARD DATA (получено автоматически с карточки Wildberries)

Название: {product.title or "нет"}
Бренд: {product.brand or "нет"}
Фотографий: {len(product.photos)}

Характеристики:
{characteristics}

Описание:
{description}

SELLER DATA (указано продавцом или получено через Seller API)

Цена: {price}
Рейтинг: {rating}
Отзывов: {feedbacks}
Продажи: {sales}
Заказы: {orders}
Период: {period}

ЗАДАНИЕ

Дай краткий экспертный разбор строго в таком формате:

1. Сильные стороны (2-3 пункта)
2. Слабые места (2-3 пункта)
3. Конкретные действия для роста продаж (3-4 пункта, каждый начинай с глагола)

ПРАВИЛА ОТВЕТА

- Пиши по-русски, коротко и по делу, без воды.
- Не используй Markdown (звёздочки, решётки) — только обычный текст,
  нумерацию и тире.
- Весь ответ — не длиннее 1200 символов.
- Значение "unavailable" означает, что этих данных НЕТ. Не придумывай
  их и не заменяй предположением — если данных не хватает для вывода,
  так и скажи.
"""


def _product_summary(product) -> str:
    """Короткая сводка товара для контекста AI."""
    return (
        f"Название: {product.title or 'нет'}\n"
        f"Бренд: {product.brand or 'нет'}\n"
        f"Артикул: {product.article}\n"
        f"Цена: {product.price or 'нет данных'} руб. "
        f"(старая: {product.old_price or '—'}, скидка: {product.discount or 0}%)\n"
        f"Рейтинг: {product.rating or 'нет'} · Отзывов: {product.feedbacks or 0}\n"
        f"Фото: {len(product.photos)} · "
        f"Характеристик: {len(product.characteristics)}"
    )


def build_analysis_system() -> str:
    """
    Системный промпт для разбора карточки.

    Используем плейбук PRODUCT_DISCUSSION — он и написан именно
    под разбор конкретного товара по его реальным цифрам.
    """
    return build_system(Intent.PRODUCT_DISCUSSION)
