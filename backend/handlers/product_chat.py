"""
Режим «🧠 Обсудить товар» + «📊 Полный отчёт».

Поток:
    анализ товара -> кнопка "🧠 Обсудить товар"
        -> discussion session (ProductChat.discussing)
        -> все сообщения идут в тот же conversation context
        -> кнопка "✅ Закончить диалог" возвращает к действиям по товару

Seller AI помнит товар через SessionService + discussion memory,
поэтому follow-up («300 курток одного размера») продолжает диалог,
а не запускает выбор нового товара.
"""

import html

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from backend.ai.advisor import ADVISOR_QUICK_PROMPTS
from backend.ai.brain import SellerBrain
from backend.ai.report import ReportBuilder
from backend.config import AI_NAME
from backend.keyboards.inline import (
    after_analysis_kb,
    after_full_report_kb,
    discuss_kb,
    discuss_entry_kb,
    keyboard_for_solution_stage,
    solution_after_pick_kb,
    solution_options_kb,
)
from backend.services.card_parser import parse_marketplace_link
from backend.services.session import SessionService
from backend.states import AnalyzeCard, ProductChat
from backend.utils.telegram_split import answer_long, edit_or_answer_long

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

    user_id = callback.from_user.id
    await state.set_state(ProductChat.discussing)

    is_new = session.start_discussion(user_id, product.article)
    if is_new:
        brain.begin_product_discussion(user_id, product.article, reset=True)
    else:
        brain.begin_product_discussion(user_id, product.article, reset=False)
    # Persistent product conversation → working memory (единая история).
    await brain.hydrate_discussion(user_id, product.article)

    title = html.escape(product.title or "товар")
    suffix = (
        "Начинаем обсуждение 👇"
        if is_new
        else "Продолжаем предыдущий разговор 👇"
    )

    # Подсказка по сохранённому диагнозу (если анализ уже был)
    diagnosis_hint = ""
    analysis = session.get_analysis(user_id) if hasattr(session, "get_analysis") else None
    if isinstance(analysis, dict):
        snap = analysis.get("diagnosis_snapshot")
        if snap is None:
            plan_obj = analysis.get("advisor_plan")
            if plan_obj is not None and hasattr(plan_obj, "diagnosis_snapshot"):
                snap = plan_obj.diagnosis_snapshot()
        if isinstance(snap, dict) and snap.get("diagnosis"):
            locus = html.escape(str(snap.get("locus") or "UNKNOWN"))
            diag = html.escape(str(snap.get("diagnosis") or "")[:160])
            diagnosis_hint = (
                f"\n📌 <b>Диагноз Argus:</b> {locus} — {diag}\n"
                "Вопросы про цену / «что решили» опираются на него.\n"
            )

    await callback.message.answer(
        f"🧠 <b>Обсуждаем:</b> {title}\n"
        f"{diagnosis_hint}"
        "\n"
        "Спрашивайте что угодно об этом товаре:\n"
        "\n"
        "<i>— А если поднять цену на 300 рублей?\n"
        "— С чего начать улучшение карточки?\n"
        "— Какую рекламу запустить?</i>\n"
        "\n"
        f"{suffix}",
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
        await state.clear()
        session.end_discussion(user_id)
        brain.end_product_discussion(user_id)
        await message.answer(
            "🤔 Я потерял товар из памяти. Проанализируйте его заново.",
            reply_markup=discuss_entry_kb(),
        )
        return

    # Защита: discussion должен быть привязан к текущему товару.
    if not session.is_discussion_active(user_id, product.article):
        session.start_discussion(user_id, product.article)
        brain.begin_product_discussion(user_id, product.article)

    question = (message.text or "").strip()

    if not question:
        await message.answer(
            "Напишите вопрос текстом 🙂",
            reply_markup=discuss_kb(),
        )
        return

    # Явная новая задача: ссылка/артикул → выходим из discussion в analyze.
    marketplace, _article = parse_marketplace_link(question)
    if marketplace is not None:
        session.end_discussion(user_id)
        brain.end_product_discussion(user_id)
        await state.set_state(AnalyzeCard.waiting_for_link)
        await message.answer(
            "🔄 Похоже, вы хотите разобрать новый товар.\n"
            "Пришлите ссылку ещё раз — или нажмите «Новый анализ».",
            reply_markup=after_analysis_kb(
                full_report_available=not session.is_full_report_shown(user_id),
            ),
            parse_mode="HTML",
        )
        return

    status = await message.answer("🧠 Думаю...")

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

    # Сохраняем ход discussion в session (для контекста/тестов).
    session.append_discussion_message(user_id, "user", question)
    session.append_discussion_message(user_id, "assistant", answer.text)

    kb = keyboard_for_solution_stage(
        getattr(answer, "ui_stage", None),
        option_labels=getattr(answer, "option_labels", None) or None,
        full_report_shown=session.is_full_report_shown(user_id),
    )
    await edit_or_answer_long(
        status,
        html.escape(answer.text),
        reply_markup=kb,
        parse_mode="HTML",
        fallback_message=message,
    )


# ----------------------------------------------------------- solution UI callbacks

@router.callback_query(F.data == "solution:find")
async def solution_find(callback: CallbackQuery, session: SessionService, brain: SellerBrain, state: FSMContext):
    await state.set_state(ProductChat.discussing)
    await _solution_prompt(callback, session, brain, "Где купить решение под проблему из отзывов?")


@router.callback_query(F.data == "solution:compare")
async def solution_compare(callback: CallbackQuery, session: SessionService, brain: SellerBrain, state: FSMContext):
    await state.set_state(ProductChat.discussing)
    await _solution_prompt(callback, session, brain, "Сравни варианты, какой лучше?")


@router.callback_query(F.data.startswith("solution:pick:"))
async def solution_pick(callback: CallbackQuery, session: SessionService, brain: SellerBrain, state: FSMContext):
    await state.set_state(ProductChat.discussing)
    idx_s = (callback.data or "").rsplit(":", 1)[-1]
    try:
        idx = int(idx_s)
    except ValueError:
        idx = 1
    session.set_pending_solution_pick(callback.from_user.id, idx)
    sol = session.get_solution_research(callback.from_user.id)
    title = ""
    label = str(idx)
    if sol is not None:
        opts = list(getattr(sol, "options", None) or [])
        if 1 <= idx <= len(opts):
            opt = opts[idx - 1]
            title = getattr(opt, "title", "") or ""
            label = getattr(opt, "label", label)
    await callback.message.answer(
        html.escape(f"Вариант {label}: {title or 'выбран'}. Подтвердить?"),
        reply_markup=solution_after_pick_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "solution:confirm")
async def solution_confirm(callback: CallbackQuery, session: SessionService, brain: SellerBrain, state: FSMContext):
    await state.set_state(ProductChat.discussing)
    pending = session.get_pending_solution_pick(callback.from_user.id)
    text = f"Беру {pending}" if pending else "Выбираю этот вариант"
    session.clear_pending_solution_pick(callback.from_user.id)
    await _solution_prompt(callback, session, brain, text)


@router.callback_query(F.data == "solution:back")
async def solution_back(callback: CallbackQuery, session: SessionService):
    sol = session.get_solution_research(callback.from_user.id) if hasattr(session, "get_solution_research") else None
    labels = []
    if sol is not None:
        labels = [str(getattr(o, "label", i + 1)) for i, o in enumerate(list(getattr(sol, "options", None) or [])[:5])]
    await callback.message.answer(
        "Ок, вернулись к вариантам.",
        reply_markup=solution_options_kb(labels or ["1", "2", "3"]),
    )
    await callback.answer()


@router.callback_query(F.data == "solution:record")
async def solution_record(callback: CallbackQuery, session: SessionService, brain: SellerBrain, state: FSMContext):
    await state.set_state(ProductChat.discussing)
    await _solution_prompt(callback, session, brain, "Записать внедрение решения")


async def _solution_prompt(
    callback: CallbackQuery,
    session: SessionService,
    brain: SellerBrain,
    text: str,
    *,
    force_stage: str | None = None,
) -> None:
    user_id = callback.from_user.id
    product = session.get_product(user_id)
    if product is None:
        await callback.message.answer(
            "Сначала нужен товар в обсуждении.",
            reply_markup=discuss_entry_kb(),
        )
        await callback.answer()
        return
    if not session.is_discussion_active(user_id, product.article):
        session.start_discussion(user_id, product.article)
        brain.begin_product_discussion(user_id, product.article)

    answer = await brain.reply(user_id, text, force_product_mode=True)
    if not answer:
        await callback.message.answer("Не удалось обработать.", reply_markup=discuss_kb())
        await callback.answer()
        return
    session.append_discussion_message(user_id, "user", text)
    session.append_discussion_message(user_id, "assistant", answer.text)
    stage = force_stage or getattr(answer, "ui_stage", None)
    kb = keyboard_for_solution_stage(
        stage,
        option_labels=getattr(answer, "option_labels", None) or None,
    )
    await answer_long(
        callback.message,
        html.escape(answer.text),
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


# ----------------------------------------------------------- advisor quick actions

@router.callback_query(F.data.startswith("advisor:"))
async def advisor_quick_action(
    callback: CallbackQuery,
    state: FSMContext,
    session: SessionService,
    brain: SellerBrain,
):
    """
    Разобрать подробнее / Как исправить / Что добавить / Как увеличить продажи.
    Тот же SellerBrain + Advisor context — не отдельный мозг.
    """
    key = (callback.data or "").split(":", 1)[-1].strip()
    prompt = ADVISOR_QUICK_PROMPTS.get(key)
    if not prompt:
        await callback.answer()
        return

    user_id = callback.from_user.id
    product = session.get_product(user_id)
    if product is None:
        await callback.message.answer(
            "🤔 Сначала проанализируйте товар.",
            reply_markup=discuss_entry_kb(),
        )
        await callback.answer()
        return

    # Enter / keep discussion so follow-ups share Advisor context
    await state.set_state(ProductChat.discussing)
    if not session.is_discussion_active(user_id, product.article):
        session.start_discussion(user_id, product.article)
        brain.begin_product_discussion(user_id, product.article, reset=True)
    else:
        brain.begin_product_discussion(user_id, product.article, reset=False)
    await brain.hydrate_discussion(user_id, product.article)

    await callback.answer()
    status = await callback.message.answer("🧠 Думаю...")

    answer = await brain.reply(user_id, prompt, force_product_mode=True)
    if not answer:
        await status.edit_text(
            f"😴 {AI_NAME} сейчас недоступен.\nПопробуй чуть позже.",
            reply_markup=discuss_kb(),
        )
        return

    session.append_discussion_message(user_id, "user", prompt)
    session.append_discussion_message(user_id, "assistant", answer.text)

    kb = keyboard_for_solution_stage(
        getattr(answer, "ui_stage", None),
        option_labels=getattr(answer, "option_labels", None) or None,
        full_report_shown=session.is_full_report_shown(user_id),
    )
    # Prefer discuss_kb with advisor shortcuts when no solution stage
    if not getattr(answer, "ui_stage", None):
        kb = discuss_kb()

    await edit_or_answer_long(
        status,
        html.escape(answer.text),
        reply_markup=kb,
        parse_mode="HTML",
        fallback_message=callback.message,
    )


# ----------------------------------------------------------- конец диалога

@router.callback_query(F.data == "product:discuss_end")
async def end_discussion(
    callback: CallbackQuery,
    state: FSMContext,
    session: SessionService,
    brain: SellerBrain,
):
    user_id = callback.from_user.id
    await state.clear()
    session.end_discussion(user_id)
    brain.end_product_discussion(user_id)

    await callback.message.answer(
        "✅ Диалог завершён.\n"
        "Товар остаётся в памяти — можно вернуться к нему в любой момент.",
        reply_markup=after_analysis_kb(
            full_report_available=not session.is_full_report_shown(user_id),
        ),
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
    session.mark_full_report_shown(user_id)

    await answer_long(
        callback.message,
        text,
        reply_markup=after_full_report_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()
