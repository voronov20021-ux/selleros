"""
Chromium fetch карточки WB (Playwright).

Опциональная зависимость: если playwright не установлен —
fetcher.is_available() == False, BrowserProvider пропускается.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from backend.browser.proxy import (
    generate_playwright_proxy,
    parse_proxy_url,
    playwright_launch_args,
    redact_proxy_url,
)
from backend.browser.system_chrome import (
    build_system_chrome_args,
    kill_process_tree,
    pick_free_tcp_port,
    resolve_system_chrome_path,
)
from backend.browser.proxy_pool import BrowserProxyPool
from backend.services.wb_reviews import Review, review_fingerprint
from backend.wb.cdn_provider import WBProduct, _sync_imt_root, extract_imt_id

log = logging.getLogger("selleros.browser.fetcher")

WB_ORIGIN = "https://www.wildberries.ru"
WALLET_DISCOUNT = 0.02

# Docker/Amvera: sandbox + tiny /dev/shm crash Chromium at launch.
_LINUX_CONTAINER_CHROMIUM_ARGS = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
)

_CHANNEL_RETRY_MARKERS = (
    "invalid channel",
    "unknown channel",
    "unsupported channel",
    'channel "chromium"',
    "channel 'chromium'",
    "browser to launch is not installed",
    "executable doesn't exist",
    "executable not found",
    "chromium revision is not downloaded",
    "looks like playwright was just installed",
)


def _commercial_debug_enabled() -> bool:
    return (os.environ.get("BROWSER_COMMERCIAL_DEBUG") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def format_commercial_debug(product: WBProduct, *, requested_nm_id: int) -> str:
    """Человекочитаемый дамп nm-verified commercial + sources."""
    prov = getattr(product, "field_provenance", None) or {}

    def _src(field: str) -> str:
        meta = prov.get(field) if isinstance(prov, dict) else None
        if not isinstance(meta, dict):
            return "None"
        return str(meta.get("source") or "None")

    verified_nm = None
    for field in ("price", "rating", "feedbacks"):
        meta = prov.get(field) if isinstance(prov, dict) else None
        if isinstance(meta, dict) and meta.get("verified") and meta.get("nm_id"):
            verified_nm = meta.get("nm_id")
            break
    lines = [
        "BROWSER COMMERCIAL DEBUG",
        f"nm_id={requested_nm_id}",
        f"price={getattr(product, 'price', None)} price_source={_src('price')}",
        f"old_price={getattr(product, 'old_price', None)} old_price_source={_src('old_price')}",
        f"discount={getattr(product, 'discount', None)} discount_source={_src('discount')}",
        f"rating={getattr(product, 'rating', None)} rating_source={_src('rating')}",
        f"review_count={getattr(product, 'feedbacks', None)} review_count_source={_src('feedbacks')}",
        f"verified_nm_id={verified_nm}",
    ]
    return "\n".join(lines)


def build_product_url(article: int) -> str:
    """Канонический URL карточки WB по nm/article."""
    return f"{WB_ORIGIN}/catalog/{int(article)}/detail.aspx"


class BrowserFetchError(Exception):
    """Ожидаемый сбой browser-пути (timeout / proxy / captcha / empty)."""


class BrowserFetcherProtocol(Protocol):
    async def fetch(self, article: int) -> tuple[WBProduct, list[Review]]:
        ...

    def is_available(self) -> bool:
        ...


class PlaywrightBrowserFetcher:
    """
    Chromium → карточка WB.
    Цепочка: ProductCache (снаружи) → ProxyPool → Playwright → serialize.
    """

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        proxy_pool: BrowserProxyPool | None = None,
        headless: bool = True,
        timeout_ms: int = 60000,
        proxy_mode: str = "sticky",
        disable_http2: bool = True,
        session_id: str = "1",
        session_time: str = "60",
        use_system_chrome: bool = True,
        chrome_path: str | None = None,
    ):
        self.proxy_url = (proxy_url or "").strip() or None
        self.proxy_pool = proxy_pool
        self.headless = headless
        self.timeout_ms = int(timeout_ms)
        self.proxy_mode = proxy_mode or "sticky"
        self.disable_http2 = bool(disable_http2)
        self.session_id = session_id or "1"
        self.session_time = session_time or "60"
        self.use_system_chrome = bool(use_system_chrome)
        self.chrome_path = (chrome_path or "").strip() or None
        self.last_proxy_url: str | None = None
        # диагностика навигации (для live smoke / логов)
        self.last_request_url: str | None = None
        self.last_final_url: str | None = None
        self.last_redirect_chain: list[str] = []
        self.last_product_page_detected: bool | None = None
        self.last_launch_kind: str | None = None
        self.last_http_status: int | None = None

    def is_available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def _pick_proxy_url(self) -> str | None:
        if self.proxy_pool:
            return self.proxy_pool.get_proxy()
        return self.proxy_url

    def _reset_nav_diagnostics(self, url: str) -> None:
        self.last_request_url = url
        self.last_final_url = None
        self.last_redirect_chain = []
        self.last_product_page_detected = None
        self.last_launch_kind = None
        self.last_http_status = None

    async def fetch(self, article: int) -> tuple[WBProduct, list[Review]]:
        article = int(article)
        log.info("Browser fetch started: article=%s", article)

        if not self.is_available():
            log.info(
                "Browser proxy configured: %s",
                "yes" if (self.proxy_pool or self.proxy_url) else "no",
            )
            log.info("Browser fetch failure")
            raise BrowserFetchError("playwright not installed")

        from playwright.async_api import async_playwright

        url = build_product_url(article)
        self._reset_nav_diagnostics(url)
        chosen = self._pick_proxy_url()
        self.last_proxy_url = chosen

        proxy_cfg = generate_playwright_proxy(
            chosen,
            mode=self.proxy_mode,
            session_id=self.session_id,
            session_time=self.session_time,
        )
        log.info("Browser proxy configured: %s", "yes" if proxy_cfg else "no")
        if chosen and proxy_cfg is None:
            if self.proxy_pool:
                self.proxy_pool.mark_failed(chosen)
            log.info("Browser fetch failure")
            raise BrowserFetchError(
                "Chromium does not support authenticated SOCKS5 proxy"
            )
        if chosen:
            log.info(
                "BrowserFetcher: article=%s proxy=%s mode=%s http2_off=%s url=%s",
                article,
                redact_proxy_url(chosen, scheme="http"),
                self.proxy_mode,
                self.disable_http2,
                url,
            )

        intercepted: dict[str, Any] = {
            "detail": None,
            "feedbacks": None,
            "feedbacks_url": None,
        }
        redirect_chain: list[str] = []
        chrome_exe = (
            resolve_system_chrome_path(self.chrome_path)
            if self.use_system_chrome
            else None
        )
        if self.use_system_chrome and not chrome_exe:
            log.warning(
                "BrowserFetcher: system Chrome not found, fallback to Playwright Chromium"
            )

        try:
            async with async_playwright() as p:
                cleanup = None
                if chrome_exe:
                    browser, page, cleanup = await self._open_system_chrome(
                        p, chrome_exe, proxy_cfg
                    )
                    self.last_launch_kind = "system_chrome"
                else:
                    browser, page = await self._open_playwright_chromium(p, proxy_cfg)
                    if not self.last_launch_kind:
                        self.last_launch_kind = "playwright_chromium"
                log.info(
                    "BrowserFetcher: article=%s launch=%s chrome=%s",
                    article,
                    self.last_launch_kind,
                    chrome_exe or "bundled",
                )
                try:
                    async def on_response(response):
                        try:
                            u = response.url
                            req = response.request
                            if req.is_navigation_request():
                                redirect_chain.append(f"{response.status} {u}")
                            if response.status != 200:
                                return
                            if _is_detail_api_url(u):
                                payload = await response.json()
                                _remember_detail(intercepted, payload, article)
                            if "feedbacks" in u and "wb.ru" in u:
                                intercepted["feedbacks"] = await response.json()
                                intercepted["feedbacks_url"] = u
                        except Exception:
                            return

                    page.on("response", on_response)
                    log.info("Browser navigation started")
                    resp = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                    nav_status = getattr(resp, "status", None) if resp is not None else None
                    self.last_http_status = nav_status if isinstance(nav_status, int) else None
                    log.info(
                        "Browser navigation result: HTTP status %s",
                        self.last_http_status
                        if self.last_http_status is not None
                        else "unknown",
                    )
                    if resp is not None and f"{resp.status} {resp.url}" not in redirect_chain:
                        redirect_chain.insert(0, f"{resp.status} {resp.url}")

                    await _wait_for_product_page(page, article, self.timeout_ms)

                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=min(10000, self.timeout_ms),
                        )
                    except Exception:
                        pass
                    await page.wait_for_timeout(800)

                    final_url = page.url or ""
                    title = (await page.title()) or ""
                    self.last_final_url = final_url
                    self.last_redirect_chain = list(redirect_chain)
                    product_ok = is_product_page(final_url, title, article)
                    diag = await _page_shell_diag(page, title, self.last_http_status)
                    if (not product_ok) or _title_looks_unhydrated(title):
                        log.warning(
                            "BrowserFetcher: article=%s page shell "
                            "status=%s title=%r html_len=%s empty=%s "
                            "challenge=%s homepage_title=%s FINAL_URL=%s",
                            article,
                            diag.get("status"),
                            diag.get("title"),
                            diag.get("html_len"),
                            diag.get("empty"),
                            diag.get("challenge"),
                            diag.get("homepage_title"),
                            final_url,
                        )
                    if (
                        not product_ok
                        and is_product_url(final_url, article)
                        and not _looks_error_page(title)
                    ):
                        # URL already /catalog/{nm}/detail — title may still be
                        # empty / storefront / "Loading" while SPA hydrates.
                        try:
                            await page.wait_for_timeout(3000)
                        except Exception:
                            pass
                        title = (await page.title()) or ""
                        final_url = page.url or final_url
                        self.last_final_url = final_url
                        product_ok = is_product_page(final_url, title, article)
                        if (
                            not product_ok
                            and is_product_url(final_url, article)
                            and not _looks_error_page(title)
                        ):
                            log.warning(
                                "BrowserFetcher: article=%s treating product "
                                "detail URL as product page (title mismatch) "
                                "status=%s title=%r html_len=%s empty=%s "
                                "challenge=%s",
                                article,
                                self.last_http_status,
                                (title or "")[:160],
                                diag.get("html_len"),
                                diag.get("empty"),
                                diag.get("challenge"),
                            )
                            product_ok = True
                    self.last_product_page_detected = product_ok

                    log.info(
                        "BrowserFetcher: article=%s FINAL_URL=%s "
                        "PRODUCT_PAGE_DETECTED=%s redirects=%s",
                        article,
                        final_url,
                        product_ok,
                        redirect_chain[:8],
                    )

                    if _looks_blocked(title, final_url):
                        raise BrowserFetchError("captcha/blocked page")
                    if _looks_error_page(title):
                        raise BrowserFetchError("wb error page")
                    if not product_ok:
                        raise BrowserFetchError(
                            f"homepage/not product page FINAL_URL={final_url}"
                        )

                    dom = await page.evaluate(_DOM_EXTRACT_JS)
                    product = _build_product(article, intercepted, dom)
                    reviews = _build_reviews(article, intercepted, dom, product)
                    if _is_homepage_title(product.title or ""):
                        raise BrowserFetchError("homepage title parsed as product")
                    if not product.title and not product.imt_id and not product.price:
                        raise BrowserFetchError("required product fields missing")
                    product.source = "browser"
                    _sync_imt_root(product)
                    if _commercial_debug_enabled():
                        msg = format_commercial_debug(product, requested_nm_id=article)
                        log.info("%s", msg)
                        print(msg, flush=True)
                    if self.proxy_pool and chosen:
                        self.proxy_pool.mark_success(chosen)
                    log.info("Browser fetch success")
                    return product, reviews
                finally:
                    if cleanup is not None:
                        await cleanup()
                    else:
                        try:
                            await browser.close()
                        except Exception:
                            pass
        except BrowserFetchError:
            if self.proxy_pool and chosen:
                self.proxy_pool.mark_failed(chosen)
            log.info("Browser fetch failure")
            raise
        except Exception as exc:
            if self.proxy_pool and chosen:
                self.proxy_pool.mark_failed(chosen)
            msg = str(exc)
            if chosen:
                msg = msg.replace(chosen, redact_proxy_url(chosen))
                parsed = parse_proxy_url(chosen)
                if parsed and parsed.get("password"):
                    msg = msg.replace(parsed["password"], "***")
            log.info("Browser fetch failure")
            raise BrowserFetchError(msg) from exc

    async def _open_system_chrome(
        self,
        p: Any,
        chrome_exe: str,
        proxy_cfg: dict | None,
    ) -> tuple[Any, Any, Any]:
        """chrome.exe + connect_over_cdp. Proven vs WB 498. No custom UA/locale."""
        proxy_server = None
        if proxy_cfg:
            proxy_server = proxy_cfg.get("server")
            if proxy_cfg.get("username") or proxy_cfg.get("password"):
                log.warning(
                    "BrowserFetcher: system Chrome ignores HTTP proxy credentials; "
                    "use SOCKS bridge (http://127.0.0.1:8080)"
                )
        user_data_dir = tempfile.mkdtemp(prefix="selleros_chrome_")
        cdp_port = pick_free_tcp_port()
        args = build_system_chrome_args(
            chrome_exe,
            cdp_port=cdp_port,
            user_data_dir=user_data_dir,
            proxy_server=proxy_server,
            headless=self.headless,
        )
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        async def cleanup() -> None:
            try:
                await browser.close()
            except Exception:
                pass
            finally:
                kill_process_tree(proc.pid)
                shutil.rmtree(user_data_dir, ignore_errors=True)

        try:
            await _wait_tcp_port(cdp_port, timeout_s=15)
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except Exception:
            kill_process_tree(proc.pid)
            shutil.rmtree(user_data_dir, ignore_errors=True)
            raise
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        return browser, page, cleanup

    async def _open_playwright_chromium(
        self,
        p: Any,
        proxy_cfg: dict | None,
    ) -> tuple[Any, Any]:
        """Bundled Chromium fallback if system Chrome is missing.

        Linux: full Chrome for Testing (`channel=chromium`), not chrome-headless-shell,
        plus container args (--no-sandbox, --disable-dev-shm-usage, --disable-gpu).
        Windows path is unchanged (no channel / no Linux flags).
        """
        launch_kwargs = build_playwright_chromium_launch_kwargs(
            headless=self.headless,
            disable_http2=self.disable_http2,
            proxy_cfg=proxy_cfg,
        )
        log.info(
            "BrowserFetcher: playwright launch %s",
            _launch_kwargs_for_log(launch_kwargs),
        )
        used_kwargs = launch_kwargs
        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as exc:
            log_chromium_launch_failure(exc, launch_kwargs)
            retry = launch_kwargs_without_channel(launch_kwargs)
            if retry is not None and should_retry_chromium_launch_without_channel(exc):
                log.warning(
                    "BrowserFetcher: retry chromium.launch without channel=%s",
                    launch_kwargs.get("channel"),
                )
                print(
                    "WARNING: BrowserFetcher: retry chromium.launch without channel",
                    flush=True,
                )
                try:
                    browser = await p.chromium.launch(**retry)
                except Exception as exc2:
                    log_chromium_launch_failure(exc2, retry)
                    raise
                used_kwargs = retry
            else:
                raise
        self.last_launch_kind = (
            "playwright_chromium_full"
            if used_kwargs.get("channel") == "chromium"
            else "playwright_chromium"
        )
        context = await browser.new_context(
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        return browser, page


def playwright_pkg_version() -> str:
    try:
        import playwright

        return str(getattr(playwright, "__version__", "") or "")
    except Exception:
        return ""


def playwright_version_tuple(raw: str | None = None) -> tuple[int, int]:
    text = (raw if raw is not None else playwright_pkg_version()) or ""
    nums: list[int] = []
    for part in re.split(r"\D+", text.strip()):
        if part.isdigit():
            nums.append(int(part))
        if len(nums) >= 2:
            break
    while len(nums) < 2:
        nums.append(0)
    return nums[0], nums[1]


def playwright_supports_chromium_channel(version: str | None = None) -> bool:
    """Playwright 1.49+ : channel='chromium' = full Chrome, not headless-shell."""
    major, minor = playwright_version_tuple(version)
    return (major, minor) >= (1, 49)


def build_playwright_chromium_launch_kwargs(
    *,
    headless: bool,
    disable_http2: bool,
    proxy_cfg: dict | None = None,
    platform: str | None = None,
    playwright_version: str | None = None,
) -> dict[str, Any]:
    """Launch kwargs for bundled Playwright Chromium (not system Chrome / CDP).

    Linux containers: full Chromium + sandbox/shm flags. Windows unchanged.
    """
    plat = (platform or sys.platform or "").lower()
    is_linux = plat.startswith("linux")
    args = list(playwright_launch_args(disable_http2=disable_http2))
    if is_linux:
        for flag in _LINUX_CONTAINER_CHROMIUM_ARGS:
            if flag not in args:
                args.append(flag)
    kwargs: dict[str, Any] = {"headless": bool(headless)}
    if args:
        kwargs["args"] = args
    if is_linux:
        kwargs["chromium_sandbox"] = False
        if headless and playwright_supports_chromium_channel(playwright_version):
            kwargs["channel"] = "chromium"
    if proxy_cfg:
        kwargs["proxy"] = proxy_cfg
    return kwargs


def should_retry_chromium_launch_without_channel(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _CHANNEL_RETRY_MARKERS)


def launch_kwargs_without_channel(
    launch_kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    if not launch_kwargs.get("channel"):
        return None
    retry = dict(launch_kwargs)
    retry.pop("channel", None)
    return retry


def _launch_kwargs_for_log(launch_kwargs: dict[str, Any]) -> str:
    return (
        f"headless={launch_kwargs.get('headless')} "
        f"channel={launch_kwargs.get('channel') or 'default'} "
        f"chromium_sandbox={launch_kwargs.get('chromium_sandbox', 'default')} "
        f"args={launch_kwargs.get('args') or []} "
        f"proxy={'yes' if launch_kwargs.get('proxy') else 'no'}"
    )


def _redact_proxy_in_text(text: str, proxy_cfg: dict | None) -> str:
    if not text or not proxy_cfg:
        return text
    out = text
    pwd = proxy_cfg.get("password")
    if pwd:
        out = out.replace(str(pwd), "***")
    user = proxy_cfg.get("username")
    if user and len(str(user)) >= 3:
        out = out.replace(str(user), "***")
    return out


def diagnose_chromium_launch_error(exc_text: str) -> str:
    """Hints for Amvera logs when chromium.launch dies immediately."""
    text = exc_text or ""
    low = text.lower()
    hints: list[str] = []
    if "headless-shell" in low or "headless_shell" in low:
        hints.append("binary=chrome-headless-shell")
    libs = re.findall(r"lib[\w.+-]+\.so[\w.]*", text)
    if libs:
        uniq = list(dict.fromkeys(libs))
        hints.append("missing libs in error: " + ", ".join(uniq[:12]))
    ldd_missing = _ldd_not_found_from_launch_log(text)
    if ldd_missing:
        hints.append("ldd not found: " + "; ".join(ldd_missing[:12]))
    if (
        ldd_missing
        or libs
        or "shared librar" in low
        or "cannot open shared object" in low
        or "error while loading shared libraries" in low
    ):
        hints.append("set PLAYWRIGHT_INSTALL_DEPS=1 and redeploy")
    if (
        "target page" in low
        or "browser has been closed" in low
        or "has been closed" in low
    ):
        hints.append("process died at launch (sandbox/shm/missing libs)")
    return " | ".join(hints)


def _ldd_not_found_from_launch_log(exc_text: str) -> list[str]:
    if not sys.platform.startswith("linux"):
        return []
    match = re.search(r"<launching>\s+(\S+)", exc_text or "")
    if not match:
        return []
    exe = match.group(1)
    if not os.path.isabs(exe) or not os.path.isfile(exe):
        return []
    try:
        proc = subprocess.run(
            ["ldd", exe],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    missing: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if "not found" in line.lower():
            missing.append(" ".join(line.split()))
    return missing


def log_chromium_launch_failure(
    exc: BaseException,
    launch_kwargs: dict[str, Any],
) -> None:
    proxy_cfg = (
        launch_kwargs.get("proxy")
        if isinstance(launch_kwargs.get("proxy"), dict)
        else None
    )
    raw = _redact_proxy_in_text(str(exc), proxy_cfg)
    diagnosis = diagnose_chromium_launch_error(raw)
    log.warning(
        "BrowserFetcher: chromium.launch failed (%s): %s",
        _launch_kwargs_for_log(launch_kwargs),
        raw[:8000],
    )
    print(
        f"WARNING: BrowserFetcher: chromium.launch failed: {raw[:4000]}",
        flush=True,
    )
    if diagnosis:
        log.warning("BrowserFetcher: launch diagnosis: %s", diagnosis)
        print(f"WARNING: BrowserFetcher: {diagnosis}", flush=True)


async def _wait_tcp_port(port: int, timeout_s: float = 15) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.3)
            sock.close()
            return
        except OSError:
            await asyncio.sleep(0.15)
    raise BrowserFetchError("system Chrome CDP did not start")


def _is_detail_api_url(url: str) -> bool:
    u = (url or "").lower()
    if "detail" not in u:
        return False
    return any(
        host in u
        for host in (
            "card.wb.ru",
            "u-card.wb.ru",
            "catalog.wb.ru",
            "wildberries.ru/__internal",
        )
    )


def is_product_url(url: str, article: int) -> bool:
    """True если path — карточка /catalog/{nm}/detail…"""
    try:
        path = (urlsplit(url or "").path or "").lower()
    except Exception:
        path = (url or "").lower()
    needle = f"/catalog/{int(article)}/"
    return needle in path and "detail" in path


def _normalize_title(text: str) -> str:
    return (
        (text or "")
        .lower()
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\xa0", " ")
        .strip()
    )


def _is_homepage_title(title: str) -> bool:
    """Storefront chrome, not a product title.

    Real card titles often end with «в интернет-магазине Wildberries» —
    that suffix is not the homepage.
    """
    t = _normalize_title(title)
    if not t:
        return False
    if "широкий ассортимент товаров" in t or "скидки каждый день" in t:
        return True
    if t.startswith("интернет-магазин wildberries"):
        return True
    if t in ("wildberries", "wildberries.com"):
        return True
    # "Wildberries — интернет-магазин" with no product name / «купить»
    if t.startswith("wildberries") and "интернет-магазин" in t and "купить" not in t:
        return True
    return False


def _looks_error_page(title: str) -> bool:
    t = _normalize_title(title)
    return "что-то не так" in t or "something went wrong" in t


def is_product_page(url: str, title: str, article: int) -> bool:
    """Карточка товара vs homepage / error shell.

    If the browser already landed on /catalog/{nm}/detail…, the URL is the
    source of truth. Empty or storefront document.title is common on the SPA
    shell / anti-bot interstitial — callers extract DOM and fail on missing
    fields, rather than classifying a product URL as homepage.
    """
    if not is_product_url(url, article):
        return False
    if _looks_error_page(title):
        return False
    t = _normalize_title(title)
    if t.startswith("loading "):
        return False
    return True


async def _wait_for_product_page(page: Any, article: int, timeout_ms: int) -> None:
    """Минимальное ожидание маркеров карточки после goto (SPA)."""
    wait_ms = min(25000, max(5000, int(timeout_ms) // 2))
    art = int(article)
    js = f"""
