"""
Экраны нового flow анализа товара:

    ✅ Товар добавлен
        -> 🤖 Предварительный анализ   (публичные данные карточки)
        -> 📊 Точный анализ
              -> публичные price/rating/feedbacks есть (CARD / VERIFIED)
                    -> полный анализ без повторного ввода
                    -> опционально «➕ Добавить данные продавца» (PRIVATE metrics)
              -> часть публичных отсутствует
                    -> ввод только недостающих полей, затем optional private
              -> есть SellerData/private -> сводка + полный анализ

Правила:
    - public_price ≠ seller_price; не автосоздавать seller из public.
    - Не выдумывать CTR/CVR/продажи/заказы/маржу.
    - Provenance (PUBLIC_BROWSER · verified nm_id) в отчёте/контексте.
"""

import logging
from dataclasses import replace
from datetime import datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from backend.ai.advisor import build_advisor_plan
from backend.ai.prompts import build_analysis_system, build_full_analysis_prompt
from backend.ai.recommendations import RecommendationGenerator
from backend.ai.report import ReportBuilder, verdict_for
from backend.ai.score import ScoreCalculator
from backend.config import AI_NAME
from backend.keyboards.inline import (
    action_recommend_kb,
    after_analysis_kb,
    after_preliminary_kb,
    api_connect_info_kb,
    cancel_kb,
    discuss_entry_kb,
    manual_input_skip_kb,
    precise_data_kb,
    precise_data_ready_kb,
    precise_data_stale_seller_kb,
)
from backend.memory import MemoryStore
from backend.services.history import HistoryService
from backend.services.parsers import (
    parse_nonneg_int as _parse_nonneg_int,
    parse_price as _parse_price,
    parse_rating as _parse_rating,
)
from backend.services.product_service import (
    has_public_commercial_minimum,
    missing_public_commercial_fields,
)
from backend.services.seller_data import PRIVATE_METRIC_FIELDS, SOURCE_USER, SellerData
from backend.services.session import SessionService
from backend.states import ManualSellerData
from backend.utils.telegram_split import answer_long, edit_or_answer_long
from backend.wb.provenance import field_provenance_label

log = logging.getLogger("selleros.product_analysis")

router = Router()

_score_calculator = ScoreCalculator()
_recommendation_generator = RecommendationGenerator()
_report_builder = ReportBuilder()

_NO_PRODUCT_TEXT = "🤔 Товар не найден в текущей сессии — пришлите ссылку заново."


async def _get_market_bundle(product, category_intelligence) -> tuple[list, object | None]:
    """
    Рыночные рекомендации + CategoryContext.
    Возвращает ([], None) при любой ошибке или если layer недоступен.
    """
    if category_intelligence is None:
        return [], None
    category = getattr(product, "subject_name", None)
    if not category:
        return [], None
    try:
        ctx = await category_intelligence.analyze(category=category, region="RU", limit=20)
        recs = _recommendation_generator.generate_market_recommendations(ctx)
        return recs, ctx
    except Exception as exc:
        log.warning("_get_market_bundle: ошибка для %r: %s", category, exc)
        return [], None


async def _get_market_recs(product, category_intelligence) -> list:
    """
    Получить рыночные рекомендации из Intelligence Layer.
    Возвращает [] при любой ошибке или если layer недоступен.
    """
    recs, _ctx = await _get_market_bundle(product, category_intelligence)
    return recs


