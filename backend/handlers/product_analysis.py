"""
Экраны нового flow анализа товара:

    ✅ Товар добавлен
        -> 🤖 Предварительный анализ   (только данные карточки)
        -> 📊 Точный анализ            (данные продавца)
              -> нет данных -> ✏️ Ввести данные / 🔑 Подключить API
              -> есть данные -> 🔄 Обновить / 📈 Получить полный анализ

Правила проекта, важные именно для этого файла:
    - Никогда не обращаемся к WB Engine / ProductService за ценой заново —
      товар уже в SessionService (или его больше нет вообще, если сессия
      пуста — тогда честно просим прислать ссылку заново).
    - Цена/рейтинг/отзывы карточки (WBProduct) и продавца (SellerData)
      никогда не смешиваются в отчёте — см. build_full_with_sections().
    - Никогда не выдумываем отсутствующие значения.
"""

import logging
from dataclasses import replace
from datetime import datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from backend.ai.prompts import build_analysis_system, build_full_analysis_prompt
from backend.ai.recommendations import RecommendationGenerator
from backend.ai.report import ReportBuilder, verdict_for
from backend.ai.score import ScoreCalculator
from backend.config import AI_NAME
from backend.keyboards.inline import (
    after_analysis_kb,
    after_preliminary_kb,
    api_connect_info_kb,
    cancel_kb,
    discuss_entry_kb,
    manual_input_skip_kb,
    precise_data_kb,
    precise_data_ready_kb,
)
from backend.memory import MemoryStore
from backend.services.history import HistoryService
from backend.services.seller_data import SOURCE_USER, SellerData
from backend.services.session import SessionService
from backend.states import ManualSellerData

log = logging.getLogger("selleros.product_analysis")

router = Router()

_score_calculator = ScoreCalculator()
_recommendation_generator = RecommendationGenerator()
_report_builder = ReportBuilder()

_NO_PRODUCT_TEXT = "🤔 Товар не найден в текущей сессии — пришлите ссылку заново."


async def _show(callback: CallbackQuery, text: str, keyboard) -> None:
    """Аккуратно перерисовать экран (или отправить новый, если нельзя)."""
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


async def _no_product(callback: CallbackQuery) -> None:
    await callback.message.answer(_NO_PRODUCT_TEXT, reply_markup=discuss_entry_kb())
    await callback.answer()


# ------------------------------------------------------------------ парсинг ввода

