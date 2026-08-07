from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from backend.keyboards.inline import main_menu_kb
from backend.config import AI_NAME

router = Router()

WELCOME_TEXT = (
    "🏠 <b>SellerOS</b>\n"
    "<i>AI-помощник продавца маркетплейсов</i>\n"
    "\n"
    f"Что умеет {AI_NAME}:\n"
    "\n"
    "📦 Разбирает карточку товара по ссылке\n"
    "🧠 Обсуждает товар и помнит контекст\n"
    "📈 Составляет план действий на день\n"
    "📅 Собирает отчёты по вашим анализам\n"
    "\n"
    "Выберите раздел 👇"
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Сбрасываем состояние: /start всегда возвращает в начало.
    await state.clear()

    # У старых пользователей могла остаться ReplyKeyboard
    # из прошлых версий — убираем её техническим сообщением.
    cleanup = await message.answer(
        "⌛",
        reply_markup=ReplyKeyboardRemove(),
    )
    try:
        await cleanup.delete()
    except Exception:
        pass

    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
