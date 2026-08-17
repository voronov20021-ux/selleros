"""
brain.py — интеллект Seller AI.

Путь сообщения:

    сообщение
        ↓
    1. загрузка dialog (discussion / general)
        ↓
    2. MemoryContext (единая память, ДО intent)
        ↓
    3. классификация intent (только задача, не память)
        ↓
    4. intent-specific ContextBuilder
        ↓
    5. системный промпт = playbook + MemoryContext + context
        ↓
    6. history = MemoryContext.recent_turns (одинаково для любого intent)
        ↓
    7. AIService → ответ → запись в память (+ summary при overflow)
"""

from __future__ import annotations

import logging

from backend.ai import smalltalk
from backend.ai.context import ContextBuilder, ContextRequest
from backend.ai.dialog import DIALOG_DEPTH, DialogMemory
from backend.ai.finance_planner import (
    FinancialContext,
    ProductCandidate,
    candidates_from_session,
    handle_finance_turn,
    should_handle_finance,
)
from backend.ai.funnel_economics import (
    FunnelContext,
    handle_funnel_turn,
    should_handle_funnel,
)
from backend.ai.dynamic_analytics import (
    DynamicsContext,
    answer_why_ctr,
    build_series_from_snapshots,
    handle_dynamics_turn,
    is_dynamics_query,
    should_handle_dynamics,
)
from backend.knowledge.chat import (
    handle_knowledge_turn,
    should_handle_knowledge,
)
from backend.ai.intents import Intent, classify, refers_to_product
from backend.ai.personality import build_system
from backend.intelligence.solution_research import (
    DecisionStatus,
    format_choice_reply,
    format_why_selected,
    infer_topic,
    is_decision_recall_query,
    is_implement_query,
    is_seller_confirm_choice,
    is_solution_choice_query,
    is_solution_research_query,
    parse_choice_index,
)
from backend.memory.context import (
    RECENT_TURNS,
    SUMMARY_OVERFLOW,
    MemoryContextBuilder,
    fold_summary,
)

log = logging.getLogger("selleros.ai.brain")

#: Сколько последних сообщений диалога уходит в модель.
HISTORY_IN_PROMPT = RECENT_TURNS


class BrainReply:
    """Ответ Seller AI вместе с тем, как он был получен."""

    def __init__(self, text: str, intent: Intent, *, used_model: bool):
        self.text = text
        self.intent = intent
        self.used_model = used_model
        #: Снимок памяти, с которым ушёл запрос (для тестов/отладки).
        self.memory = None
        #: Telegram UI stage: after_rec | after_options | after_pick | after_recorded
        self.ui_stage: str | None = None
        #: Option labels for [1][2][3] buttons
        self.option_labels: list[str] = []

    def __bool__(self) -> bool:
        return bool(self.text)


