"""
builder.py — сборщик контекста.

Опрашивает все зарегистрированные источники параллельно,
отбирает подходящие под тип вопроса и склеивает в один текст
с учётом бюджета символов.

Добавить новый источник знаний = одна строка register().
"""

import asyncio
import logging

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource

log = logging.getLogger("selleros.ai.context")

#: Сколько символов контекста максимум уходит в промпт.
#: Больше — дороже запрос и выше шанс, что модель утонет в деталях.
CONTEXT_BUDGET = 3000


class ContextBuilder:

    def __init__(self, budget: int = CONTEXT_BUDGET):
        self.budget = budget
        self._sources: list[ContextSource] = []

    def register(self, source: ContextSource) -> None:
        self._sources.append(source)
        log.info("Источник контекста подключён: %s", source.name)

    async def build(self, request: ContextRequest) -> str:
        """Готовый текст контекста для промпта. Пустая строка, если данных нет."""

        sources = [s for s in self._sources if s.relevant_for(request)]

        if not sources:
            return ""

        results = await asyncio.gather(
            *(self._safe_fetch(source, request) for source in sources),
            return_exceptions=False,
        )

        blocks = [block for block in results if block is not None]

        if not blocks:
            return ""

        blocks.sort(key=lambda block: block.priority)

        parts: list[str] = []
        used = 0

        for block in blocks:
            rendered = block.render()

            if used + len(rendered) > self.budget:
                continue

            parts.append(rendered)
            used += len(rendered)

        return "\n\n".join(parts)

    async def _safe_fetch(
        self,
        source: ContextSource,
        request: ContextRequest,
    ) -> ContextBlock | None:
        """Упавший источник не должен ронять весь ответ."""
        try:
            return await source.fetch(request)
        except Exception as error:
            log.warning("Источник %s упал: %s", source.name, error)
            return None