() => {{
  const path = (location.pathname || '').toLowerCase();
  const title = (document.title || '');
  const t = title.toLowerCase().replace(/\\u2011/g, '-');
  const urlOk = path.includes('/catalog/{art}/') && path.includes('detail');
  if (!urlOk) return false;
  if (t.includes('широкий ассортимент товаров') || t.includes('скидки каждый день'))
    return false;
  if (t.startsWith('интернет-магазин wildberries'))
    return false;
  if (t.includes('что-то не так')) return false;
  if (t.startsWith('loading ')) return false;
  const hasShell = !!document.querySelector(
    '[class*="product-page"], [class*="productCard"], [class*="product-detail"]'
  );
  const titleOk = title.includes('{art}') || (title.length > 12 && t.includes('купить'));
  return hasShell || titleOk;
}}
"""
    try:
        await page.wait_for_function(js, timeout=wait_ms)
    except Exception:
        # ниже is_product_page / required fields решат fail
        pass
    # доп. маркеры цены/заголовка — best effort
    for sel in (
        "h1",
        "[class*='product-page']",
        "[class*='priceBlock']",
        "[class*='price-block']",
    ):
        try:
            await page.wait_for_selector(sel, timeout=1500)
            break
        except Exception:
            continue


def _title_looks_unhydrated(title: str) -> bool:
    t = _normalize_title(title)
    if not t or t.startswith("loading "):
        return True
    return _is_homepage_title(title)


async def _page_shell_diag(page: Any, title: str, status: int | None) -> dict[str, Any]:
    """HTTP status / title / html size / challenge flags. No proxy secrets."""
    html_len = 0
    blob = (title or "").lower()
    try:
        info = await page.evaluate(
            """() => {
              const html = (document.documentElement && document.documentElement.outerHTML) || '';
              const text = ((document.body && document.body.innerText) || '').slice(0, 800);
              return {html_len: html.length, text: text};
            }"""
        )
        if isinstance(info, dict):
            try:
                html_len = int(info.get("html_len") or 0)
            except (TypeError, ValueError):
                html_len = 0
            blob = f"{title} {info.get('text') or ''}".lower()
    except Exception:
        pass
    challenge_markers = (
        "captcha",
        "challenge",
        "access denied",
        "доступ ограничен",
        "cloudflare",
        "just a moment",
    )
    return {
        "status": status,
        "title": (title or "")[:160],
        "html_len": html_len,
        "empty": html_len < 500,
        "challenge": any(m in blob for m in challenge_markers),
        "homepage_title": _is_homepage_title(title),
    }


def _looks_blocked(title: str, url: str) -> bool:
    blob = f"{title} {url}".lower()
    markers = ("captcha", "access denied", "доступ ограничен", "challenge")
    return any(m in blob for m in markers)


_DOM_EXTRACT_JS = """
() => {
  const text = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.textContent || '').trim() : null;
  };
  const meta = (name) => {
    const el = document.querySelector(`meta[property="${name}"], meta[name="${name}"]`);
    return el ? el.getAttribute('content') : null;
  };
  const norm = (s) => (s || '').toLowerCase().replace(/\\u2011/g, '-');
  const isHomeTitle = (s) => {
    const t = norm(s);
    if (!t) return false;
    if (t.includes('широкий ассортимент товаров') || t.includes('скидки каждый день'))
      return true;
    if (t.startsWith('интернет-магазин wildberries')) return true;
    if (t === 'wildberries' || t === 'wildberries.com') return true;
    return t.startsWith('wildberries') && t.includes('интернет-магазин') && !t.includes('купить');
  };
  const h1 = text('h1');
  const og = meta('og:title');
  const docTitle = (document.title || '').trim();
  let title = null;
  for (const cand of [h1, docTitle, og]) {
    if (cand && !isHomeTitle(cand) && !norm(cand).includes('что-то не так')) {
      title = cand;
      break;
    }
  }
  const imgs = Array.from(document.querySelectorAll('img'))
    .map(i => i.src)
    .filter(s => s && (s.includes('wbbasket') || s.includes('wbcontent') || s.includes('/images/')))
    .slice(0, 20);
  const reviewNodes = Array.from(document.querySelectorAll(
    '[class*="feedback"], [class*="comment"], [data-link*="feedback"]'
  )).slice(0, 60);
  const reviews = [];
  for (const n of reviewNodes) {
    const t = (n.innerText || '').trim();
    if (t.length >= 20 && t.length < 2000) reviews.push(t.slice(0, 1500));
  }
  const chars = {};
  const rows = Array.from(document.querySelectorAll(
    '[class*="charact"] tr, [class*="params"] tr, [class*="product-params"] li, table tr'
  )).slice(0, 40);
  for (const row of rows) {
    const cells = row.querySelectorAll('td, th, span, div');
    if (cells.length >= 2) {
      const k = (cells[0].textContent || '').trim();
      const v = (cells[1].textContent || '').trim();
      if (k && v && k.length < 80 && v.length < 200) chars[k] = v;
    }
  }
  let next = null;
  const nd = document.querySelector('#__NEXT_DATA__');
  if (nd && nd.textContent) {
    try { next = JSON.parse(nd.textContent); } catch (e) { next = null; }
  }
  const price_text = text('[class*="priceBlock"]')
    || text('[class*="price-block"]')
    || text('[class*="product-page"] [class*="price"]')
    || text('[class*="price"]');
  // рейтинг+оценки на карточке: «4,4·2 983 оценки»
  const review_line = text('a[href*="feedbacks"]')
    || text('[class*="product-review"]')
    || text('[class*="product-page__review"]')
    || text('[class*="j-product-review"]');
  const rating_text = review_line
    || text('[class*="product-page__rating"]')
    || text('[class*="reviewRating"]');
  return {
    title,
    description: meta('og:description') || text('[class*="description"]'),
    price_text,
    rating_text,
    review_line,
    photos: imgs,
    review_texts: reviews,
    characteristics: chars,
    next_data: next,
    doc_title: docTitle,
    path: location.pathname,
  };
}
"""


def _remember_detail(
    intercepted: dict[str, Any],
    payload: Any,
    article: int,
) -> None:
    """
    Сохранить detail JSON только если есть nm-match.

    Не затираем уже хороший nm-matched product более слабым batch-ответом.
    """
    from backend.wb.provenance import raw_nm_id

    unwrapped = _unwrap_detail(payload, article=article)
    if unwrapped is None:
        return
    nm = raw_nm_id(unwrapped)
    if nm is None or int(nm) != int(article):
        return

    prev = intercepted.get("detail")
    prev_u = _unwrap_detail(prev, article=article) if prev is not None else None
    if prev_u is not None:
        prev_price = _price_bundle_from_detail(prev_u)
        new_price = _price_bundle_from_detail(unwrapped)
        # уже есть цена — не откатываемся на payload без цены
        if prev_price.get("price") is not None and new_price.get("price") is None:
            return
        prev_rating = prev_u.get("nmReviewRating") or prev_u.get("reviewRating")
        new_rating = unwrapped.get("nmReviewRating") or unwrapped.get("reviewRating")
        if prev_price.get("price") is not None and prev_rating is not None:
            if new_price.get("price") is None or new_rating is None:
                return

    intercepted["detail"] = payload
    intercepted["detail_nm_id"] = int(nm)


def _build_product(
    article: int,
    intercepted: dict[str, Any],
    dom: dict[str, Any] | None,
) -> WBProduct:
    from backend.wb.provenance import (
        looks_sitewide_description,
        note_field,
        raw_nm_id,
    )

    article = int(article)
    product = WBProduct(article=article)
    detail = _unwrap_detail(intercepted.get("detail"), article=article)
    extracted_nm: int | None = None
    if isinstance(detail, dict):
        extracted_nm = raw_nm_id(detail)
        if extracted_nm is not None and int(extracted_nm) != article:
            # чужой nm — не трогаем commercial / identity из detail
            detail = None
            extracted_nm = None

    if isinstance(detail, dict) and extracted_nm == article:
        product.title = detail.get("name") or product.title
        product.brand = detail.get("brand") or product.brand
        product.brand_id = detail.get("brandId") or product.brand_id
        product.supplier = detail.get("supplier") or product.supplier
        product.supplier_id = detail.get("supplierId") or product.supplier_id
        imt = extract_imt_id(detail)
        if imt is not None:
            product.imt_id = product.imt_id or imt
            product.root_id = product.root_id or imt
        root = detail.get("root")
        if root is not None:
            try:
                product.root_id = product.root_id or int(root)
                product.imt_id = product.imt_id or int(root)
            except (TypeError, ValueError):
                pass
        pics = detail.get("pics")
        if isinstance(pics, int) and pics > 0:
            product.photo_count = pics
            note_field(
                product, "photo_count", product.photo_count,
                "browser.detail.pics", nm_id=article, verified=True, scope="nm",
            )
        _apply_detail_commercial(product, detail, article)

    _apply_feedbacks_meta(product, intercepted)

    dom = dom or {}
    if not product.title:
        product.title = _clean_title(dom.get("title") or dom.get("doc_title"))
    if product.title and _is_homepage_title(product.title):
        product.title = None
    if not product.description:
        desc = dom.get("description")
        # не берём site-wide og:description
        if desc and not looks_sitewide_description(desc):
            product.description = desc
            note_field(
                product, "description", (desc or "")[:80],
                "browser.dom", nm_id=article, verified=False, scope="nm",
            )
    photos = dom.get("photos") or []
    if isinstance(photos, list) and photos:
        # DOM imgs — только URL-список; photo_count НЕ из len(imgs[:20])
        product.photos = [p for p in photos if isinstance(p, str)][:20]
    chars = dom.get("characteristics")
    if isinstance(chars, dict) and chars and not product.characteristics:
        product.characteristics = {
            str(k): v for k, v in chars.items() if k and v is not None
        }
        note_field(
            product, "characteristics", len(product.characteristics),
            "browser.dom", nm_id=article, verified=False, scope="nm",
        )

    # DOM commercial — только если страница доказывает тот же nm_id
    if _dom_proves_nm(dom, article):
        _apply_dom_commercial(product, dom, article)

    # __NEXT_DATA__ — если есть imt / commercial (nm-checked)
    next_data = dom.get("next_data")
    if isinstance(next_data, dict):
        if product.imt_id is None:
            imt = _find_imt_in_obj(next_data)
            if imt is not None:
                product.imt_id = imt
                product.root_id = product.root_id or imt
        _apply_next_data_commercial(product, next_data, article)

    _sync_imt_root(product)
    return product


def _apply_detail_commercial(
    product: WBProduct,
    detail: dict[str, Any],
    article: int,
) -> None:
    """price/old_price/discount/rating/feedbacks из nm-matched detail."""
    from backend.wb.provenance import note_field, raw_nm_id

    nm = raw_nm_id(detail)
    if nm is None or int(nm) != int(article):
        return

    prices = _price_bundle_from_detail(detail)
    if product.price is None and prices.get("price") is not None:
        product.price = prices["price"]
        note_field(
            product, "price", product.price,
            "browser.detail", nm_id=article, verified=True, scope="nm",
        )
    if product.old_price is None and prices.get("old_price") is not None:
        product.old_price = prices["old_price"]
        note_field(
            product, "old_price", product.old_price,
            "browser.detail", nm_id=article, verified=True, scope="nm",
        )
    if product.discount is None and prices.get("discount") is not None:
        product.discount = prices["discount"]
        note_field(
            product, "discount", product.discount,
            "browser.detail", nm_id=article, verified=True, scope="nm",
        )
    if product.wallet_price is None and product.price is not None:
        product.wallet_price = round(int(product.price) * (1 - WALLET_DISCOUNT))

    rating = detail.get("nmReviewRating")
    if rating is None:
        rating = detail.get("reviewRating")
    if product.rating is None and rating is not None:
        try:
            r = round(float(rating), 2)
            if 0 < r <= 5:
                product.rating = r
                note_field(
                    product, "rating", product.rating,
                    "browser.detail", nm_id=article, verified=True, scope="nm",
                )
        except (TypeError, ValueError):
            pass

    fb = detail.get("nmFeedbacks")
    if fb is None:
        # на nm-matched product dict card-level feedbacks = «оценки» этого nm
        fb = detail.get("feedbacks")
    if product.feedbacks is None and fb is not None:
        try:
            product.feedbacks = int(fb)
            note_field(
                product, "feedbacks", product.feedbacks,
                "browser.detail", nm_id=article, verified=True, scope="nm",
            )
        except (TypeError, ValueError):
            pass


def _dom_proves_nm(dom: dict[str, Any], article: int) -> bool:
    """DOM/URL доказывает, что мы на карточке нужного nm."""
    art = str(int(article))
    blob = " ".join(
        str(dom.get(k) or "")
        for k in ("path", "doc_title", "title")
    )
    return art in blob


def _apply_dom_commercial(
    product: WBProduct,
    dom: dict[str, Any],
    article: int,
) -> None:
    """DOM fallback commercial — только после nm proof; verified=True через page nm."""
    from backend.wb.provenance import note_field

    if product.price is None:
        # title «… 218510904 купить за 1 059 ₽» надёжнее, чем склеенный priceBlock
        title_price = _parse_price_from_title(dom.get("doc_title") or dom.get("title"))
        price = title_price or _parse_price_text(dom.get("price_text"))
        if price is not None:
            product.price = price
            note_field(
                product, "price", product.price,
                "browser.dom", nm_id=article, verified=True, scope="nm",
            )

    review_line = dom.get("review_line") or dom.get("rating_text")
    rating, reviews = _parse_review_line(review_line)
    if product.rating is None:
        if rating is None:
            rating = _parse_rating_text(dom.get("rating_text"))
        if rating is not None and 0 < rating <= 5:
            product.rating = rating
            note_field(
                product, "rating", product.rating,
                "browser.dom", nm_id=article, verified=True, scope="nm",
            )
    if product.feedbacks is None and reviews is not None:
        product.feedbacks = reviews
        note_field(
            product, "feedbacks", product.feedbacks,
            "browser.dom", nm_id=article, verified=True, scope="nm",
        )


def _apply_next_data_commercial(
    product: WBProduct,
    next_data: dict[str, Any],
    article: int,
) -> None:
    """Достать commercial из __NEXT_DATA__ только для matching nm."""
    from backend.wb.provenance import note_field, raw_nm_id

    node = _find_nm_product_node(next_data, article)
    if not isinstance(node, dict):
        return
    if raw_nm_id(node) != int(article):
        return
    if product.price is None:
        prices = _price_bundle_from_detail(node)
        if prices.get("price") is not None:
            product.price = prices["price"]
            note_field(
                product, "price", product.price,
                "browser.next_data", nm_id=article, verified=True, scope="nm",
            )
            if product.old_price is None and prices.get("old_price") is not None:
                product.old_price = prices["old_price"]
                note_field(
                    product, "old_price", product.old_price,
                    "browser.next_data", nm_id=article, verified=True, scope="nm",
                )
            if product.discount is None and prices.get("discount") is not None:
                product.discount = prices["discount"]
                note_field(
                    product, "discount", product.discount,
                    "browser.next_data", nm_id=article, verified=True, scope="nm",
                )
    if product.rating is None:
        rating = node.get("nmReviewRating") or node.get("reviewRating")
        if rating is not None:
            try:
                r = round(float(rating), 2)
                if 0 < r <= 5:
                    product.rating = r
                    note_field(
                        product, "rating", product.rating,
                        "browser.next_data", nm_id=article, verified=True, scope="nm",
                    )
            except (TypeError, ValueError):
                pass
    if product.feedbacks is None:
        fb = node.get("nmFeedbacks")
        if fb is None:
            fb = node.get("feedbacks")
        if fb is not None:
            try:
                product.feedbacks = int(fb)
                note_field(
                    product, "feedbacks", product.feedbacks,
                    "browser.next_data", nm_id=article, verified=True, scope="nm",
                )
            except (TypeError, ValueError):
                pass


def _find_nm_product_node(obj: Any, article: int, depth: int = 0) -> dict | None:
    if depth > 8 or obj is None:
        return None
    if isinstance(obj, dict):
        from backend.wb.provenance import raw_nm_id

        nm = raw_nm_id(obj)
        if nm == int(article) and (
            "sizes" in obj or "reviewRating" in obj or "nmReviewRating" in obj
            or "salePriceU" in obj or "priceU" in obj or "feedbacks" in obj
        ):
            return obj
        for v in obj.values():
            found = _find_nm_product_node(v, article, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj[:40]:
            found = _find_nm_product_node(item, article, depth + 1)
            if found is not None:
                return found
    return None


def _apply_feedbacks_meta(product: WBProduct, intercepted: dict[str, Any]) -> None:
    """imt / rating / feedbacks из feedbacks*.wb.ru — только nm-safe."""
    from backend.wb.product_card_provider import raw_from_feedbacks_meta
    from backend.wb.provenance import note_field

    fb = intercepted.get("feedbacks")
    if isinstance(fb, dict):
        if product.imt_id is None:
            for key in ("imtId", "imt_id", "root"):
                val = fb.get(key)
                try:
                    n = int(val)
                    if n > 1000:
                        product.imt_id = n
                        product.root_id = product.root_id or n
                        break
                except (TypeError, ValueError):
                    pass
        # IMT-wide valuation/feedbackCount — только если ownership доказан
        meta = raw_from_feedbacks_meta(fb, int(product.article))
        if product.rating is None and meta.get("reviewRating") is not None:
            try:
                r = round(float(meta["reviewRating"]), 2)
                if 0 < r <= 5:
                    product.rating = r
                    note_field(
                        product, "rating", product.rating,
                        "browser.feedbacks1", nm_id=int(product.article),
                        verified=True, scope="nm",
                    )
            except (TypeError, ValueError):
                pass
        if product.feedbacks is None and meta.get("feedbacks") is not None:
            try:
                product.feedbacks = int(meta["feedbacks"])
                note_field(
                    product, "feedbacks", product.feedbacks,
                    "browser.feedbacks1", nm_id=int(product.article),
                    verified=True, scope="nm",
                )
            except (TypeError, ValueError):
                pass

    if product.imt_id is None:
        url = intercepted.get("feedbacks_url") or ""
        m = re.search(r"/feedbacks/v\d+/(\d+)", str(url))
        if m:
            try:
                product.imt_id = int(m.group(1))
                product.root_id = product.root_id or product.imt_id
            except (TypeError, ValueError):
                pass


def _build_reviews(
    article: int,
    intercepted: dict[str, Any],
    dom: dict[str, Any] | None,
    product: WBProduct,
) -> list[Review]:
    from backend.services.wb_reviews import parse_feedbacks_payload

    fb = intercepted.get("feedbacks")
    if isinstance(fb, dict):
        try:
            from backend.config import MAX_REVIEW_TEXTS
            lim = int(MAX_REVIEW_TEXTS)
        except Exception:
            lim = 60
        reviews = parse_feedbacks_payload(
            fb,
            article_id=article,
            imt_id=product.imt_id or product.root_id,
            limit=lim,
        )
        if reviews:
            return reviews

    out: list[Review] = []
    seen: set[str] = set()
    try:
        from backend.config import MAX_REVIEW_TEXTS
        lim = int(MAX_REVIEW_TEXTS)
    except Exception:
        lim = 60
    for text in (dom or {}).get("review_texts") or []:
        if not isinstance(text, str) or len(text.strip()) < 10:
            continue
        fp = review_fingerprint(text)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(
            Review(
                review_id=fp,
                article_id=article,
                text=text.strip()[:2000],
                fingerprint=fp,
                source_url=product.url,
                metadata={"source": "browser_dom"},
            )
        )
        if len(out) >= lim:
            break
    return out


def _unwrap_detail(payload: Any, article: int | None = None) -> dict | None:
    """Достать detail product dict; при списке — только matching nm_id."""
    if not isinstance(payload, dict):
        return None

    def _pick(products: list) -> dict | None:
        matched: list[dict] = []
        for p in products:
            if not isinstance(p, dict):
                continue
            if article is None:
                matched.append(p)
                continue
            try:
                pid = int(p.get("id") or p.get("nmId") or 0)
            except (TypeError, ValueError):
                continue
            if pid == int(article):
                matched.append(p)
        if matched:
            return matched[0]
        # без match по nm — не берём чужой products[0]
        if article is not None:
            return None
        return products[0] if products and isinstance(products[0], dict) else None

    products = payload.get("products")
    if isinstance(products, list) and products:
        picked = _pick(products)
        if picked is not None:
            return picked
    data = payload.get("data")
    if isinstance(data, dict):
        products = data.get("products")
        if isinstance(products, list) and products:
            picked = _pick(products)
            if picked is not None:
                return picked
    if "root" in payload or "name" in payload or "id" in payload:
        if article is not None:
            try:
                pid = int(payload.get("id") or payload.get("nmId") or 0)
                if pid and pid != int(article):
                    return None
            except (TypeError, ValueError):
                pass
        return payload
    return None


def _kopecks_to_rub(val: Any) -> int | None:
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n // 100 if n > 10000 else n


def _price_bundle_from_detail(detail: dict) -> dict[str, int | None]:
    """price / old_price / discount из sizes[].price или salePriceU/priceU."""
    out: dict[str, int | None] = {
        "price": None, "old_price": None, "discount": None,
    }
    sizes = detail.get("sizes")
    if isinstance(sizes, list):
        for size in sizes:
            if not isinstance(size, dict):
                continue
            price = size.get("price") or {}
            if not isinstance(price, dict):
                continue
            final = _kopecks_to_rub(
                price.get("product") if price.get("product") is not None
                else price.get("total")
            )
            basic = _kopecks_to_rub(price.get("basic"))
            if final is None:
                continue
            out["price"] = final
            if basic is not None:
                out["old_price"] = basic
                if basic > final:
                    out["discount"] = round((1 - final / basic) * 100)
                else:
                    out["discount"] = 0
            break
    if out["price"] is None:
        for key in ("salePriceU", "priceU", "price"):
            val = detail.get(key)
            if val is None or isinstance(val, dict):
                continue
            rub = _kopecks_to_rub(val)
            if rub is not None:
                out["price"] = rub
                break
    if out["old_price"] is None:
        for key in ("priceU", "basicPriceU"):
            val = detail.get(key)
            if val is None or isinstance(val, dict):
                continue
            rub = _kopecks_to_rub(val)
            if rub is not None and (out["price"] is None or rub >= out["price"]):
                out["old_price"] = rub
                break
    if (
        out["discount"] is None
        and out["price"] is not None
        and out["old_price"] is not None
        and out["old_price"] > out["price"]
    ):
        out["discount"] = round((1 - out["price"] / out["old_price"]) * 100)
    return out


def _price_from_detail(detail: dict) -> int | None:
    return _price_bundle_from_detail(detail).get("price")


def _parse_price_from_title(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").replace("\u202f", " ")
    m = re.search(
        r"купить\s+за\s+(\d[\d\s]{0,12})\s*₽",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return int(re.sub(r"\s+", "", m.group(1)))
    except ValueError:
        return None


def _parse_price_text(text: str | None) -> int | None:
    if not text:
        return None
    # Берём первую цену вида «974 ₽» / «1 990₽», не склеиваем все цифры блока.
    cleaned = text.replace("\xa0", " ").replace("\u202f", " ")
    m = re.search(r"(\d[\d\s]{0,12})\s*₽", cleaned)
    raw = m.group(1) if m else None
    if not raw:
        digits = re.sub(r"[^\d]", "", cleaned)
        if not digits:
            return None
        try:
            n = int(digits)
            return n if n < 10_000_000 else None
        except ValueError:
            return None
    try:
        return int(re.sub(r"\s+", "", raw))
    except ValueError:
        return None


def _parse_review_line(text: str | None) -> tuple[float | None, int | None]:
    """«4,4·2 983 оценки» / «4.8 · 120 отзывов» → (rating, count)."""
    if not text:
        return None, None
    cleaned = (
        text.replace("\xa0", " ")
        .replace("\u202f", " ")
        .replace("·", " ")
        .replace("•", " ")
    )
    rating = None
    count = None
    m = re.search(r"(\d+[.,]\d+|\d+)\s+(\d[\d\s]{0,12})\s*(оцен|отзыв)", cleaned, re.I)
    if m:
        try:
            r = float(m.group(1).replace(",", "."))
            if 0 < r <= 5:
                rating = r
        except ValueError:
            pass
        try:
            count = int(re.sub(r"\s+", "", m.group(2)))
        except ValueError:
            pass
        return rating, count
    return _parse_rating_text(cleaned), None


def _parse_rating_text(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").replace("\u202f", " ").strip()
    # чистое «4,4» / «4.8»
    m_pure = re.fullmatch(r"(\d+[.,]\d+|\d+)", cleaned)
    if m_pure:
        try:
            r = float(m_pure.group(1).replace(",", "."))
        except ValueError:
            return None
        return r if 0 < r <= 5 else None
    # строки с оценками/отзывами
    if not re.search(r"оцен|отзыв|рейтинг", cleaned, re.I):
        return None
    m = re.search(r"(\d+[.,]\d+|\d+)", cleaned.replace(",", "."))
    if not m:
        return None
    try:
        r = float(m.group(1))
    except ValueError:
        return None
    if r <= 0 or r > 5:
        return None
    return r


def _clean_title(title: str | None) -> str | None:
    if not title:
        return None
    t = title.strip()
    if _is_homepage_title(t) or _looks_error_page(t):
        return None
    for suf in (
        " купить за ",
        " — купить",
        " - купить",
        " | Wildberries",
        " — Wildberries",
        " в интернет-магазине Wildberries",
        " в интернет‑магазине Wildberries",
    ):
        if suf in t:
            t = t.split(suf)[0].strip()
    # «… Eltime 279904819» → убрать хвостовой nm
    t = re.sub(r"\s+\d{6,}$", "", t).strip()
    return t or None


def _find_imt_in_obj(obj: Any, depth: int = 0) -> int | None:
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for key in ("imt_id", "imtId", "imtID", "root"):
            val = obj.get(key)
            try:
                n = int(val)
                if n > 1000:
                    return n
            except (TypeError, ValueError):
                pass
        for v in obj.values():
            found = _find_imt_in_obj(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj[:20]:
            found = _find_imt_in_obj(item, depth + 1)
            if found:
                return found
    return None