class SellerBrain:

    def __init__(
        self,
        ai_service,
        session,
        context_builder: ContextBuilder,
        memory_store=None,
        learning_brain=None,
        search_service=None,
        outcome_tracker=None,
        public_cache=None,
    ):
        self.ai = ai_service
        self.session = session
        self.context = context_builder
        self.store = memory_store
        self.learning_brain = learning_brain
        self.search_service = search_service
        self.outcome_tracker = outcome_tracker
        self.public_cache = public_cache
        self._memory_builder = MemoryContextBuilder(
            session=session,
            memory_store=memory_store,
            learning_brain=learning_brain,
        )

        self._dialogs: dict[int, DialogMemory] = {}
        self._discussion_dialogs: dict[int, DialogMemory] = {}
        self._discussion_article: dict[int, int] = {}
        #: Последний SOLUTION_RESEARCH на продавца (для выбора / confirm).
        self._last_solutions: dict[int, object] = {}

    # ----------------------------------------------------------------- память

    async def memory(self, user_id: int) -> DialogMemory:
        if user_id not in self._dialogs:
            dialog = DialogMemory()

            if self.store is not None:
                for message in await self.store.last_messages(user_id, limit=DIALOG_DEPTH):
                    if message.role == "user":
                        dialog.add_user(message.content)
                    else:
                        dialog.add_assistant(message.content)

            self._dialogs[user_id] = dialog

        return self._dialogs[user_id]

    def begin_product_discussion(
        self,
        user_id: int,
        article: int,
        *,
        reset: bool = True,
    ) -> None:
        current = self._discussion_article.get(user_id)
        if reset or current != article or user_id not in self._discussion_dialogs:
            self._discussion_dialogs[user_id] = DialogMemory()
            self._discussion_article[user_id] = article
        else:
            self._discussion_article[user_id] = article

    async def hydrate_discussion(self, user_id: int, article: int) -> None:
        """Подтянуть persistent product conversation в working dialog."""
        dialog = self.discussion_memory(user_id)
        if len(dialog) > 0:
            return
        if self.store is None or not hasattr(self.store, "last_product_messages"):
            return
        try:
            rows = await self.store.last_product_messages(
                user_id, article, limit=DIALOG_DEPTH,
            )
        except Exception as exc:
            log.debug("hydrate_discussion failed: %s", exc)
            return
        for message in rows:
            if message.role == "user":
                dialog.add_user(message.content)
            else:
                dialog.add_assistant(message.content)
        # Session mirror
        if hasattr(self.session, "append_discussion_message"):
            for message in rows:
                self.session.append_discussion_message(
                    user_id, message.role, message.content,
                )
        if self.store is not None and hasattr(self.store, "get_conversation_summary"):
            try:
                summary = await self.store.get_conversation_summary(user_id, article)
                if summary and hasattr(self.session, "set_conversation_summary"):
                    self.session.set_conversation_summary(user_id, summary)
            except Exception:
                pass

    def end_product_discussion(self, user_id: int) -> None:
        self._discussion_dialogs.pop(user_id, None)
        self._discussion_article.pop(user_id, None)

    def discussion_memory(self, user_id: int) -> DialogMemory:
        return self._discussion_dialogs.setdefault(user_id, DialogMemory())

    def forget(self, user_id: int) -> None:
        self._dialogs.pop(user_id, None)
        self.end_product_discussion(user_id)

    async def _remember(
        self,
        user_id: int,
        dialog: DialogMemory,
        role: str,
        text: str,
        *,
        persist: bool = True,
        article: int | None = None,
    ) -> None:
        if role == "user":
            dialog.add_user(text)
        else:
            dialog.add_assistant(text)

        if not persist or self.store is None:
            return

        if article is not None and hasattr(self.store, "add_product_message"):
            await self.store.add_product_message(user_id, article, role, text)
        else:
            await self.store.add_message(user_id, role, text)

    async def _maybe_fold_summary(
        self,
        user_id: int,
        article: int | None,
        dialog: DialogMemory,
    ) -> str:
        """Если реплик слишком много — свернуть старые в summary."""
        messages = dialog.messages()
        if len(messages) <= SUMMARY_OVERFLOW:
            current = ""
            if hasattr(self.session, "get_conversation_summary"):
                current = self.session.get_conversation_summary(user_id) or ""
            return current

        overflow = [
            {"role": m.role, "content": m.content}
            for m in messages[:-RECENT_TURNS]
        ]
        keep = messages[-RECENT_TURNS:]
        old_summary = ""
        if hasattr(self.session, "get_conversation_summary"):
            old_summary = self.session.get_conversation_summary(user_id) or ""

        summary = fold_summary(old_summary, overflow)

        # Пересобрать dialog: только recent turns
        dialog.clear()
        for m in keep:
            if m.role == "user":
                dialog.add_user(m.content)
            else:
                dialog.add_assistant(m.content)

        if hasattr(self.session, "set_conversation_summary"):
            self.session.set_conversation_summary(user_id, summary)
        # Session discussion messages — только recent
        if article is not None and hasattr(self.session, "get_discussion_messages"):
            sess = self.session._session(user_id)
            sess["discussion_messages"] = [
                {"role": m.role, "content": m.content} for m in keep
            ]

        if (
            self.store is not None
            and article is not None
            and hasattr(self.store, "save_conversation_summary")
        ):
            try:
                await self.store.save_conversation_summary(user_id, article, summary)
            except Exception as exc:
                log.debug("save_conversation_summary failed: %s", exc)

        return summary

    # ----------------------------------------------------------- быстрый путь

    @staticmethod
    def is_quick(text: str) -> bool:
        return classify(text) is Intent.SMALL_TALK

    # ----------------------------------------------------------------- ответ

    async def reply(
        self,
        user_id: int,
        text: str,
        *,
        force_product_mode: bool = False,
    ) -> BrainReply:
        text = (text or "").strip()
        product = self.session.get_product(user_id)
        article = getattr(product, "article", None) if product is not None else None

        # --- 0. Dialog buffer (до intent) ---
        if force_product_mode and article is not None:
            if self._discussion_article.get(user_id) != article:
                self.begin_product_discussion(user_id, article, reset=True)
            await self.hydrate_discussion(user_id, article)
            dialog = self.discussion_memory(user_id)
            persist = True  # product_conversations
            persist_article = article
        else:
            dialog = await self.memory(user_id)
            persist = True
            persist_article = None

        # --- 1. Единый MemoryContext ДО intent ---
        recent = dialog.to_api(limit=None)
        mem_ctx = await self._memory_builder.build(
            user_id,
            recent_turns=recent,
        )

        intent = classify(text, has_product=product is not None)

        # Finance / procurement — до SOLUTION_RESEARCH
        # («закупиться и посчитать» ≠ «где купить упаковку»).
        fin_raw = None
        if hasattr(self.session, "get_finance_context"):
            fin_raw = self.session.get_finance_context(user_id)
        has_fin_ctx = bool(fin_raw)
        if intent is Intent.FINANCE or should_handle_finance(text, has_ctx=has_fin_ctx):
            intent = Intent.FINANCE
        elif is_solution_research_query(text) or is_solution_choice_query(text):
            intent = Intent.SOLUTION_RESEARCH

        # --- 2. Small talk ---
        if intent is Intent.SMALL_TALK and not force_product_mode:
            answer = smalltalk.reply(text, product=product)
            await self._remember(user_id, dialog, "user", text, persist=persist)
            await self._remember(user_id, dialog, "assistant", answer, persist=persist)
            log.info("intent=%s memory_turns=%d (без модели)", intent.value, len(recent))
            reply = BrainReply(answer, intent, used_model=False)
            reply.memory = mem_ctx
            return reply

        # --- 2b. Decision recall / confirm / implement (deterministic, product-scoped) ---
        if article is not None and (
            is_decision_recall_query(text)
            or is_seller_confirm_choice(text)
            or is_implement_query(text)
        ):
            topic = infer_topic(text)
            why = any(
                k in (text or "").lower().replace("ё", "е")
                for k in ("почему", "зачем именно", "почему этот", "почему эту", "почему выбрали")
            )
            if is_implement_query(text):
                answer = await self._mark_implemented(
                    user_id, int(article), topic, text,
                )
                stage = "after_recorded"
            elif is_seller_confirm_choice(text):
                answer = await self._save_seller_choice(
                    user_id, int(article), topic, text,
                )
                stage = "after_recorded"
            elif why:
                answer = await self._recall_why(user_id, int(article), topic)
                stage = "after_recorded"
            else:
                answer = await self._recall_decision(user_id, int(article), topic)
                stage = "after_recorded"
            await self._remember(
                user_id, dialog, "user", text,
                persist=persist, article=persist_article,
            )
            await self._remember(
                user_id, dialog, "assistant", answer,
                persist=persist, article=persist_article,
            )
            if force_product_mode and hasattr(self.session, "append_discussion_message"):
                self.session.append_discussion_message(user_id, "user", text)
                self.session.append_discussion_message(user_id, "assistant", answer)
            reply = BrainReply(answer, Intent.SOLUTION_RESEARCH, used_model=False)
            reply.memory = mem_ctx
            reply.ui_stage = stage
            return reply

        # --- 2c. Snapshot FAQ (почему / что делать / цену / что решили) — без модели ---
        if article is not None and force_product_mode:
            snap_faq = self._snapshot_faq_answer(user_id, text)
            if snap_faq:
                await self._remember(
                    user_id, dialog, "user", text,
                    persist=persist, article=persist_article,
                )
                await self._remember(
                    user_id, dialog, "assistant", snap_faq,
                    persist=persist, article=persist_article,
                )
                if hasattr(self.session, "append_discussion_message"):
                    self.session.append_discussion_message(user_id, "user", text)
                    self.session.append_discussion_message(user_id, "assistant", snap_faq)
                reply = BrainReply(snap_faq, Intent.PRODUCT_DISCUSSION, used_model=False)
                reply.memory = mem_ctx
                reply.ui_stage = "after_recorded"
                return reply

        # --- 2c2. Knowledge / WB Policy / Action check (no Browser, no LLM) ---
        # Definitions beat finance calculator when phrased as «что такое…».
        from backend.knowledge.chat import (
            is_knowledge_query as _is_kn,
            is_wb_policy_query as _is_pol,
            is_action_check_query as _is_act,
        )
        _kn_priority = _is_kn(text) or _is_pol(text) or _is_act(text)
        if _kn_priority or (
            should_handle_knowledge(text)
            and not should_handle_finance(text, has_ctx=has_fin_ctx)
        ):
            if _kn_priority or not should_handle_finance(text, has_ctx=has_fin_ctx):
                seller_data = None
                try:
                    if hasattr(self.session, "get_seller_data") and article is not None:
                        seller_data = self.session.get_seller_data(user_id, int(article))
                except Exception:
                    seller_data = None
                fin_ctx_obj = None
                if isinstance(fin_raw, dict):
                    try:
                        fin_ctx_obj = FinancialContext.from_dict(fin_raw)
                    except Exception:
                        fin_ctx_obj = None
                kn = handle_knowledge_turn(
                    text,
                    product=product,
                    seller_data=seller_data,
                    finance_ctx=fin_ctx_obj,
                )
                if kn and kn.text:
                    intent_kn = Intent.KNOWLEDGE
                    if kn.kind == "policy":
                        intent_kn = Intent.WB_POLICY
                    elif kn.kind == "action_check":
                        intent_kn = Intent.ACTION_CHECK
                    await self._remember(
                        user_id, dialog, "user", text,
                        persist=persist, article=persist_article,
                    )
                    await self._remember(
                        user_id, dialog, "assistant", kn.text,
                        persist=persist, article=persist_article,
                    )
                    if force_product_mode and hasattr(self.session, "append_discussion_message"):
                        self.session.append_discussion_message(user_id, "user", text)
                        self.session.append_discussion_message(user_id, "assistant", kn.text)
                    reply = BrainReply(kn.text, intent_kn, used_model=False)
                    reply.memory = mem_ctx
                    reply.ui_stage = "after_recorded"
                    return reply

        # --- 2d. Financial / procurement planning (deterministic) ---
        if intent is Intent.FINANCE or should_handle_finance(text, has_ctx=has_fin_ctx):
            fin_reply = await self._finance_reply(user_id, text, mem_ctx=mem_ctx)
            if fin_reply:
                await self._remember(
                    user_id, dialog, "user", text,
                    persist=persist, article=persist_article,
                )
                await self._remember(
                    user_id, dialog, "assistant", fin_reply,
                    persist=persist, article=persist_article,
                )
                if force_product_mode and hasattr(self.session, "append_discussion_message"):
                    self.session.append_discussion_message(user_id, "user", text)
                    self.session.append_discussion_message(user_id, "assistant", fin_reply)
                reply = BrainReply(fin_reply, Intent.FINANCE, used_model=False)
                reply.memory = mem_ctx
                reply.ui_stage = "after_recorded"
                return reply

        # --- 2d2. Dynamic Analytics (history / trends / forecast) ---
        dyn_raw = None
        if hasattr(self.session, "get_dynamics_context"):
            dyn_raw = self.session.get_dynamics_context(user_id)
        has_dyn_ctx = bool(dyn_raw)
        if (
            should_handle_dynamics(text, has_ctx=has_dyn_ctx)
            and not should_handle_finance(text, has_ctx=has_fin_ctx)
        ):
            # Prefer dynamics over generic funnel when explicitly about trends/history
            # or when funnel markers overlap with «почему CTR» dynamics FAQ.
            prefer_dyn = is_dynamics_query(text) or has_dyn_ctx
            if prefer_dyn:
                dyn_reply = await self._dynamics_reply(user_id, text, mem_ctx=mem_ctx)
                if dyn_reply:
                    await self._remember(
                        user_id, dialog, "user", text,
                        persist=persist, article=persist_article,
                    )
                    await self._remember(
                        user_id, dialog, "assistant", dyn_reply,
                        persist=persist, article=persist_article,
                    )
                    if force_product_mode and hasattr(self.session, "append_discussion_message"):
                        self.session.append_discussion_message(user_id, "user", text)
                        self.session.append_discussion_message(user_id, "assistant", dyn_reply)
                    reply = BrainReply(dyn_reply, Intent.SELLER_ANALYTICS, used_model=False)
                    reply.memory = mem_ctx
                    reply.ui_stage = "after_recorded"
                    return reply

        # --- 2e. Funnel + unit economics (deterministic) ---
        fun_raw = None
        if hasattr(self.session, "get_funnel_context"):
            fun_raw = self.session.get_funnel_context(user_id)
        has_fun_ctx = bool(fun_raw)
        if (
            intent is Intent.SELLER_ANALYTICS
            or should_handle_funnel(text, has_ctx=has_fun_ctx)
        ) and not should_handle_finance(text, has_ctx=has_fin_ctx):
            fun_reply = await self._funnel_reply(user_id, text, mem_ctx=mem_ctx)
            if fun_reply:
                await self._remember(
                    user_id, dialog, "user", text,
                    persist=persist, article=persist_article,
                )
                await self._remember(
                    user_id, dialog, "assistant", fun_reply,
                    persist=persist, article=persist_article,
                )
                if force_product_mode and hasattr(self.session, "append_discussion_message"):
                    self.session.append_discussion_message(user_id, "user", text)
                    self.session.append_discussion_message(user_id, "assistant", fun_reply)
                reply = BrainReply(fun_reply, Intent.SELLER_ANALYTICS, used_model=False)
                reply.memory = mem_ctx
                reply.ui_stage = "after_recorded"
                return reply

        # --- 2f. Competitor Intelligence (SearchService evidence → Advisor) ---
        if intent is Intent.COMPETITOR:
            comp_reply = await self._competitor_reply(user_id, text, mem_ctx=mem_ctx)
            if comp_reply:
                await self._remember(
                    user_id, dialog, "user", text,
                    persist=persist, article=persist_article,
                )
                await self._remember(
                    user_id, dialog, "assistant", comp_reply,
                    persist=persist, article=persist_article,
                )
                if force_product_mode and hasattr(self.session, "append_discussion_message"):
                    self.session.append_discussion_message(user_id, "user", text)
                    self.session.append_discussion_message(user_id, "assistant", comp_reply)
                reply = BrainReply(comp_reply, Intent.COMPETITOR, used_model=False)
                reply.memory = mem_ctx
                reply.ui_stage = "after_recorded"
                return reply

        # --- 3. Intent уточнение (не трогает память) ---
        if force_product_mode and product is not None:
            if intent in (Intent.GENERAL_QUESTION, Intent.SMALL_TALK):
                intent = Intent.PRODUCT_DISCUSSION
        elif product is not None and refers_to_product(text):
            if intent is Intent.GENERAL_QUESTION:
                intent = Intent.PRODUCT_DISCUSSION

        # --- 4. Intent-specific context (рынок / reasoner / …) ---
        extra_ctx: dict = {
            "product_mode": force_product_mode,
            "user_hash": mem_ctx.user_hash,
        }
        # Discussion memory: saved Argus diagnosis for price / «что решили»
        if hasattr(self.session, "get_analysis"):
            try:
                analysis = self.session.get_analysis(user_id)
                if isinstance(analysis, dict):
                    snap = analysis.get("diagnosis_snapshot")
                    if snap is None:
                        plan_obj = analysis.get("advisor_plan")
                        if plan_obj is not None and hasattr(plan_obj, "diagnosis_snapshot"):
                            snap = plan_obj.diagnosis_snapshot()
                    if snap:
                        extra_ctx["diagnosis_snapshot"] = snap
            except Exception:
                pass
        request = ContextRequest(
            user_id=user_id,
            text=text,
            intent=intent,
            extra=extra_ctx,
        )
        context = await self.context.build(request)

        # Capture solution research for later choice / memory
        if intent is Intent.SOLUTION_RESEARCH or is_solution_research_query(text):
            cached = None
            if hasattr(self.session, "get_solution_research"):
                cached = self.session.get_solution_research(user_id)
            if cached is not None:
                self._last_solutions[user_id] = cached
                if article is not None:
                    await self._remember_solution_options(
                        user_id, int(article), cached, seller_question=text,
                    )

        # Deterministic choice answer when LLM off / empty options path
        if intent is Intent.SOLUTION_RESEARCH and is_solution_choice_query(text):
            sol = self._last_solutions.get(user_id)
            if sol is None and hasattr(self.session, "get_solution_research"):
                sol = self.session.get_solution_research(user_id)
            if sol is not None:
                # Still call model with context; if model fails — fallback below
                pass

        # --- 5. System = playbook + MemoryContext + context ---
        extra = _merge_prompt_sections(mem_ctx.to_prompt_section(), context, product)
        system = build_system(intent, extra=extra)

        # --- 6. History = единые recent_turns (НЕ зависит от intent) ---
        history = mem_ctx.history_for_api(limit=HISTORY_IN_PROMPT)

        log.info(
            "intent=%s memory_turns=%d summary=%d контекст=%d симв.",
            intent.value,
            len(history),
            len(mem_ctx.conversation_summary or ""),
            len(context),
        )

        answer = await self.ai.generate(text, system=system, history=history)

        ui_stage = None
        option_labels: list[str] = []
        if intent is Intent.SOLUTION_RESEARCH:
            sol = self._last_solutions.get(user_id)
            if sol is None and hasattr(self.session, "get_solution_research"):
                sol = self.session.get_solution_research(user_id)
            if sol is not None and getattr(sol, "options", None):
                option_labels = [
                    str(getattr(o, "label", i + 1))
                    for i, o in enumerate(list(sol.options)[:5])
                ]
                ui_stage = "after_options" if is_solution_choice_query(text) or option_labels else "after_options"
            else:
                ui_stage = "after_rec"

        if answer is None:
            # Fallback for solution research without LLM
            if intent is Intent.SOLUTION_RESEARCH:
                sol = self._last_solutions.get(user_id)
                if sol is None and hasattr(self.session, "get_solution_research"):
                    sol = self.session.get_solution_research(user_id)
                if sol is not None:
                    answer = format_choice_reply(sol)
                else:
                    answer = (
                        "Актуальные предложения недоступны — не выдумываю магазины и цены. "
                        "Скажите, какую проблему закрываем (упаковка, размер…), "
                        "и я дам типы решений и критерии выбора."
                    )
            else:
                reply = BrainReply("", intent, used_model=True)
                reply.memory = mem_ctx
                return reply

        await self._remember(
            user_id, dialog, "user", text,
            persist=persist, article=persist_article,
        )
        await self._remember(
            user_id, dialog, "assistant", answer,
            persist=persist, article=persist_article,
        )

        if force_product_mode and hasattr(self.session, "append_discussion_message"):
            self.session.append_discussion_message(user_id, "user", text)
            self.session.append_discussion_message(user_id, "assistant", answer)

        await self._maybe_fold_summary(user_id, article, dialog)

        reply = BrainReply(answer, intent, used_model=True)
        reply.memory = mem_ctx
        reply.ui_stage = ui_stage
        reply.option_labels = option_labels
        return reply

    async def _remember_solution_options(
        self,
        user_id: int,
        article: int,
        result,
        *,
        seller_question: str = "",
    ) -> None:
        topic = getattr(result, "topic", None) or infer_topic(seller_question)
        options_txt = ""
        options_json = ""
        try:
            opts = list(getattr(result, "options", None) or [])
            options_txt = "\n".join(
                f"{getattr(o, 'label', '?')}) {getattr(o, 'title', '')}"
                for o in opts
            )
            if hasattr(result, "options_json"):
                options_json = result.options_json()
            else:
                import json
                options_json = json.dumps(
                    [o.to_dict() if hasattr(o, "to_dict") else {"title": str(o)} for o in opts],
                    ensure_ascii=False,
                )
        except Exception:
            options_txt = ""
            options_json = ""
        preferred = getattr(result, "preferred_label", None) or ""
        reason = getattr(result, "preferred_reason", "") or ""
        evidence_ids = list(getattr(result, "evidence_ids", None) or [])
        problem_id = getattr(result, "problem_id", None)
        problem_label = getattr(result, "problem_label", None) or topic
        payload = {
            "topic": topic,
            "problem": problem_label,
            "problem_id": problem_id,
            "evidence": ",".join(evidence_ids),
            "recommendation": f"я бы выбрал {preferred}: {reason}" if preferred else "",
            "seller_question": seller_question,
            "solution_options": options_json or options_txt,
            "seller_choice": None,
            "selected_solution_id": getattr(result, "preferred_option_id", None),
            "action": None,
            "outcome": None,
            "status": DecisionStatus.PROPOSED.value,
        }
        if hasattr(self.session, "set_product_decision_cache"):
            self.session.set_product_decision_cache(user_id, article, topic, payload)
        if self.store is not None and hasattr(self.store, "upsert_product_decision"):
            try:
                await self.store.upsert_product_decision(
                    user_id, article, topic,
                    problem=payload["problem"],
                    evidence=payload["evidence"],
                    recommendation=payload["recommendation"],
                    seller_question=seller_question,
                    solution_options=options_json or options_txt,
                    problem_id=problem_id,
                    status=DecisionStatus.PROPOSED.value,
                )
            except Exception as exc:
                log.debug("upsert_product_decision failed: %s", exc)

    def _get_diagnosis_snapshot(self, user_id: int) -> dict | None:
        if not hasattr(self.session, "get_analysis"):
            return None
        try:
            analysis = self.session.get_analysis(user_id)
        except Exception:
            return None
        if not isinstance(analysis, dict):
            return None
        snap = analysis.get("diagnosis_snapshot")
        if snap is None:
            plan_obj = analysis.get("advisor_plan")
            if plan_obj is not None and hasattr(plan_obj, "diagnosis_snapshot"):
                try:
                    snap = plan_obj.diagnosis_snapshot()
                except Exception:
                    snap = None
        return snap if isinstance(snap, dict) else None

    async def _finance_reply(self, user_id: int, text: str, *, mem_ctx=None) -> str | None:
        """Детерминированный financial / procurement planning ответ."""
        raw = self.session.get_finance_context(user_id) if hasattr(
            self.session, "get_finance_context"
        ) else None
        ctx = FinancialContext.from_dict(raw)

        product = self.session.get_product(user_id)
        current = None
        if product is not None and getattr(product, "article", None) is not None:
            price = getattr(product, "price", None)
            seller = None
            if hasattr(self.session, "get_seller_data"):
                seller = self.session.get_seller_data(
                    user_id, article=getattr(product, "article", None),
                )
            if seller is not None and getattr(seller, "price", None) is not None:
                price = seller.price
            current = ProductCandidate(
                article=int(product.article),
                title=str(getattr(product, "title", "") or ""),
                brand=str(getattr(product, "brand", "") or ""),
                price=float(price) if price is not None else None,
                subject=str(getattr(product, "subject_name", "") or ""),
                source="session",
            )
            # seed known seller private costs without inventing
            if seller is not None:
                ctx.merge_from_card(
                    seller_cost=getattr(seller, "cost", None),
                    seller_commission=getattr(seller, "commission", None),
                    seller_ads=getattr(seller, "ad_spend", None),
                )

        history = []
        memory_products = []
        if mem_ctx is not None:
            history = list(getattr(mem_ctx, "relevant_product_history", None) or [])
        if self.store is not None and hasattr(self.store, "list_products"):
            try:
                memory_products = await self.store.list_products(user_id)
            except Exception as exc:
                log.debug("finance list_products failed: %s", exc)

        cands = candidates_from_session(
            current_product=product,
            history=history,
            memory_products=memory_products,
        )

        result = handle_finance_turn(
            text,
            ctx=ctx,
            candidates=cands,
            current=current,
        )
        if hasattr(self.session, "set_finance_context"):
            self.session.set_finance_context(user_id, result.ctx.to_dict())
        return result.text

    async def _funnel_reply(self, user_id: int, text: str, *, mem_ctx=None) -> str | None:
        """Детерминированный funnel + unit economics ответ."""
        raw = self.session.get_funnel_context(user_id) if hasattr(
            self.session, "get_funnel_context"
        ) else None
        ctx = FunnelContext.from_dict(raw)

        product = self.session.get_product(user_id)
        seller = None
        current = None
        if product is not None and getattr(product, "article", None) is not None:
            price = getattr(product, "price", None)
            if hasattr(self.session, "get_seller_data"):
                seller = self.session.get_seller_data(
                    user_id, article=getattr(product, "article", None),
                )
            if seller is not None and getattr(seller, "price", None) is not None:
                price = seller.price
            current = ProductCandidate(
                article=int(product.article),
                title=str(getattr(product, "title", "") or ""),
                brand=str(getattr(product, "brand", "") or ""),
                price=float(price) if price is not None else None,
                subject=str(getattr(product, "subject_name", "") or ""),
                source="session",
            )

        history = []
        memory_products = []
        if mem_ctx is not None:
            history = list(getattr(mem_ctx, "relevant_product_history", None) or [])
        if self.store is not None and hasattr(self.store, "list_products"):
            try:
                memory_products = await self.store.list_products(user_id)
            except Exception as exc:
                log.debug("funnel list_products failed: %s", exc)

        cands = candidates_from_session(
            current_product=product,
            history=history,
            memory_products=memory_products,
        )

        fin_raw = None
        if hasattr(self.session, "get_finance_context"):
            fin_raw = self.session.get_finance_context(user_id)
        finance_ctx = FinancialContext.from_dict(fin_raw) if fin_raw else None

        review_risks = []
        if hasattr(self.session, "get_analysis"):
            try:
                analysis = self.session.get_analysis(user_id)
                if isinstance(analysis, dict):
                    plan = analysis.get("advisor_plan")
                    if plan is not None:
                        probs = getattr(plan, "problems", None) or []
                        review_risks = list(probs)
                    snap = analysis.get("diagnosis_snapshot")
                    if isinstance(snap, dict) and snap.get("problems"):
                        review_risks = list(snap.get("problems") or review_risks)
            except Exception:
                pass

        market_compare = None
        if hasattr(self.session, "get_analysis"):
            try:
                analysis = self.session.get_analysis(user_id) or {}
                plan = analysis.get("advisor_plan") if isinstance(analysis, dict) else None
                meta = getattr(plan, "metadata", None) if plan is not None else None
                if isinstance(meta, dict):
                    market_compare = meta.get("market_compare")
            except Exception:
                pass

        result = handle_funnel_turn(
            text,
            ctx=ctx,
            seller_data=seller,
            product=product,
            finance_ctx=finance_ctx,
            candidates=cands,
            current=current,
            review_risks=review_risks,
            market_compare=market_compare if isinstance(market_compare, dict) else None,
        )
        if hasattr(self.session, "set_funnel_context"):
            self.session.set_funnel_context(user_id, result.ctx.to_dict())
        return result.text

    async def _competitor_reply(self, user_id: int, text: str, *, mem_ctx=None) -> str | None:
        """Deterministic competitor evidence → Advisor UX. Browser = 0."""
        from backend.ai.advisor import build_advisor_plan, compute_unit_economics
        from backend.ai.finance_planner import ProductCandidate, candidates_from_session
        from backend.competitor_intelligence.service import handle_competitor_turn

        product = None
        if hasattr(self.session, "get_product"):
            product = self.session.get_product(user_id)
        seller = None
        if hasattr(self.session, "get_seller_data"):
            art = getattr(product, "article", None) if product is not None else None
            try:
                seller = self.session.get_seller_data(user_id, art)
            except Exception:
                seller = None

        cands = []
        try:
            cands = list(candidates_from_session(self.session, user_id) or [])
        except Exception:
            cands = []
        current = None
        if product is not None and getattr(product, "article", None) is not None:
            current = ProductCandidate(
                article=int(product.article),
                title=str(getattr(product, "title", "") or ""),
                brand=str(getattr(product, "brand", "") or ""),
                price=getattr(product, "price", None),
                subject=str(getattr(product, "subject_name", "") or ""),
            )

        assessment = None
        if hasattr(self.session, "get_analysis"):
            try:
                analysis = self.session.get_analysis(user_id) or {}
                if isinstance(analysis, dict):
                    assessment = analysis.get("review_assessment")
            except Exception:
                assessment = None

        unit = compute_unit_economics(seller, product)
        cached = None
        if hasattr(self.session, "get_competitor_comparison"):
            cached = self.session.get_competitor_comparison(user_id)

        result = await handle_competitor_turn(
            text,
            product=product,
            candidates=cands,
            current=current,
            search_service=self.search_service,
            seller_data=seller,
            review_assessment=assessment,
            unit_econ=unit,
            cached=cached,
            public_cache=self.public_cache,
        )
        if result.clarify:
            return result.text
        if result.comparison is not None and hasattr(self.session, "set_competitor_comparison"):
            self.session.set_competitor_comparison(user_id, result.comparison)

        plan = build_advisor_plan(
            product=product,
            seller_data=seller,
            review_assessment=assessment,
            competitor_comparison=result.comparison,
            competitive_diagnosis=result.diagnosis,
        )
        if plan.has_content():
            return plan.format_plain()
        return result.text

    async def _dynamics_reply(self, user_id: int, text: str, *, mem_ctx=None) -> str | None:
        """Deterministic Dynamic Analytics turn (history / trends / forecast)."""
        product = None
        if hasattr(self.session, "get_product"):
            product = self.session.get_product(user_id)
        seller = None
        if hasattr(self.session, "get_seller_data"):
            art = getattr(product, "article", None) if product is not None else None
            seller = self.session.get_seller_data(user_id, art)

        dyn_raw = None
        if hasattr(self.session, "get_dynamics_context"):
            dyn_raw = self.session.get_dynamics_context(user_id)
        ctx = DynamicsContext.from_dict(dyn_raw)

        fin_raw = None
        if hasattr(self.session, "get_finance_context"):
            fin_raw = self.session.get_finance_context(user_id)
        finance_ctx = None
        if fin_raw:
            finance_ctx = FinancialContext.from_dict(fin_raw)

        # Candidates for «мои белые Nike» — reuse finance resolve
        current = None
        if product is not None and getattr(product, "article", None) is not None:
            price = getattr(product, "price", None)
            if seller is not None and getattr(seller, "price", None) is not None:
                price = seller.price
            current = ProductCandidate(
                article=int(product.article),
                title=str(getattr(product, "title", "") or ""),
                brand=str(getattr(product, "brand", "") or ""),
                price=float(price) if price is not None else None,
                subject=str(getattr(product, "subject_name", "") or ""),
                source="session",
            )

        history = []
        memory_products = []
        if mem_ctx is not None:
            history = list(getattr(mem_ctx, "relevant_product_history", None) or [])
        store = getattr(self.session, "memory_store", None)
        if store is None:
            store = getattr(self, "store", None)
        if store is not None and hasattr(store, "list_products"):
            try:
                memory_products = await store.list_products(user_id)
            except Exception as exc:
                log.debug("dynamics list_products failed: %s", exc)

        cands = candidates_from_session(
            current_product=product,
            history=history,
            memory_products=memory_products,
        )

        # Resolve sticky article early (before loading snaps)
        from backend.ai.finance_planner import resolve_product as _resolve_prod
        resolved_art = getattr(product, "article", None) if product is not None else ctx.article
        try:
            resolved, ambiguous, clarify_msg = _resolve_prod(
                text, cands, current=current,
            )
            if resolved is not None and not ambiguous:
                resolved_art = int(resolved.article)
                ctx.article = resolved_art
                if resolved.title:
                    ctx.product_title = str(resolved.title)
            elif clarify_msg and ambiguous:
                if hasattr(self.session, "set_dynamics_context"):
                    self.session.set_dynamics_context(user_id, ctx.to_dict())
                return clarify_msg
        except Exception:
            pass

        points = []
        article = resolved_art
        if store is not None and article is not None and hasattr(store, "list_metric_snapshots"):
            try:
                rows = await store.list_metric_snapshots(user_id, int(article))
                points = build_series_from_snapshots(rows)
            except Exception:
                points = []

        # Seller metrics for resolved article if still in session
        if (
            seller is None
            and article is not None
            and hasattr(self.session, "get_seller_data")
        ):
            seller = self.session.get_seller_data(user_id, int(article))

        card_healthy = False
        review_risks = []
        if hasattr(self.session, "get_analysis"):
            try:
                analysis = self.session.get_analysis(user_id) or {}
                plan = analysis.get("advisor_plan") if isinstance(analysis, dict) else None
                meta = getattr(plan, "metadata", None) if plan is not None else None
                if isinstance(meta, dict):
                    card_healthy = bool(meta.get("card_healthy"))
                probs = getattr(plan, "problems", None) if plan is not None else None
                if probs:
                    review_risks = list(probs)
                snap = analysis.get("diagnosis_snapshot") if isinstance(analysis, dict) else None
                if isinstance(snap, dict) and snap.get("problems"):
                    review_risks = list(snap.get("problems") or review_risks)
            except Exception:
                pass

        # Short FAQ path when text is specifically why-CTR and we have meta
        low = (text or "").lower().replace("ё", "е")
        if any(k in low for k in ("почему ctr", "почему цтр", "почему конверси", "почему показы")):
            dyn_meta = None
            if hasattr(self.session, "get_analysis"):
                try:
                    analysis = self.session.get_analysis(user_id) or {}
                    plan = analysis.get("advisor_plan") if isinstance(analysis, dict) else None
                    meta = getattr(plan, "metadata", None) if plan is not None else None
                    if isinstance(meta, dict):
                        dyn_meta = meta.get("dynamic_analytics")
                except Exception:
                    dyn_meta = None
            snap = self._get_diagnosis_snapshot(user_id)
            faq = answer_why_ctr(dyn_meta if isinstance(dyn_meta, dict) else None, snap)
            if faq and (dyn_meta or snap):
                if hasattr(self.session, "set_dynamics_context"):
                    self.session.set_dynamics_context(user_id, ctx.to_dict())
                return faq

        result = handle_dynamics_turn(
            text,
            ctx=ctx,
            points=points,
            seller_data=seller,
            product=product,
            finance_ctx=finance_ctx,
            card_healthy=card_healthy,
            candidates=cands,
            current=current,
            review_risks=review_risks,
        )
        if hasattr(self.session, "set_dynamics_context"):
            self.session.set_dynamics_context(user_id, result.ctx.to_dict())
        return result.text

    def _snapshot_faq_answer(self, user_id: int, text: str) -> str | None:
        """
        Короткие follow-up без модели, строго из diagnosis_snapshot:
        почему? / что делать? / а цену менять? / что мы решили?
        """
        raw = (text or "").strip()
        if not raw or len(raw) > 120:
            return None
        low = raw.lower().replace("ё", "е")
        # «что мы решили?» уже идёт через is_decision_recall_query — здесь не дублируем
        if is_decision_recall_query(raw):
            return None
        snap = self._get_diagnosis_snapshot(user_id)
        if not snap:
            return None

        why_q = low in ("почему?", "почему", "почему так?", "почему так", "а почему?")
        do_q = any(
            low.startswith(p) for p in (
                "что делать", "что мне делать", "с чего начать", "что первым",
            )
        ) or low in ("что делать?", "что делать")
        price_q = any(
            k in low for k in (
                "цену менять", "менять цену", "снизить цену", "поднять цену",
                "а цену", "цену трогать", "резать цену",
            )
        )
        ctr_why_q = any(
            k in low for k in (
                "почему ctr", "почему цтр", "почему конверси", "почему показы",
                "почему клик", "а ctr", "а цтр",
            )
        )
        if not (why_q or do_q or price_q or ctr_why_q):
            return None

        if ctr_why_q:
            dyn_meta = None
            try:
                analysis = None
                if hasattr(self.session, "get_analysis"):
                    analysis = self.session.get_analysis(user_id)
                if isinstance(analysis, dict):
                    plan_obj = analysis.get("advisor_plan")
                    meta = getattr(plan_obj, "metadata", None) if plan_obj is not None else None
                    if isinstance(meta, dict):
                        dyn_meta = meta.get("dynamic_analytics")
                    if dyn_meta is None and isinstance(analysis.get("diagnosis_snapshot"), dict):
                        dyn_meta = analysis["diagnosis_snapshot"].get("dynamic_analytics")
            except Exception:
                dyn_meta = None
            faq = answer_why_ctr(
                dyn_meta if isinstance(dyn_meta, dict) else None,
                snap,
            )
            if faq:
                return faq

        main = snap.get("main_problem") or snap.get("diagnosis") or "—"
        do_first = snap.get("do_first") or ""
        actions = [str(a) for a in (snap.get("actions") or []) if a][:3]
        not_rec = [str(n) for n in (snap.get("not_recommended") or []) if n][:4]
        leave = snap.get("leave_alone") or ""
        kind = snap.get("main_problem_kind") or ""
        locus = (snap.get("locus") or "").upper()
        why = snap.get("main_problem_why") or snap.get("confidence_why") or ""

        if why_q:
            lines = ["Почему так (из последнего разбора):", f"• {main}"]
            if why:
                lines.append(f"• {why[:220]}")
            if snap.get("main_problem_proof"):
                lines.append(f"• Опора: {str(snap.get('main_problem_proof'))[:160]}")
            if kind == "no_systemic":
                lines.append("• СИСТЕМНОЙ ПРОБЛЕМЫ НЕ ВИЖУ — подтверждённых рисков нет.")
            return "\n".join(lines)

        if do_q:
            lines = ["Что делать (из последнего разбора):"]
            if do_first:
                lines.append(f"1. {do_first}")
            for i, a in enumerate(actions, 1 if not do_first else 2):
                if do_first and a == do_first:
                    continue
                lines.append(f"{i}. {a}")
            if leave:
                lines.append(f"Не трогать: {leave}")
            elif not_rec:
                lines.append(f"Не трогать: {not_rec[0]}")
            if len(lines) == 1:
                lines.append("Пока нет конкретного шага — сначала закрой пробелы в данных.")
            return "\n".join(lines)

        # price_q
        lines = ["По цене (из последнего разбора, без выдумок):"]
        if locus == "PRICE" or any("цен" in n.lower() and "не" not in n.lower()[:8] for n in not_rec):
            # still respect not_recommended
            pass
        price_blocked = any(
            "цен" in n.lower() for n in not_rec
        ) or ("цен" in leave.lower()) or kind == "no_systemic" or locus not in ("PRICE",)
        if price_blocked and locus != "PRICE":
            lines.append("• Цену сейчас не менять — PRICE-риск не подтверждён.")
            if not_rec:
                hit = next((n for n in not_rec if "цен" in n.lower()), not_rec[0])
                lines.append(f"• {hit}")
            lines.append(f"• Сейчас в фокусе: {main}")
            if do_first:
                lines.append(f"• Сначала: {do_first}")
        else:
            lines.append(f"• Диагноз связан с ценой (locus={locus or '—'}).")
            lines.append(f"• Вывод: {main}")
            if do_first:
                lines.append(f"• Шаг: {do_first}")
            lines.append("• Новую цену не назначаю без seller_price / бенчмарка конкурентов.")
        return "\n".join(lines)

    async def _save_seller_choice(
        self,
        user_id: int,
        article: int,
        topic: str,
        text: str,
    ) -> str:
        sol = self._last_solutions.get(user_id)
        if sol is None and hasattr(self.session, "get_solution_research"):
            sol = self.session.get_solution_research(user_id)
        # Prefer topic from last research / cache over weak infer from "беру её"
        if sol is not None and getattr(sol, "topic", None):
            topic = sol.topic
        else:
            if hasattr(self.session, "get_product_decision_cache"):
                for cand in ("упаковка", topic):
                    cached = self.session.get_product_decision_cache(user_id, article, cand)
                    if cached:
                        topic = cached.get("topic") or cand
                        break

        opts = list(getattr(sol, "options", None) or []) if sol is not None else []
        idx = parse_choice_index(text)  # 1-based
        selected = None
        if idx is not None and 1 <= idx <= len(opts):
            selected = opts[idx - 1]
        elif opts:
            # "беру её" → preferred, else first
            pref = getattr(sol, "preferred_label", None) if sol is not None else None
            for opt in opts:
                if getattr(opt, "label", None) == pref:
                    selected = opt
                    break
            if selected is None:
                selected = opts[0]

        selected_id = getattr(selected, "id", None) if selected is not None else None
        choice_label = getattr(selected, "label", None) if selected is not None else None
        title = getattr(selected, "title", "") if selected is not None else ""

        if selected is not None:
            choice_text = f"{choice_label}) {title}".strip()
        else:
            prev = None
            if hasattr(self.session, "get_product_decision_cache"):
                prev = self.session.get_product_decision_cache(user_id, article, topic)
            if prev and prev.get("recommendation"):
                choice_text = prev.get("seller_choice") or prev.get("recommendation")
                selected_id = prev.get("selected_solution_id") or selected_id
            elif sol is not None and not getattr(sol, "available", True):
                choice_text = (
                    "выбор по критериям (поиск недоступен / без выдуманных магазинов)"
                )
            else:
                choice_text = text.strip() or "выбор продавца"

        action = f"Взять вариант {choice_text}"
        if hasattr(self.session, "set_product_decision_cache"):
            prev = self.session.get_product_decision_cache(user_id, article, topic) or {}
            prev.update({
                "seller_choice": choice_text,
                "selected_solution_id": selected_id,
                "action": action,
                "topic": topic,
                "status": DecisionStatus.SELECTED.value,
            })
            self.session.set_product_decision_cache(user_id, article, topic, prev)
        if self.store is not None and hasattr(self.store, "set_product_decision_choice"):
            try:
                await self.store.set_product_decision_choice(
                    user_id, article, topic, choice_text,
                    action=action,
                    selected_solution_id=selected_id,
                    status=DecisionStatus.SELECTED.value,
                    seller_comment=text.strip()[:200],
                )
            except Exception as exc:
                log.debug("set_product_decision_choice failed: %s", exc)
        return (
            f"Зафиксировал: по теме «{topic}» берём «{choice_text}»"
            + (f" (id={selected_id})." if selected_id else ".")
            + f" Потом спрошу «что мы решили по {topic}?» — отвечу из памяти решения."
        )

    async def _recall_decision(
        self,
        user_id: int,
        article: int,
        topic: str,
    ) -> str:
        # Prefer last research topic if recall is generic
        if topic in ("решение",):
            sol = self._last_solutions.get(user_id)
            if sol is not None and getattr(sol, "topic", None):
                topic = sol.topic
            else:
                topic = "упаковка"
        cached = None
        if hasattr(self.session, "get_product_decision_cache"):
            cached = self.session.get_product_decision_cache(user_id, article, topic)
        row = None
        if self.store is not None and hasattr(self.store, "get_product_decision"):
            try:
                row = await self.store.get_product_decision(user_id, article, topic)
            except Exception as exc:
                log.debug("get_product_decision failed: %s", exc)
        choice = None
        options = ""
        recommendation = ""
        selected_id = None
        status = None
        if row is not None:
            choice = row.seller_choice
            options = row.solution_options or ""
            recommendation = row.recommendation or ""
            selected_id = getattr(row, "selected_solution_id", None)
            status = getattr(row, "status", None)
        elif cached:
            choice = cached.get("seller_choice")
            options = cached.get("solution_options") or ""
            recommendation = cached.get("recommendation") or ""
            selected_id = cached.get("selected_solution_id")
            status = cached.get("status")
        if not choice and not recommendation and not options:
            # Fallback: diagnosis snapshot from last analysis («что мы решили?» without topic choice)
            snap = None
            if hasattr(self.session, "get_analysis"):
                try:
                    analysis = self.session.get_analysis(user_id)
                    if isinstance(analysis, dict):
                        snap = analysis.get("diagnosis_snapshot")
                        if snap is None:
                            plan_obj = analysis.get("advisor_plan")
                            if plan_obj is not None and hasattr(plan_obj, "diagnosis_snapshot"):
                                snap = plan_obj.diagnosis_snapshot()
                except Exception:
                    snap = None
            if snap:
                lines = [
                    f"По «{topic}» отдельного выбора пока нет — опираюсь на последний диагноз ARGUS:",
                    f"• Вывод: {snap.get('main_problem') or snap.get('diagnosis') or '—'}",
                    f"• Делать: {snap.get('do_first') or '—'}",
                    f"• Не делать: {snap.get('leave_alone') or (snap.get('not_recommended') or ['—'])[0]}",
                ]
                actions = list(snap.get("actions") or [])[:2]
                if actions:
                    lines.append("• Действия: " + "; ".join(str(a) for a in actions))
                decisions = list(snap.get("decisions") or [])[:2]
                for d in decisions:
                    if isinstance(d, dict) and (d.get("seller_choice") or d.get("status")):
                        lines.append(
                            f"• Решение «{d.get('topic')}»: "
                            f"{d.get('seller_choice') or d.get('selected_solution_id')} "
                            f"({d.get('status')})"
                        )
                return "\n".join(lines)
            return (
                f"По «{topic}» пока нет сохранённого решения. "
                f"Сначала разберём проблему и варианты — потом зафиксируем выбор."
            )
        lines = [f"По «{topic}» мы решили так:"]
        if choice:
            lines.append(f"• Выбор: {choice}")
        if selected_id:
            lines.append(f"• selected_solution_id: {selected_id}")
        if status:
            lines.append(f"• Статус: {status}")
        if recommendation:
            lines.append(f"• Рекомендация: {recommendation}")
        if options:
            lines.append("• Варианты, которые смотрели:")
            lines.append(options[:500])
        # Enrich with diagnosis snapshot when available
        if hasattr(self.session, "get_analysis"):
            try:
                analysis = self.session.get_analysis(user_id)
                snap = analysis.get("diagnosis_snapshot") if isinstance(analysis, dict) else None
                if snap:
                    lines.append(
                        f"• Диагноз на момент решения: {snap.get('main_problem') or snap.get('diagnosis')}"
                    )
                    if snap.get("do_first"):
                        lines.append(f"• Первый шаг ARGUS: {snap.get('do_first')}")
            except Exception:
                pass
        return "\n".join(lines)

    async def _recall_why(
        self,
        user_id: int,
        article: int,
        topic: str,
    ) -> str:
        if topic in ("решение",):
            topic = "упаковка"
        if self.store is not None and hasattr(self.store, "get_decision_record"):
            try:
                rec = await self.store.get_decision_record(user_id, article, topic)
                if rec is not None:
                    return format_why_selected(rec)
            except Exception as exc:
                log.debug("get_decision_record failed: %s", exc)
        # Fallback to text recall
        base = await self._recall_decision(user_id, article, topic)
        return "Почему именно этот:\n" + base

    async def _mark_implemented(
        self,
        user_id: int,
        article: int,
        topic: str,
        text: str,
    ) -> str:
        """IMPLEMENT status + optional OutcomeTracker hook (no heavy outcome engine)."""
        if topic in ("решение",):
            sol = self._last_solutions.get(user_id)
            topic = getattr(sol, "topic", None) or "упаковка"
        note = (text or "").strip()[:300]
        outcome_id = None
        if self.store is not None and hasattr(self.store, "set_decision_status"):
            try:
                await self.store.set_decision_status(
                    user_id, article, topic,
                    DecisionStatus.IMPLEMENTED.value,
                    seller_comment=note,
                )
            except Exception as exc:
                log.debug("set_decision_status failed: %s", exc)

        # OutcomeTracker hook — record recommendation/action for future review check
        if self.outcome_tracker is not None:
            try:
                from backend.memory.context import make_user_hash
                product = self.session.get_product(user_id) if self.session else None
                category = getattr(product, "subject_name", None) or topic
                decision = None
                if self.store is not None and hasattr(self.store, "get_product_decision"):
                    decision = await self.store.get_product_decision(user_id, article, topic)
                action_txt = (
                    (decision.seller_choice if decision else None)
                    or note
                    or f"implement:{topic}"
                )
                evid = []
                if decision and decision.evidence:
                    evid = [x for x in decision.evidence.split(",") if x.strip()]
                outcome = await self.outcome_tracker.record_recommendation(
                    user_hash=make_user_hash(user_id),
                    category=str(category),
                    article=str(article),
                    recommendation_type="solution_research",
                    recommendation_action=str(action_txt)[:200],
                    recommendation_confidence=0.6,
                    evidence_ids=evid,
                )
                outcome_id = getattr(outcome, "id", None)
                if outcome_id and hasattr(self.outcome_tracker, "record_action"):
                    await self.outcome_tracker.record_action(
                        outcome_id, action_taken=str(action_txt)[:200],
                    )
                if outcome_id and self.store is not None and hasattr(self.store, "set_decision_status"):
                    await self.store.set_decision_status(
                        user_id, article, topic,
                        DecisionStatus.IMPLEMENTED.value,
                        seller_comment=note,
                        outcome_tracker_id=str(outcome_id),
                    )
            except Exception as exc:
                log.debug("OutcomeTracker implement hook failed: %s", exc)

        msg = (
            f"Отметил внедрение по «{topic}» (статус IMPLEMENTED)."
            " Позже можно проверить отзывы — OutcomeTracker готов принять результат."
        )
        if outcome_id:
            msg += f"\noutcome_id={outcome_id}"
        return msg


def _merge_prompt_sections(memory_section: str, context: str, product) -> str:
    parts: list[str] = []
    if memory_section:
        parts.append(memory_section)
    if context:
        parts.append(
            "ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ (intent-specific)\n\n"
            f"{context}"
        )
    if not parts:
        if product is None:
            return (
                "КОНТЕКСТ\n\n"
                "Товар пока не разбирали. Если вопрос требует данных карточки — "
                "попроси прислать ссылку на товар, не выдумывай цифры."
            )
        return ""
    parts.append(
        "Опирайся на ЕДИНУЮ ПАМЯТЬ и дополнительный контекст. "
        "Не выдумывай цифры. Intent меняет задачу, но не товар и не историю."
    )
    return "\n\n".join(parts)
