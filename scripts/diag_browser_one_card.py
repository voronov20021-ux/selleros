"""One Chromium WB card fetch — same wiring as ProductService (API/bot).

  python scripts/diag_browser_one_card.py
  python scripts/diag_browser_one_card.py 279904819

Prints success/fail + HTTP status only. No cookies, no proxy URL, no secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_ARTICLE = 279904819


async def main(article: int) -> int:
    import backend.config as config
    from backend.browser.fetcher import PlaywrightBrowserFetcher
    from backend.browser.proxy_pool import BrowserProxyPool

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    proxy_urls = config.effective_browser_proxy_urls()
    proxy_pool = BrowserProxyPool.from_urls(proxy_urls)
    fetcher = PlaywrightBrowserFetcher(
        proxy_pool=proxy_pool if proxy_pool else None,
        proxy_url=(
            (proxy_urls[0] if proxy_urls else None) if not proxy_pool else None
        ),
        headless=config.BROWSER_HEADLESS,
        timeout_ms=config.BROWSER_TIMEOUT_MS,
        proxy_mode=config.BROWSER_PROXY_MODE,
        disable_http2=config.BROWSER_DISABLE_HTTP2,
        session_id=config.BROWSER_PROXY_SESSION_ID,
        session_time=config.BROWSER_PROXY_SESSION_TIME,
        use_system_chrome=config.BROWSER_USE_SYSTEM_CHROME,
        chrome_path=config.BROWSER_CHROME_PATH,
    )

    ok = False
    try:
        product, _reviews = await fetcher.fetch(int(article))
        ok = product is not None
    except Exception:
        ok = False

    status = fetcher.last_http_status
    print("success" if ok else "failure")
    print(f"HTTP status: {status if status is not None else 'unknown'}")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One Browser card fetch")
    parser.add_argument(
        "article",
        nargs="?",
        type=int,
        default=DEFAULT_ARTICLE,
        help="WB nmID (default: 279904819)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.article)))