async def _show(callback: CallbackQuery, text: str, keyboard) -> None:
    """Аккуратно перерисовать экран (или отправить новый, если нельзя)."""
    await edit_or_answer_long(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


async def _no_product(callback: CallbackQuery) -> None:
    await callback.message.answer(_NO_PRODUCT_TEXT, reply_markup=discuss_entry_kb())
    await callback.answer()


# --------------------------------------------------------------- seller data

def _record_to_seller_data(record) -> SellerData | None:
    """Собрать SellerData из ProductRecord; None если нет seller-origin полей."""
    has_commercial_src = any(
        getattr(record, f, None) in (SOURCE_USER, "api")
        for f in ("price_source", "rating_source", "feedbacks_source")
    )
    has_private = any(
        getattr(record, name, None) is not None for name in PRIVATE_METRIC_FIELDS
    ) or bool(getattr(record, "period", None))

    # Публичные price/rating без source — карточка WB, не продавец.
    if not has_commercial_src and not has_private:
        return None

    # Коммерцию продавца берём только при явном source (не путать с CARD).
    price = record.price if record.price_source in (SOURCE_USER, "api") else None
    rating = record.rating if record.rating_source in (SOURCE_USER, "api") else None
    feedbacks = (
        record.feedbacks if record.feedbacks_source in (SOURCE_USER, "api") else None
    )

    if (
        price is None and rating is None and feedbacks is None and not has_private
    ):
        return None

    updated_at = (
        datetime.fromtimestamp(record.seller_updated_at)
        if record.seller_updated_at
        else None
    )
    return SellerData(
        price=price,
        rating=rating,
        feedbacks=feedbacks,
        sales=record.sales,
        orders=record.orders,
        period=record.period,
        ctr=getattr(record, "ctr", None),
        cvr=getattr(record, "cvr", None),
        impressions=getattr(record, "impressions", None),
        views=getattr(record, "views", None),
        returns=getattr(record, "returns", None),
        ad_spend=getattr(record, "ad_spend", None),
        cost=getattr(record, "cost", None),
        commission=getattr(record, "commission", None),
        logistics=getattr(record, "logistics", None),
        storage=getattr(record, "storage", None),
        price_source=record.price_source if price is not None else None,
        rating_source=record.rating_source if rating is not None else None,
        feedbacks_source=record.feedbacks_source if feedbacks is not None else None,
        sales_source=SOURCE_USER if record.sales is not None else None,
        orders_source=SOURCE_USER if record.orders is not None else None,
        period_source=SOURCE_USER if record.period else None,
        updated_at=updated_at,
        confirmed_current=False,
    )


async def _peek_memory_seller_data(
    memory: MemoryStore,
    user_id: int,
    article: int,
) -> SellerData | None:
    """SellerData из памяти — без записи в session (stale candidate)."""
    if memory is None:
        return None
    record = await memory.get_product(user_id, article)
    if record is None:
        return None
    return _record_to_seller_data(record)


def _confirmed_seller_data(
    session: SessionService,
    user_id: int,
    article: int,
) -> SellerData | None:
    """Только подтверждённые в текущей сессии данные продавца."""
    cached = session.get_seller_data(user_id, article=article)
    if cached is None:
        return None
    if getattr(cached, "confirmed_current", False):
        return cached
    return None


def _seller_data_summary_text(seller_data: SellerData) -> str:
    lines = ["👤 <b>Данные продавца</b>", ""]

    if seller_data.price is not None:
        lines.append(f"💰 Цена продавца: {seller_data.price} ₽")
    if seller_data.rating is not None:
        lines.append(f"⭐ Рейтинг продавца: {seller_data.rating}")
    if seller_data.feedbacks is not None:
        lines.append(f"💬 Отзывы продавца: {seller_data.feedbacks}")

    private_lines = []
    if seller_data.ctr is not None:
        private_lines.append(f"CTR: {seller_data.ctr}")
    if seller_data.cvr is not None:
        private_lines.append(f"CVR: {seller_data.cvr}")
    if getattr(seller_data, "impressions", None) is not None:
        private_lines.append(f"Показы: {seller_data.impressions}")
    if getattr(seller_data, "views", None) is not None:
        private_lines.append(f"Просмотры: {seller_data.views}")
    if seller_data.sales is not None:
        private_lines.append(f"Продажи: {seller_data.sales}")
    if seller_data.orders is not None:
        private_lines.append(f"Заказы: {seller_data.orders}")
    if seller_data.returns is not None:
        private_lines.append(f"Возвраты: {seller_data.returns}")
    if seller_data.ad_spend is not None:
        private_lines.append(f"Реклама: {seller_data.ad_spend}")
    if seller_data.cost is not None:
        private_lines.append(f"Себестоимость: {seller_data.cost}")
    if seller_data.commission is not None:
        private_lines.append(f"Комиссия: {seller_data.commission}")
    if seller_data.logistics is not None:
        private_lines.append(f"Логистика: {seller_data.logistics}")
    if seller_data.storage is not None:
        private_lines.append(f"Хранение: {seller_data.storage}")
    if seller_data.period:
        private_lines.append(f"Период: {seller_data.period}")

    if private_lines:
        lines.append("")
        lines.append("📈 <b>Бизнес-метрики</b>")
        lines.extend(private_lines)
    elif seller_data.price is None and seller_data.rating is None and seller_data.feedbacks is None:
        lines.append("не указаны")

    if seller_data.updated_at:
        lines.append("")
        lines.append(f"<i>Обновлено: {seller_data.updated_at.strftime('%d.%m.%Y %H:%M')}</i>")

    return "\n".join(lines)


def _card_commercial_line(product, emoji_label: str, field: str, unit: str = "") -> str:
    val = getattr(product, field, None)
    if val is None:
        return f"{emoji_label}: нет данных"
    suffix = f" {unit}" if unit else ""
    prov = field_provenance_label(product, field)
    if prov:
        return f"{emoji_label}: {val}{suffix} (Источник: {prov})"
    return f"{emoji_label}: {val}{suffix}"


def _public_card_summary_text(product) -> str:
    """CARD DATA: публичные поля карточки WB (не SellerData)."""
    lines = ["📦 <b>Карточка</b>", ""]
    lines.append(_card_commercial_line(product, "💰 Публичная цена", "price", "₽"))
    lines.append(_card_commercial_line(product, "⭐ Рейтинг", "rating"))
    lines.append(_card_commercial_line(product, "💬 Отзывов на карточке", "feedbacks"))
    lines.append("")
    lines.append("Публичные данные карточки уже есть — можно получить полный анализ.")
    lines.append("")
    lines.append("<b>Что ещё можно добавить (PRIVATE, опционально):</b>")
    lines.append(
        "CTR · CVR · показы · просмотры · продажи · заказы · возвраты · "
        "реклама · себестоимость · комиссия · логистика · хранение"
    )
    return "\n".join(lines)


def _stale_seller_offer_text(product, memory_seller: SellerData) -> str:
    """Карточка + предложение подтвердить/обновить SellerData из памяти."""
    lines = [_public_card_summary_text(product), ""]
    lines.append("💾 В памяти есть данные продавца по <b>этому</b> артикулу.")
    lines.append("Не подставляю их в анализ автоматически — подтвердите актуальность.")
    if memory_seller.period:
        lines.append(f"Период в снимке: {memory_seller.period}")
    if memory_seller.updated_at:
        lines.append(
            f"Обновлено: {memory_seller.updated_at.strftime('%d.%m.%Y %H:%M')}"
        )
    return "\n".join(lines)


def _missing_commercial_prompt(product) -> str:
    missing = missing_public_commercial_fields(product)
    labels = {
        "price": "💰 Цена",
        "rating": "⭐ Средняя оценка",
        "feedbacks": "💬 Количество отзывов",
    }
    lines = [
        "Публичная карточка не отдала часть коммерческих данных.",
        "Укажите только недостающее (это данные продавца, отдельно от карточки):",
        "",
    ]
    for field in ("price", "rating", "feedbacks"):
        if field in missing:
            lines.append(f"{labels[field]}: ❌ не найдена")
        else:
            val = getattr(product, field)
            unit = " ₽" if field == "price" else ""
            lines.append(f"{labels[field]}: {val}{unit} ✅")
    return "\n".join(lines)


# ------------------------------------------------------------- предварительный анализ

@router.callback_query(F.data == "product:prelim")
async def show_preliminary(
    callback: CallbackQuery,
    session: SessionService,
    category_intelligence=None,
):
    user_id = callback.from_user.id
    product = session.get_product(user_id)

    if product is None:
        await _no_product(callback)
        return

    analysis = session.get_analysis(user_id)

    # Предварительный анализ ЭТОГО ЖЕ товара уже показан выше — не считаем
    # его заново и не шлём дубликат того же сообщения.
    # Явный пересчёт — только через «🔄 Повторить анализ».
    if (
        analysis is not None
        and analysis.get("kind") == "preliminary"
        and analysis.get("article") == product.article
    ):
        await callback.answer("Предварительный анализ уже показан выше ⬆️")
        return

    await _render_preliminary(callback, session, product, category_intelligence)


@router.callback_query(F.data == "product:prelim_retry")
async def retry_preliminary(
    callback: CallbackQuery,
    session: SessionService,
    category_intelligence=None,
):
    """Явный пересчёт предварительного анализа — единственный путь его повторить."""
    product = session.get_product(callback.from_user.id)

    if product is None:
        await _no_product(callback)
        return

    await _render_preliminary(callback, session, product, category_intelligence)


async def _render_preliminary(
    callback: CallbackQuery,
    session: SessionService,
    product,
    category_intelligence=None,
) -> None:
    score_data = _score_calculator.calculate(product)
    recommendations = _recommendation_generator.generate(product)

    market_recs = await _get_market_recs(product, category_intelligence)
    text = _report_builder.build_preliminary(
        product, score_data, recommendations, market_recs=market_recs or None
    )

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

    confirmed = _confirmed_seller_data(session, user_id, product.article)
    memory_seller = None
    if confirmed is None:
        memory_seller = await _peek_memory_seller_data(memory, user_id, product.article)

    # Verified / present public commercial — CARD DATA, без повторного ввода.
    if has_public_commercial_minimum(product):
        if confirmed is not None and confirmed.has_any_seller_value():
            text = (
                _public_card_summary_text(product)
                + "\n\n"
                + _seller_data_summary_text(confirmed)
            )
            await _show(callback, text, precise_data_ready_kb())
            return
        if memory_seller is not None and memory_seller.has_any_seller_value():
            await _show(
                callback,
                _stale_seller_offer_text(product, memory_seller),
                precise_data_stale_seller_kb(),
            )
            return
        await _show(callback, _public_card_summary_text(product), precise_data_ready_kb())
        return

    # Card incomplete: gap-fill only missing commercial; optional private after.
    if confirmed is not None and confirmed.has_any_seller_value():
        await _show(callback, _seller_data_summary_text(confirmed), precise_data_ready_kb())
        return
    if memory_seller is not None and memory_seller.has_any_seller_value():
        await _show(
            callback,
            _missing_commercial_prompt(product)
            + "\n\n💾 Есть сохранённые данные продавца — подтвердите или обновите.",
            precise_data_stale_seller_kb(),
        )
        return

    await _show(callback, _missing_commercial_prompt(product), precise_data_kb())


@router.callback_query(F.data == "product:confirm_seller")
async def confirm_memory_seller(
    callback: CallbackQuery,
    session: SessionService,
    memory: MemoryStore,
):
    """Подтвердить SellerData из памяти для текущего артикула/анализа."""
    user_id = callback.from_user.id
    product = session.get_product(user_id)
    if product is None:
        await _no_product(callback)
        return

    seller = await _peek_memory_seller_data(memory, user_id, product.article)
    if seller is None or not seller.has_any_seller_value():
        await callback.message.answer(
            "Сохранённых данных продавца по этому артикулу нет.",
            reply_markup=precise_data_ready_kb() if has_public_commercial_minimum(product)
            else precise_data_kb(),
        )
        await callback.answer()
        return

    seller.confirmed_current = True
    if seller.updated_at is None:
        seller.updated_at = datetime.now()
    await session.set_seller_data(user_id, product.article, seller)

    text = _seller_data_summary_text(seller)
    if has_public_commercial_minimum(product):
        text = _public_card_summary_text(product) + "\n\n" + text
    await _show(callback, "✅ Данные продавца подтверждены для этого анализа.\n\n" + text, precise_data_ready_kb())

# ------------------------------------------------------------------- ручной ввод / private metrics

_FIELD_PROMPTS = {
    "price": "💰 Укажите текущую цену товара:",
    "rating": "⭐ Укажите среднюю оценку товара от 0 до 5:",
    "feedbacks": "💬 Укажите количество отзывов:",
}

_PRIVATE_STEPS = (
    ("ctr", ManualSellerData.waiting_for_ctr, "📊 CTR (%), либо «-» чтобы пропустить:"),
    ("cvr", ManualSellerData.waiting_for_cvr, "📊 CVR (%), либо «-» чтобы пропустить:"),
    ("impressions", ManualSellerData.waiting_for_impressions, "👁 Показы (число), либо «-»:"),
    ("views", ManualSellerData.waiting_for_views, "👀 Просмотры (число), либо «-»:"),
    ("sales", ManualSellerData.waiting_for_sales, "📈 Продажи за период (число), либо «-»:"),
    ("orders", ManualSellerData.waiting_for_orders, "📦 Заказы за период (число), либо «-»:"),
    ("returns", ManualSellerData.waiting_for_returns, "↩️ Возвраты (число), либо «-»:"),
    ("ad_spend", ManualSellerData.waiting_for_ad_spend, "📢 Рекламные расходы (₽), либо «-»:"),
    ("cost", ManualSellerData.waiting_for_cost, "🧮 Себестоимость (₽), либо «-»:"),
    ("commission", ManualSellerData.waiting_for_commission, "💳 Комиссия (₽ или %), либо «-»:"),
    ("logistics", ManualSellerData.waiting_for_logistics, "🚚 Логистика (₽), либо «-»:"),
    ("storage", ManualSellerData.waiting_for_storage, "🏬 Хранение (₽), либо «-»:"),
    ("period", ManualSellerData.waiting_for_period, "⏳ Период (например «30 дней»), либо «-»:"),
)


def _gap_ask_flags(product) -> dict:
    missing = set(missing_public_commercial_fields(product))
    return {
        "ask_price": "price" in missing,
        "ask_rating": "rating" in missing,
        "ask_feedbacks": "feedbacks" in missing,
    }


async def _ask_next_gap_or_extra(message: Message, state: FSMContext, after: str) -> None:
    """После заполнения gap-поля — следующее отсутствующее или private choice."""
    data = await state.get_data()
    order = [("price", "ask_price"), ("rating", "ask_rating"), ("feedbacks", "ask_feedbacks")]
    passed = False
    for field, flag in order:
        if field == after:
            passed = True
            continue
        if not passed:
            continue
        if data.get(flag):
            await state.set_state(getattr(ManualSellerData, f"waiting_for_{field}"))
            await message.answer(_FIELD_PROMPTS[field], reply_markup=cancel_kb())
            return
    await state.set_state(ManualSellerData.waiting_for_extra_choice)
    await message.answer(
        "Хотите добавить данные продавца (PRIVATE)?\n"
        "\n"
        "CTR · CVR · продажи · заказы · возвраты · реклама · себестоимость…\n"
        "Все поля необязательны.",
        reply_markup=manual_input_skip_kb(),
    )


async def _start_private_metrics(message: Message, state: FSMContext) -> None:
    await state.set_state(ManualSellerData.waiting_for_ctr)
    await message.answer(
        "➕ <b>Данные продавца (PRIVATE)</b>\n"
        "\n"
        "Публичные цена/рейтинг/отзывы карточки уже не спрашиваем.\n"
        "Введите метрики или «-» чтобы пропустить каждое поле.\n"
        "\n"
        f"{_PRIVATE_STEPS[0][2]}",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "product:manual_input")
async def start_manual_input(callback: CallbackQuery, state: FSMContext, session: SessionService):
    product = session.get_product(callback.from_user.id)
    if product is None:
        await _no_product(callback)
        return

    flags = _gap_ask_flags(product)
    # Если CARD commercial полная — не спрашиваем price/rating/feedbacks.
    if not any(flags.values()):
        await state.update_data(**flags)
        await callback.answer()
        await _start_private_metrics(callback.message, state)
        return

    await state.update_data(**flags)
    await callback.answer()

    if flags["ask_price"]:
        await state.set_state(ManualSellerData.waiting_for_price)
        await callback.message.answer(_FIELD_PROMPTS["price"], reply_markup=cancel_kb())
    elif flags["ask_rating"]:
        await state.set_state(ManualSellerData.waiting_for_rating)
        await callback.message.answer(_FIELD_PROMPTS["rating"], reply_markup=cancel_kb())
    else:
        await state.set_state(ManualSellerData.waiting_for_feedbacks)
        await callback.message.answer(_FIELD_PROMPTS["feedbacks"], reply_markup=cancel_kb())


@router.callback_query(F.data == "product:seller_metrics")
async def start_seller_metrics(callback: CallbackQuery, state: FSMContext, session: SessionService):
    """Только private metrics — без повторного ввода CARD price/rating/feedbacks."""
    if session.get_product(callback.from_user.id) is None:
        await _no_product(callback)
        return
    await state.update_data(ask_price=False, ask_rating=False, ask_feedbacks=False)
    await callback.answer()
    await _start_private_metrics(callback.message, state)


@router.message(ManualSellerData.waiting_for_price)
async def handle_price(message: Message, state: FSMContext):
    price = _parse_price(message.text or "")
    if price is None:
        await message.answer(
            "🤔 Цена должна быть числом больше 0. Попробуйте ещё раз:",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(price=price, price_source=SOURCE_USER)
    await _ask_next_gap_or_extra(message, state, after="price")


@router.message(ManualSellerData.waiting_for_rating)
async def handle_rating(message: Message, state: FSMContext):
    rating = _parse_rating(message.text or "")
    if rating is None:
        await message.answer(
            "🤔 Оценка должна быть числом от 0 до 5. Попробуйте ещё раз:",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(rating=rating, rating_source=SOURCE_USER)
    await _ask_next_gap_or_extra(message, state, after="rating")


@router.message(ManualSellerData.waiting_for_feedbacks)
async def handle_feedbacks(message: Message, state: FSMContext):
    feedbacks = _parse_nonneg_int(message.text or "")
    if feedbacks is None:
        await message.answer(
            "🤔 Количество отзывов должно быть целым числом от 0. Попробуйте ещё раз:",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(feedbacks=feedbacks, feedbacks_source=SOURCE_USER)
    await _ask_next_gap_or_extra(message, state, after="feedbacks")


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
    await callback.answer()
    await _start_private_metrics(callback.message, state)


async def _parse_optional_number(text: str, *, as_int: bool = False):
    raw = (text or "").strip()
    if raw == "-":
        return None, True  # skipped
    if as_int:
        val = _parse_nonneg_int(raw)
    else:
        # allow float for CTR/CVR/money
        try:
            val = float(raw.replace(",", ".").replace("%", "").strip())
        except ValueError:
            val = None
        if val is not None and val < 0:
            val = None
        if as_int and val is not None:
            val = int(val)
    if val is None:
        return None, False
    return val, True


async def _advance_private(message: Message, state: FSMContext, session: SessionService, current: str) -> None:
    names = [s[0] for s in _PRIVATE_STEPS]
    idx = names.index(current)
    if idx + 1 >= len(_PRIVATE_STEPS):
        seller_data = await _finish_manual_input(message.from_user.id, state, session)
        await message.answer(
            "✅ Данные сохранены.\n\n" + _seller_data_summary_text(seller_data),
            reply_markup=precise_data_ready_kb(),
            parse_mode="HTML",
        )
        return
    _name, st, prompt = _PRIVATE_STEPS[idx + 1]
    await state.set_state(st)
    await message.answer(prompt, reply_markup=cancel_kb())


@router.message(ManualSellerData.waiting_for_ctr)
async def handle_ctr(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "")
    if not ok:
        await message.answer("🤔 CTR — число или «-». Попробуйте ещё раз:", reply_markup=cancel_kb())
        return
    await state.update_data(ctr=val, ctr_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "ctr")


@router.message(ManualSellerData.waiting_for_cvr)
async def handle_cvr(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "")
    if not ok:
        await message.answer("🤔 CVR — число или «-». Попробуйте ещё раз:", reply_markup=cancel_kb())
        return
    await state.update_data(cvr=val, cvr_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "cvr")


@router.message(ManualSellerData.waiting_for_impressions)
async def handle_impressions(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "", as_int=True)
    if not ok:
        await message.answer("🤔 Показы — целое число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(
        impressions=val,
        impressions_source=SOURCE_USER if val is not None else None,
    )
    await _advance_private(message, state, session, "impressions")


@router.message(ManualSellerData.waiting_for_views)
async def handle_views(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "", as_int=True)
    if not ok:
        await message.answer("🤔 Просмотры — целое число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(
        views=val,
        views_source=SOURCE_USER if val is not None else None,
    )
    await _advance_private(message, state, session, "views")


@router.message(ManualSellerData.waiting_for_sales)
async def handle_sales(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "", as_int=True)
    if not ok:
        await message.answer("🤔 Продажи — целое число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(sales=val, sales_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "sales")


@router.message(ManualSellerData.waiting_for_orders)
async def handle_orders(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "", as_int=True)
    if not ok:
        await message.answer("🤔 Заказы — целое число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(orders=val, orders_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "orders")


@router.message(ManualSellerData.waiting_for_returns)
async def handle_returns(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "", as_int=True)
    if not ok:
        await message.answer("🤔 Возвраты — целое число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(returns=val, returns_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "returns")


@router.message(ManualSellerData.waiting_for_ad_spend)
async def handle_ad_spend(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "")
    if not ok:
        await message.answer("🤔 Реклама — число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(ad_spend=val, ad_spend_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "ad_spend")


@router.message(ManualSellerData.waiting_for_cost)
async def handle_cost(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "")
    if not ok:
        await message.answer("🤔 Себестоимость — число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(cost=val, cost_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "cost")


@router.message(ManualSellerData.waiting_for_commission)
async def handle_commission(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "")
    if not ok:
        await message.answer("🤔 Комиссия — число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(commission=val, commission_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "commission")


@router.message(ManualSellerData.waiting_for_logistics)
async def handle_logistics(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "")
    if not ok:
        await message.answer("🤔 Логистика — число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(logistics=val, logistics_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "logistics")


@router.message(ManualSellerData.waiting_for_storage)
async def handle_storage(message: Message, state: FSMContext, session: SessionService):
    val, ok = await _parse_optional_number(message.text or "")
    if not ok:
        await message.answer("🤔 Хранение — число или «-»:", reply_markup=cancel_kb())
        return
    await state.update_data(storage=val, storage_source=SOURCE_USER if val is not None else None)
    await _advance_private(message, state, session, "storage")


@router.message(ManualSellerData.waiting_for_period)
async def handle_period(message: Message, state: FSMContext, session: SessionService):
    text = (message.text or "").strip()
    period = None if text == "-" else text
    await state.update_data(
        period=period,
        period_source=SOURCE_USER if period is not None else None,
    )
    await _advance_private(message, state, session, "period")


async def _finish_manual_input(
    user_id: int,
    state: FSMContext,
    session: SessionService,
) -> SellerData:
    """Собрать SellerData из FSM. Не копировать public CARD в seller_price."""
    data = await state.get_data()
    product = session.get_product(user_id)

    def _src(key: str, value) -> str | None:
        if value is None:
            return None
        return data.get(f"{key}_source") or SOURCE_USER

    seller_data = SellerData(
        price=data.get("price"),
        rating=data.get("rating"),
        feedbacks=data.get("feedbacks"),
        sales=data.get("sales"),
        orders=data.get("orders"),
        period=data.get("period"),
        ctr=data.get("ctr"),
        cvr=data.get("cvr"),
        impressions=data.get("impressions"),
        views=data.get("views"),
        returns=data.get("returns"),
        ad_spend=data.get("ad_spend"),
        cost=data.get("cost"),
        commission=data.get("commission"),
        logistics=data.get("logistics"),
        storage=data.get("storage"),
        price_source=_src("price", data.get("price")),
        rating_source=_src("rating", data.get("rating")),
        feedbacks_source=_src("feedbacks", data.get("feedbacks")),
        sales_source=_src("sales", data.get("sales")),
        orders_source=_src("orders", data.get("orders")),
        period_source=_src("period", data.get("period")),
        ctr_source=_src("ctr", data.get("ctr")),
        cvr_source=_src("cvr", data.get("cvr")),
        impressions_source=_src("impressions", data.get("impressions")),
        views_source=_src("views", data.get("views")),
        returns_source=_src("returns", data.get("returns")),
        ad_spend_source=_src("ad_spend", data.get("ad_spend")),
        cost_source=_src("cost", data.get("cost")),
        commission_source=_src("commission", data.get("commission")),
        logistics_source=_src("logistics", data.get("logistics")),
        storage_source=_src("storage", data.get("storage")),
        updated_at=datetime.now(),
        confirmed_current=True,
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
    category_intelligence=None,
    review_intel=None,
    wb_reviews=None,
    search_service=None,
    browser_product_cache=None,
):
    user_id = callback.from_user.id
    product = session.get_product(user_id)

    if product is None:
        await _no_product(callback)
        return

    seller_data = _confirmed_seller_data(session, user_id, product.article)
    has_public_min = has_public_commercial_minimum(product)
    # Gap-fill seller commercial only if card incomplete and seller provided those fields
    seller_fills_gap = False
    if seller_data is not None and not has_public_min:
        missing = missing_public_commercial_fields(product)
        seller_fills_gap = all(
            getattr(seller_data, f, None) is not None for f in missing
        ) and (
            (product.price is not None or seller_data.price is not None)
            and (product.rating is not None or seller_data.rating is not None)
            and (product.feedbacks is not None or seller_data.feedbacks is not None)
        )

    if not has_public_min and not seller_fills_gap:
        await callback.message.answer(
            "🤔 Не хватает цены/рейтинга/числа отзывов с карточки. "
            "Укажите только недостающие поля.",
            reply_markup=precise_data_kb(),
        )
        await callback.answer()
        return

    await callback.answer()
    status = await callback.message.answer(f"⏳ {AI_NAME} готовит полный анализ...")

    # Score: CARD public fields — база. Seller gap-fill только поверх None.
    # Не создавать seller_price из public_price.
    scoring_product = product
    if seller_data is not None and not has_public_min:
        scoring_product = replace(
            product,
            price=product.price if product.price is not None else seller_data.price,
            rating=product.rating if product.rating is not None else seller_data.rating,
            feedbacks=(
                product.feedbacks if product.feedbacks is not None else seller_data.feedbacks
            ),
        )

    score_data = _score_calculator.calculate(scoring_product)
    recommendations = _recommendation_generator.generate(scoring_product)
    market_recs, category_context = await _get_market_bundle(product, category_intelligence)

    # Review Intelligence → Advisor (production path, no rewrite of RI)
    assessment = None
    review_recs: list = []
    raw_reviews = None
    if hasattr(session, "get_product_reviews"):
        raw_reviews = session.get_product_reviews(user_id, product.article)
    if raw_reviews is None and wb_reviews is not None:
        try:
            raw_reviews = await wb_reviews.load_into_session(session, user_id, product)
        except Exception as exc:
            log.warning("full_analysis: reviews fetch failed: %s", exc)
            raw_reviews = None
    if review_intel is not None and raw_reviews:
        try:
            payload = []
            for item in raw_reviews:
                if hasattr(item, "to_ri_dict"):
                    payload.append(item.to_ri_dict())
                elif isinstance(item, dict):
                    payload.append(item)
                elif isinstance(item, str) and item.strip():
                    payload.append({"text": item})
            if payload:
                from backend.memory.context import make_user_hash
                assessment = await review_intel.analyze(
                    payload,
                    category=getattr(product, "subject_name", None),
                    article=str(product.article),
                    user_hash=make_user_hash(user_id),
                    persist=True,
                )
                review_recs = _recommendation_generator.generate_review_recommendations(
                    assessment
                )
        except Exception as exc:
            log.warning("full_analysis: RI failed: %s", exc)
            assessment = None

    metric_snapshots = []
    store = getattr(session, "memory_store", None)
    if store is not None and hasattr(store, "list_metric_snapshots"):
        try:
            metric_snapshots = await store.list_metric_snapshots(
                user_id, int(product.article),
            )
        except Exception as exc:
            log.debug("full_analysis: metric snapshots skip: %s", exc)
            metric_snapshots = []

    competitor_comparison = None
    competitive_diagnosis = None
    if search_service is not None:
        try:
            from backend.competitor_intelligence.service import ArgusCompetitorIntelligence
            from backend.ai.advisor import compute_unit_economics
            ci = ArgusCompetitorIntelligence(
                search_service,
                intel_store=getattr(search_service, "_store", None),
                public_cache=browser_product_cache,
            )
            unit = compute_unit_economics(seller_data, scoring_product)
            discovery = await ci.analyze_product(
                scoring_product,
                seller_data=seller_data,
                review_assessment=assessment,
                unit_econ=unit,
            )
            competitor_comparison = discovery.comparison
            competitive_diagnosis = discovery.diagnosis
            if hasattr(session, "set_competitor_comparison"):
                session.set_competitor_comparison(user_id, competitor_comparison)
            if hasattr(session, "set_competitor_context_prompt") and competitor_comparison is not None:
                from backend.competitor_intelligence.comparison import format_market_block
                session.set_competitor_context_prompt(
                    user_id, format_market_block(competitor_comparison),
                )
        except Exception as exc:
            log.warning("full_analysis: competitor intelligence skip: %s", exc)

    advisor_plan = build_advisor_plan(
        product=scoring_product,
        score_data=score_data,
        seller_data=seller_data,
        review_assessment=assessment,
        category_context=category_context,
        card_recommendations=recommendations,
        market_recommendations=market_recs,
        review_recommendations=review_recs,
        metric_snapshots=metric_snapshots,
        competitor_comparison=competitor_comparison,
        competitive_diagnosis=competitive_diagnosis,
    )
    advisor_text = advisor_plan.format_plain() if advisor_plan.has_content() else ""

    ai_comment = None
    if ai_service is not None:
        prompt = build_full_analysis_prompt(
            product, seller_data, advisor_text=advisor_text or None,
        )
        ai_comment = await ai_service.generate(prompt, system=build_analysis_system())

        if ai_comment is None:
            log.warning("AI недоступен — полный анализ будет без AI-комментария")

    text = _report_builder.build_full_with_sections(
        product=product,
        seller_data=seller_data,
        score_data=score_data,
        recommendations=recommendations,
        ai_comment=ai_comment,
        market_recs=market_recs or None,
        advisor_plan=advisor_plan,
    )

    await history.add(
        user_id=user_id,
        article=product.article,
        title=product.title or "Без названия",
        score=score_data["score"],
        price=(
            seller_data.price if (seller_data is not None and seller_data.price is not None)
            else product.price
        ),
        verdict=verdict_for(
            score_data["score"],
            diagnosis_kind=getattr(advisor_plan, "main_problem_kind", None),
            funnel_complete=bool(
                seller_data is not None
                and getattr(seller_data, "ctr", None) is not None
                and getattr(seller_data, "cvr", None) is not None
            ),
        ),
    )

    # SellerData сохраняем только если продавец реально вводил/API.
    # Публичные поля остаются на product в session, не смешиваем.
    baseline_snapshot_id = None
    store = getattr(session, "memory_store", None) or memory
    if seller_data is not None:
        await session.set_seller_data(user_id, product.article, seller_data)
        try:
            from backend.ai.dynamic_analytics import persist_metric_snapshot
            baseline_snapshot_id = await persist_metric_snapshot(
                store,
                user_id,
                int(product.article),
                seller_data=seller_data,
                product=product,
                source="analysis",
            )
        except Exception as exc:
            log.debug("full_analysis: seller snapshot skip: %s", exc)
    else:
        # Card-only analysis: still accumulate a measured snapshot (price/rating/fb/stock).
        # Never invents private CTR/CVR/orders. Enables LIVE history over repeated analyses.
        try:
            from backend.ai.dynamic_analytics import persist_metric_snapshot
            baseline_snapshot_id = await persist_metric_snapshot(
                store,
                user_id,
                int(product.article),
                seller_data=None,
                product=product,
                source="analysis",
            )
        except Exception as exc:
            log.debug("full_analysis: card snapshot skip: %s", exc)

    # Action Verification foundation: Recommendation → Action + baseline + check_after
    proposed_action = None
    try:
        from backend.foundation.action_bridge import propose_primary_from_plan
        from backend.handlers.action_verify import _svc_for

        act_svc = _svc_for(user_id, session)
        proposed_action = await propose_primary_from_plan(
            action_service=act_svc,
            seller_id=user_id,
            article=int(product.article),
            advisor_plan=advisor_plan,
            product=scoring_product or product,
            baseline_snapshot_id=baseline_snapshot_id,
        )
    except Exception as exc:
        log.debug("full_analysis: action propose skip: %s", exc)

    analysis_payload = {
        "kind": "full",
        "article": product.article,
        "score": score_data["score"],
        "reasons": score_data["reasons"],
        "recommendations": recommendations,
        "market_recommendations": market_recs or [],
        "advisor_text": advisor_text,
        "baseline_snapshot_id": baseline_snapshot_id,
    }
    if proposed_action is not None:
        analysis_payload["proposed_action_id"] = proposed_action.action_id
        analysis_payload["proposed_action_type"] = proposed_action.action_type.value
    session.set_analysis(user_id, analysis_payload)
    session.mark_full_report_shown(user_id)

    await status.delete()

    await answer_long(
        callback.message,
        text,
        reply_markup=after_analysis_kb(full_report_available=False),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    if proposed_action is not None:
        try:
            check_hint = ""
            if proposed_action.check_after:
                from datetime import timezone
                dt = datetime.fromtimestamp(proposed_action.check_after, tz=timezone.utc)
                check_hint = f"\nПроверка применения: ~{dt.strftime('%d.%m')} (UTC)."
            await callback.message.answer(
                (
                    f"📌 Рекомендация зафиксирована как действие "
                    f"<b>{proposed_action.action_type.value}</b>.\n"
                    f"{proposed_action.recommendation[:280]}"
                    f"{check_hint}\n"
                    "Можно принять / отметить «Сделал» / проверить — "
                    "автопроверка идёт через API/parser, не через догадки."
                ),
                reply_markup=action_recommend_kb(proposed_action.action_id),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.debug("full_analysis: action UX skip: %s", exc)


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
