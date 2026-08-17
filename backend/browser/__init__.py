"""
browser — публичный Chromium-путь получения карточки WB.

Цепочка: ProductCache → BrowserProxyPool → Playwright → serialize.
BrowserProvider: только HTTP proxy.
"""

from .cache import CacheStatus, PublicProductCache
from .proxy import (
    generate_playwright_proxy,
    playwright_launch_args,
    playwright_proxy_config,
    redact_proxy_url,
)
from .proxy_pool import BrowserProxyPool
from .reviews_cache import PublicReviewsCache
from .serialize import product_from_public_dict, product_to_public_dict
from .socks_bridge import SocksHttpBridge, start_bridge_from_config, stop_bridge

__all__ = [
    "BrowserProxyPool",
    "CacheStatus",
    "PublicProductCache",
    "PublicReviewsCache",
    "SocksHttpBridge",
    "product_from_public_dict",
    "product_to_public_dict",
    "generate_playwright_proxy",
    "playwright_launch_args",
    "playwright_proxy_config",
    "redact_proxy_url",
    "start_bridge_from_config",
    "stop_bridge",
]
