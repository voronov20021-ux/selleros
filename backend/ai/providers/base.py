from abc import ABC, abstractmethod


class BaseAIProvider(ABC):

    @abstractmethod
    async def analyze(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        """
        system  — системный промпт (характер Seller AI).
        history — прошлые сообщения диалога в формате chat-API.

        Оба параметра необязательны: старые вызовы analyze(prompt)
        продолжают работать.
        """
        raise NotImplementedError
