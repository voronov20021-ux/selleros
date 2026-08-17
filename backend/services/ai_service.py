"""
AIService — ЕДИНАЯ точка входа в AI для всего проекта.

Routing:
    PRIMARY_MODEL → FALLBACK_MODEL → остальные из FALLBACK_ORDER

Если модель недоступна на тарифе VseGPT (HTTP 400) — провайдер
помечается disabled на время жизни процесса и больше не вызывается.
"""

import asyncio
import logging

from backend.ai.ai_manager import AIManager
from backend.ai.providers.vsegpt import VseGPTError

from backend.config import (
    AI_ENABLED,
    AI_PROVIDER,
    AI_TIMEOUT,
    CLAUDE_ENABLED,
    FALLBACK_MODEL,
    PRIMARY_MODEL,
)

log = logging.getLogger("selleros.ai")

#: Порядок запасных провайдеров после PRIMARY/FALLBACK.
FALLBACK_ORDER = ["claude", "openai", "gemini"]

OFF_VALUES = ("off", "none", "disabled", "false", "0", "")

#: Подстроки ошибок «модель недоступна» — sticky disable.
_MODEL_UNAVAILABLE_MARKERS = (
    "недоступна на вашем тарифе",
    "not available on your subscription",
    "не знает такую модель",
    "no such model",
    "model not found",
)


class AIService:

    def __init__(self, provider: str | None = None):
        if provider is None:
            self.provider_name = PRIMARY_MODEL or AI_PROVIDER
            self.enabled = AI_ENABLED
        else:
            self.provider_name = provider.strip().lower()
            self.enabled = self.provider_name not in OFF_VALUES

        self.manager = AIManager(self.provider_name) if self.enabled else None
        self.last_error: str | None = None

        #: Провайдеры, которые больше не вызываем в этом процессе.
        self._disabled: set[str] = set()
        if not CLAUDE_ENABLED:
            self._disabled.add("claude")
            log.info("Claude отключён (CLAUDE_ENABLED=off)")

        if self.enabled:
            log.info(
                "Seller AI включён (primary=%s, fallback=%s)",
                PRIMARY_MODEL, FALLBACK_MODEL,
            )
        else:
            log.info("Seller AI выключен — в .env указано AI_PROVIDER=off")

    def disable_provider(self, name: str, reason: str = "") -> None:
        """Sticky disable — следующий generate() не будет звонить в name."""
        name = name.lower()
        if name not in self._disabled:
            self._disabled.add(name)
            log.warning(
                "AI provider %s отключён до перезапуска: %s",
                name, reason or "unavailable",
            )

    def is_disabled(self, name: str) -> bool:
        return name.lower() in self._disabled

    def _provider_chain(self) -> list[str]:
        """PRIMARY → FALLBACK → остальные, без disabled и дублей."""
        ordered: list[str] = []
        for name in (PRIMARY_MODEL, FALLBACK_MODEL, self.provider_name, *FALLBACK_ORDER):
            n = (name or "").lower()
            if not n or n in OFF_VALUES:
                continue
            if n in self._disabled:
                continue
            if n not in ordered:
                ordered.append(n)
        return ordered

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> str | None:
        if not self.enabled:
            return None

        self.last_error = None
        chain = self._provider_chain()
        if not chain:
            log.error("Нет доступных AI-провайдеров (все disabled)")
            return None

        for name in chain:
            text = await self._try_provider(
                name, prompt, system=system, history=history,
            )
            if text:
                return text

        log.error("Все AI-провайдеры недоступны. Причина: %s", self.last_error)
        return None

    def _remember(self, message: str) -> None:
        if self.last_error is None:
            self.last_error = message

    def _should_disable(self, name: str, error: Exception) -> bool:
        if name == "claude" and not CLAUDE_ENABLED:
            return True
        text = str(error).lower()
        if isinstance(error, VseGPTError) and error.fatal:
            if any(m in text for m in _MODEL_UNAVAILABLE_MARKERS):
                return True
            # Тариф/модель — disable; ключ/баланс тоже (повторять бессмысленно)
            if "тариф" in text or "model" in text or "модел" in text:
                return True
        return any(m in text for m in _MODEL_UNAVAILABLE_MARKERS)

    async def _try_provider(
        self,
        name: str,
        prompt: str,
        *,
        system: str | None = None,
        history: list[dict] | None = None,
    ) -> str | None:
        if self.is_disabled(name):
            return None

        try:
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
            self._remember(str(error))
            log.error("🔴 Seller AI (%s): %s", name, error)
            if self._should_disable(name, error):
                self.disable_provider(name, str(error))

        except asyncio.TimeoutError:
            self._remember(f"{name}: таймаут {AI_TIMEOUT} сек")
            log.warning("AI %s: таймаут %d сек", name, AI_TIMEOUT)

        except ValueError as error:
            self._remember(f"{name}: {error}")
            log.warning("AI %s не настроен: %s", name, error)

        except Exception as error:
            self._remember(f"{name}: {type(error).__name__}: {error}")
            log.exception("AI %s: неожиданная ошибка", name)
            if self._should_disable(name, error):
                self.disable_provider(name, str(error))

        return None
