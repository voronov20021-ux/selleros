"""
Экран добавления товара.

Поток (с этапа «staged product-analysis flow»):
    кнопка "➕ Добавить товар"
        -> состояние waiting_for_link
        -> пользователь присылает ссылку/артикул
        -> "⏳ Получаем карточку товара..."
        -> фото + "✅ Товар добавлен" (только данные карточки)
        -> кнопки "🤖 Предварительный анализ" / "📊 Точный анализ"

Полный AI-анализ (со Score и рекомендациями) больше НЕ запускается
автоматически здесь — это отдельные шаги в
backend/handlers/product_analysis.py, потому что цена/рейтинг/отзывы
могут быть ещё не получены (WB card API их не всегда отдаёт), и это
не должно выглядеть как ошибка добавления товара.

Товар берём ТОЛЬКО через ProductService —
хендлер не знает, откуда пришли данные (BrowserPool сегодня,
Seller API завтра, Ozon послезавтра).
"""

import html
import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from backend.keyboards.inline import cancel_kb, product_added_kb
from backend.services.card_parser import parse_marketplace_link
from backend.services.product_service import ProductService
from backend.services.session import SessionService
from backend.states import AnalyzeCard

log = logging.getLogger("selleros.analyze")

router = Router()


def _added_text(product) -> str:
    """«✅ Товар добавлен» — только то, что реально получено с карточки."""

    lines = ["✅ <b>Товар добавлен</b>", ""]

    lines.append(f"📦 Название: {html.escape(product.title or 'не определено')}")
    lines.append(f"📝 Описание: {'есть' if product.description else 'нет'}")
    lines.append(f"🖼 Фото: {len(product.photos)}")
    lines.append(f"⚙️ Характеристики: {len(product.characteristics)}")

    # Честно показываем то, чего парсер сейчас не получил, а не молчим
    # и не подставляем 0/None как реальное значение.
    if product.price is None:
        lines.append("💰 Цена: не получена автоматически")
    if product.rating is None:
        lines.append("⭐ Рейтинг: не получен автоматически")
    if product.feedbacks is None:
        lines.append("💬 Отзывы: не получены автоматически")

    lines.append("")
    lines.append("Для предварительного анализа данных достаточно.")

    return "\n".join(lines)


@router.message(AnalyzeCard.waiting_for_link)
async def handle_link(
    message: Message,
    state: FSMContext,
    product_service: ProductService,
    session: SessionService,
):
    text = (message.text or "").strip()

    marketplace, article = parse_marketplace_link(text)

    # --- Валидация ссылки -------------------------------------------------

    if marketplace is None:
        await message.answer(
            "🤔 Это не похоже на ссылку карточки.\n"
            "\n"
            "Пришлите ссылку вида:\n"
            "<code>https://www.wildberries.ru/catalog/211246754/detail.aspx</code>",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return

    if marketplace == "Ozon":
        await message.answer(
            "🟠 Ozon подключим совсем скоро!\n"
            "\n"
            "Пока пришлите ссылку Wildberries 👇",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return

    # --- Получение карточки -------------------------------------------------

    status = await message.answer("⏳ Получаем карточку товара...")

    product = await product_service.get_product("wildberries", int(article))

    if product is None:
        await status.edit_text(
            "😔 Не удалось получить карточку.\n"
            "\n"
            "Проверьте ссылку или попробуйте чуть позже.",
            reply_markup=cancel_kb(),
        )
        return

    # Seller AI запоминает товар — теперь его можно обсуждать.
    # Полный анализ (Score/рекомендации) здесь ещё НЕ считается — это
    # отдельный шаг («Предварительный анализ» / «Точный анализ»), поэтому
    # analysis не передаём (в set_product он необязателен).
    await session.set_product(
        user_id=message.from_user.id,
        product=product,
    )

    # Карточка получена — выходим из состояния ожидания ссылки.
    await state.clear()

    # --- Красивый вывод: фото + подтверждение добавления --------------------

    photo_sent = False

    if product.photos:
        try:
            await message.answer_photo(
                photo=product.photos[0],
                caption=html.escape(product.title or "Товар добавлен")[:1000],
            )
            photo_sent = True
        except Exception as error:
            log.warning("Фото не отправилось: %s", error)

    await status.delete()

    await message.answer(
        _added_text(product),
        reply_markup=product_added_kb(),
        parse_mode="HTML",
    )

    if not photo_sent:
        log.info("Товар %s добавлен без фото", product.article)
