"""
Навигация по меню. Все переходы — через CallbackQuery (inline-кнопки).

Экраны редактируют одно и то же сообщение (edit_text) —
чат не засоряется, интерфейс выглядит как мини-приложение.
"""

import html
import time

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from backend.handlers.start import WELCOME_TEXT
from backend.config import AI_NAME
from backend.keyboards.inline import (
    main_menu_kb,
    back_kb,
    cancel_kb,
    reports_kb,
    discuss_entry_kb,
)
from backend.services.daily import DailyPlanner
from backend.services.history import HistoryService, PERIOD_TITLES
from backend.services.session import SessionService
from backend.states import AnalyzeCard, SellerAIChat

router = Router()


async def _show(callback: CallbackQuery, text: str, keyboard):
    """Аккуратно перерисовать экран (или отправить новый, если нельзя)."""
    try:
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception:
        # Сообщение нельзя редактировать (например, это фото) —
        # отправляем новый экран.
        await callback.message.answer(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()


# --------------------------------------------------------------------- главное меню

@router.callback_query(F.data == "menu:main")
async def show_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show(callback, WELCOME_TEXT, main_menu_kb())


# --------------------------------------------------------------------- анализ товара

@router.callback_query(F.data == "menu:analyze")
async def show_analyze(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AnalyzeCard.waiting_for_link)

    await _show(
        callback,
        "📦 <b>Анализ товара</b>\n"
        "\n"
        "Пришлите ссылку на карточку Wildberries.\n"
        "\n"
        "Например:\n"
        "<code>https://www.wildberries.ru/catalog/211246754/detail.aspx</code>",
        cancel_kb(),
    )


# --------------------------------------------------------------------- Seller AI

@router.callback_query(F.data == "menu:ai")
async def show_seller_ai(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SellerAIChat.waiting_for_question)

    await _show(
        callback,
        f"🧠 <b>{AI_NAME}</b>\n"
        "\n"
        "Ваш личный эксперт по маркетплейсам.\n"
        "\n"
        "Задайте любой вопрос о продажах:\n"
        "\n"
        "<i>— Как поднять карточку в поиске?\n"
        "— Какую скидку ставить на старте?\n"
        "— Как отвечать на негативные отзывы?</i>\n"
        "\n"
        "Просто напишите вопрос сообщением 👇",
        cancel_kb(),
    )


# --------------------------------------------------------------------- что сделать сегодня

@router.callback_query(F.data == "menu:today")
async def show_today(
    callback: CallbackQuery,
    state: FSMContext,
    session: SessionService,
    daily: DailyPlanner,
):
    await state.clear()

    user_id = callback.from_user.id
    product = session.get_product(user_id)
    analysis = session.get_analysis(user_id)

    actions = daily.build_plan(product, analysis)

    if not actions:
        text = (
            "📈 <b>Что сделать сегодня</b>\n"
            "\n"
            "План строится по вашему последнему анализу,\n"
            "а анализов пока не было.\n"
            "\n"
            f"Начните с 📦 Анализа товара — и {AI_NAME}\n"
            "составит список действий на день."
        )
        await _show(callback, text, discuss_entry_kb())
        return

    title = html.escape((product.title or "товар")[:50])

    lines = [
        "📈 <b>Что сделать сегодня</b>",
        f"<i>по товару: {title}</i>",
        "",
    ]
    for index, action in enumerate(actions, start=1):
        lines.append(f"{index}. {action}")

    lines.append("")
    lines.append("🧠 <i>План обновится после следующего анализа.</i>")

    await _show(callback, "\n".join(lines), back_kb())


# --------------------------------------------------------------------- отчёты

@router.callback_query(F.data == "menu:reports")
async def show_reports_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    await _show(
        callback,
        "📅 <b>Отчёты</b>\n"
        "\n"
        "Сводка по вашим анализам за период.\n"
        "\n"
        "<i>Когда подключим Seller API — здесь появятся\n"
        "заказы, выручка, CTR и расходы.</i>\n"
        "\n"
        "Выберите период 👇",
        reports_kb(),
    )


@router.callback_query(F.data.startswith("reports:"))
async def show_report_period(
    callback: CallbackQuery,
    history: HistoryService,
):
    period = callback.data.split(":", 1)[1]
    period_title = PERIOD_TITLES.get(period, period)

    summary = await history.summary(callback.from_user.id, period)

    if summary is None:
        text = (
            f"📅 <b>Отчёт {period_title}</b>\n"
            "\n"
            "За этот период анализов не было.\n"
            "\n"
            "Проанализируйте товар — и он попадёт в отчёт."
        )
        await _show(callback, text, reports_kb())
        return

    best = summary["best"]
    lines = [
        f"📅 <b>Отчёт {period_title}</b>",
        "",
        f"🔍 Анализов: <b>{summary['count']}</b>",
        f"🧠 Средний Score: <b>{summary['avg_score']}/100</b>",
        "",
        f"🏆 Лучший: {html.escape(best['title'][:40])} — <b>{best['score']}/100</b>",
    ]

    if summary["count"] > 1:
        worst = summary["worst"]
        lines.append(
            f"🛠 Слабый: {html.escape(worst['title'][:40])} — <b>{worst['score']}/100</b>"
        )

    lines.append("")
    lines.append("<b>Последние анализы</b>")

    for item in summary["items"]:
        when = time.strftime("%d.%m %H:%M", time.localtime(item["time"]))
        price = f" · {item['price']} ₽" if item.get("price") else ""
        lines.append(
            f"• {when} · <b>{item['score']}/100</b> · "
            f"{html.escape(item['title'][:35])}{price}"
        )

    await _show(callback, "\n".join(lines), reports_kb())


# --------------------------------------------------------------------- аналитика (старые кнопки)

@router.callback_query(F.data == "menu:analytics")
async def show_analytics(callback: CallbackQuery, state: FSMContext):
    # Раздел «Аналитика» превратился в «Отчёты».
    # Хендлер оставлен, чтобы старые кнопки в переписке не ломались.
    await show_reports_menu(callback, state)


# --------------------------------------------------------------------- история

@router.callback_query(F.data == "menu:history")
async def show_history(
    callback: CallbackQuery,
    state: FSMContext,
    history: HistoryService,
):
    await state.clear()

    items = await history.list(callback.from_user.id)

    if not items:
        text = (
            "📊 <b>История анализов</b>\n"
            "\n"
            "Пока пусто.\n"
            "\n"
            "Проанализируйте первый товар — он появится здесь."
        )
    else:
        lines = ["📊 <b>История анализов</b>", ""]

        for item in items:
            when = time.strftime("%d.%m %H:%M", time.localtime(item["time"]))
            title = html.escape(item["title"][:40])
            verdict = item.get("verdict", "")
            lines.append(
                f"• {when} · <b>{item['score']}/100</b> {verdict}\n"
                f"  {title} (<code>{item['article']}</code>)"
            )

        text = "\n".join(lines)

    await _show(callback, text, back_kb())


# --------------------------------------------------------------------- настройки

@router.callback_query(F.data == "menu:settings")
async def show_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    await _show(
        callback,
        "⚙ <b>Настройки</b>\n"
        "\n"
        f"🧠 Движок: <b>{AI_NAME} v1</b>\n"
        "🌍 Маркетплейс: <b>Wildberries</b>\n"
        "   <i>Ozon и Avito — скоро</i>\n"
        "\n"
        "🔔 Уведомления и подписка появятся\n"
        "в следующем обновлении.",
        back_kb(),
    )
