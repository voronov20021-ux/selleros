"""
Режим «🧠 Seller AI» — свободные вопросы.

Хендлер не строит промпты и не знает про модели.
Он передаёт сообщение в SellerBrain и показывает ответ.
Вся логика (классификация, контекст, память) — в backend/ai/brain.py
"""

import html

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from backend.api.miniapp_catalog import parse_article_input
from backend.ai.brain import SellerBrain
from backend.ai.intents import Intent
from backend.config import AI_NAME
from backend.handlers.analyze import ingest_wb_card
from backend.keyboards.inline import ai_chat_kb, back_kb
from backend.services.product_service import ProductService
from backend.services.session import SessionService
from backend.states import SellerAIChat
from backend.utils.telegram_split import answer_long, edit_or_answer_long

router = Router()


@router.message(SellerAIChat.waiting_for_question)
async def handle_question(
    message: Message,
    state: FSMContext,
    brain: SellerBrain,
    product_service: ProductService | None = None,
    session: SessionService | None = None,
    wb_reviews=None,
):
    question = (message.text or "").strip()

    if not question:
        await message.answer(
            "Напиши вопрос текстом 🙂",
            reply_markup=back_kb(),
        )
        return

    if product_service is not None and session is not None and parse_article_input(question):
        await ingest_wb_card(
            message,
            state,
            product_service,
            session,
            brain=brain,
            wb_reviews=wb_reviews,
            require_article=True,
        )
        await state.set_state(SellerAIChat.waiting_for_question)
        return

    # На «привет» и «спасибо» Seller AI отвечает мгновенно,
    # без обращения к модели — статус-сообщение тут лишнее.
    quick = brain.is_quick(question)

    status = None
    if not quick:
        status = await message.answer("🧠 Думаю...")

    answer = await brain.reply(message.from_user.id, question)

    # Диалог продолжается: остаёмся в режиме вопросов,
    # чтобы можно было спрашивать дальше без нажатия кнопки.
    await state.set_state(SellerAIChat.waiting_for_question)

    if not answer:
        text = (
            f"😴 {AI_NAME} сейчас недоступен.\n"
            "\n"
            "Анализ карточек при этом работает —\n"
            "загляни в 📦 Анализ товара."
        )
        if status:
            await status.edit_text(text, reply_markup=back_kb())
        else:
            await message.answer(text, reply_markup=back_kb())
        return

    text = html.escape(answer.text)

    # Под болтовнёй кнопка «Ещё вопрос» выглядит нелепо.
    keyboard = back_kb() if answer.intent is Intent.SMALL_TALK else ai_chat_kb()

    if status:
        await edit_or_answer_long(
            status,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            fallback_message=message,
        )
    else:
        await answer_long(message, text, reply_markup=keyboard, parse_mode="HTML")
