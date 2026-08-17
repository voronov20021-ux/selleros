"""
handlers/action_verify.py — Telegram UX for Action verification.

Callbacks: actv:{yes|no|later|again|accept|done|check}:{action_id}
Does NOT call Browser. Uses ActionService + VerificationEngine only.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from backend.foundation.action_service import ActionService
from backend.foundation.action_verification import SellerConfirmIntent
from backend.keyboards.inline import action_recommend_kb, action_verification_kb

log = logging.getLogger("selleros.handlers.action_verify")
router = Router(name="action_verify")

# Shared ActionService (set from bot startup) — same store as scheduler
_SHARED: ActionService | None = None
_SERVICES: dict[int, ActionService] = {}


def set_shared_action_service(svc: ActionService | None) -> None:
    global _SHARED
    _SHARED = svc


def _svc_for(user_id: int, session=None) -> ActionService:
    if _SHARED is not None:
        store = None
        if session is not None:
            store = getattr(session, "memory_store", None)
        if store is not None and _SHARED._store is None:
            _SHARED._store = store
        return _SHARED
    store = None
    if session is not None:
        store = getattr(session, "memory_store", None)
    key = int(user_id)
    if key not in _SERVICES:
        _SERVICES[key] = ActionService(memory_store=store)
    elif store is not None and _SERVICES[key]._store is None:
        _SERVICES[key]._store = store
    return _SERVICES[key]


@router.callback_query(F.data.startswith("actv:"))
async def on_action_verify(callback: CallbackQuery, session=None) -> None:
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        await callback.answer("Некорректная кнопка", show_alert=False)
        return
    _, kind, action_id = parts[0], parts[1], parts[2]
    user_id = callback.from_user.id if callback.from_user else 0
    svc = _svc_for(user_id, session)
    action = await svc.get(action_id)
    if action is None:
        await callback.answer("Действие не найдено (или сессия сброшена)", show_alert=True)
        return

    text = ""
    kb = None
    try:
        if kind == "accept":
            action = await svc.accept(action_id)
            text = (
                f"✅ Рекомендация принята.\n"
                f"{action.recommendation}\n"
                f"Проверка применения: check_after; эффект: outcome_after."
            )
            kb = action_recommend_kb(action_id)
        elif kind == "done":
            action = await svc.accept(action_id) if action.status.value == "PROPOSED" else action
            if action.status.value in ("PROPOSED", "ACCEPTED"):
                action = await svc.mark_executed(action_id)
            action, result = await svc.verify_action(
                action_id,
                seller_intent=SellerConfirmIntent.YES_APPLIED,
            )
            text = (
                "🛠 Зафиксировал «Сделал».\n"
                f"Verification: **{result.status.value}** (source={result.source}).\n"
                "Это ещё не SUCCESS — outcome посчитаем после outcome_after."
            )
            kb = action_verification_kb(action_id) if result.needs_seller_confirmation else None
        elif kind == "check":
            action, result = await svc.verify_action(action_id)
            text = svc._verify.format_result_message(action, result)
            if result.needs_seller_confirmation:
                kb = action_verification_kb(action_id)
        elif kind == "yes":
            action, result = await svc.verify_action(
                action_id, seller_intent=SellerConfirmIntent.YES_APPLIED,
            )
            text = result.message + "\nSELLER_CONFIRMED ≠ SUCCESS."
        elif kind == "no":
            action, result = await svc.verify_action(
                action_id, seller_intent=SellerConfirmIntent.NO_NOT_APPLIED,
            )
            text = result.message + "\nРекомендация не считается FAILED."
        elif kind == "later":
            action = await svc.defer(action_id, days=3.0)
            action, result = await svc.verify_action(
                action_id, seller_intent=SellerConfirmIntent.NOT_YET,
            )
            text = "⏳ Ок, проверю позже."
        elif kind == "again":
            action, result = await svc.verify_action(
                action_id, seller_intent=SellerConfirmIntent.CHECK_AGAIN,
            )
            action.last_verification_at = None
            await svc._save(action)
            action, result = await svc.verify_action(action_id)
            text = svc._verify.format_result_message(action, result)
            if result.needs_seller_confirmation:
                kb = action_verification_kb(action_id)
        else:
            await callback.answer("Неизвестное действие", show_alert=False)
            return
    except Exception as exc:
        log.exception("actv handler failed: %s", exc)
        await callback.answer("Ошибка проверки", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.answer(text, reply_markup=kb)
