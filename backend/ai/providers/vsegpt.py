"""
VseGPTProvider — базовый клиент к VseGPT API.

VseGPT — российский шлюз к 120+ нейросетям с OpenAI-совместимым API.
Мы ходим туда обычным пакетом `openai`, просто подменив base_url.

    Telegram -> Seller AI -> AIService -> ClaudeProvider
             -> VseGPTProvider -> VseGPT API -> Claude Sonnet 5

Важная особенность VseGPT:
    Ошибки «неверный ключ» и «кончился баланс» приходят
    НЕ как 401/402, а как обычный HTTP 400 с текстом внутри.
    Поэтому мы разбираем текст ошибки, а не только код статуса.
    Иначе продавец видел бы просто «AI недоступен» и гадал, почему.
"""

import asyncio
import logging

from openai import AsyncOpenAI
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from backend.ai.providers.base import BaseAIProvider
from backend.config import (
    AI_MAX_TOKENS,
    AI_TIMEOUT,
    AI_TEMPERATURE,
    VSEGPT_API_KEY,
    VSEGPT_BASE_URL,
)

log = logging.getLogger("selleros.ai.vsegpt")

# VseGPT на обычных тарифах разрешает примерно 1 запрос в 2 секунды.
# Поэтому при 429 ждём чуть больше и пробуем ещё раз.
RATE_LIMIT_PAUSE = 2.5
RATE_LIMIT_RETRIES = 2

MONEY_URL = "https://vsegpt.ru/User/Money"
KEY_URL = "https://vsegpt.ru/User/API"


class VseGPTError(Exception):
    """Ошибка VseGPT с понятным человеку текстом."""

    def __init__(self, message: str, *, hint: str = "", fatal: bool = False):
        super().__init__(message)
        self.message = message
        #: Подсказка, что делать. Пишется в лог рядом с ошибкой.
        self.hint = hint
        #: fatal=True — повторять запрос бессмысленно (ключ, баланс, тариф).
        self.fatal = fatal

    def __str__(self) -> str:
        return f"{self.message} {self.hint}".strip()


def _classify(text: str, status: int | None) -> VseGPTError:
    """Текст ошибки VseGPT -> понятное сообщение по-русски."""

    low = (text or "").lower()

    # --- Ключ ---------------------------------------------------------------
    if "user with this api key not found" in low:
        return VseGPTError(
            "Неверный ключ VseGPT: пользователь с таким ключом не найден.",
            hint=f"Возьмите актуальный ключ на {KEY_URL} и впишите в .env (VSEGPT_API_KEY).",
            fatal=True,
        )

    if "original openai api key" in low:
        return VseGPTError(
            "В .env лежит ключ от OpenAI, а нужен ключ VseGPT.",
            hint=f"Ключи VseGPT выдаются на {KEY_URL}.",
            fatal=True,
        )

    # --- Деньги -------------------------------------------------------------
    if "out of budget" in low or "add some money" in low:
        return VseGPTError(
            "Закончился баланс VseGPT — запрос не оплачен.",
            hint=f"Пополните счёт: {MONEY_URL}",
            fatal=True,
        )

    if "exceeded soft user limit" in low:
        return VseGPTError(
            "Запрос дороже вашего лимита на один вызов.",
            hint="Поднимите лимит: https://vsegpt.ru/User/SettingsModels",
            fatal=True,
        )

    # --- Тариф и модель -----------------------------------------------------
    if "not available on your subscription" in low:
        return VseGPTError(
            "Выбранная модель недоступна на вашем тарифе VseGPT.",
            hint="Смените тариф или модель (CLAUDE_MODEL в .env): https://vsegpt.ru/Docs/Tariffs",
            fatal=True,
        )

    if "model" in low and ("not found" in low or "no such model" in low):
        return VseGPTError(
            "VseGPT не знает такую модель.",
            hint="Проверьте CLAUDE_MODEL в .env. Список: python -m backend.test_ai --models",
            fatal=True,
        )

    # --- Размер запроса -----------------------------------------------------
    if "maximum context length" in low or "context" in low and "tokens" in low:
        return VseGPTError(
            "Запрос длиннее контекста модели.",
            hint="Уменьшите объём данных в промпте или AI_MAX_TOKENS в .env.",
            fatal=True,
        )

    # --- Общие статусы ------------------------------------------------------
    if status == 403:
        return VseGPTError(
            "VseGPT отклонил запрос (403).",
            hint="Обычно это блокировка по правилам сервиса. Напишите в поддержку VseGPT.",
            fatal=True,
        )

    if status == 429:
        return VseGPTError(
            "Слишком частые запросы к VseGPT.",
            hint="На обычном тарифе лимит около 1 запроса в 2 секунды.",
        )

    if status == 504:
        return VseGPTError(
            "VseGPT не дождался ответа модели (504).",
            hint="Попробуйте ещё раз или выберите модель побыстрее.",
        )

    if status and status >= 500:
        return VseGPTError(
            f"Сбой на стороне VseGPT ({status}).",
            hint="Проверьте статус: https://vsegpt.ru/Docs/Uptime",
        )

    return VseGPTError(
        f"VseGPT вернул ошибку{f' ({status})' if status else ''}: {text[:200]}",
    )


