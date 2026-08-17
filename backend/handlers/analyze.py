"""
Быстрый ARGUS в Telegram: nmID / WB URL → тот же ProductService.

Полный разбор — Mini App (кнопка с nmID). Здесь только короткие факты.
"""

import html
import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from backend.api.miniapp_catalog import parse_article_input
from backend.config import AI_NAME
from backend.keyboards.inline import cancel_kb, product_added_kb
from backend.services.card_parser import parse_marketplace_link
from backend.services.product_service import ProductService
from backend.services.session import SessionService
from backend.states import AnalyzeCard

log = logging.getLogger("selleros.analyze")

router = Router()

ASK_ARTICLE = "Пришлите артикул WB или ссылку на товар — я разберу его."


def persist_focused_article(user_id: int, article: int) -> None:
    """Thin MiniAppStore sticky so Mini App ARGUS restores this nmID."""
    try:
        from backend.api.miniapp_store import MiniAppStore

        MiniAppStore().upsert(str(int(user_id)), sticky_article=int(article))
    except Exception as exc:
        log.debug("sticky persist skip: %s", exc)


def _fmt_num(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        text = f"{val:.1f}".rstrip("0").rstrip(".")
        return text
    return str(val)


def _quick_argus_text(product) -> str:
    title = html.escape(product.title or "без названия")
    article = getattr(product, "article", "")
    price = getattr(product, "price", None)
    rating = getattr(product, "rating", None)
    feedbacks = getattr(product, "feedbacks", None)
    price_s = f"{_fmt_num(price)} ₽" if price is not None else "цена —"
    return (
        f"🧠 <b>{html.escape(AI_NAME)}</b>\n"
        f"📦 {title}\n"
        f"nmID <code>{html.escape(str(article))}</code>\n"
        f"{price_s} · рейтинг {_fmt_num(rating)} · отзывы {_fmt_num(feedbacks)}\n"
        "\n"
        "Карточка распознана. Полный разбор — в Seller OS."
    )


@router.message(AnalyzeCard.waiting_for_link)
async def handle_link(
    message: Message,
    state: FSMContext,
    product_service: ProductService,
    session: SessionService,
    brain=None,
    wb_reviews=None,
):
    await ingest_wb_card(
        message,
        state,
        product_service,
        session,
        brain=brain,
        wb_reviews=wb_reviews,
        require_article=True,
    )


async def ingest_wb_card(
    message: Message,
    state: FSMContext,
    product_service: ProductService,
    session: SessionService,
    *,
    brain=None,
    wb_reviews=None,
    require_article: bool = True,
) -> bool:
    """Parse nmID/WB URL, fetch via ProductService, persist sticky, short reply.

    Returns True if a card was processed (or a marketplace error was shown).
    """
    text = (message.text or "").strip()
    marketplace, article = parse_marketplace_link(text)
    if marketplace is None:
        article = parse_article_input(text)
        if article:
            marketplace = "Wildberries"

    if marketplace == "Ozon":
        await message.answer(
            "🟠 Ozon подключим совсем скоро!\n"
            "\n"
            "Пока пришлите артикул WB или ссылку 👇",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return True

    if marketplace is None or article is None:
        if not require_article:
            return False
        await message.answer(
            ASK_ARTICLE,
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return True

    status = await message.answer("⏳ Смотрю карточку...")

    user_id = message.from_user.id
    previous = session.get_product(user_id)
    if hasattr(product_service, "get_product_snapshot"):
        product = await product_service.get_product_snapshot(
            "wildberries",
            int(article),
            session_product=previous,
        )
    else:
        product = await product_service.get_product("wildberries", int(article))

    if product is None:
        await status.edit_text(
            "Не удалось получить карточку. Проверьте артикул или ссылку.",
            reply_markup=cancel_kb(),
        )
        return True

    prev_article = getattr(previous, "article", None) if previous is not None else None

    await session.set_product(
        user_id=user_id,
        product=product,
    )
    persist_focused_article(user_id, int(product.article))

    if wb_reviews is not None:
        try:
            await wb_reviews.load_into_session(session, user_id, product)
        except Exception as exc:
            log.warning("WB reviews load skipped: %s", exc)

    if brain is not None and (prev_article is None or prev_article != product.article):
        brain.forget(user_id)

    await state.clear()

    if product.photos:
        try:
            await message.answer_photo(
                photo=product.photos[0],
                caption=html.escape(product.title or "Товар")[:200],
            )
        except Exception as error:
            log.warning("Фото не отправилось: %s", error)

    try:
        await status.delete()
    except Exception:
        pass

    await message.answer(
        _quick_argus_text(product),
        reply_markup=product_added_kb(int(product.article)),
        parse_mode="HTML",
    )
    return True
