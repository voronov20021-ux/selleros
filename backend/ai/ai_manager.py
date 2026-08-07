"""
AIManager — хранит провайдеров (Gemini / Claude / OpenAI)
и создаёт их ЛЕНИВО (только при первом обращении).

Зачем лениво:
    Если у пользователя нет ключа OpenAI — проект всё равно
    должен запускаться и работать на Gemini.

Наружу AIManager не торчит — весь проект работает
через backend/services/ai_service.py.
"""

from backend.ai.providers.base import BaseAIProvider


class AIManager:

    def __init__(self, default_provider: str = "gemini"):
        self.default_provider = default_provider.lower()

        # Кеш созданных провайдеров: {"gemini": GeminiProvider(), ...}
        self._providers: dict[str, BaseAIProvider] = {}

    def _get_provider(self, name: str) -> BaseAIProvider:
        name = name.lower()

        if name in self._providers:
            return self._providers[name]

        # Импорты внутри метода — чтобы отсутствие библиотеки
        # одного провайдера не ломало остальных.
        if name == "gemini":
            from backend.ai.providers.gemini import GeminiProvider
            provider = GeminiProvider()

        elif name == "claude":
            from backend.ai.providers.claude import ClaudeProvider
            provider = ClaudeProvider()

        elif name == "openai":
            from backend.ai.providers.openai import OpenAIProvider
            provider = OpenAIProvider()

        else:
            raise ValueError(f"Неизвестный AI Provider: {name}")

        self._providers[name] = provider
        return provider

    async def analyze(
        self,
        prompt: str,
        provider: str | None = None,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        name = provider or self.default_provider
        return await self._get_provider(name).analyze(
            prompt,
            system=system,
            history=history,
        )
