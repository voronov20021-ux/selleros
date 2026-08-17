"""
browser/proxy_pool.py — HTTP proxy pool для BrowserProvider / Playwright.

Только HTTP. SOCKS5 сюда не кладём (Chromium не умеет SOCKS5+auth).

API:
  get_proxy() / get()
  mark_success(proxy)
  mark_failed(proxy)
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field

from backend.browser.proxy import parse_proxy_url, redact_proxy_url

log = logging.getLogger("selleros.browser.proxy_pool")

FAIL_THRESHOLD = 3
COOLDOWN_SEC = 10 * 60  # 10 минут


@dataclass
class _ProxyHealth:
    url: str
    score: int = 0
    fail_streak: int = 0
    cooldown_until: float = 0.0
    total_success: int = 0
    total_fail: int = 0

    def is_available(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now >= self.cooldown_until


class BrowserProxyPool:
    """
    Random rotation + health tracking для HTTP browser proxies.
    """

    def __init__(self, proxies: list[str] | None = None):
        self._items: dict[str, _ProxyHealth] = {}
        for raw in proxies or []:
            self._add(raw)
        if self._items:
            log.info("BrowserProxyPool: %d HTTP proxy", len(self._items))
        else:
            log.info("BrowserProxyPool: empty (browser без proxy)")

    def _add(self, raw: str) -> None:
        url = (raw or "").strip().strip('"').strip("'")
        if not url:
            return
        parsed = parse_proxy_url(url)
        if not parsed:
            log.warning("BrowserProxyPool: skip unparseable proxy")
            return
        scheme = (parsed.get("scheme") or "http").lower()
        if scheme.startswith("socks"):
            log.warning(
                "BrowserProxyPool: skip SOCKS5 (%s) — "
                "Chromium does not support authenticated SOCKS5 proxy",
                redact_proxy_url(url),
            )
            return
        # Канонический HTTP URL (сохраняем credentials из raw через rebuild)
        from urllib.parse import quote

        user = parsed.get("username") or ""
        password = parsed.get("password") or ""
        auth = ""
        if user or password:
            auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
        canon = f"http://{auth}{parsed['host']}:{parsed['port']}"
        if canon not in self._items:
            self._items[canon] = _ProxyHealth(url=canon)

    @classmethod
    def from_urls(cls, urls: list[str] | str | None) -> "BrowserProxyPool":
        if urls is None:
            return cls([])
        if isinstance(urls, str):
            parts = _split_proxy_list(urls)
        else:
            parts = list(urls)
        return cls(parts)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    @property
    def proxies(self) -> list[str]:
        return list(self._items.keys())

    def available(self) -> list[str]:
        now = time.time()
        return [u for u, h in self._items.items() if h.is_available(now)]

    def get_proxy(self) -> str | None:
        """Выбрать HTTP proxy (random среди доступных)."""
        avail = self.available()
        if not avail:
            if self._items:
                log.warning("BrowserProxyPool: all proxies in cooldown")
            return None
        # Weighted: выше score — чуть чаще (floor at 1)
        weights = []
        for u in avail:
            score = self._items[u].score
            weights.append(max(1, 3 + score))
        chosen = random.choices(avail, weights=weights, k=1)[0]
        log.info("BrowserProxyPool: selected %s", redact_proxy_url(chosen))
        return chosen

    # alias
    def get(self) -> str | None:
        return self.get_proxy()

    def mark_success(self, proxy: str | None) -> None:
        if not proxy or proxy not in self._items:
            return
        h = self._items[proxy]
        h.score += 1
        h.fail_streak = 0
        h.total_success += 1
        h.cooldown_until = 0.0
        log.debug(
            "BrowserProxyPool: success %s score=%s",
            redact_proxy_url(proxy), h.score,
        )

    def mark_failed(self, proxy: str | None) -> None:
        if not proxy or proxy not in self._items:
            return
        h = self._items[proxy]
        h.score -= 1
        h.fail_streak += 1
        h.total_fail += 1
        if h.fail_streak >= FAIL_THRESHOLD:
            h.cooldown_until = time.time() + COOLDOWN_SEC
            h.fail_streak = 0
            log.warning(
                "BrowserProxyPool: cooldown %s for %ds (score=%s)",
                redact_proxy_url(proxy), COOLDOWN_SEC, h.score,
            )
        else:
            log.debug(
                "BrowserProxyPool: fail %s streak=%s score=%s",
                redact_proxy_url(proxy), h.fail_streak, h.score,
            )

    def status(self) -> list[dict]:
        now = time.time()
        out = []
        for u, h in self._items.items():
            out.append({
                "proxy": redact_proxy_url(u),
                "score": h.score,
                "fail_streak": h.fail_streak,
                "available": h.is_available(now),
                "cooldown_left": max(0, int(h.cooldown_until - now)),
                "total_success": h.total_success,
                "total_fail": h.total_fail,
            })
        return out


def _split_proxy_list(raw: str) -> list[str]:
    """Разбить BROWSER_PROXY_LIST по запятым / переводам строк."""
    text = (raw or "").replace("\r", "\n")
    parts: list[str] = []
    for chunk in text.split(","):
        for line in chunk.split("\n"):
            s = line.strip().strip('"').strip("'")
            if s:
                parts.append(s)
    return parts
