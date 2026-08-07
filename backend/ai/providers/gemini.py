import warnings

# Пакет google.generativeai объявлен устаревшим и печатает FutureWarning
# при импорте. Сам Gemini мы сейчас не трогаем — просто убираем шум из логов.
warnings.filterwarnings("ignore", category=FutureWarning, module="google.*")

import google.generativeai as genai

from backend.ai.providers.base import BaseAIProvider

# Ключи читаем только из backend.config — единого места загрузки .env.
from backend.config import AI_NAME, GEMINI_API_KEY, GEMINI_MODEL


class GeminiProvider(BaseAIProvider):

    def __init__(self):
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY не найден в .env")

        genai.configure(api_key=GEMINI_API_KEY)

        self.model_name = GEMINI_MODEL
        self.model = genai.GenerativeModel(self.model_name)

    async def analyze(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        # Gemini в этом пакете не принимает chat-историю так же, как VseGPT,
        # поэтому склеиваем всё в один текст. Gemini у нас запасной вариант.
        if system or history:
            parts = []
            if system:
                parts.append(system)
            for message in history or []:
                who = "Продавец" if message["role"] == "user" else AI_NAME
                parts.append(f"{who}: {message['content']}")
            parts.append(f"Продавец: {prompt}")
            prompt = "\n\n".join(parts)

        # generate_content_async, а не generate_content:
        # синхронный вызов заморозил бы весь Telegram-бот.
        response = await self.model.generate_content_async(prompt)

        return {
            "provider": "Gemini",
            "model": self.model_name,
            "result": response.text,
            "prompt": prompt,
        }
