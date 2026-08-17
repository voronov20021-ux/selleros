"""
Split long Telegram messages without breaking HTML/Markdown entities.

Project replies use parse_mode=HTML. This module is the single send-path
helper for Argus / Seller AI outbound text that may exceed Telegram's limit.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

# Telegram Bot API hard limit for text messages / captions on text edits.
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Tags Telegram HTML mode commonly uses in this project.
_TAG_NAMES = (
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
    "tg-spoiler",
    "blockquote",
    "a",
    "span",
)

_OPEN_RE = re.compile(
    r"<(" + "|".join(_TAG_NAMES) + r")(\s[^>]*)?>",
    re.IGNORECASE,
)
_CLOSE_RE = re.compile(
    r"</(" + "|".join(_TAG_NAMES) + r")\s*>",
    re.IGNORECASE,
)

# Prefer natural boundaries (order matters: strongest first).
_BOUNDARY_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\n\n+"),  # paragraphs
    re.compile(r"(?<=[.!?…])(?:[ \t]+|\n)"),  # sentence ends
    re.compile(r"\n(?=<b>|<strong>|<i>|<em>|<code>|#|\*\*)"),  # headers / bold lines
    re.compile(r"\n(?=[•\-\*]\s|\d+\.\s)"),  # list items
    re.compile(r"\n"),  # any line break
    re.compile(r"[ \t]+"),  # words
)


def split_telegram_message(
    text: str,
    *,
    limit: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    parse_mode: str | None = "HTML",
) -> list[str]:
    """
    Split ``text`` into chunks each ≤ ``limit`` characters.

    Prefers paragraph / line / list boundaries. For HTML, never splits inside
    a tag; open tags are closed at chunk end and reopened on the next chunk
    so Telegram parse_mode stays valid and joined visible text is preserved.
    """
    if text is None:
        return [""]
    if limit < 32:
        raise ValueError("limit must be at least 32")
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    mode = (parse_mode or "").upper()
    use_html = mode in ("HTML",)

    chunks: list[str] = []
    remaining = text
    carry_open = ""

    safety = 0
    max_iters = max(8, len(text) // max(1, limit // 2) + 8)

    while remaining:
        safety += 1
        if safety > max_iters:
            # Hard-fail safe: dump the rest in oversized-safe slices.
            while remaining:
                piece, remaining = _hard_slice(carry_open + remaining, limit, use_html)
                carry_open = ""
                if use_html:
                    piece, carry_open, remaining = _seal_html_chunk(
                        piece, remaining, limit
                    )
                chunks.append(piece)
            break

        full = carry_open + remaining
        if len(full) <= limit:
            chunks.append(full)
            break

        # Leave headroom so closing tags still fit under ``limit``.
        # Cap reserve so small test limits still make progress.
        reserve = min(64, max(8, limit // 8)) if use_html else 0
        budget = limit - reserve
        if budget <= len(carry_open):
            budget = limit

        cut = _choose_cut(full, budget, use_html=use_html)
        if cut <= len(carry_open):
            # Must advance into the body.
            min_body = max(1, min(32, limit - len(carry_open)))
            cut = min(len(full), len(carry_open) + min_body)
            if use_html:
                cut = _avoid_mid_tag(full, cut)
            if cut <= len(carry_open):
                # Inside an opening tag right after carry — skip past next '>'
                gt = full.find(">", len(carry_open))
                cut = (gt + 1) if gt >= 0 else min(len(full), len(carry_open) + 1)

        piece = full[:cut]
        rest = full[cut:]

        if use_html:
            piece, carry_open, rest = _seal_html_chunk(piece, rest, limit)
        else:
            carry_open = ""

        # Soft-trim a single leading newline so the next message starts clean.
        if rest.startswith("\n"):
            rest = rest[1:]

        if not piece and rest:
            # Avoid stalling: force one character forward.
            piece, rest = _hard_slice(carry_open + rest, limit, use_html)
            carry_open = ""
            if use_html:
                piece, carry_open, rest = _seal_html_chunk(piece, rest, limit)

        chunks.append(piece)
        remaining = rest

    while len(chunks) > 1 and not chunks[-1].strip():
        chunks.pop()
    return chunks or [""]


def _seal_html_chunk(
    piece: str,
    rest: str,
    limit: int,
) -> tuple[str, str, str]:
    """
    Close open HTML tags at the end of ``piece`` and return
    (sealed_piece, reopen_prefix_for_next, rest).
    """
    stack = _open_stack(piece)
    if not stack:
        return piece, "", rest

    closers = "".join(f"</{name}>" for name, _ in reversed(stack))
    while piece and len(piece) + len(closers) > limit:
        cut2 = _choose_cut(piece, max(1, len(piece) - 1), use_html=True)
        if cut2 >= len(piece):
            cut2 = _avoid_mid_tag(piece, max(1, len(piece) - 1))
        if cut2 <= 0:
            break
        rest = piece[cut2:] + rest
        piece = piece[:cut2]
        stack = _open_stack(piece)
        closers = "".join(f"</{name}>" for name, _ in reversed(stack))

    sealed = piece + closers
    reopen = "".join(tag for _, tag in stack)
    return sealed, reopen, rest


def _hard_slice(text: str, limit: int, use_html: bool) -> tuple[str, str]:
    cut = min(len(text), limit)
    if use_html:
        cut = _avoid_mid_tag(text, cut)
        if cut <= 0:
            cut = min(len(text), limit)
    # Never split mid-word if a space exists in the window.
    if cut < len(text) and cut > 0 and not text[cut - 1].isspace():
        sp = text.rfind(" ", 0, cut)
        if sp >= max(1, limit // 5):
            cut = sp + 1
    return text[:cut], text[cut:]


def trim_at_sentence(text: str, max_chars: int) -> str:
    """Trim to max_chars at a sentence/paragraph/word boundary. No mid-word '…'."""
    if text is None:
        return ""
    if max_chars < 8:
        return text[:max_chars]
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    min_keep = max(8, max_chars // 5)
    for sep in ("\n\n", ". ", ".\n", "? ", "! ", "… ", "\n", " "):
        idx = window.rfind(sep)
        if idx >= min_keep:
            return window[: idx + len(sep)].rstrip()
    return window.rstrip()


def _choose_cut(text: str, limit: int, *, use_html: bool) -> int:
    if len(text) <= limit:
        return len(text)

    window_end = limit
    if use_html:
        window_end = _avoid_mid_tag(text, limit)
    window = text[:window_end]

    min_keep = max(1, limit // 5)
    for pattern in _BOUNDARY_PATTERNS:
        cut = _rfind_boundary(window, pattern, min_keep)
        if cut is not None:
            return cut

    return len(window)


def _rfind_boundary(window: str, pattern: re.Pattern[str], min_keep: int) -> int | None:
    best: int | None = None
    for m in pattern.finditer(window):
        end = m.end()
        if end >= min_keep and end < len(window):
            best = end
    return best


def _avoid_mid_tag(text: str, pos: int) -> int:
    """Move ``pos`` left if it falls inside an HTML tag ``<...>``."""
    if pos <= 0 or pos >= len(text):
        return max(0, min(pos, len(text)))
    lt = text.rfind("<", 0, pos)
    if lt < 0:
        return pos
    gt = text.find(">", lt, pos)
    if gt >= 0:
        return pos
    return lt


def _open_stack(html: str) -> list[tuple[str, str]]:
    """Stack of still-open tags as (name_lower, full_open_tag)."""
    stack: list[tuple[str, str]] = []
    i = 0
    n = len(html)
    while i < n:
        if html[i] != "<":
            i += 1
            continue
        close_m = _CLOSE_RE.match(html, i)
        if close_m:
            name = close_m.group(1).lower()
            for j in range(len(stack) - 1, -1, -1):
                if stack[j][0] == name:
                    del stack[j]
                    break
            i = close_m.end()
            continue
        open_m = _OPEN_RE.match(html, i)
        if open_m:
            name = open_m.group(1).lower()
            stack.append((name, open_m.group(0)))
            i = open_m.end()
            continue
        i += 1
    return stack


def strip_html_tags(text: str) -> str:
    """Visible text only (for join-equality checks in tests)."""
    return re.sub(r"<[^>]+>", "", text)


def html_chunks_balanced(chunks: Sequence[str]) -> bool:
    """True if every chunk has a balanced Telegram-HTML tag stack."""
    for chunk in chunks:
        if _open_stack(chunk):
            return False
    return True


async def answer_long(
    message: Any,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool | None = None,
    limit: int = TELEGRAM_MAX_MESSAGE_LENGTH,
) -> list[Any]:
    """
    Send ``text`` as one or more ``message.answer`` calls.
    Keyboard and preview flags apply to the **last** chunk only.
    """
    parts = split_telegram_message(text, limit=limit, parse_mode=parse_mode)
    sent: list[Any] = []
    for idx, part in enumerate(parts):
        is_last = idx == len(parts) - 1
        kwargs: dict[str, Any] = {}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if is_last and reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if is_last and disable_web_page_preview is not None:
            kwargs["disable_web_page_preview"] = disable_web_page_preview
        sent.append(await message.answer(part, **kwargs))
    return sent


async def edit_or_answer_long(
    target: Any,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool | None = None,
    limit: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    fallback_message: Any | None = None,
) -> list[Any]:
    """
    Prefer editing ``target`` for the first chunk; send further chunks via
    ``answer`` on ``fallback_message`` or ``target``.

    If the text fits in one chunk, behaves like ``edit_text``.
    If edit fails (e.g. message is not editable), falls back to ``answer_long``.
    """
    parts = split_telegram_message(text, limit=limit, parse_mode=parse_mode)
    answer_from = fallback_message or target

    if len(parts) == 1:
        kwargs: dict[str, Any] = {}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if disable_web_page_preview is not None:
            kwargs["disable_web_page_preview"] = disable_web_page_preview
        try:
            return [await target.edit_text(parts[0], **kwargs)]
        except Exception:
            return await answer_long(
                answer_from,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
                limit=limit,
            )

    sent: list[Any] = []
    first_kwargs: dict[str, Any] = {}
    if parse_mode:
        first_kwargs["parse_mode"] = parse_mode
    try:
        sent.append(await target.edit_text(parts[0], **first_kwargs))
    except Exception:
        return await answer_long(
            answer_from,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            limit=limit,
        )

    for idx, part in enumerate(parts[1:], start=1):
        is_last = idx == len(parts) - 1
        kwargs: dict[str, Any] = {}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if is_last and reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if is_last and disable_web_page_preview is not None:
            kwargs["disable_web_page_preview"] = disable_web_page_preview
        sent.append(await answer_from.answer(part, **kwargs))
    return sent
