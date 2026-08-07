"""
Все inline-клавиатуры SellerOS. ReplyKeyboard в проекте больше нет.

callback_data — формат "раздел:действие":
    menu:*     — навигация по разделам
    reports:*  — отчёты за период
    product:*  — действия с последним товаром
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from backend.config import AI_NAME


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


def after_analysis_kb() -> InlineKeyboardMarkup:
    """Кнопки под готовым анализом товара (этап 3, пункт 7)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧠 Обсудить товар", callback_data="product:discuss"),
        ],
        [
            InlineKeyboardButton(text="📊 Полный отчёт", callback_data="product:full"),
            InlineKeyboardButton(text="🔄 Новый анализ", callback_data="menu:analyze"),
        ],
        [
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main"),
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
        [InlineKeyboardButton(text="✅ Закончить диалог", callback_data="product:discuss_end")],
    ])


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


def product_added_kb() -> InlineKeyboardMarkup:
    """После «✅ Товар добавлен» — выбор между предварительным и точным анализом."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Предварительный анализ", callback_data="product:prelim")],
        [InlineKeyboardButton(text="📊 Точный анализ", callback_data="product:precise")],
    ])


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
    """Данных продавца ещё нет — предлагаем ввести вручную или подключить API."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ввести данные", callback_data="product:manual_input")],
        [InlineKeyboardButton(text="🔑 Подключить свой API", callback_data="product:connect_api")],
    ])


def precise_data_ready_kb() -> InlineKeyboardMarkup:
    """Данные продавца уже есть — можно обновить их или запросить полный анализ."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить данные", callback_data="product:manual_input")],
        [InlineKeyboardButton(text="📈 Получить полный анализ", callback_data="product:full_analysis")],
        [InlineKeyboardButton(text="🔑 Подключить свой API", callback_data="product:connect_api")],
    ])


def manual_input_skip_kb() -> InlineKeyboardMarkup:
    """После обязательных полей (цена/рейтинг/отзывы) — доп. данные необязательны."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Добавить продажи", callback_data="manual:extra_add")],
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
