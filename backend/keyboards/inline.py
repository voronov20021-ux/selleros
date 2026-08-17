"""
Все inline-клавиатуры SellerOS. ReplyKeyboard в проекте больше нет.

callback_data — формат "раздел:действие":
    menu:*     — навигация по разделам
    reports:*  — отчёты за период
    product:*  — действия с последним товаром
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from backend.config import AI_NAME, miniapp_product_url


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            # Раньше текст был "📦 Анализ товара" — переименовал, чтобы не путать
            # с новой кнопкой "Мои товары" ниже. callback_data не менял,
            # поэтому вся логика анализа работает как прежде.
            InlineKeyboardButton(text="➕ Добавить товар", callback_data="menu:analyze"),
        ],
        [
            InlineKeyboardButton(text="📦 Мои товары", callback_data="menu:products"),
        ],
        [
            InlineKeyboardButton(text="📈 Что сделать сегодня", callback_data="menu:today"),
        ],
        [
            InlineKeyboardButton(text="📅 Отчёты", callback_data="menu:reports"),
            InlineKeyboardButton(text="📊 История", callback_data="menu:history"),
        ],
        [
            InlineKeyboardButton(text=f"🧠 {AI_NAME}", callback_data="menu:ai"),
            InlineKeyboardButton(text="⚙ Настройки", callback_data="menu:settings"),
        ],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="menu:main")],
    ])


def after_analysis_kb(*, full_report_available: bool = True) -> InlineKeyboardMarkup:
    """
    Кнопки под готовым анализом товара.

    full_report_available=False — после успешного полного отчёта/анализа,
    чтобы не дублировать кнопку «Полный отчёт».

    Advisor quick-actions — одна компактная строка (не раздуваем UI).
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🧠 Обсудить товар", callback_data="product:discuss")],
        [
            InlineKeyboardButton(text="🔧 Как исправить", callback_data="advisor:fix"),
            InlineKeyboardButton(text="🚀 Как вырасти", callback_data="advisor:grow"),
        ],
    ]
    actions: list[InlineKeyboardButton] = []
    if full_report_available:
        actions.append(
            InlineKeyboardButton(text="📊 Полный отчёт", callback_data="product:full"),
        )
    actions.append(
        InlineKeyboardButton(text="🔄 Новый анализ", callback_data="menu:analyze"),
    )
    rows.append(actions)
    rows.append(
        [InlineKeyboardButton(text="📦 Мои товары", callback_data="menu:products")],
    )
    rows.append(
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_full_report_kb() -> InlineKeyboardMarkup:
    """После показа «Полный отчёт» — без повторной кнопки отчёта."""
    return after_analysis_kb(full_report_available=False)


def action_verification_kb(action_id: str) -> InlineKeyboardMarkup:
    """Seller confirmation when API/parser cannot prove application."""
    aid = str(action_id)[:36]
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, поменял", callback_data=f"actv:yes:{aid}"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"actv:no:{aid}"),
        ],
        [
            InlineKeyboardButton(text="⏳ Проверить позже", callback_data=f"actv:later:{aid}"),
            InlineKeyboardButton(text="🔄 Проверить ещё раз", callback_data=f"actv:again:{aid}"),
        ],
    ])


def action_recommend_kb(action_id: str) -> InlineKeyboardMarkup:
    """Accept / done / defer for a proposed recommendation."""
    aid = str(action_id)[:36]
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"actv:accept:{aid}"),
            InlineKeyboardButton(text="🛠 Сделал", callback_data=f"actv:done:{aid}"),
        ],
        [
            InlineKeyboardButton(text="🔍 Проверить", callback_data=f"actv:check:{aid}"),
            InlineKeyboardButton(text="⏳ Отложить", callback_data=f"actv:later:{aid}"),
        ],
    ])


