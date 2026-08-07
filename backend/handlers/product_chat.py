"""
Режим «🧠 Обсудить товар» + «📊 Полный отчёт».

Поток:
    анализ товара -> кнопка "🧠 Обсудить товар"
        -> состояние ProductChat.discussing
        -> все сообщения идут к Seller AI с контекстом ЭТОГО товара
        -> кнопка "✅ Закончить диалог" возвращает в меню

Seller AI помнит товар через SessionService,
поэтому понимает вопросы вида «А если поднять цену на 300 рублей?».
"""

import html

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from backend.ai.brain import SellerBrain
from backend.ai.report import ReportBuilder
from backend.config import AI_NAME
from backend.keyboards.inline import (
    after_analysis_kb,
    back_kb,
    discuss_kb,
    discuss_entry_kb,
)
from backend.services.session import SessionService
from backend.states import ProductChat

router = Router()

_report_builder = ReportBuilder()


# ----------------------------------------------------------- вход в диалог

@router.callback_query(F.data == "product:discuss")
async def start_discussion(
    callback: CallbackQuery,
    state: FSMContext,
    session: SessionService,
    brain: SellerBrain,
):
    product = session.get_product(callback.from_user.id)

    if product is None:
        await callback.message.answer(
            "🤔 Пока нечего обсуждать — сначала проанализируйте товар.",
            reply_markup=discuss_entry_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.set_state(ProductChat.discussing)

    # Новое обсуждение начинается с чистого листа.
    brain.forget(callback.from_user.id)

    title = html.escape(product.title or "товар")

    await callback.message.answer(
        f"🧠 <b>Обсуждаем:</b> {title}\n"
        "\n"
        "Спрашивайте что угодно об этом товаре:\n"
        "\n"
        "<i>— А если поднять цену на 300 рублей?\n"
        "— С чего начать улучшение карточки?\n"
        "— Какую рекламу запустить?</i>\n"
        "\n"
        "Я помню всё, что мы уже обсудили 👇",
        reply_markup=discuss_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ----------------------------------------------------------- сам диалог

@router.message(ProductChat.discussing)
async def handle_discussion(
    message: Message,
    state: FSMContext,
    session: SessionService,
    brain: SellerBrain,
):
    user_id = message.from_user.id
    product = session.get_product(user_id)

    if product is None:
        # Бот перезапустился, память пуста.
        await state.clear()
        await message.answer(
            "🤔 Я потерял товар из памяти. Проанализируйте его заново.",
            reply_markup=discuss_entry_kb(),
        )
        return

    question = (message.text or "").strip()

    if not question:
        await message.answer(
            "Напишите вопрос текстом 🙂",
            reply_markup=discuss_kb(),
        )
        return

    status = await message.answer("🧠 Думаю...")

    # force_product_mode=True — любая реплика трактуется
    # как разговор про эту карточку.
    answer = await brain.reply(
        user_id,
        question,
        force_product_mode=True,
    )

    if not answer:
        await status.edit_text(
            f"😴 {AI_NAME} сейчас недоступен.\n"
            "Попробуй чуть позже.",
            reply_markup=discuss_kb(),
        )
        return

    await status.edit_text(
        html.escape(answer.text),
        reply_markup=discuss_kb(),
        parse_mode="HTML",
    )


# ----------------------------------------------------------- конец диалога

@router.callback_query(F.data == "product:discuss_end")
async def end_discussion(
    callback: CallbackQuery,
    state: FSMContext,
    session: SessionService,
    brain: SellerBrain,
):
    await state.clear()
    brain.forget(callback.from_user.id)

    await callback.message.answer(
        "✅ Диалог завершён.\n"
        "Товар остаётся в памяти — можно вернуться к нему в любой момент.",
        reply_markup=after_analysis_kb(),
    )
    await callback.answer()


# ----------------------------------------------------------- полный отчёт

@router.callback_query(F.data == "product:full")
async def show_full_report(
    callback: CallbackQuery,
    session: SessionService,
):
    user_id = callback.from_user.id
    product = session.get_product(user_id)
    analysis = session.get_analysis(user_id)

    if product is None or analysis is None:
        await callback.message.answer(
            "🤔 Отчёта пока нет — сначала проанализируйте товар.",
            reply_markup=discuss_entry_kb(),
        )
        await callback.answer()
        return

    text = _report_builder.build_full_report(product, analysis)

    await callback.message.answer(
        text,
        reply_markup=after_analysis_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()
