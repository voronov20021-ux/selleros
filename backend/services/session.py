"""
SessionService — рабочая память ARGUS о товаре пользователя.

Это «оперативная память» — быстрая, живёт в процессе бота,
нужна для плавного разговора прямо сейчас (get_product/get_analysis
остаются синхронными и мгновенными, как раньше).

Долговременная память — отдельно, в backend/memory/store.py.
set_product() при каждом анализе тихо записывает снимок туда:
    • товар (backend.memory.store.upsert_product)
    • рекомендации (backend.memory.store.add_recommendations)
Если что-то из цены/рейтинга/Score/числа фото изменилось с прошлого
раза — MemoryStore сам зафиксирует это в журнале изменений.

analysis в set_product() необязателен: товар можно сохранить сразу
после получения карточки (сценарий «✅ Товар добавлен»), ещё до того,
как посчитан Score — полноценный анализ строится позже отдельным
шагом (кнопки «Предварительный анализ» / «Точный анализ»).

seller-данные (цена/рейтинг/отзывы, которые ввёл продавец или отдал
Seller API) — отдельная сущность SellerData (backend/services/seller_data.py).
Она НЕ подменяет WBProduct и хранится отдельно: в оперативном кэше этого
сервиса и, для персистентности между перезапусками, в MemoryStore
(колонки products, добавленные под SellerData).

Если memory_store не передан (например, в тестах) — SessionService
работает как раньше, только в оперативной памяти.
"""

import logging
import time

from backend.services.seller_data import SellerData

log = logging.getLogger("selleros.session")


class SessionService:

    def __init__(self, memory_store=None):
        self._sessions: dict[int, dict] = {}
        self.memory_store = memory_store

    def _session(self, user_id: int) -> dict:
        return self._sessions.setdefault(user_id, {
            "product": None,
            "analysis": None,
            "seller_data": None,
        })

    async def set_product(self, user_id: int, product, analysis: dict | None = None):
        session = self._session(user_id)
        session["product"] = product
        session["analysis"] = analysis

        if self.memory_store is None:
            return

        await self.memory_store.touch_user(user_id)

        await self.memory_store.upsert_product(
            user_id=user_id,
            article=product.article,
            marketplace="wildberries",
            title=product.title or "Без названия",
            price=product.price,
            rating=product.rating,
            score=analysis.get("score") if analysis else None,
            photos=len(product.photos),
        )

        recommendations = analysis.get("recommendations") if analysis else None
        if recommendations:
            await self.memory_store.add_recommendations(
                user_id, product.article, recommendations,
            )

    def get_product(self, user_id: int):
        return self._session(user_id)["product"]

    def get_analysis(self, user_id: int) -> dict | None:
        return self._session(user_id)["analysis"]

    def has_product(self, user_id: int) -> bool:
        return self._session(user_id)["product"] is not None

    def clear_product(self, user_id: int, article: int | None = None) -> None:
        """
        Сбросить товар/анализ/seller-данные из ОПЕРАТИВНОЙ сессии.

        Используется после удаления товара из «Мои товары» (см.
        backend/handlers/products.py), чтобы «Обсудить товар» и «Точный
        анализ» не продолжали ссылаться на то, чего больше нет в памяти.

        Если article указан и не совпадает с товаром, который сейчас в
        сессии, — ничего не делаем: значит, продавец удалил ДРУГОЙ товар
        из списка, а не тот, с которым сейчас работает.
        """
        session = self._session(user_id)
        current = session.get("product")

        if article is not None and current is not None and getattr(current, "article", None) != article:
            return

        session["product"] = None
        session["analysis"] = None
        session["seller_data"] = None

    # --------------------------------------------------------- seller data

    async def set_seller_data(self, user_id: int, article: int, data: SellerData) -> None:
        """
        Сохранить данные продавца: в оперативный кэш сразу,
        в долговременную память — если MemoryStore подключён.

        Строка товара в products должна уже существовать (её создаёт
        set_product()/upsert_product() в момент добавления товара) —
        MemoryStore.save_seller_data() сам логирует и ничего не делает,
        если это не так, вместо того чтобы падать.
        """
        session = self._session(user_id)
        session["seller_data"] = data

        if self.memory_store is None:
            return

        updated_at = data.updated_at.timestamp() if data.updated_at else time.time()

        await self.memory_store.save_seller_data(
            user_id=user_id,
            article=article,
            marketplace="wildberries",
            price=data.price,
            rating=data.rating,
            feedbacks=data.feedbacks,
            sales=data.sales,
            orders=data.orders,
            period=data.period,
            price_source=data.price_source,
            rating_source=data.rating_source,
            feedbacks_source=data.feedbacks_source,
            updated_at=updated_at,
        )

    def get_seller_data(self, user_id: int) -> SellerData | None:
        """Данные продавца из оперативного кэша (текущая сессия)."""
        return self._session(user_id)["seller_data"]
