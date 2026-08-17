"""Launch installed Google Chrome and attach via CDP.

Playwright chromium.launch() is detected by WB (498 loop). Vanilla chrome.exe
+ connect_over_cdp is the proven path. No stealth / UA spoof / extra flags.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path


_WIN_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_POSIX_CANDIDATES = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def resolve_system_chrome_path(explicit: str | None = None) -> str | None:
    """Return chrome.exe path or None if not installed."""
    if explicit:
        p = Path(explicit.strip().strip('"'))
        if p.is_file():
            return str(p)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or ""
        extra = (
            [str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe")]
            if local
            else []
        )
        for cand in (*_WIN_CANDIDATES, *extra):
            if cand and Path(cand).is_file():
                return cand
        which = _which("chrome")
        return which
    for cand in _POSIX_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return _which("google-chrome") or _which("google-chrome-stable") or _which("chromium")


def _which(name: str) -> str | None:
    from shutil import which

    found = which(name)
    return found if found and Path(found).is_file() else None


def pick_free_tcp_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def build_system_chrome_args(
    chrome_path: str,
    *,
    cdp_port: int,
    user_data_dir: str,
    proxy_server: str | None = None,
    headless: bool = False,
) -> list[str]:
    """Minimal flags matching the working diagnostic (cell 9)."""
    args = [
        chrome_path,
        f"--remote-debugging-port={int(cdp_port)}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--proxy-bypass-list=<-loopback>",
    ]
    if proxy_server:
        args.append(f"--proxy-server={proxy_server}")
    if headless:
        args.append("--headless=new")
    args.append("about:blank")
    return args


def kill_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass
