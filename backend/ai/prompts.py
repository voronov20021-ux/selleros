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


def build_product_analysis_prompt(product, advisor_text: str | None = None) -> str:
    """Промпт анализа карточки. product — WBProduct."""

    characteristics = "\n".join(
        f"- {name}: {value}"
        for name, value in list(product.characteristics.items())[:15]
    ) or "нет данных"

    description = (product.description or "нет описания")[:1500]

    advisor_section = ""
    if advisor_text:
        advisor_section = f"""

ADVISOR PLAN (уже собран детерминированно — опирайся, не противоречь):
{advisor_text[:2000]}
"""

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
{advisor_section}
ЗАДАНИЕ

Дай краткий экспертный разбор. Если есть ADVISOR PLAN — оберни его живым языком,
сохрани цепочку: главный вывод → где проблема (locus) → что хорошо → что подтверждено →
что не доказано → что делать → чего не хватает → приоритет P1/P2/P3.
Не противоречь диагнозу: без PRICE-риска не советуй снижать цену.
Не выдумывай факты, %, цены, магазины, CTR/CVR, рост рынка вне данных выше.
IDEA из плана — не жалобы покупателей. Фото без разбора кадров — не выдумывай CV.
Если карточка здоровая и нет экономики — «ничего критичного / ничего не менять».
«Проанализировано N» — используй именно N.
Если CTR/CVR/заказов нет — не пиши «системной проблемы с продажами не наблюдается».
Пиши: «Системной проблемы по доступным данным пока не видно.»
Рейтинг и число отзывов — факты, не «высокое доверие» и не причина продаж.
Число фото — счётчик, не плюс и не качество. Жалобы (царапины, повреждения,
ожидания, упаковка) не клади в «что уже хорошо».

Если ADVISOR PLAN нет, используй формат:
1. Главный вывод (1-2 предложения)
2. Что уже хорошо / что подтверждено
3. Что делать (2-3 шага) + чего не хватает

ПРАВИЛА ОТВЕТА

- Пиши по-русски, коротко и по делу, без воды.
- Не используй Markdown (звёздочки, решётки) — только обычный текст,
  нумерацию и тире.
- Весь ответ — не длиннее 1200 символов.
- Не выдумывай данные, которых нет в карточке / Advisor Plan.
"""


def build_full_analysis_prompt(product, seller_data, advisor_text: str | None = None) -> str:
    """
    Промпт «📈 Полного анализа» — CARD DATA и SELLER/PRIVATE явно разделены.

    public_price ≠ seller_price. Не выдумывать CTR/CVR/продажи/заказы.
    unavailable / «нет данных» — буквально нет данных.
    """
    from backend.wb.provenance import field_provenance_label

    characteristics = "\n".join(
        f"- {name}: {value}"
        for name, value in list(product.characteristics.items())[:15]
    ) or "нет данных"

    description = (product.description or "нет описания")[:1500]

    def _card_val(field, unit=""):
        val = getattr(product, field, None)
        if val is None:
            return "нет данных"
        prov = field_provenance_label(product, field)
        base = f"{val}{(' ' + unit) if unit else ''}"
        return f"{base} [Источник: {prov}]" if prov else base

    card_price = _card_val("price", "руб.")
    card_rating = _card_val("rating")
    card_feedbacks = _card_val("feedbacks")

    def _priv(attr, unit=""):
        if seller_data is None:
            return "нет данных"
        val = getattr(seller_data, attr, None)
        if val is None:
            return "нет данных"
        return f"{val}{(' ' + unit) if unit else ''}"

    advisor_section = ""
    if advisor_text:
        advisor_section = f"""

ADVISOR PLAN (детерминированный — опирайся, не противоречь):
{advisor_text[:2000]}
"""

    return f"""Ты — {AI_NAME}, эксперт по продажам на маркетплейсе Wildberries.
Продавец прислал карточку товара. Помоги увеличить продажи. Не выдумывай цифры.

CARD DATA (публичная карточка Wildberries / PUBLIC_BROWSER)

Название: {product.title or "нет"}
Бренд: {product.brand or "нет"}
Фотографий: {len(product.photos)}
Публичная цена (public_price): {card_price}
Рейтинг карточки: {card_rating}
Отзывов на карточке (card_feedbacks): {card_feedbacks}

Характеристики:
{characteristics}

Описание:
{description}

SELLER / PRIVATE ANALYTICS (только если продавец указал; иначе «нет данных»)
Цена продавца (seller_price): {_priv("price", "руб.")}
Рейтинг продавца: {_priv("rating")}
Отзывов продавца: {_priv("feedbacks")}
CTR: {_priv("ctr")}
CVR: {_priv("cvr")}
Показы: {_priv("impressions")}
Просмотры: {_priv("views")}
Продажи: {_priv("sales")}
Заказы: {_priv("orders")}
Возвраты: {_priv("returns")}
Рекламные расходы: {_priv("ad_spend")}
Себестоимость: {_priv("cost")}
Комиссия: {_priv("commission")}
Логистика: {_priv("logistics")}
Хранение: {_priv("storage")}
Период: {_priv("period")}
{advisor_section}
ЗАДАНИЕ

Дай краткий экспертный разбор. Если есть ADVISOR PLAN — оберни живым языком,
сохраняя: главный вывод → где проблема (X не Y) → что хорошо → подтверждено →
не доказано → что делать → чего не хватает → приоритет P1/P2/P3.
IDEA ≠ жалобы покупателей и не в «что подтверждено». Не выдумывай факты вне данных.
«нет данных» / unavailable означает, что этих данных НЕТ — так и скажи.
Если CTR/CVR нет — напиши: «не могу оценить CTR/CVR».
Не путай public_price и seller_price.
Не пиши универсальные советы без основания в CARD/Advisor/отзывах.
Для отзывов опирайся на processed_reviews из Advisor, не на выдуманный n.
Если карточка здоровая и нет экономики — можно сказать «ничего не трогай».
Если CTR/CVR/заказов нет — не пиши «системной проблемы с продажами не наблюдается».
Пиши: «Системной проблемы по доступным данным пока не видно.»
Не превращай рейтинг/число отзывов в «высокое доверие» или вывод про продажи.
Verified поле (рейтинг, цена, число отзывов) ≠ diagnostic confidence.
Не пиши «цена адекватная» без confirmed commercial fields конкурентов.
Не пиши, что характеристики негативно влияют на продажи, без доказанной связи.
Не пиши «есть спрос» без CTR/CVR/заказов или sales.
Число фото ≠ плюс/качество. Не клади жалобы из отзывов в «что уже хорошо».

Если ADVISOR PLAN нет:
1. Главный вывод
2. Что подтверждено / что не доказано
3. Что делать + чего не хватает

ПРАВИЛА ОТВЕТА

- Пиши по-русски, коротко и по делу, без воды.
- Не используй Markdown (звёздочки, решётки) — только обычный текст,
  нумерацию и тире.
- Весь ответ — не длиннее 1200 символов.
- Не придумывай CTR, CVR, заказы, возвраты, маржу, рекламу, число отзывов.
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
