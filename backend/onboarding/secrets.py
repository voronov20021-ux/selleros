"""Encrypt seller WB API keys at rest. Never log plaintext."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Optional


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _resolve_secret(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    from backend import config

    key = (getattr(config, "SELLER_SECRETS_KEY", None) or "").strip()
    if key:
        return key
    # Deterministic fallback from BOT_TOKEN so MVP works without extra env;
    # production should set SELLER_SECRETS_KEY explicitly.
    bot = (getattr(config, "BOT_TOKEN", None) or "").strip()
    if not bot:
        raise RuntimeError("SELLER_SECRETS_KEY (or BOT_TOKEN) required for seller secrets")
    return f"selleros-secrets-v1:{bot}"


def encrypt_secret(plaintext: str, *, secret: Optional[str] = None) -> str:
    """Encrypt plaintext API key → urlsafe token (HMAC-stream + tag)."""
    if not plaintext:
        raise ValueError("empty secret")
    key = _derive_key(_resolve_secret(secret))
    iv = os.urandom(16)
    data = plaintext.encode("utf-8")
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        stream.extend(
            hmac.new(key, iv + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        )
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, iv + cipher, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(iv + tag + cipher).decode("ascii")


def decrypt_secret(token: str, *, secret: Optional[str] = None) -> str:
    """Decrypt token → plaintext API key. Raises ValueError on tamper."""
    if not token:
        raise ValueError("empty token")
    key = _derive_key(_resolve_secret(secret))
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    if len(raw) < 32:
        raise ValueError("corrupt token")
    iv, tag, cipher = raw[:16], raw[16:32], raw[32:]
    expect = hmac.new(key, iv + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expect):
        raise ValueError("corrupt token")
    stream = bytearray()
    counter = 0
    while len(stream) < len(cipher):
        stream.extend(
            hmac.new(key, iv + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        )
        counter += 1
    data = bytes(a ^ b for a, b in zip(cipher, stream))
    return data.decode("utf-8")
