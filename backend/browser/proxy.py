"""
browser/proxy.py — HTTP proxy для Playwright / BrowserProvider.

Chromium НЕ поддерживает authenticated SOCKS5.
SOCKS5 для WBEngine живёт отдельно (WB_PROXY / ProxyPool).
"""

from __future__ import annotations

import logging
from urllib.parse import quote, unquote, urlsplit

log = logging.getLogger("selleros.browser.proxy")

_CHROMIUM_SOCKS5_WARN = "Chromium does not support authenticated SOCKS5 proxy"


def redact_proxy_url(url: str | None, *, scheme: str | None = None) -> str:
    """http://user:secret@host:port → http://user:***@host:port"""
    if not url:
        return ""
    try:
        parsed = parse_proxy_url(url)
        if not parsed:
            return "<proxy>"
        user = parsed.get("username") or ""
        sch = scheme or parsed.get("scheme") or "http"
        auth = f"{user}:***@" if user else ""
        return f"{sch}://{auth}{parsed['host']}:{parsed['port']}"
    except Exception:
        return "<proxy>"


def parse_proxy_url(proxy_url: str | None) -> dict | None:
    """
    Разобрать proxy URL на части (host/port/user/password/scheme).

    Не подменяет socks5→http. Для Playwright используйте
    generate_playwright_proxy() — он отклонит socks5.
    """
    if not proxy_url or not str(proxy_url).strip():
        return None
    raw = proxy_url.strip().strip('"').strip("'")

    parts = urlsplit(raw)
    host = parts.hostname
    port = parts.port
    scheme = (parts.scheme or "http").lower()
    user = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None

    if not host:
        if "://" in raw:
            scheme, rest = raw.split("://", 1)
            scheme = scheme.lower()
        else:
            scheme, rest = "http", raw
        if "@" in rest:
            creds, hostport = rest.rsplit("@", 1)
            if ":" in creds:
                user_enc, pass_enc = creds.split(":", 1)
                user = unquote(user_enc)
                password = unquote(pass_enc)
            else:
                user = unquote(creds)
                password = None
        else:
            hostport = rest
        if ":" in hostport:
            host, port_s = hostport.rsplit(":", 1)
            try:
                port = int(port_s)
            except ValueError:
                port = None
        else:
            host = hostport
            port = None

    if not host:
        return None
    if port is None:
        port = 8080

    return {
        "scheme": scheme,
        "host": host,
        "port": int(port),
        "username": user,
        "password": password,
    }


def apply_proxy_username_mode(
    username: str | None,
    *,
    mode: str = "as_is",
    session_id: str = "1",
    session_time: str = "60",
) -> str | None:
    """sticky / base / as_is для residential session username."""
    if not username:
        return username
    mode = (mode or "as_is").strip().lower()
    base = username.split("-session-")[0]
    if mode in ("base", "rotating", "rotate"):
        return base
    if mode in ("sticky", "session"):
        return f"{base}-session-{session_id}-time-{session_time}"
    return username


def generate_playwright_proxy(
    proxy_url: str | None,
    *,
    mode: str | None = None,
    session_id: str = "1",
    session_time: str = "60",
) -> dict | None:
    """
    HTTP-only proxy dict для chromium.launch(proxy=...).

    Returns:
      {"server": "http://host:port", "username": "...", "password": "..."}
      или None если URL пуст / socks5 (с warning).
    """
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return None

    scheme = (parsed.get("scheme") or "http").lower()
    if scheme.startswith("socks"):
        log.warning(_CHROMIUM_SOCKS5_WARN)
        print(f"WARNING: {_CHROMIUM_SOCKS5_WARN}")
        return None

    username = apply_proxy_username_mode(
        parsed.get("username"),
        mode=mode or "as_is",
        session_id=session_id,
        session_time=session_time,
    )
    cfg: dict = {
        "server": f"http://{parsed['host']}:{parsed['port']}",
    }
    if username:
        cfg["username"] = username
    if parsed.get("password") is not None:
        cfg["password"] = parsed["password"]
    return cfg


# Обратная совместимость для fetcher / старых импортов
def playwright_proxy_config(
    proxy_url: str | None,
    *,
    mode: str | None = None,
    session_id: str = "1",
    session_time: str = "60",
    scheme: str | None = None,  # ignored — BrowserProvider = HTTP only
) -> dict | None:
    if scheme and str(scheme).lower().startswith("socks"):
        log.warning(_CHROMIUM_SOCKS5_WARN)
        print(f"WARNING: {_CHROMIUM_SOCKS5_WARN}")
        return None
    return generate_playwright_proxy(
        proxy_url,
        mode=mode,
        session_id=session_id,
        session_time=session_time,
    )


def playwright_launch_args(*, disable_http2: bool = True) -> list[str]:
    args: list[str] = []
    if disable_http2:
        args.append("--disable-http2")
    return args


def encode_proxy_password(password: str) -> str:
    return quote(password or "", safe="")


def to_requests_proxy_url(
    proxy_url: str | None,
    *,
    mode: str | None = None,
    scheme: str | None = None,
) -> str | None:
    """
    URL для requests/curl.
    scheme=http → http://...
    scheme=socks5|socks5h → socks5h://...
    Если scheme не задан — берём из URL (socks* → socks5h).
    """
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return None

    username = apply_proxy_username_mode(parsed.get("username"), mode=mode or "as_is")
    user = quote(username or "", safe="")
    password = quote(parsed.get("password") or "", safe="")

    if scheme:
        sch = scheme.lower()
    else:
        sch = (parsed.get("scheme") or "http").lower()

    if sch in ("socks5", "socks", "socks5h"):
        sch = "socks5h"
    elif sch in ("http", "https"):
        sch = "http"
    else:
        sch = "http"

    return f"{sch}://{user}:{password}@{parsed['host']}:{parsed['port']}"


def normalize_wb_proxy_url(proxy_url: str, *, scheme: str = "socks5") -> str:
    """
    Нормализация URL для WBEngine ProxyPool.
    socks5 → socks5h://user:pass@host:port
    http  → http://user:pass@host:port
    """
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return proxy_url.strip()
    username = parsed.get("username") or ""
    password = parsed.get("password") or ""
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    auth = f"{user}:{pwd}@" if (username or password) else ""
    sch = (scheme or "socks5").strip().lower()
    if sch in ("socks5", "socks", "socks5h"):
        out_scheme = "socks5h"
    else:
        out_scheme = "http"
    return f"{out_scheme}://{auth}{parsed['host']}:{parsed['port']}"
