"""
Экран «📦 Мои товары» — первый кирпич личного кабинета SellerOS.

Список строится из ДОЛГОВРЕМЕННОЙ памяти (MemoryStore.list_products) —
той же самой, что тихо пополняется после каждого анализа (см. Этап 2).
Ничего заново собирать не нужно — просто показываем то, что ARGUS
уже помнит.

ВАЖНО про данные:
    В карточке товара пока НЕТ CTR, заказов и выручки — этих цифр
    физически нет без официального Seller API Wildberries (он не
    подключён, это отдельный будущий этап). Вместо того чтобы
    показать 0 или выдумать число, честно пишем «🔒 нужен API».

Про «Обсудить» и «Полный отчёт»:
    В списке хранится только СНИМОК (цена/рейтинг/Score на момент
    последнего анализа) — его достаточно для карточки, но недостаточно
    для полноценного разговора с ARGUS (нужны фото, характеристики,
    описание). Кнопка «🔄 Обновить» запускает ТОТ ЖЕ путь, что и вставка
    ссылки (product_service -> analyzer) — просто с уже известным
    артикулом. После обновления появляются привычные кнопки
    «Обсудить»/«Полный отчёт» — это код, который уже работает
    в handlers/analyze.py, здесь он просто переиспользован.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery

from backend.ai.analyzer import AIAnalyzer
from backend.ai.report import verdict_for
from backend.config import AI_NAME
from backend.keyboards.inline import (
    after_analysis_kb,
    back_kb,
    delete_confirm_kb,
    deleted_product_kb,
    product_card_kb,
    products_list_kb,
)
from backend.memory import MemoryStore
from backend.services.history import HistoryService
from backend.services.product_service import ProductService
from backend.services.session import SessionService
from backend.utils.telegram_split import edit_or_answer_long

import html
import time

router = Router()


async def _render(callback: CallbackQuery, text: str, keyboard) -> None:
    """Аккуратно перерисовать экран (или отправить новый, если нельзя)."""
    await edit_or_answer_long(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


# --------------------------------------------------------------------- список

@router.callback_query(F.data == "menu:products")
async def show_products(callback: CallbackQuery, memory: MemoryStore):
    user_id = callback.from_user.id
    products = await memory.list_products(user_id)

    if not products:
        text = (
            "📦 <b>Мои товары</b>\n"
            "\n"
            "Пока пусто — ARGUS ещё не разбирал ваши товары.\n"
            "\n"
            "Нажмите «➕ Добавить товар» в меню, пришлите ссылку —\n"
            "и он появится здесь, а ARGUS будет помнить его всегда."
        )
        await _render(callback, text, back_kb())
        return

    lines = [
        "📦 <b>Мои товары</b>",
        f"<i>Отслеживается: {len(products)}</i>",
        "",
        "Выберите товар 👇",
    ]
    await _render(callback, "\n".join(lines), products_list_kb(products))


# --------------------------------------------------------------- карточка товара

@router.callback_query(F.data.startswith("products:open:"))
async def open_product(callback: CallbackQuery, memory: MemoryStore):
    article = int(callback.data.split(":")[2])
    await _render_product_card(callback, memory, article)


async def _render_product_card(callback: CallbackQuery, memory: MemoryStore, article: int) -> bool:
    """Отрисовать карточку сохранённого товара. Возвращает False, если товар не найден."""
    user_id = callback.from_user.id
    record = await _find_product(memory, user_id, article)

    if record is None:
        await callback.answer("Товар не найден в памяти 🤔", show_alert=True)
        return False

    when = time.strftime("%d.%m.%Y", time.localtime(record.last_seen))
    title = html.escape(record.title or "Без названия")

    lines = [f"👟 <b>{title}</b>", f"арт. <code>{record.article}</code>", ""]

    if record.price:
        lines.append(f"💰 Цена: {record.price} ₽")
    if record.rating:
        lines.append(f"⭐ Рейтинг: {record.rating}")
    if record.score is not None:
        lines.append(f"🧠 {AI_NAME}: {record.score}/100")
    lines.append(f"📸 Фото: {record.photos}")

    # Честно показываем, чего не знаем, вместо нулей или выдумки.
    lines.append("")
    lines.append("📈 CTR: 🔒 нужен Wildberries API")
    lines.append("📦 Заказы: 🔒 нужен Wildberries API")
    lines.append("💰 Выручка: 🔒 нужен Wildberries API")

    lines.append("")
    lines.append(f"<i>Обновлено: {when}</i>")

    await _render(callback, "\n".join(lines), product_card_kb(article))
    return True


# ------------------------------------------------------------------- удаление

@router.callback_query(F.data.startswith("products:delete:"))
async def confirm_delete(callback: CallbackQuery, memory: MemoryStore):
    """
    Только показывает подтверждение — ничего не удаляет.
    Реальное удаление — отдельный шаг, products:delete_yes:.
    """
    article = int(callback.data.split(":")[2])
    user_id = callback.from_user.id

    record = await _find_product(memory, user_id, article)

    if record is None:
        await callback.answer("Товар не найден в памяти 🤔", show_alert=True)
        return

    title = html.escape(record.title or "Без названия")

    text = (
        "🗑 <b>Удалить товар?</b>\n"
        "\n"
        f"👟 {title}\n"
        f"арт. <code>{record.article}</code>\n"
        "\n"
        "Товар пропадёт из «Мои товары» вместе с журналом его изменений. "
        "История ваших анализов и другие товары не затронутся."
    )

    await _render(callback, text, delete_confirm_kb(article))


@router.callback_query(F.data.startswith("products:delete_no:"))
async def cancel_delete(callback: CallbackQuery, memory: MemoryStore):
    """Отмена — возвращаемся к обычной карточке товара, ничего не удаляя."""
    article = int(callback.data.split(":")[2])
    await _render_product_card(callback, memory, article)


@router.callback_query(F.data.startswith("products:delete_yes:"))
async def perform_delete(
    callback: CallbackQuery,
    memory: MemoryStore,
    session: SessionService,
):
    article = int(callback.data.split(":")[2])
    user_id = callback.from_user.id

    # Название нужно ДО удаления, чтобы показать его в подтверждении.
    record = await _find_product(memory, user_id, article)
    title = html.escape(record.title or "Без названия") if record else None

    deleted = await memory.delete_product(user_id, article)

    # Оперативная сессия (Обсудить товар / Точный анализ) не должна
    # продолжать ссылаться на удалённый товар.
    session.clear_product(user_id, article)

    if not deleted:
        # Уже удалили раньше (например, повторный клик) — просто
        # показываем актуальный список, без второго answer() на тот же
        # callback (Telegram не разрешает отвечать на него дважды).
        await show_products(callback, memory)
        return

    text = "✅ <b>Товар удалён</b>"
    if title:
        text += f"\n\n👟 {title}\nарт. <code>{article}</code>"

    await _render(callback, text, deleted_product_kb())


# ------------------------------------------------------------------- обновить

@router.callback_query(F.data.startswith("products:refresh:"))
async def refresh_product(
    callback: CallbackQuery,
    product_service: ProductService,
    analyzer: AIAnalyzer,
    session: SessionService,
    history: HistoryService,
    wb_reviews=None,
):
    # Отвечаем на нажатие СРАЗУ и один раз — дальше несколько
    # редактирований одного и того же сообщения, без повторных answer().
    await callback.answer()

    article = int(callback.data.split(":")[2])

    await callback.message.edit_text(f"⏳ {AI_NAME} обновляет данные...")

    # Explicit refresh: allow Browser if cache STALE/MISS (force_refresh).
    if hasattr(product_service, "get_product_snapshot"):
        product = await product_service.get_product_snapshot(
            "wildberries",
            article,
            force_refresh=True,
        )
    else:
        product = await product_service.get_product("wildberries", article)

    if product is None:
        await callback.message.edit_text(
            "😔 Не удалось обновить — карточка недоступна.\n"
            "Попробуйте позже.",
            reply_markup=back_kb(),
        )
        return

    result = await analyzer.analyze(product)

    await session.set_product(
        user_id=callback.from_user.id,
        product=product,
        analysis=result,
    )
    if wb_reviews is not None:
        try:
            await wb_reviews.load_into_session(
                session, callback.from_user.id, product,
            )
        except Exception:
            pass
    await history.add(
        user_id=callback.from_user.id,
        article=product.article,
        title=product.title or "Без названия",
        score=result["score"],
        price=product.price,
        verdict=verdict_for(result["score"]),
    )

    await edit_or_answer_long(
        callback.message,
        result["report"],
        reply_markup=after_analysis_kb(),
        parse_mode="HTML",
    )


async def _find_product(memory: MemoryStore, user_id: int, article: int):
    for record in await memory.list_products(user_id):
        if record.article == article:
            return record
    return None
