from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from backend.keyboards.inline import main_menu_kb
from backend.config import AI_NAME

router = Router()

WELCOME_TEXT = (
    f"🧠 <b>{AI_NAME}</b>\n"
    "<i>Помощник продавца Wildberries</i>\n"
    "\n"
    "Пришлите артикул WB или ссылку на товар — я разберу его.\n"
    "\n"
    "Или откройте раздел:"
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