def _parse_price(text: str) -> float | None:
    try:
        value = float((text or "").strip().replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_rating(text: str) -> float | None:
    try:
        value = float((text or "").strip().replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None


def _parse_nonneg_int(text: str) -> int | None:
    try:
        value = int((text or "").strip())
    except ValueError:
        return None
    return value if value >= 0 else None


# --------------------------------------------------------------- seller data

async def _load_seller_data(
    session: SessionService,
    memory: MemoryStore,
    user_id: int,
    article: int,
) -> SellerData | None:
    """
    SellerData текущей сессии, а если бот перезапускался — последний
    сохранённый снимок этого товара у ЭТОГО продавца из MemoryStore.
    """
    cached = session.get_seller_data(user_id)
    if cached is not None:
        return cached

    if memory is None:
        return None

    record = await memory.get_product(user_id, article)
    if record is None:
        return None

    if record.price is None and record.rating is None and record.feedbacks is None:
        return None

    updated_at = (
        datetime.fromtimestamp(record.seller_updated_at)
        if record.seller_updated_at
        else None
    )

    return SellerData(
        price=record.price,
        rating=record.rating,
        feedbacks=record.feedbacks,
        sales=record.sales,
        orders=record.orders,
        period=record.period,
        price_source=record.price_source,
        rating_source=record.rating_source,
        feedbacks_source=record.feedbacks_source,
        # Продажи/заказы/период отдельных колонок-источников в БД не имеют
        # (см. backend/memory/store.py) — они вводятся в одном заходе с
        # ценой/рейтингом/отзывами, поэтому используем тот же источник.
        sales_source=record.price_source if record.sales is not None else None,
        orders_source=record.price_source if record.orders is not None else None,
        period_source=record.price_source if record.period else None,
        updated_at=updated_at,
    )


def _seller_data_summary_text(seller_data: SellerData) -> str:
    lines = ["📊 <b>Данные продавца</b>", ""]

    lines.append(
        f"💰 Цена: {seller_data.price} ₽" if seller_data.price is not None
        else "💰 Цена: не указано"
    )
    lines.append(
        f"⭐ Рейтинг: {seller_data.rating}" if seller_data.rating is not None
        else "⭐ Рейтинг: не указано"
    )
    lines.append(
        f"💬 Отзывы: {seller_data.feedbacks}" if seller_data.feedbacks is not None
        else "💬 Отзывы: не указано"
    )

    if seller_data.sales is not None:
        lines.append(f"📈 Продажи: {seller_data.sales}")
    if seller_data.orders is not None:
        lines.append(f"📦 Заказы: {seller_data.orders}")
    if seller_data.period:
        lines.append(f"⏳ Период: {seller_data.period}")

    if seller_data.updated_at:
        lines.append("")
        lines.append(f"<i>Обновлено: {seller_data.updated_at.strftime('%d.%m.%Y %H:%M')}</i>")

    return "\n".join(lines)


# ------------------------------------------------------------- предварительный анализ

@router.callback_query(F.data == "product:prelim")
async def show_preliminary(callback: CallbackQuery, session: SessionService):
    user_id = callback.from_user.id
    product = session.get_product(user_id)

    if product is None:
        await _no_product(callback)
        return

    analysis = session.get_analysis(user_id)

    # Предварительный анализ ЭТОГО ЖЕ товара уже показан выше — не считаем
    # его заново и не шлём дубликат того же сообщения (например, если
    # кнопку нажали ещё раз со старого/повторно открытого экрана).
    # Явный пересчёт — только через «🔄 Повторить анализ».
    if (
        analysis is not None
        and analysis.get("kind") == "preliminary"
        and analysis.get("article") == product.article
    ):
        await callback.answer("Предварительный анализ уже показан выше ⬆️")
        return

    await _render_preliminary(callback, session, product)


@router.callback_query(F.data == "product:prelim_retry")
async def retry_preliminary(callback: CallbackQuery, session: SessionService):
    """Явный пересчёт предварительного анализа — единственный путь его повторить."""
    product = session.get_product(callback.from_user.id)

    if product is None:
        await _no_product(callback)
        return

    await _render_preliminary(callback, session, product)


async def _render_preliminary(callback: CallbackQuery, session: SessionService, product) -> None:
    score_data = _score_calculator.calculate(product)
    recommendations = _recommendation_generator.generate(product)

    text = _report_builder.build_preliminary(product, score_data, recommendations)

    # Запоминаем, что предварительный анализ ЭТОГО товара уже показан —
    # используется в show_preliminary(), чтобы не дублировать сообщение.
    # История анализов (MemoryStore.add_analysis / HistoryService) сюда
    # не пишется — как и раньше, туда попадает только «Получить полный
    # анализ» (см. show_full_analysis), предварительный анализ в неё не лезет.
    await session.set_product(
        user_id=callback.from_user.id,
        product=product,
        analysis={
            "kind": "preliminary",
            "article": product.article,
            "score": score_data["score"],
            "reasons": score_data["reasons"],
            "recommendations": recommendations,
        },
    )

    # Кнопка «🤖 Предварительный анализ» здесь больше не нужна — он уже
    # показан. Дальше — точный анализ или явный повтор.
    await _show(callback, text, after_preliminary_kb())


# ------------------------------------------------------------------- точный анализ

@router.callback_query(F.data == "product:precise")
async def show_precise(callback: CallbackQuery, session: SessionService, memory: MemoryStore):
    user_id = callback.from_user.id
    product = session.get_product(user_id)

    if product is None:
        await _no_product(callback)
        return

    seller_data = await _load_seller_data(session, memory, user_id, product.article)

    if seller_data is None or not seller_data.has_minimum():
        text = (
            "Для точного анализа нам нужны данные продавца:\n"
            "\n"
            "💰 Цена\n"
            "⭐ Средняя оценка\n"
            "💬 Количество отзывов"
        )
        await _show(callback, text, precise_data_kb())
        return

    await _show(callback, _seller_data_summary_text(seller_data), precise_data_ready_kb())


# ------------------------------------------------------------------- ручной ввод

@router.callback_query(F.data == "product:manual_input")
async def start_manual_input(callback: CallbackQuery, state: FSMContext, session: SessionService):
    if session.get_product(callback.from_user.id) is None:
        await _no_product(callback)
        return

    await state.set_state(ManualSellerData.waiting_for_price)

    await callback.message.answer(
        "💰 Укажите текущую цену товара:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(ManualSellerData.waiting_for_price)
async def handle_price(message: Message, state: FSMContext):
    price = _parse_price(message.text or "")

    if price is None:
        await message.answer(
            "🤔 Цена должна быть числом больше 0. Попробуйте ещё раз:",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(price=price)
    await state.set_state(ManualSellerData.waiting_for_rating)
    await message.answer("⭐ Укажите среднюю оценку товара от 0 до 5:", reply_markup=cancel_kb())


@router.message(ManualSellerData.waiting_for_rating)
async def handle_rating(message: Message, state: FSMContext):
    rating = _parse_rating(message.text or "")

    if rating is None:
        await message.answer(
            "🤔 Оценка должна быть числом от 0 до 5. Попробуйте ещё раз:",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(rating=rating)
    await state.set_state(ManualSellerData.waiting_for_feedbacks)
    await message.answer("💬 Укажите количество отзывов:", reply_markup=cancel_kb())


@router.message(ManualSellerData.waiting_for_feedbacks)
async def handle_feedbacks(message: Message, state: FSMContext):
    feedbacks = _parse_nonneg_int(message.text or "")

    if feedbacks is None:
        await message.answer(
            "🤔 Количество отзывов должно быть целым числом от 0. Попробуйте ещё раз:",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(feedbacks=feedbacks)
    await state.set_state(ManualSellerData.waiting_for_extra_choice)

    await message.answer(
        "Хотите добавить дополнительные данные?\n"
        "\n"
        "📈 Продажи\n"
        "📦 Заказы",
        reply_markup=manual_input_skip_kb(),
    )


@router.callback_query(F.data == "manual:extra_skip", ManualSellerData.waiting_for_extra_choice)
async def skip_extra(callback: CallbackQuery, state: FSMContext, session: SessionService):
    seller_data = await _finish_manual_input(callback.from_user.id, state, session)

    await callback.message.answer(
        "✅ Данные сохранены.\n\n" + _seller_data_summary_text(seller_data),
        reply_markup=precise_data_ready_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "manual:extra_add", ManualSellerData.waiting_for_extra_choice)
async def add_extra(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ManualSellerData.waiting_for_sales)

    await callback.message.answer(
        "📈 Укажите продажи за период (число), либо «-» чтобы пропустить:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(ManualSellerData.waiting_for_sales)
async def handle_sales(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if text == "-":
        await state.update_data(sales=None)
    else:
        sales = _parse_nonneg_int(text)
        if sales is None:
            await message.answer(
                "🤔 Продажи должны быть целым числом от 0, либо «-» чтобы пропустить:",
                reply_markup=cancel_kb(),
            )
            return
        await state.update_data(sales=sales)

    await state.set_state(ManualSellerData.waiting_for_orders)
    await message.answer(
        "📦 Укажите заказы за период (число), либо «-» чтобы пропустить:",
        reply_markup=cancel_kb(),
    )


@router.message(ManualSellerData.waiting_for_orders)
async def handle_orders(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if text == "-":
        await state.update_data(orders=None)
    else:
        orders = _parse_nonneg_int(text)
        if orders is None:
            await message.answer(
                "🤔 Заказы должны быть целым числом от 0, либо «-» чтобы пропустить:",
                reply_markup=cancel_kb(),
            )
            return
        await state.update_data(orders=orders)

    await state.set_state(ManualSellerData.waiting_for_period)
    await message.answer(
        "⏳ Укажите период (например «30 дней»), либо «-» чтобы пропустить:",
        reply_markup=cancel_kb(),
    )


@router.message(ManualSellerData.waiting_for_period)
async def handle_period(message: Message, state: FSMContext, session: SessionService):
    text = (message.text or "").strip()
    period = None if text == "-" else text
    await state.update_data(period=period)

    seller_data = await _finish_manual_input(message.from_user.id, state, session)

    await message.answer(
        "✅ Данные сохранены.\n\n" + _seller_data_summary_text(seller_data),
        reply_markup=precise_data_ready_kb(),
        parse_mode="HTML",
    )


async def _finish_manual_input(
    user_id: int,
    state: FSMContext,
    session: SessionService,
) -> SellerData:
    """Собрать SellerData из накопленных данных FSM, сохранить, сбросить состояние."""
    data = await state.get_data()
    product = session.get_product(user_id)

    seller_data = SellerData(
        price=data.get("price"),
        rating=data.get("rating"),
        feedbacks=data.get("feedbacks"),
        sales=data.get("sales"),
        orders=data.get("orders"),
        period=data.get("period"),
        price_source=SOURCE_USER,
        rating_source=SOURCE_USER,
        feedbacks_source=SOURCE_USER,
        sales_source=SOURCE_USER if data.get("sales") is not None else None,
        orders_source=SOURCE_USER if data.get("orders") is not None else None,
        period_source=SOURCE_USER if data.get("period") is not None else None,
        updated_at=datetime.now(),
    )

    if product is not None:
        await session.set_seller_data(user_id, product.article, seller_data)
    else:
        log.warning("Ручной ввод завершён, но товар %s уже не в сессии", user_id)

    await state.clear()
    return seller_data


# ------------------------------------------------------------------- полный анализ

@router.callback_query(F.data == "product:full_analysis")
async def show_full_analysis(
    callback: CallbackQuery,
    session: SessionService,
    memory: MemoryStore,
    history: HistoryService,
    ai_service,
):
    user_id = callback.from_user.id
    product = session.get_product(user_id)

    if product is None:
        await _no_product(callback)
        return

    seller_data = await _load_seller_data(session, memory, user_id, product.article)

    if seller_data is None or not seller_data.has_minimum():
        await callback.message.answer(
            "🤔 Сначала укажите данные продавца — цену, рейтинг и отзывы.",
            reply_markup=precise_data_kb(),
        )
        await callback.answer()
        return

    await callback.answer()
    status = await callback.message.answer(f"⏳ {AI_NAME} готовит полный анализ...")

    # Score считаем по тем же правилам, что и раньше (ScoreCalculator уже
    # умеет работать без цены/рейтинга/отзывов), но подставляем данные
    # ПРОДАВЦА вместо пустых полей карточки — WBProduct и так умеет их
    # хранить, отдельная модель не нужна. Это временная копия ТОЛЬКО для
    # подсчёта очков — исходный product (и session) не меняем, чтобы не
    # перезаписать уже сохранённые в products price/rating/feedbacks.
    scoring_product = replace(
        product,
        price=seller_data.price,
        rating=seller_data.rating,
        feedbacks=seller_data.feedbacks,
    )

    score_data = _score_calculator.calculate(scoring_product)
    recommendations = _recommendation_generator.generate(scoring_product)

    ai_comment = None
    if ai_service is not None:
        prompt = build_full_analysis_prompt(product, seller_data)
        ai_comment = await ai_service.generate(prompt, system=build_analysis_system())

        if ai_comment is None:
            log.warning("AI недоступен — полный анализ будет без AI-комментария")

    text = _report_builder.build_full_with_sections(
        product=product,
        seller_data=seller_data,
        score_data=score_data,
        recommendations=recommendations,
        ai_comment=ai_comment,
    )

    await history.add(
        user_id=user_id,
        article=product.article,
        title=product.title or "Без названия",
        score=score_data["score"],
        price=seller_data.price,
        verdict=verdict_for(score_data["score"]),
    )

    await status.delete()

    await callback.message.answer(
        text,
        reply_markup=after_analysis_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ------------------------------------------------------------------ подключить API

@router.callback_query(F.data == "product:connect_api")
async def show_connect_api(callback: CallbackQuery):
    # Реализация не нужна прямо сейчас — заготовки уже есть в проекте:
    # WBSellerAPIProvider, SellerAPISource, SellerStatsProvider. Когда
    # появится ключ, экран останется тем же — поменяется только то,
    # что скрыто за is_available()/api_key.
    text = (
        "🔑 <b>Подключение Seller API</b>\n"
        "\n"
        "Для максимально точного анализа можно подключить API вашего магазина.\n"
        "\n"
        "После подключения SellerOS сможет получать данные продавца напрямую, "
        "например:\n"
        "• цену\n"
        "• рейтинг\n"
        "• отзывы\n"
        "• продажи\n"
        "• заказы\n"
        "• другие доступные показатели\n"
        "\n"
        "Пока подключение API находится в подготовке."
    )
    await _show(callback, text, api_connect_info_kb())
