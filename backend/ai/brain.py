"""
brain.py — интеллект Seller AI.

Единственное место, где принимается решение «как ответить продавцу».

Путь сообщения:

    сообщение
        ↓
    1. классификация   (intents.py)      — о чём вопрос
        ↓
    2. small talk?     (smalltalk.py)    — если да, отвечаем мгновенно, без модели
        ↓
    3. контекст        (context/)        — товар, история, позже Seller API и RAG
        ↓
    4. системный промпт (personality.py) — характер + плейбук темы
        ↓
    5. память диалога  (dialog.py)       — последние 20 сообщений
        ↓
    6. AIService                          — единственная дверь к моделям
        ↓
    ответ + запись в память

Хендлеры Telegram знают только метод reply(). Всё остальное — здесь.
"""

import logging

from backend.ai import smalltalk
from backend.ai.context import ContextBuilder, ContextRequest
from backend.ai.dialog import DIALOG_DEPTH, DialogMemory
from backend.ai.intents import Intent, classify, refers_to_product
from backend.ai.personality import build_system

log = logging.getLogger("selleros.ai.brain")

#: Сколько последних сообщений диалога уходит в модель.
HISTORY_IN_PROMPT = 12


class BrainReply:
    """Ответ Seller AI вместе с тем, как он был получен."""

    def __init__(self, text: str, intent: Intent, *, used_model: bool):
        self.text = text
        self.intent = intent
        #: False — ответили локально (small talk), модель не дёргали.
        self.used_model = used_model

    def __bool__(self) -> bool:
        return bool(self.text)


class SellerBrain:

    def __init__(self, ai_service, session, context_builder: ContextBuilder, memory_store=None):
        self.ai = ai_service
        self.session = session
        self.context = context_builder
        self.store = memory_store

        #: Рабочая копия диалога в оперативной памяти: {user_id: DialogMemory}.
        #: Долговременная копия — в MemoryStore (переживает перезапуск).
        self._dialogs: dict[int, DialogMemory] = {}

    # ----------------------------------------------------------------- память

    async def memory(self, user_id: int) -> DialogMemory:
        """
        Рабочая память разговора. При первом обращении к пользователю
        после старта бота — подгружает последние сообщения из
        долговременной памяти, поэтому разговор не обрывается
        после перезапуска.
        """
        if user_id not in self._dialogs:
            dialog = DialogMemory()

            if self.store is not None:
                for message in await self.store.last_messages(user_id, limit=DIALOG_DEPTH):
                    if message.role == "user":
                        dialog.add_user(message.content)
                    else:
                        dialog.add_assistant(message.content)

            self._dialogs[user_id] = dialog

        return self._dialogs[user_id]

    def forget(self, user_id: int) -> None:
        """
        Сбросить РАБОЧУЮ память разговора — новая тема начинается с чистого листа.

        Долговременная история в MemoryStore при этом НЕ удаляется:
        как человек, начиная разговор о новой теме, не стирает себе
        память о прошлых разговорах — просто сейчас с ней не сверяется.
        """
        self._dialogs.pop(user_id, None)

    async def _remember(self, user_id: int, dialog: DialogMemory, role: str, text: str) -> None:
        """Записать реплику и в рабочую память, и в долговременную."""
        if role == "user":
            dialog.add_user(text)
        else:
            dialog.add_assistant(text)

        if self.store is not None:
            await self.store.add_message(user_id, role, text)

    # ----------------------------------------------------------- быстрый путь

    @staticmethod
    def is_quick(text: str) -> bool:
        """
        Ответим ли мы мгновенно, без обращения к модели.

        Нужно хендлерам: под «привет» не стоит показывать
        сообщение «🧠 Думаю...» — ответ уже готов.
        """
        return classify(text) is Intent.SMALL_TALK

    # ----------------------------------------------------------------- ответ

    async def reply(
        self,
        user_id: int,
        text: str,
        *,
        force_product_mode: bool = False,
    ) -> BrainReply:
        """
        Главный метод. force_product_mode=True — режим «Обсудить товар»:
        любая реплика трактуется как разговор про текущую карточку.
        """

        text = (text or "").strip()
        product = self.session.get_product(user_id)

        intent = classify(text, has_product=product is not None)

        # --- 1. Болтовня: отвечаем сами, мгновенно ---
        if intent is Intent.SMALL_TALK and not force_product_mode:
            answer = smalltalk.reply(text, product=product)

            dialog = await self.memory(user_id)
            await self._remember(user_id, dialog, "user", text)
            await self._remember(user_id, dialog, "assistant", answer)

            log.info("intent=%s (без модели)", intent.value)
            return BrainReply(answer, intent, used_model=False)

        # --- 2. Уточняем намерение ---
        if force_product_mode and product is not None:
            # В режиме обсуждения товара тема остаётся (цена, фото, реклама),
            # но если она не определилась — считаем это разговором о карточке.
            if intent in (Intent.GENERAL_QUESTION, Intent.SMALL_TALK):
                intent = Intent.PRODUCT_DISCUSSION

        elif product is not None and refers_to_product(text):
            # «А если поднять цену?» — вопрос про последний товар.
            if intent is Intent.GENERAL_QUESTION:
                intent = Intent.PRODUCT_DISCUSSION

        # --- 3. Контекст ---
        request = ContextRequest(
            user_id=user_id,
            text=text,
            intent=intent,
            extra={"product_mode": force_product_mode},
        )
        context = await self.context.build(request)

        # --- 4. Системный промпт ---
        system = build_system(intent, extra=_context_section(context, product))

        # --- 5. История диалога ---
        dialog = await self.memory(user_id)
        history = dialog.to_api(limit=HISTORY_IN_PROMPT)

        log.info(
            "intent=%s контекст=%d симв. история=%d сообщ.",
            intent.value, len(context), len(history),
        )

        # --- 6. Запрос к модели через единый AIService ---
        answer = await self.ai.generate(text, system=system, history=history)

        if answer is None:
            return BrainReply("", intent, used_model=True)

        await self._remember(user_id, dialog, "user", text)
        await self._remember(user_id, dialog, "assistant", answer)

        return BrainReply(answer, intent, used_model=True)


def _context_section(context: str, product) -> str:
    """Оформляем контекст как часть системного промпта."""

    if not context:
        if product is None:
            return (
                "КОНТЕКСТ\n\n"
                "Товар пока не разбирали. Если вопрос требует данных карточки — "
                "попроси прислать ссылку на товар, не выдумывай цифры."
            )
        return ""

    return (
        "ЧТО ТЫ ЗНАЕШЬ О ЭТОМ ПРОДАВЦЕ\n\n"
        f"{context}\n\n"
        "Опирайся на эти данные. Всё, чего здесь нет, ты не знаешь — "
        "спрашивай, а не придумывай."
    )
