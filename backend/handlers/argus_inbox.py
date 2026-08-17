"""Catch-all ARGUS inbox. Must be included LAST so FSM handlers win first."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from backend.config import AI_NAME
from backend.handlers.analyze import ASK_ARTICLE, ingest_wb_card
from backend.keyboards.inline import main_menu_kb
from backend.services.product_service import ProductService
from backend.services.session import SessionService
from backend.states import SellerAIChat

router = Router()


@router.message(StateFilter(None), F.text)
async def argus_inbox(
    message: Message,
    state: FSMContext,
    product_service: ProductService,
    session: SessionService,
    brain=None,
    wb_reviews=None,
):
    if await state.get_state() is not None:
        return

    handled = await ingest_wb_card(
        message,
        state,
        product_service,
        session,
        brain=brain,
        wb_reviews=wb_reviews,
        require_article=False,
    )
    if handled:
        return

    if session.has_product(message.from_user.id) and brain is not None:
        from backend.handlers.seller_ai import handle_question

        await state.set_state(SellerAIChat.waiting_for_question)
        await handle_question(
            message,
            state,
            brain,
            product_service=product_service,
            session=session,
            wb_reviews=wb_reviews,
        )
        return

    await message.answer(
        f"🧠 <b>{AI_NAME}</b>\n\n{ASK_ARTICLE}",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