def reports_kb() -> InlineKeyboardMarkup:
    """Подменю раздела 📅 Отчёты."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="За день", callback_data="reports:day"),
            InlineKeyboardButton(text="За неделю", callback_data="reports:week"),
        ],
        [
            InlineKeyboardButton(text="За месяц", callback_data="reports:month"),
            InlineKeyboardButton(text="За год", callback_data="reports:year"),
        ],
        [
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main"),
        ],
    ])


def discuss_kb() -> InlineKeyboardMarkup:
    """Показывается в режиме диалога о товаре."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔎 Подробнее", callback_data="advisor:detail"),
            InlineKeyboardButton(text="➕ Что добавить", callback_data="advisor:add"),
        ],
        [InlineKeyboardButton(text="✅ Закончить диалог", callback_data="product:discuss_end")],
    ])


def solution_after_rec_kb() -> InlineKeyboardMarkup:
    """После рекомендации / проблемы: найти варианты / сравнить / обсудить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Найти варианты", callback_data="solution:find"),
            InlineKeyboardButton(text="Сравнить", callback_data="solution:compare"),
        ],
        [
            InlineKeyboardButton(text="Обсудить", callback_data="product:discuss"),
        ],
        [
            InlineKeyboardButton(text="✅ Закончить диалог", callback_data="product:discuss_end"),
        ],
    ])


def solution_options_kb(labels: list[str] | None = None) -> InlineKeyboardMarkup:
    """После вариантов: [1][2][3]."""
    labs = labels or ["1", "2", "3"]
    row = [
        InlineKeyboardButton(
            text=str(lab),
            callback_data=f"solution:pick:{i+1}",
        )
        for i, lab in enumerate(labs[:5])
    ]
    rows = [row] if row else []
    rows.append([
        InlineKeyboardButton(text="Сравнить", callback_data="solution:compare"),
        InlineKeyboardButton(text="Обсудить", callback_data="product:discuss"),
    ])
    rows.append([
        InlineKeyboardButton(text="✅ Закончить диалог", callback_data="product:discuss_end"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def solution_after_pick_kb() -> InlineKeyboardMarkup:
    """После выбора варианта: Выбрать / Назад / Обсудить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Выбрать", callback_data="solution:confirm"),
            InlineKeyboardButton(text="Назад", callback_data="solution:back"),
        ],
        [
            InlineKeyboardButton(text="Обсудить", callback_data="product:discuss"),
        ],
        [
            InlineKeyboardButton(text="✅ Закончить диалог", callback_data="product:discuss_end"),
        ],
    ])


def solution_after_recorded_kb() -> InlineKeyboardMarkup:
    """После записи решения."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Записать решение", callback_data="solution:record"),
            InlineKeyboardButton(text="Обсудить дальше", callback_data="product:discuss"),
        ],
        [
            InlineKeyboardButton(text="✅ Закончить диалог", callback_data="product:discuss_end"),
        ],
    ])


def keyboard_for_solution_stage(
    stage: str | None,
    *,
    option_labels: list[str] | None = None,
    full_report_shown: bool = False,
) -> InlineKeyboardMarkup:
    """
    Minimal stage keyboard. Never duplicates product:full after full report.
    """
    if stage == "after_options":
        return solution_options_kb(option_labels)
    if stage == "after_pick":
        return solution_after_pick_kb()
    if stage == "after_recorded":
        return solution_after_recorded_kb()
    if stage == "after_rec":
        return solution_after_rec_kb()
    # default discuss — no product:full here
    return discuss_kb()


def discuss_entry_kb() -> InlineKeyboardMarkup:
    """Если нажали «Обсудить», а товара в памяти нет."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Проанализировать товар", callback_data="menu:analyze")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])


def ai_chat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Ещё вопрос", callback_data="menu:ai"),
        ],
        [
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main"),
        ],
    ])


