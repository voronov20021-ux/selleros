#!/bin/sh
# Idempotent Playwright Chromium install for Amvera (Linux).
# pip package comes from requirements.txt (build). This downloads browser binaries.
# Call from Amvera start command BEFORE uvicorn / bot.
#
#   sh scripts/install_playwright.sh && \
#     sh -c 'uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-80} & python -m backend.bot & wait'
#
# Does not print secrets. Safe to re-run (skips download if Chromium already in PLAYWRIGHT_BROWSERS_PATH).

set -e

if [ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ] && [ -d /data ]; then
  PLAYWRIGHT_BROWSERS_PATH=/data/ms-playwright
  export PLAYWRIGHT_BROWSERS_PATH
fi
if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
  mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
fi

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

echo "install_playwright: using $PY, PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-default}"

if ! "$PY" -c "import playwright" >/dev/null 2>&1; then
  echo "install_playwright: ERROR python package 'playwright' missing (uncomment in requirements.txt and rebuild)"
  exit 1
fi

"$PY" -m playwright install chromium
echo "install_playwright: chromium ok"

# System libraries live in the ephemeral container OS, not /data.
# Skip unless explicitly requested — apt every start is slow.
if [ "${PLAYWRIGHT_INSTALL_DEPS:-0}" = "1" ]; then
  if "$PY" -m playwright install-deps chromium; then
    echo "install_playwright: install-deps ok"
  else
    echo "install_playwright: install-deps failed (no apt/root?). Set PLAYWRIGHT_INSTALL_DEPS=0 or use a Dockerfile."
  fi
fi
