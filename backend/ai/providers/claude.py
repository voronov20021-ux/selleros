"""
ClaudeProvider — Claude через VseGPT.

Заглушки больше нет: это рабочий провайдер.
Вся низкоуровневая работа (async, таймаут, ретраи, разбор ошибок)
живёт в VseGPTProvider — здесь только выбор модели.
"""

from backend.ai.providers.vsegpt import VseGPTProvider
from backend.config import CLAUDE_MODEL


class ClaudeProvider(VseGPTProvider):

    provider_label = "Claude"

    def __init__(self, model: str | None = None):
        super().__init__(model=model or CLAUDE_MODEL)