def _error_text(error: APIStatusError) -> str:
    """Достаём текст ошибки из ответа VseGPT (формат бывает разный)."""

    body = getattr(error, "body", None)

    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
        if isinstance(inner, str):
            return inner
        if body.get("message"):
            return str(body["message"])

    return str(getattr(error, "message", "") or error)


class VseGPTProvider(BaseAIProvider):
    """Базовый провайдер. Наследники задают только model_name."""

    #: ID модели в каталоге VseGPT, например "anthropic/claude-sonnet-5".
    model_name: str = ""

    #: Как называется провайдер в логах. Пользователю не показывается.
    provider_label: str = "VseGPT"

    def __init__(self, model: str | None = None):
        if not VSEGPT_API_KEY:
            raise ValueError(
                "VSEGPT_API_KEY не найден в .env. "
                f"Ключ берётся на {KEY_URL}"
            )

        if model:
            self.model_name = model

        if not self.model_name:
            raise ValueError("Не задана модель VseGPT")

        self.client = AsyncOpenAI(
            api_key=VSEGPT_API_KEY,
            base_url=VSEGPT_BASE_URL,
            timeout=AI_TIMEOUT,
            max_retries=0,   # ретраями управляем сами, см. _call()
        )

    async def analyze(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        text = await self._call(prompt, system=system, history=history)

        return {
            "provider": self.provider_label,
            "model": self.model_name,
            "result": text,
            "prompt": prompt,
        }

    @staticmethod
    def _build_messages(
        prompt: str,
        system: str | None,
        history: list[dict] | None,
    ) -> list[dict]:
        """Системный промпт + история диалога + новый вопрос."""
        messages: list[dict] = []

        if system:
            messages.append({"role": "system", "content": system})

        if history:
            messages.extend(history)

        messages.append({"role": "user", "content": prompt})
        return messages

    async def _call(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> str:
        last_error: VseGPTError | None = None
        messages = self._build_messages(prompt, system, history)

        for attempt in range(1, RATE_LIMIT_RETRIES + 2):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    max_tokens=AI_MAX_TOKENS,
                    temperature=AI_TEMPERATURE,
                    extra_headers={"X-Title": "SellerOS"},
                )

            except (RateLimitError, APIStatusError) as error:
                status = getattr(error, "status_code", None)
                failure = _classify(_error_text(error), status)

                if failure.fatal:
                    raise failure from error

                last_error = failure

                if attempt <= RATE_LIMIT_RETRIES:
                    log.warning(
                        "%s: %s Повтор через %.1f сек (попытка %d)",
                        self.model_name, failure, RATE_LIMIT_PAUSE, attempt,
                    )
                    await asyncio.sleep(RATE_LIMIT_PAUSE)
                    continue

                raise failure from error

            except APITimeoutError as error:
                raise VseGPTError(
                    f"Модель не ответила за {AI_TIMEOUT} сек.",
                    hint="Увеличьте AI_TIMEOUT в .env или возьмите модель побыстрее.",
                ) from error

            except APIConnectionError as error:
                raise VseGPTError(
                    "Не удалось соединиться с VseGPT.",
                    hint="Проверьте интернет и что VseGPT доступен: https://vsegpt.ru",
                ) from error

            # --- Ответ получен ---
            if not response.choices:
                raise VseGPTError("VseGPT вернул пустой ответ (нет choices).")

            content = (response.choices[0].message.content or "").strip()

            if not content:
                raise VseGPTError("Модель вернула пустой текст.")

            usage = getattr(response, "usage", None)
            if usage:
                log.info(
                    "%s ответил: %d симв., токенов %s→%s",
                    self.model_name, len(content),
                    getattr(usage, "prompt_tokens", "?"),
                    getattr(usage, "completion_tokens", "?"),
                )

            return content

        raise last_error or VseGPTError("VseGPT недоступен.")

    async def list_models(self) -> list[str]:
        """Список доступных моделей — для диагностики (test_ai --models)."""
        models = await self.client.models.list()
        return sorted(model.id for model in models.data)


# Классы исключений openai, которые может быть полезно ловить снаружи.
__all__ = [
    "VseGPTProvider",
    "VseGPTError",
    "AuthenticationError",
    "BadRequestError",
    "InternalServerError",
    "PermissionDeniedError",
]
