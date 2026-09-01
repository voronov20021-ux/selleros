"""Unit checks for Playwright Chromium launch kwargs (no live WB / no browser)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.browser.fetcher import (
    build_playwright_chromium_launch_kwargs,
    diagnose_chromium_launch_error,
    launch_kwargs_without_channel,
    playwright_supports_chromium_channel,
    should_retry_chromium_launch_without_channel,
)

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


def main() -> int:
    linux = build_playwright_chromium_launch_kwargs(
        headless=True,
        disable_http2=True,
        proxy_cfg=None,
        platform="linux",
        playwright_version="1.50.0",
    )
    linux_args = linux.get("args") or []
    check("linux has --no-sandbox", "--no-sandbox" in linux_args)
    check("linux has --disable-dev-shm-usage", "--disable-dev-shm-usage" in linux_args)
    check("linux has --disable-gpu", "--disable-gpu" in linux_args)
    check("linux keeps --disable-http2", "--disable-http2" in linux_args)
    check("linux channel=chromium", linux.get("channel") == "chromium")
    check("linux chromium_sandbox=False", linux.get("chromium_sandbox") is False)

    win = build_playwright_chromium_launch_kwargs(
        headless=True,
        disable_http2=True,
        proxy_cfg=None,
        platform="win32",
        playwright_version="1.50.0",
    )
    win_args = win.get("args") or []
    check("windows args only --disable-http2", win_args == ["--disable-http2"])
    check("windows has no channel", "channel" not in win)
    check("windows has no chromium_sandbox", "chromium_sandbox" not in win)

    win_no_http2 = build_playwright_chromium_launch_kwargs(
        headless=True,
        disable_http2=False,
        platform="win32",
        playwright_version="1.50.0",
    )
    check("windows no-http2 has no args", "args" not in win_no_http2)

    old = build_playwright_chromium_launch_kwargs(
        headless=True,
        disable_http2=True,
        platform="linux",
        playwright_version="1.40.0",
    )
    check("old playwright has no channel", "channel" not in old)
    check("old playwright still has --no-sandbox", "--no-sandbox" in (old.get("args") or []))

    headed = build_playwright_chromium_launch_kwargs(
        headless=False,
        disable_http2=True,
        platform="linux",
        playwright_version="1.50.0",
    )
    check("headed linux has no channel", "channel" not in headed)

    proxy = {"server": "http://127.0.0.1:8080", "username": "u", "password": "secret"}
    with_proxy = build_playwright_chromium_launch_kwargs(
        headless=True,
        disable_http2=True,
        proxy_cfg=proxy,
        platform="win32",
        playwright_version="1.50.0",
    )
    check("proxy passed through", with_proxy.get("proxy") == proxy)

    check("channel supported 1.50", playwright_supports_chromium_channel("1.50.1") is True)
    check("channel unsupported 1.40", playwright_supports_chromium_channel("1.40.0") is False)

    check(
        "retry on invalid channel",
        should_retry_chromium_launch_without_channel(
            RuntimeError("Invalid channel: chromium")
        )
        is True,
    )
    check(
        "no retry on target closed",
        should_retry_chromium_launch_without_channel(
            RuntimeError("Target page, context or browser has been closed")
        )
        is False,
    )
    dropped = launch_kwargs_without_channel(linux)
    check("retry kwargs drop channel", dropped is not None and "channel" not in dropped)
    check(
        "retry keeps linux args",
        dropped is not None and "--no-sandbox" in (dropped.get("args") or []),
    )

    diag = diagnose_chromium_launch_error(
        "BrowserType.launch: Target page, context or browser has been closed\n"
        "Browser logs:\n"
        "<launching> /data/ms-playwright/chromium_headless_shell-1234/"
        "chrome-headless-shell-linux64/chrome-headless-shell --disable-field-trial-config\n"
        "error while loading shared libraries: libnss3.so: cannot open shared object file"
    )
    check("diagnosis notes headless-shell", "headless-shell" in diag)
    check("diagnosis notes libnss3", "libnss3.so" in diag)
    check("diagnosis notes PLAYWRIGHT_INSTALL_DEPS", "PLAYWRIGHT_INSTALL_DEPS=1" in diag)

    failed = [x for x in RESULTS if not x[1]]
    print(f"\n  {sum(1 for x in RESULTS if x[1])}/{len(RESULTS)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
