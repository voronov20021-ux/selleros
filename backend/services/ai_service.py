"""
AIService — ЕДИНАЯ точка входа в AI для всего проекта.

Правило проекта:
    Никто в SellerOS не вызывает Claude/GPT/Gemini напрямую.
    Все вызовы AI идут только через AIService.

Для пользователя это всегда «Seller AI».
Какая модель под капотом — деталь реализации, наружу не протекает.

    Telegram -> Seller AI -> AIService -> ClaudeProvider
             -> VseGPT API -> Claude Sonnet 5

Провайдер выбирается в .env: AI_PROVIDER=claude | gemini | openai | off
Если основной не ответил — пробуем запасные из FALLBACK_ORDER.
Если не ответил никто — возвращаем None, и бот показывает отчёт
без AI-комментария (но не падает).
"""

import asyncio
import logging

from backend.ai.ai_manager import AIManager
from backend.ai.providers.vsegpt import VseGPTError

# Импорт backend.config гарантирует, что .env уже загружен,
# каким бы способом ни запустили проект.
from backend.config import AI_ENABLED, AI_PROVIDER, AI_TIMEOUT

log = logging.getLogger("selleros.ai")

#: Порядок запасных провайдеров, если основной не ответил.
FALLBACK_ORDER = ["claude", "openai", "gemini"]

OFF_VALUES = ("off", "none", "disabled", "false", "0", "")


class AIService:

    def __init__(self, provider: str | None = None):
        # provider передают только тесты. В обычной работе
        # значение приходит из .env через backend.config.
        if provider is None:
            self.provider_name = AI_PROVIDER
            self.enabled = AI_ENABLED
        else:
            self.provider_name = provider.strip().lower()
            self.enabled = self.provider_name not in OFF_VALUES

        self.manager = AIManager(self.provider_name) if self.enabled else None

        #: Последняя ошибка — чтобы test_ai мог показать причину.
        self.last_error: str | None = None

        if self.enabled:
            log.info("Seller AI включён (провайдер: %s)", self.provider_name)
        else:
            log.info("Seller AI выключен — в .env указано AI_PROVIDER=off")

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> str | None:
        """
        Главный метод. Возвращает текст ответа Seller AI
        или None, если AI выключен либо все провайдеры недоступны.
        """

        if not self.enabled:
            return None

        self.last_error = None

        # Основной провайдер + запасные, без повторов.
        chain = [self.provider_name] + [
            name for name in FALLBACK_ORDER
            if name != self.provider_name
        ]

        for name in chain:
            text = await self._try_provider(
                name, prompt, system=system, history=history,
            )

            if text:
                return text

        log.error("Все AI-провайдеры недоступны. Причина: %s", self.last_error)
        return None

    def _remember(self, message: str) -> None:
        # Запоминаем ошибку ОСНОВНОГО провайдера: она интереснее всего.
        # Ошибки запасных уходят в лог, но причину показываем первую.
        if self.last_error is None:
            self.last_error = message

    async def _try_provider(
        self,
        name: str,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> str | None:
        try:
            # Таймаут на уровне HTTP уже стоит в провайдере;
            # этот — страховка на случай зависания на нашей стороне.
            result = await asyncio.wait_for(
                self.manager.analyze(
                    prompt, provider=name, system=system, history=history,
                ),
                timeout=AI_TIMEOUT + 5,
            )

            text = ((result or {}).get("result") or "").strip()

            if not text:
                self._remember(f"{name}: пустой ответ")
                log.warning("AI %s вернул пустой ответ", name)
                return None

            return text

        except VseGPTError as error:
            # Понятная ошибка VseGPT: ключ, баланс, тариф, лимит.
            self._remember(str(error))
            log.error("🔴 Seller AI (%s): %s", name, error)

        except asyncio.TimeoutError:
            self._remember(f"{name}: таймаут {AI_TIMEOUT} сек")
            log.warning("AI %s: таймаут %d сек", name, AI_TIMEOUT)

        except ValueError as error:
            # Нет ключа / не задана модель / неизвестный провайдер.
            self._remember(f"{name}: {error}")
            log.warning("AI %s не настроен: %s", name, error)

        except Exception as error:
            self._remember(f"{name}: {type(error).__name__}: {error}")
            log.exception("AI %s: неожиданная ошибка", name)

        return None
