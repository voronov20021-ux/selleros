"""Onboarding status machine constants."""

from __future__ import annotations

from enum import Enum


class OnboardingStatus(str, Enum):
    NEW = "NEW"
    WB_CONNECTED = "WB_CONNECTED"
    FIRST_PRODUCT_ADDED = "FIRST_PRODUCT_ADDED"
    READY = "READY"


# Forward-only progress ranks (disconnect resets to NEW separately).
STATUS_RANK = {
    OnboardingStatus.NEW: 0,
    OnboardingStatus.WB_CONNECTED: 1,
    OnboardingStatus.FIRST_PRODUCT_ADDED: 2,
    OnboardingStatus.READY: 3,
}


def parse_status(raw: str | OnboardingStatus | None) -> OnboardingStatus:
    if isinstance(raw, OnboardingStatus):
        return raw
    text = (raw or OnboardingStatus.NEW.value).strip().upper()
    try:
        return OnboardingStatus(text)
    except ValueError:
        return OnboardingStatus.NEW
