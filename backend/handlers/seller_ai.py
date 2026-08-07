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

from backend.ai.brain import SellerBrain
from backend.ai.intents import Intent
from backend.config import AI_NAME
from backend.keyboards.inline import ai_chat_kb, back_kb
from backend.states import SellerAIChat

router = Router()


@router.message(SellerAIChat.waiting_for_question)
async def handle_question(
    message: Message,
    state: FSMContext,
    brain: SellerBrain,
):
    question = (message.text or "").strip()

    if not question:
        await message.answer(
            "Напиши вопрос текстом 🙂",
            reply_markup=back_kb(),
        )
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
        await status.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
