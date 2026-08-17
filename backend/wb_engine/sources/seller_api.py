"""
seller_api.py — заготовка под официальный Seller API Wildberries.

Реализации пока нет — только каркас. Когда появится ключ,
подключение сведётся к одной строке в bot.py:

    engine.register(SellerAPISource(api_key=...), priority=0)

Пока ключа нет, is_available() возвращает False, и WBEngine
даже не пытается его дёргать — сразу переходит к следующему
источнику по приоритету.

Credential check (onboarding): GET common-api.wildberries.ru/ping
with Authorization header — same Seller API surface, no second client.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from backend.wb.cdn_provider import WBProduct
from backend.wb_engine.source import DataSource

log = logging.getLogger("selleros.wb.seller_api")

#: Official WB Seller «common» ping — validates token without fetching products.
WB_SELLER_PING_URL = "https://common-api.wildberries.ru/ping"


async def check_seller_api_key(
    api_key: str,
    *,
    timeout: float = 15.0,
    client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """
    Validate a WB seller API token via /ping.

    Returns True on HTTP 200. Never logs the key.
    ``client`` may be injected by tests (mock transport).
    """
    key = (api_key or "").strip()
    if not key:
        return False

    headers = {"Authorization": key}

    async def _do(http: httpx.AsyncClient) -> bool:
        try:
            resp = await http.get(WB_SELLER_PING_URL, headers=headers)
        except httpx.HTTPError as exc:
            log.info("WB seller ping failed: %s", type(exc).__name__)
            return False
        if resp.status_code == 200:
            return True
        if resp.status_code in (401, 403):
            return False
        log.info("WB seller ping unexpected status=%s", resp.status_code)
        return False

    if client is not None:
        return await _do(client)

    async with httpx.AsyncClient(timeout=timeout) as http:
        return await _do(http)


class SellerAPISource(DataSource):

    name = "seller_api"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    async def is_available(self) -> bool:
        return bool(self.api_key)

    async def fetch(self, article: int) -> WBProduct | None:
        # TODO: реализовать после получения ключа официального Seller API.
        # Это единственное место, которое придётся дописать —
        # WBEngine, кэш, cooldown и остальные источники трогать не нужно.
        raise NotImplementedError("Seller API ещё не подключён")

    async def check_credentials(self) -> bool:
        """Ping with this source's key (onboarding / health)."""
        return await check_seller_api_key(self.api_key or "")