def products_list_kb(products) -> InlineKeyboardMarkup:
    """
    Список «Мои товары». products — список ProductRecord из MemoryStore.

    Каждая кнопка = один товар, по нажатию открывается его карточка.
    callback_data хранит артикул: "products:open:211246754".
    """
    rows = []

    for record in products[:20]:  # с запасом; телеграм не любит гигантские клавиатуры
        title = (record.title or "Без названия")[:28]
        score = f" · {record.score}/100" if record.score is not None else ""
        rows.append([InlineKeyboardButton(
            text=f"👟 {title}{score}",
            callback_data=f"products:open:{record.article}",
        )])

    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_added_kb(article: int | None = None) -> InlineKeyboardMarkup:
    """Короткий Telegram-разбор: Mini App с nmID, иначе локальные шаги."""
    rows: list[list[InlineKeyboardButton]] = []
    url = miniapp_product_url(article)
    if url:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📱 Открыть полный разбор в Seller OS",
                    web_app=WebAppInfo(url=url),
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="🤖 Короткий разбор здесь", callback_data="product:prelim")],
            [InlineKeyboardButton(text="📊 Точный анализ", callback_data="product:precise")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_preliminary_kb() -> InlineKeyboardMarkup:
    """
    После «🤖 Предварительный анализ» — кнопка самого предварительного
    анализа больше не нужна (он уже показан), поэтому её здесь нет.
    Вместо неё — переход к точному анализу и явный повтор по запросу.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Точный анализ", callback_data="product:precise")],
        [InlineKeyboardButton(text="🔄 Повторить анализ", callback_data="product:prelim_retry")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])


def precise_data_kb() -> InlineKeyboardMarkup:
    """Публичная коммерция неполная — ввод только недостающих CARD-полей."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ввести недостающие данные", callback_data="product:manual_input")],
        [InlineKeyboardButton(text="🔑 Подключить свой API", callback_data="product:connect_api")],
    ])


def precise_data_ready_kb() -> InlineKeyboardMarkup:
    """
    Публичные price/rating/feedbacks уже есть (CARD DATA) — полный анализ доступен.
    «Добавить данные продавца» = только private metrics (CTR/CVR/sales/...),
    без повторного запроса цены/рейтинга/отзывов карточки.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Получить полный анализ", callback_data="product:full_analysis")],
        [InlineKeyboardButton(text="➕ Добавить данные продавца", callback_data="product:seller_metrics")],
        [InlineKeyboardButton(text="🔑 Подключить свой API", callback_data="product:connect_api")],
    ])


def precise_data_stale_seller_kb() -> InlineKeyboardMarkup:
    """
    В памяти есть SellerData по артикулу, но он не подтверждён для текущего анализа.
    Не авто-подставляем — предлагаем использовать / обновить / идти в анализ.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Получить полный анализ", callback_data="product:full_analysis")],
        [InlineKeyboardButton(
            text="✅ Использовать сохранённые данные продавца",
            callback_data="product:confirm_seller",
        )],
        [InlineKeyboardButton(text="🔄 Обновить данные продавца", callback_data="product:seller_metrics")],
        [InlineKeyboardButton(text="🔑 Подключить свой API", callback_data="product:connect_api")],
    ])


def manual_input_skip_kb() -> InlineKeyboardMarkup:
    """После gap-fill — private metrics необязательны."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить данные продавца", callback_data="manual:extra_add")],
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="manual:extra_skip")],
    ])


def api_connect_info_kb() -> InlineKeyboardMarkup:
    """Экран «🔑 Подключить свой API» — пока только информационный."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])


def product_card_kb(article: int) -> InlineKeyboardMarkup:
    """Кнопки под карточкой сохранённого товара (не только что проанализированного)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"products:refresh:{article}")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"products:delete:{article}")],
        [InlineKeyboardButton(text="◀️ К товарам", callback_data="menu:products")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])


def delete_confirm_kb(article: int) -> InlineKeyboardMarkup:
    """
    Подтверждение удаления товара.

    Намеренно нет пути «удалить одним нажатием»: кнопка «🗑 Удалить товар»
    в product_card_kb() ведёт СЮДА, а не сразу удаляет.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"products:delete_no:{article}"),
            InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"products:delete_yes:{article}"),
        ],
    ])


def deleted_product_kb() -> InlineKeyboardMarkup:
    """После «✅ Товар удалён» — вернуться к списку «Мои товары»."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Мои товары", callback_data="menu:products")],
    ])
