from __future__ import annotations

import re
from typing import Any, Optional

from app.agent.policy import ConversationPolicyService
from app.agent.scenario_services import (
    InstructionService,
    RoleDiscoveryService,
    SystemDiscoveryService,
)
from app.agent.slot_resolution import SlotResolutionService
from app.agent.state_reducer import StateReducer
from app.agent.turn_plan import SlotResolutionStatus, SlotSourceKind
from app.agent.turn_planner import TurnPlanner
from app.models.api import (
    ChatMessageResponse,
    ConfirmationOption,
    ConfirmationPayload,
    PendingQuestionPayload,
    SearchAnswerPayload,
    SuggestedActionPayload,
)
from app.models.domain import SearchAnswer, ToolAttempt, TurnInterpretation
from app.repositories.search_repository import SearchRepository
from app.rag.service import RagService
from app.services.gigachat import GigaChatClient
from app.services.text import normalize_text, similarity

from ..config import AppConfig


SUPPORT_RECOMMENDATION = (
    "Если роль должна быть доступна по умолчанию, но в АС она не появилась, "
    "сначала проверьте применение ролевой модели и повторную авторизацию. "
    "Если проблема сохраняется, обратитесь в техподдержку или на "
    "Effectiveness@sberbank.ru."
)
STRUCTURED_INTENTS = {"SYSTEM_DISCOVERY", "ROLE_DISCOVERY", "ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"}
ALL_INTENTS = STRUCTURED_INTENTS | {"INSTRUCTION_LOOKUP", "UNKNOWN"}
ALL_DIALOG_ACTS = {
    "PROVIDE_SLOT",
    "SELECT_OPTION",
    "SHOW_MORE",
    "CHANGE_SYSTEM",
    "CHANGE_ROLE",
    "SWITCH_INTENT",
    "RESET_CONTEXT",
    "ASK_HELP",
    "UNKNOWN",
}
ALL_CONTEXT_SHIFTS = {
    "NONE",
    "CHANGE_SYSTEM_FOCUS",
    "CHANGE_ORG_CONTEXT",
    "SWITCH_GOAL",
    "RESET_CONTEXT",
}
PAGE_SIZE = 5
SLOT_AUTOFILL_SCORE = {
    "city": 0.93,
    "department": 0.90,
    "position": 0.90,
}
SLOT_ENTITY_MIN_SCORE = {
    "city": 0.45,
    "department": 0.35,
    "position": 0.45,
}
SYSTEM_ENTITY_MIN_SCORE = 0.45
SLOT_CONFIRM_GAP = 0.10
ROLE_DISCOVERY_LIST_LIMIT = 20
PHASE_BY_SLOT = {
    "position": "COLLECT_POSITION",
    "city": "COLLECT_CITY",
    "department": "COLLECT_DEPARTMENT",
}
ROLE_DISCOVERY_FOLLOWUP = "Если хотите, подскажу, как проверить или запросить доступ к этой или другим АС."
SYSTEM_DISCOVERY_FOLLOWUP = "Если нужна конкретная АС, напишите ее название или выберите ее, и я сразу покажу роли."
INSTRUCTION_OFFER_PROMPT = "Нужна инструкция, как проверить или запросить доступ?"


class ChatAgent:
    def __init__(
        self,
        config: AppConfig,
        search_repository: Optional[SearchRepository] = None,
        rag_service: Optional[RagService] = None,
        gigachat: Optional[GigaChatClient] = None,
    ) -> None:
        self.config = config
        self.search_repository = search_repository or SearchRepository(config)
        self.rag_service = rag_service or RagService(config)
        self.gigachat = gigachat or GigaChatClient(config)
        self.policy_service = ConversationPolicyService(self.search_repository)
        self.turn_planner = TurnPlanner()
        self.slot_resolution_service = SlotResolutionService(self.search_repository)
        self.state_reducer = StateReducer(self.search_repository)
        self.system_discovery_service = SystemDiscoveryService(self.search_repository)
        self.role_discovery_service = RoleDiscoveryService(self.search_repository)
        self.instruction_service = InstructionService(self.search_repository, self.rag_service)
        self._response_prefixes: dict[str, str] = {}

    def start_session(self) -> tuple[str, str]:
        session_id = self.search_repository.create_session()
        greeting = (
            "Опишите вопрос по доступу, роли или инструкции. "
            "Если вопрос про роли, лучше сразу указать АС, должность, город и отдел."
        )
        self.search_repository.add_message(
            session_id,
            "ASSISTANT",
            greeting,
            structured_payload={"type": "greeting"},
        )
        return session_id, greeting

    def get_session_state(self, session_id: str) -> dict[str, Any]:
        session = self.search_repository.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} was not found")
        state = self.search_repository.get_slot_state(session_id)
        feedback = self.search_repository.get_session_feedback(session_id)
        pending_question = self._hydrate_pending_question(state)
        context = self._build_context(state)
        return {
            "session_id": session_id,
            "status": session["status"],
            "current_intent_type": session.get("current_intent_type"),
            "feedback": feedback,
            "resolved": self._resolved_payload(state),
            "context": context,
            "active_goal": state.get("active_goal") or state.get("last_intent_type"),
            "context_shift": state.get("context_shift"),
            "conversation_phase": state.get("conversation_phase"),
            "resume_goal": state.get("resume_goal"),
            "resume_phase": state.get("resume_phase"),
            "instruction_mode": state.get("instruction_mode"),
            "pending_question": pending_question.model_dump() if pending_question else None,
            "state_revision": int(state.get("state_revision") or 0),
            "suggested_actions": [item.model_dump() for item in self._build_suggested_actions(state, pending_question)],
            "messages": self.search_repository.list_messages(session_id),
        }

    def handle_message(self, session_id: str, text: str) -> ChatMessageResponse:
        session = self.search_repository.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} was not found")
        message_id = self.search_repository.add_message(session_id, "USER", text)
        state = self.search_repository.get_slot_state(session_id)
        interpretation = self._interpret_turn(session_id, message_id, text, state)
        interpretation = self._maybe_promote_instruction_query(session_id, text, state, interpretation)
        interpretation = self._maybe_promote_instruction_followup(session_id, text, state, interpretation)
        interpretation = self._maybe_promote_system_discovery_query(session_id, text, state, interpretation)
        self.search_repository.add_turn_interpretation(session_id, message_id, interpretation)
        return self._apply_policy(session_id, text, interpretation)

    def _apply_policy(
        self,
        session_id: str,
        text: str,
        interpretation: TurnInterpretation,
    ) -> ChatMessageResponse:
        state = self.search_repository.get_slot_state(session_id)
        self._coerce_system_show_more_to_change(session_id, state, interpretation, text)
        self._coerce_requested_entitlement_answer(state, interpretation, text)

        instruction_offer_response = self._handle_instruction_offer_pending(session_id, state, text, interpretation)
        if instruction_offer_response is not None:
            return instruction_offer_response
        state = self.search_repository.get_slot_state(session_id)

        if interpretation.dialog_act == "RESET_CONTEXT":
            self.policy_service.reset_context(session_id)
            state = self.search_repository.get_slot_state(session_id)
            if interpretation.intent_type == "UNKNOWN":
                return self._store_assistant_response(
                    session_id=session_id,
                    assistant_text="Контекст сброшен. Опишите новый вопрос по роли, доступу или инструкции.",
                    intent_type="UNKNOWN",
                    dialog_act="RESET_CONTEXT",
                )

        if interpretation.dialog_act in {"SELECT_OPTION", "SHOW_MORE"}:
            pending_question = state.get("pending_question") or {}
            if pending_question.get("kind") == "candidate_selection":
                if interpretation.dialog_act == "SHOW_MORE":
                    response = self._show_more_candidates(session_id, state)
                    if response is not None:
                        return response
                else:
                    state_before_selection = dict(state)
                    response = self._apply_candidate_selection(
                        session_id,
                        state,
                        interpretation,
                        text,
                        silent_on_miss=True,
                    )
                    if response is not None:
                        return response
                    state_after_selection = self.search_repository.get_slot_state(session_id)
                    if self._candidate_selection_was_applied(
                        state_before_selection,
                        state_after_selection,
                        pending_question.get("topic"),
                    ):
                        state = state_after_selection
                        state = self.search_repository.get_slot_state(session_id)
                        active_goal = state.get("active_goal") or interpretation.intent_type or "UNKNOWN"
                        source_text = None
                        return self._continue_structured_goal(
                            session_id,
                            state,
                            source_text,
                            active_goal,
                            interpretation.dialog_act,
                        )

        context_shift_result = self.policy_service.apply_context_shift(session_id, state, interpretation)
        response_prefix = str(context_shift_result.get("response_prefix") or "").strip()
        if response_prefix:
            self._response_prefixes[session_id] = response_prefix
        state = self.search_repository.get_slot_state(session_id)

        self.policy_service.apply_goal_transition(session_id, state, interpretation)
        state = self.search_repository.get_slot_state(session_id)

        if interpretation.dialog_act == "SHOW_MORE":
            response = self._show_more_candidates(session_id, state)
            if response is not None:
                return response

        selection_applied_to_org_slot = False
        if interpretation.dialog_act == "SELECT_OPTION":
            state_before_selection = dict(state)
            pending_before_selection = state.get("pending_question") or {}
            response = self._apply_candidate_selection(session_id, state, interpretation, text)
            if response is not None:
                return response
            state = self.search_repository.get_slot_state(session_id)
            selection_applied_to_org_slot = (
                pending_before_selection.get("topic") in {"position", "city", "department"}
                and self._candidate_selection_was_applied(
                    state_before_selection,
                    state,
                    pending_before_selection.get("topic"),
                )
            )

        entity_result = self._apply_entities(session_id, state, interpretation, text)
        state = self.search_repository.get_slot_state(session_id)
        response_prefix = str(entity_result.get("response_prefix") or "").strip()
        if response_prefix:
            self._response_prefixes[session_id] = response_prefix
        suppress_source_text_for_system = bool(entity_result.get("answered_org_slot")) or selection_applied_to_org_slot
        suggested_intent = entity_result.get("suggested_intent")
        active_goal = state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN"
        if suggested_intent in STRUCTURED_INTENTS and suggested_intent != active_goal:
            goal_stack = list(state.get("goal_stack") or [])
            if active_goal in ALL_INTENTS and active_goal != "UNKNOWN":
                goal_stack = (goal_stack + [active_goal])[-3:]
            updates: dict[str, Any] = {
                "active_goal": suggested_intent,
                "last_intent_type": suggested_intent,
                "goal_stack": goal_stack,
            }
            if suggested_intent == "ROLE_DISCOVERY":
                updates["requested_entitlement_raw"] = None
                updates["requested_entitlement_type_hint"] = None
            self.search_repository.update_slot_state(session_id, **updates)
            self.search_repository.set_session_resolution(session_id, intent_type=suggested_intent)
            state = self.search_repository.get_slot_state(session_id)

        invalid_system_raw = self._clean_slot_text(entity_result.get("invalid_system_raw"))
        active_goal = state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN"
        if invalid_system_raw and active_goal in STRUCTURED_INTENTS:
            if active_goal == "SYSTEM_DISCOVERY":
                active_goal = "ROLE_DISCOVERY"
                goal_stack = list(state.get("goal_stack") or [])
                self.search_repository.update_slot_state(
                    session_id,
                    active_goal=active_goal,
                    last_intent_type=active_goal,
                    goal_stack=goal_stack,
                )
                self.search_repository.set_session_resolution(session_id, intent_type=active_goal)
                state = self.search_repository.get_slot_state(session_id)
            return self._ask_for_slot(
                session_id,
                "system",
                (
                    f"АС '{invalid_system_raw}' не найдена в справочнике. "
                    "Уточните официальное или рабочее название АС."
                ),
                active_goal,
                "PROVIDE_SLOT",
                phase="COLLECT_SYSTEM_HINT",
            )

        system_candidates = list(entity_result.get("system_candidates") or [])
        if system_candidates:
            options = [
                {
                    "option_key": str(candidate["system_id"]),
                    "option_label": candidate["system_name_raw"],
                    "option_payload": {
                        "system_id": candidate["system_id"],
                        "system_name": candidate["system_name_raw"],
                    },
                }
                for candidate in system_candidates
            ]
            return self._prompt_candidate_question(
                session_id=session_id,
                topic="system",
                prompt="Найдено несколько похожих АС. Подтвердите нужный вариант.",
                source_query=self._clean_slot_text(interpretation.entities.get("system_raw")) or text,
                options=options,
                intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                profile_candidates=None,
                phase="BROWSE_SYSTEMS",
            )

        loop_guardrail = self._apply_loop_guardrail(session_id, state, interpretation)
        if loop_guardrail is not None:
            return loop_guardrail

        active_goal = state.get("active_goal") or interpretation.intent_type or "UNKNOWN"
        if active_goal == "INSTRUCTION_LOOKUP":
            return self._answer_instruction_lookup(session_id, state, text, interpretation)

        if active_goal == "UNKNOWN":
            return self._store_assistant_response(
                session_id=session_id,
                assistant_text="Нужна конкретизация. Укажите, пожалуйста, о какой АС, роли или инструкции идет речь.",
                intent_type="UNKNOWN",
                dialog_act=interpretation.dialog_act,
            )

        source_text = None if suppress_source_text_for_system else text
        return self._continue_structured_goal(session_id, state, source_text, active_goal, interpretation.dialog_act)

    def _coerce_system_show_more_to_change(
        self,
        session_id: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
        raw_text: str,
    ) -> None:
        if interpretation.dialog_act != "SHOW_MORE":
            return
        pending_question = state.get("pending_question") or {}
        if pending_question.get("topic") != "system":
            return
        if interpretation.entities.get("system_raw"):
            interpretation.dialog_act = "CHANGE_SYSTEM"
            interpretation.context_shift = "CHANGE_SYSTEM_FOCUS"
            return
        system_raw = self._detect_direct_system_entity(raw_text)
        if not system_raw:
            return
        interpretation.dialog_act = "CHANGE_SYSTEM"
        interpretation.context_shift = "CHANGE_SYSTEM_FOCUS"
        interpretation.entities["system_raw"] = system_raw
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="show_more_system_hint_guardrail",
                attempt_no=1,
                input_payload={"text": raw_text},
                result_status="success",
                result_summary="coerced_to_change_system",
            ),
            {"system_raw": system_raw},
        )

    @staticmethod
    def _coerce_requested_entitlement_answer(
        state: dict[str, Any],
        interpretation: TurnInterpretation,
        raw_text: str,
    ) -> None:
        pending_question = state.get("pending_question") or {}
        if pending_question.get("kind") != "slot_request" or pending_question.get("topic") != "requested_entitlement":
            return
        if interpretation.intent_type in {"INSTRUCTION_LOOKUP", "ROLE_DISCOVERY"} or interpretation.context_shift in {
            "CHANGE_SYSTEM_FOCUS",
            "RESET_CONTEXT",
            "SWITCH_GOAL",
        }:
            return
        interpretation.dialog_act = "PROVIDE_SLOT"
        interpretation.context_shift = "NONE"
        interpretation.entities["requested_entitlement_raw"] = raw_text.strip()
        for org_key in ("position_raw", "city_raw", "department_raw"):
            interpretation.entities.pop(org_key, None)

    def _handle_instruction_offer_pending(
        self,
        session_id: str,
        state: dict[str, Any],
        raw_text: str,
        interpretation: TurnInterpretation,
    ) -> Optional[ChatMessageResponse]:
        pending_question = self._hydrate_pending_question(state)
        if not pending_question or pending_question.kind != "instruction_offer":
            return None
        if interpretation.intent_type == "INSTRUCTION_LOOKUP":
            self._clear_pending_question(session_id)
            updated_state = self.search_repository.get_slot_state(session_id)
            return self._answer_instruction_lookup(session_id, updated_state, raw_text, interpretation)

        decision = self._classify_instruction_offer_response(session_id, state, raw_text, interpretation)
        if decision == "ACCEPT":
            promoted = TurnInterpretation(
                dialog_act=interpretation.dialog_act if interpretation.dialog_act in ALL_DIALOG_ACTS else "ASK_HELP",
                intent_type="INSTRUCTION_LOOKUP",
                entities=dict(interpretation.entities),
                slot_candidates=dict(interpretation.slot_candidates),
                context_shift="SWITCH_GOAL",
                goal_transition="SWITCH",
                needs_clarification=False,
                reasoning_trace_short="instruction_offer_accept",
                confidence=max(interpretation.confidence, 0.8),
                references_pending_question=True,
                user_correction=interpretation.user_correction,
            )
            self._clear_pending_question(session_id)
            updated_state = self.search_repository.get_slot_state(session_id)
            return self._answer_instruction_lookup(session_id, updated_state, raw_text, promoted)

        if decision == "DECLINE":
            self._clear_pending_question(session_id)
            return self._store_assistant_response(
                session_id=session_id,
                assistant_text="Хорошо, продолжаем без инструкции. Если понадобится, напишите.",
                intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                dialog_act=interpretation.dialog_act,
            )
        return None

    def _classify_instruction_offer_response(
        self,
        session_id: str,
        state: dict[str, Any],
        raw_text: str,
        interpretation: TurnInterpretation,
    ) -> str:
        if not self.config.gigachat_use_for_intent or not self.gigachat.enabled:
            return "OTHER"
        system_prompt = (
            "Ты классификатор ответа пользователя на вопрос ассистента о необходимости инструкции. "
            "Верни строго JSON: {decision:string, confidence:number, reason:string}. "
            "decision только один из: ACCEPT, DECLINE, OTHER. "
            "ACCEPT — пользователь согласился получить инструкцию. "
            "DECLINE — пользователь отказался от инструкции. "
            "OTHER — пользователь сменил тему, уточнил контекст или ответ неоднозначен."
        )
        user_prompt = (
            f"Контекст: {{'active_goal': '{state.get('active_goal')}', "
            f"'conversation_phase': '{state.get('conversation_phase')}', "
            f"'interpreted_intent': '{interpretation.intent_type}', "
            f"'interpreted_dialog_act': '{interpretation.dialog_act}'}}\n"
            f"Сообщение пользователя: {raw_text}\n"
            "Верни JSON."
        )
        try:
            payload = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chat_model,
                max_tokens=160,
            )
        except Exception as exc:
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="gigachat_instruction_offer_classifier",
                    attempt_no=1,
                    input_payload={"text": raw_text},
                    result_status="error",
                    result_summary="failed",
                    error_text=str(exc),
                ),
                {"error": str(exc)},
            )
            return "OTHER"
        decision = str((payload or {}).get("decision") or "OTHER").strip().upper()
        confidence = self._normalize_confidence((payload or {}).get("confidence"))
        if decision not in {"ACCEPT", "DECLINE", "OTHER"}:
            decision = "OTHER"
        if decision != "OTHER" and confidence < 0.55:
            decision = "OTHER"
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="gigachat_instruction_offer_classifier",
                attempt_no=1,
                input_payload={"text": raw_text},
                result_status="success",
                result_summary=f"decision={decision}, confidence={confidence:.2f}",
            ),
            payload if isinstance(payload, dict) else {"payload": payload},
        )
        return decision

    def _clear_pending_question(self, session_id: str) -> None:
        self.search_repository.update_slot_state(
            session_id,
            pending_question=None,
            pending_slot=None,
            needs_confirmation=False,
            confirmation_topic=None,
            confirmation_options=None,
        )

    def _apply_loop_guardrail(
        self,
        session_id: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> Optional[ChatMessageResponse]:
        if not interpretation.needs_clarification:
            return None
        pending_question = self._hydrate_pending_question(state)
        if pending_question is None:
            return None
        assistant_messages = [
            row
            for row in self.search_repository.list_messages(session_id)[-6:]
            if str(row.get("role", "")).upper() == "ASSISTANT"
        ]
        repeated = sum(
            1
            for row in assistant_messages
            if normalize_text(str(row.get("message_text") or "")) == normalize_text(pending_question.prompt)
        )
        if repeated < 2:
            return None
        if pending_question.kind == "candidate_selection":
            return self._store_assistant_response(
                session_id=session_id,
                assistant_text=(
                    "Нужен выбор из предложенных вариантов. "
                    "Укажите номер варианта или попросите показать следующую страницу."
                ),
                intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                dialog_act="ASK_HELP",
            )
        return self._store_assistant_response(
            session_id=session_id,
            assistant_text=f"Нужно уточнить поле '{pending_question.topic}'. {pending_question.prompt}",
            intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
            dialog_act="ASK_HELP",
        )

    def _maybe_promote_instruction_followup(
        self,
        session_id: str,
        raw_text: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> TurnInterpretation:
        if interpretation.intent_type == "INSTRUCTION_LOOKUP":
            return interpretation
        active_goal = self._normalize_intent_type(state.get("active_goal") or state.get("last_intent_type"))
        if active_goal not in STRUCTURED_INTENTS:
            return interpretation
        pending_question = self._hydrate_pending_question(state)
        if interpretation.dialog_act in {"SHOW_MORE", "RESET_CONTEXT"}:
            return interpretation
        if interpretation.dialog_act == "SELECT_OPTION" and pending_question and pending_question.kind == "candidate_selection":
            return interpretation
        if interpretation.entities.get("requested_entitlement_raw"):
            return interpretation
        if self._last_assistant_answer_type(session_id) != "ROLE_DISCOVERY":
            return interpretation
        if not self.config.gigachat_use_for_intent or not self.gigachat.enabled:
            return interpretation
        prompt_context = {
            "active_goal": active_goal,
            "conversation_phase": state.get("conversation_phase"),
            "pending_question": pending_question.model_dump() if pending_question else None,
            "system_raw": state.get("system_raw"),
            "city_raw": state.get("city_raw"),
            "position_raw": state.get("position_raw"),
            "department_raw": state.get("department_raw"),
            "interpreted_intent": interpretation.intent_type,
            "interpreted_dialog_act": interpretation.dialog_act,
        }
        system_prompt = (
            "Ты детектор инструкционного запроса в чате по ролевой модели. "
            "Верни строго JSON: {is_instruction_request:boolean, confidence:number, reason:string}. "
            "Ставь true, если пользователь просит объяснить процесс/шаги/инструкцию после выдачи ролей."
        )
        user_prompt = (
            f"Контекст: {prompt_context}\n"
            f"Сообщение пользователя: {raw_text}\n"
            "Верни JSON."
        )
        try:
            payload = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chat_model,
                max_tokens=180,
            )
        except Exception as exc:
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="gigachat_instruction_gate",
                    attempt_no=1,
                    input_payload={"text": raw_text, "active_goal": active_goal},
                    result_status="error",
                    result_summary="failed",
                    error_text=str(exc),
                ),
                {"error": str(exc)},
            )
            return interpretation
        is_instruction = bool(payload.get("is_instruction_request")) if isinstance(payload, dict) else False
        confidence = self._normalize_confidence((payload or {}).get("confidence")) if isinstance(payload, dict) else 0.0
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="gigachat_instruction_gate",
                attempt_no=1,
                input_payload={"text": raw_text, "active_goal": active_goal},
                result_status="success",
                result_summary=f"is_instruction={is_instruction}, confidence={confidence:.2f}",
            ),
            payload if isinstance(payload, dict) else {"payload": payload},
        )
        if not is_instruction or confidence < 0.55:
            return interpretation
        interpretation.intent_type = "INSTRUCTION_LOOKUP"
        interpretation.goal_transition = "SWITCH"
        interpretation.dialog_act = "ASK_HELP"
        interpretation.needs_clarification = False
        interpretation.references_pending_question = True
        return interpretation

    def _maybe_promote_instruction_query(
        self,
        session_id: str,
        raw_text: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> TurnInterpretation:
        if interpretation.intent_type == "INSTRUCTION_LOOKUP":
            return interpretation
        if interpretation.dialog_act in {"SHOW_MORE", "RESET_CONTEXT"}:
            return interpretation
        pending_question = self._hydrate_pending_question(state)
        if pending_question and pending_question.kind == "candidate_selection":
            return interpretation
        if not self.config.gigachat_use_for_intent or not self.gigachat.enabled:
            return interpretation
        prompt_context = {
            "active_goal": state.get("active_goal") or state.get("last_intent_type"),
            "conversation_phase": state.get("conversation_phase"),
            "pending_question": pending_question.model_dump() if pending_question else None,
            "interpreted_intent": interpretation.intent_type,
            "interpreted_dialog_act": interpretation.dialog_act,
        }
        system_prompt = (
            "Ты детектор инструкционного запроса в чате по ролевой модели. "
            "Верни строго JSON: {is_instruction_request:boolean, confidence:number, reason:string}. "
            "Ставь true, если пользователь просит объяснить процесс, шаги, инструкцию, как проверить доступ, как применить ролевую модель, как войти или что делать при ошибке. "
            "Ставь false, если пользователь просит подобрать роли, проверить конкретную роль, получить обоснование или вывести список доступных АС. "
            "Если пользователь спрашивает, какие АС/системы/доступы/роли ему положены, доступны или нужны для работы, это фактический структурный поиск, а не инструкция."
        )
        user_prompt = (
            f"Контекст: {prompt_context}\n"
            f"Сообщение пользователя: {raw_text}\n"
            "Верни JSON."
        )
        try:
            payload = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chat_model,
                max_tokens=160,
            )
        except Exception as exc:
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="gigachat_instruction_query_detector",
                    attempt_no=1,
                    input_payload={"text": raw_text},
                    result_status="error",
                    result_summary="failed",
                    error_text=str(exc),
                ),
                {"error": str(exc)},
            )
            return interpretation
        is_instruction = bool((payload or {}).get("is_instruction_request"))
        confidence = self._normalize_confidence((payload or {}).get("confidence"))
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="gigachat_instruction_query_detector",
                attempt_no=1,
                input_payload={"text": raw_text},
                result_status="success",
                result_summary=f"instruction={is_instruction}, confidence={confidence:.2f}",
            ),
            payload if isinstance(payload, dict) else {"payload": payload},
        )
        if not is_instruction or confidence < 0.60:
            return interpretation
        interpretation.intent_type = "INSTRUCTION_LOOKUP"
        interpretation.goal_transition = "SWITCH"
        interpretation.dialog_act = "ASK_HELP"
        interpretation.context_shift = "SWITCH_GOAL"
        interpretation.needs_clarification = False
        return interpretation

    def _last_assistant_answer_type(self, session_id: str) -> str:
        for row in reversed(self.search_repository.list_messages(session_id)):
            if str(row.get("role", "")).upper() != "ASSISTANT":
                continue
            payload = row.get("structured_payload") or {}
            if not isinstance(payload, dict):
                continue
            answer = payload.get("answer") or {}
            if isinstance(answer, dict):
                answer_type = self._normalize_intent_type(answer.get("answer_type"))
                if answer_type != "UNKNOWN":
                    return answer_type
        return "UNKNOWN"

    def _maybe_promote_system_discovery_query(
        self,
        session_id: str,
        raw_text: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> TurnInterpretation:
        if self._should_force_system_discovery_on_low_confidence(raw_text, state, interpretation):
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="system_discovery_low_confidence_guardrail",
                    attempt_no=1,
                    input_payload={
                        "text": raw_text,
                        "intent_type": interpretation.intent_type,
                        "confidence": interpretation.confidence,
                    },
                    result_status="success",
                    result_summary="force_system_discovery",
                ),
                {"applied": True},
            )
            interpretation.intent_type = "SYSTEM_DISCOVERY"
            interpretation.goal_transition = "SWITCH"
            interpretation.dialog_act = "PROVIDE_SLOT"
            interpretation.needs_clarification = False
            interpretation.context_shift = "SWITCH_GOAL"
            return interpretation
        if interpretation.intent_type == "INSTRUCTION_LOOKUP":
            return interpretation
        if interpretation.entities.get("system_raw"):
            return interpretation
        active_goal = self._normalize_intent_type(state.get("active_goal") or state.get("last_intent_type"))
        if interpretation.intent_type not in {"ROLE_DISCOVERY", "ROLE_ACQUISITION", "SYSTEM_DISCOVERY", "UNKNOWN"}:
            return interpretation
        if not self.config.gigachat_use_for_intent or not self.gigachat.enabled:
            return interpretation
        pending_question = self._hydrate_pending_question(state)
        prompt_context = {
            "active_goal": active_goal,
            "conversation_phase": state.get("conversation_phase"),
            "pending_question": pending_question.model_dump() if pending_question else None,
            "city_raw": state.get("city_raw"),
            "department_raw": state.get("department_raw"),
            "position_raw": state.get("position_raw"),
            "has_resolved_system": bool(state.get("resolved_system_id")),
            "has_interpreted_system_entity": bool(interpretation.entities.get("system_raw")),
            "interpreted_intent": interpretation.intent_type,
            "interpreted_dialog_act": interpretation.dialog_act,
        }
        system_prompt = (
            "Ты детектор сценария запроса в чате по ролевой модели. "
            "Верни строго JSON: {target:string, confidence:number, reason:string}. "
            "target только один из: SYSTEM_LIST, ROLE_LIST, OTHER. "
            "SYSTEM_LIST — пользователь хочет полный список доступных АС по орг-контексту или спрашивает, какие доступы ему положены без конкретной АС. "
            "ROLE_LIST — пользователь хочет роли только в уже выбранной или явно названной конкретной АС. "
            "Если конкретная АС не названа и не выбрана в контексте, не выбирай ROLE_LIST."
        )
        user_prompt = (
            f"Контекст: {prompt_context}\n"
            f"Сообщение пользователя: {raw_text}\n"
            "Верни JSON."
        )
        try:
            payload = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chat_model,
                max_tokens=180,
            )
        except Exception as exc:
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="gigachat_system_discovery_gate",
                    attempt_no=1,
                    input_payload={"text": raw_text, "active_goal": active_goal},
                    result_status="error",
                    result_summary="failed",
                    error_text=str(exc),
                ),
                {"error": str(exc)},
            )
            return interpretation
        if not isinstance(payload, dict):
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="gigachat_system_discovery_gate",
                    attempt_no=1,
                    input_payload={"text": raw_text, "active_goal": active_goal},
                    result_status="error",
                    result_summary="invalid_payload",
                ),
                {"payload": payload},
            )
            return interpretation
        target = str(payload.get("target") or "").strip().upper()
        confidence = self._normalize_confidence(payload.get("confidence"))
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="gigachat_system_discovery_gate",
                attempt_no=1,
                input_payload={"text": raw_text, "active_goal": active_goal},
                result_status="success",
                result_summary=f"target={target or 'UNKNOWN'}, confidence={confidence:.2f}",
            ),
            payload,
        )
        if target != "SYSTEM_LIST" or confidence < 0.55:
            return interpretation
        interpretation.intent_type = "SYSTEM_DISCOVERY"
        interpretation.goal_transition = "SWITCH" if active_goal not in {"UNKNOWN", "SYSTEM_DISCOVERY"} else "START"
        interpretation.dialog_act = "PROVIDE_SLOT"
        interpretation.needs_clarification = False
        interpretation.context_shift = "SWITCH_GOAL"
        return interpretation

    def _should_force_system_discovery_on_low_confidence(
        self,
        raw_text: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> bool:
        if interpretation.intent_type != "ROLE_ACQUISITION":
            return False
        if interpretation.confidence > 0.2:
            return False
        if self._clean_slot_text(interpretation.entities.get("requested_entitlement_raw")):
            return False
        if self._clean_slot_text(interpretation.entities.get("system_raw")) or state.get("resolved_system_id"):
            return False
        if state.get("pending_question"):
            return False
        text = normalize_text(raw_text)
        if not text:
            return False
        has_system_marker = bool(re.search(r"\bас\b|автоматизирован", text))
        has_list_marker = bool(re.search(r"каки|спис|переч|доступ|полож", text))
        return has_system_marker and has_list_marker

    def _interpret_turn(
        self,
        session_id: str,
        message_id: int,
        text: str,
        state: dict[str, Any],
    ) -> TurnInterpretation:
        interpretation = self._interpret_turn_with_llm(session_id, text, state)
        if interpretation is None:
            interpretation = self._interpret_turn_fallback(text, state)
        return self._apply_interpretation_guardrails(interpretation, text, state)

    def _interpret_turn_with_llm(
        self,
        session_id: str,
        text: str,
        state: dict[str, Any],
    ) -> Optional[TurnInterpretation]:
        if not self.config.gigachat_use_for_intent or not self.gigachat.enabled:
            return None
        pending_question = self._hydrate_pending_question(state)
        history: list[dict[str, str]] = []
        for row in self.search_repository.list_messages(session_id)[-8:]:
            role = str(row.get("role", "")).upper()
            if role not in {"USER", "ASSISTANT"}:
                continue
            history.append(
                {
                    "role": "user" if role == "USER" else "assistant",
                    "text": str(row.get("message_text") or "")[:400],
                }
            )
        context = {
            "active_goal": state.get("active_goal") or state.get("last_intent_type"),
            "resume_goal": state.get("resume_goal"),
            "conversation_phase": state.get("conversation_phase"),
            "resume_phase": state.get("resume_phase"),
            "context_shift": state.get("context_shift"),
            "system_raw": state.get("system_raw"),
            "system_query_raw": state.get("system_query_raw"),
            "system_resolution_mode": state.get("system_resolution_mode"),
            "resolved_system_id": state.get("resolved_system_id"),
            "city_raw": state.get("city_raw"),
            "department_raw": state.get("department_raw"),
            "position_raw": state.get("position_raw"),
            "requested_entitlement_raw": state.get("requested_entitlement_raw"),
            "pending_question": pending_question.model_dump() if pending_question else None,
            "history": history,
        }
        system_prompt = (
            "Ты интерпретатор реплик пользователя для чат-агента по ролевой модели. "
            "Верни строго JSON с ключами: dialog_act, intent_type, entities, slot_candidates, "
            "context_shift, confidence, goal_transition, needs_clarification, references_pending_question, "
            "user_correction, reasoning_trace_short. "
            "dialog_act должен быть одним из: PROVIDE_SLOT, SELECT_OPTION, SHOW_MORE, "
            "CHANGE_SYSTEM, CHANGE_ROLE, SWITCH_INTENT, RESET_CONTEXT, ASK_HELP, UNKNOWN. "
            "intent_type должен быть одним из: SYSTEM_DISCOVERY, ROLE_DISCOVERY, ROLE_ACQUISITION, "
            "JUSTIFICATION_LOOKUP, INSTRUCTION_LOOKUP, UNKNOWN. "
            "context_shift должен быть одним из: NONE, CHANGE_SYSTEM_FOCUS, CHANGE_ORG_CONTEXT, SWITCH_GOAL, RESET_CONTEXT. "
            "В entities используй только ключи: system_raw, position_raw, city_raw, "
            "department_raw, requested_entitlement_raw, selection_text, selection_number. "
            "Если пользователь упоминает возможное рабочее название АС, аббревиатуру или часть названия, "
            "заполни system_raw этим фрагментом, даже если АС не полная и требует выбора из справочника. "
            "Не записывай в system_raw город, должность или отдел. "
            "slot_candidates — объект по слотам system/position/city/department/profile с массивами кандидатов "
            "вида {value, score}. Если кандидатов нет, верни пустой объект. "
            "goal_transition должен быть одним из: STAY, START, SWITCH. "
            "needs_clarification=true, если реплика не дает уверенного следующего шага. "
            "Определяй смысл реплики по контексту диалога и pending_question, без опоры на буквальное совпадение фраз."
        )
        user_prompt = (
            f"Контекст: {context}\n"
            f"Сообщение пользователя: {text}\n"
            "Верни JSON."
        )
        try:
            payload = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chat_model,
                max_tokens=400,
            )
        except Exception as exc:
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="gigachat_interpret_turn",
                    attempt_no=1,
                    input_payload={"text": text},
                    result_status="error",
                    result_summary="failed",
                    error_text=str(exc),
                ),
                {"error": str(exc)},
            )
            return None
        if not isinstance(payload, dict):
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="gigachat_interpret_turn",
                    attempt_no=1,
                    input_payload={"text": text},
                    result_status="error",
                    result_summary="invalid_payload",
                ),
                {"payload": payload},
            )
            return None
        interpretation = TurnInterpretation(
            dialog_act=self._normalize_dialog_act(payload.get("dialog_act")),
            intent_type=self._normalize_intent_type(payload.get("intent_type")),
            entities=self._normalize_entities(payload.get("entities")),
            slot_candidates=self._normalize_slot_candidates(payload.get("slot_candidates")),
            context_shift=self._normalize_context_shift(payload.get("context_shift")),
            goal_transition=self._normalize_goal_transition(payload.get("goal_transition")),
            needs_clarification=bool(payload.get("needs_clarification")),
            reasoning_trace_short=self._clean_slot_text(payload.get("reasoning_trace_short")),
            confidence=self._normalize_confidence(payload.get("confidence")),
            references_pending_question=bool(payload.get("references_pending_question")),
            user_correction=bool(payload.get("user_correction")),
        )
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="gigachat_interpret_turn",
                attempt_no=1,
                input_payload={"text": text},
                result_status="success",
                result_summary=interpretation.dialog_act,
            ),
            {
                "dialog_act": interpretation.dialog_act,
                "intent_type": interpretation.intent_type,
                "entities": interpretation.entities,
                "slot_candidates": interpretation.slot_candidates,
                "context_shift": interpretation.context_shift,
                "confidence": interpretation.confidence,
                "goal_transition": interpretation.goal_transition,
                "needs_clarification": interpretation.needs_clarification,
                "references_pending_question": interpretation.references_pending_question,
            },
        )
        return interpretation

    def _interpret_turn_fallback(self, text: str, state: dict[str, Any]) -> TurnInterpretation:
        pending_question = state.get("pending_question") or {}
        active_goal = self._normalize_intent_type(state.get("active_goal") or state.get("last_intent_type"))
        entities: dict[str, Any] = {}
        dialog_act = "ASK_HELP"
        references_pending_question = bool(pending_question)

        if pending_question.get("kind") == "slot_request":
            topic = pending_question.get("topic")
            slot_key = self._slot_key_for_topic(topic)
            if slot_key:
                entities[slot_key] = text.strip()
                dialog_act = "PROVIDE_SLOT"
        elif pending_question.get("kind") == "candidate_selection":
            number_match = re.fullmatch(r"\s*(\d+)\s*", text)
            if number_match:
                entities["selection_number"] = int(number_match.group(1))
                dialog_act = "SELECT_OPTION"
            else:
                entities["selection_text"] = text.strip()
                dialog_act = "SELECT_OPTION"
        return TurnInterpretation(
            dialog_act=dialog_act,
            intent_type=active_goal if active_goal != "UNKNOWN" else "UNKNOWN",
            entities=self._normalize_entities(entities),
            slot_candidates={},
            context_shift="NONE",
            goal_transition="STAY",
            needs_clarification=dialog_act == "ASK_HELP",
            reasoning_trace_short="fallback_without_llm",
            confidence=0.3,
            references_pending_question=references_pending_question,
        )

    def _interpret_turn_heuristic(self, text: str, state: dict[str, Any]) -> TurnInterpretation:
        """Backward-compatible wrapper for existing tests."""
        llm = self._interpret_turn_with_llm("test-session", text, state)
        if llm is not None:
            return self._apply_interpretation_guardrails(llm, text, state)
        return self._interpret_turn_fallback(text, state)

    def _apply_interpretation_guardrails(
        self,
        interpretation: TurnInterpretation,
        raw_text: str,
        state: dict[str, Any],
    ) -> TurnInterpretation:
        pending_question = state.get("pending_question") or {}
        active_goal = self._normalize_intent_type(state.get("active_goal") or state.get("last_intent_type"))
        text_value = self._clean_slot_text(raw_text) or ""

        interpretation.dialog_act = self._normalize_dialog_act(interpretation.dialog_act)
        interpretation.intent_type = self._normalize_intent_type(interpretation.intent_type)
        interpretation.entities = self._normalize_entities(interpretation.entities)
        interpretation.goal_transition = self._normalize_goal_transition(interpretation.goal_transition)
        interpretation.slot_candidates = self._normalize_slot_candidates(interpretation.slot_candidates)
        interpretation.context_shift = self._normalize_context_shift(interpretation.context_shift)
        interpretation.confidence = self._normalize_confidence(interpretation.confidence)
        interpretation.reasoning_trace_short = self._clean_slot_text(interpretation.reasoning_trace_short)

        if interpretation.intent_type == "UNKNOWN" and active_goal != "UNKNOWN":
            interpretation.intent_type = active_goal

        if pending_question.get("kind") == "slot_request":
            slot_key = self._slot_key_for_topic(pending_question.get("topic"))
            if (
                slot_key
                and not interpretation.entities.get(slot_key)
                and text_value
                and interpretation.dialog_act in {"PROVIDE_SLOT", "UNKNOWN", "ASK_HELP"}
            ):
                interpretation.entities[slot_key] = text_value
            if interpretation.dialog_act in {"UNKNOWN", "ASK_HELP"} and interpretation.entities.get(slot_key):
                interpretation.dialog_act = "PROVIDE_SLOT"

        if pending_question.get("kind") == "candidate_selection":
            if (
                interpretation.intent_type != "INSTRUCTION_LOOKUP"
                and interpretation.dialog_act in {"PROVIDE_SLOT", "ASK_HELP"}
                and text_value
            ):
                interpretation.dialog_act = "SELECT_OPTION"
                if "selection_number" not in interpretation.entities and re.fullmatch(r"\s*\d+\s*", text_value):
                    interpretation.entities["selection_number"] = int(text_value.strip())
                elif "selection_text" not in interpretation.entities:
                    interpretation.entities["selection_text"] = text_value
            if interpretation.dialog_act == "UNKNOWN":
                if interpretation.intent_type == "INSTRUCTION_LOOKUP":
                    interpretation.dialog_act = "ASK_HELP"
                elif "selection_number" in interpretation.entities or "selection_text" in interpretation.entities:
                    interpretation.dialog_act = "SELECT_OPTION"
                elif text_value:
                    interpretation.dialog_act = "SELECT_OPTION"
                    interpretation.entities["selection_text"] = text_value
            if interpretation.dialog_act == "SHOW_MORE":
                interpretation.references_pending_question = True

        if interpretation.dialog_act == "SHOW_MORE" and pending_question.get("kind") != "candidate_selection":
            interpretation.dialog_act = "ASK_HELP"
            interpretation.needs_clarification = True

        if interpretation.goal_transition is None:
            if active_goal == "UNKNOWN" and interpretation.intent_type != "UNKNOWN":
                interpretation.goal_transition = "START"
            elif interpretation.intent_type != "UNKNOWN" and interpretation.intent_type != active_goal:
                interpretation.goal_transition = "SWITCH"
            else:
                interpretation.goal_transition = "STAY"

        if interpretation.context_shift == "NONE":
            interpretation.context_shift = self._infer_context_shift(state, interpretation, text_value)

        if interpretation.dialog_act == "UNKNOWN":
            interpretation.dialog_act = "ASK_HELP"
            interpretation.needs_clarification = True
        return interpretation

    def _apply_goal_transition(
        self,
        session_id: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> None:
        current_goal = state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN"
        next_goal = current_goal if current_goal in ALL_INTENTS else "UNKNOWN"
        goal_stack = list(state.get("goal_stack") or [])
        interpreted_goal = interpretation.intent_type if interpretation.intent_type in ALL_INTENTS else "UNKNOWN"
        pending_question = state.get("pending_question") or {}
        goal_transition = self._normalize_goal_transition(interpretation.goal_transition) or "STAY"

        if goal_transition == "START" and interpreted_goal != "UNKNOWN":
            next_goal = interpreted_goal
        elif goal_transition == "SWITCH" and interpreted_goal != "UNKNOWN" and interpreted_goal != next_goal:
            if next_goal != "UNKNOWN":
                goal_stack = (goal_stack + [next_goal])[-3:]
            next_goal = interpreted_goal
        elif (
            next_goal in {"ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"}
            and pending_question.get("topic") == "requested_entitlement"
            and interpreted_goal == "ROLE_DISCOVERY"
        ):
            if next_goal != "UNKNOWN":
                goal_stack = (goal_stack + [next_goal])[-3:]
            next_goal = interpreted_goal
        elif (
            goal_transition == "STAY"
            and next_goal in STRUCTURED_INTENTS
            and interpreted_goal == "INSTRUCTION_LOOKUP"
        ):
            goal_stack = (goal_stack + [next_goal])[-3:]
            next_goal = interpreted_goal
        elif goal_transition == "STAY" and next_goal == "INSTRUCTION_LOOKUP" and interpreted_goal in STRUCTURED_INTENTS:
            next_goal = interpreted_goal
        elif goal_transition == "SWITCH" and next_goal in STRUCTURED_INTENTS and interpreted_goal == "INSTRUCTION_LOOKUP":
            goal_stack = (goal_stack + [next_goal])[-3:]
            next_goal = interpreted_goal

        self.search_repository.update_slot_state(
            session_id,
            active_goal=next_goal,
            goal_stack=goal_stack,
            last_intent_type=next_goal,
        )
        self.search_repository.set_session_resolution(session_id, intent_type=next_goal)

    def _apply_entities(
        self,
        session_id: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
        raw_text: str,
    ) -> dict[str, Any]:
        original_state = dict(state)
        entities = dict(interpretation.entities)
        pending_question = state.get("pending_question") or {}
        result: dict[str, Any] = {}
        validation_messages: list[str] = []
        pending_topic = str(pending_question.get("topic") or "")
        if (
            pending_question.get("kind") == "slot_request"
            and pending_topic in {"position", "city", "department"}
            and entities.get(f"{pending_topic}_raw")
        ):
            result["answered_org_slot"] = True
        allow_direct_system_detection = pending_topic not in {"position", "city", "department", "requested_entitlement"}
        if allow_direct_system_detection and not entities.get("system_raw") and not state.get("resolved_system_id"):
            direct_system = self._detect_direct_system_entity(raw_text)
            if direct_system:
                entities["system_raw"] = direct_system
        mixed_result = self._parse_mixed_slot_input(raw_text, state, interpretation)
        if mixed_result.get("used"):
            for key, value in mixed_result.get("entities", {}).items():
                entities.setdefault(key, value)
            if mixed_result.get("suggested_intent"):
                result["suggested_intent"] = mixed_result["suggested_intent"]
        role_like_system = self._coerce_system_from_role_like_reference(
            interpretation=interpretation,
            entities=entities,
            state=state,
        )
        if role_like_system:
            entities.pop("requested_entitlement_raw", None)
            if not entities.get("system_raw") and not state.get("resolved_system_id") and not state.get("system_raw"):
                entities["system_raw"] = role_like_system
            if interpretation.intent_type in {"ROLE_ACQUISITION", "UNKNOWN"}:
                result["suggested_intent"] = "ROLE_DISCOVERY"
        system_from_access_issue = self._coerce_generic_access_issue_to_system(
            interpretation=interpretation,
            entities=entities,
            state=state,
        )
        if system_from_access_issue:
            entities["system_raw"] = system_from_access_issue
            entities.pop("requested_entitlement_raw", None)
            if interpretation.intent_type in {"ROLE_ACQUISITION", "UNKNOWN"}:
                result["suggested_intent"] = "ROLE_DISCOVERY"

        if (
            pending_question.get("kind") == "slot_request"
            and not mixed_result.get("used")
            and interpretation.dialog_act == "PROVIDE_SLOT"
        ):
            slot_key = self._slot_key_for_topic(pending_question.get("topic"))
            if slot_key and not entities.get(slot_key):
                entities[slot_key] = raw_text.strip()

        if (
            pending_question.get("kind") == "slot_request"
            and pending_question.get("topic") == "system"
            and self._is_unknown_system_signal(raw_text)
        ):
            self.search_repository.update_slot_state(
                session_id,
                system_raw=None,
                system_query_raw=None,
                system_resolution_mode=None,
                resolved_system_id=None,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, system_id=None)
            result["suggested_intent"] = "SYSTEM_DISCOVERY"
            result["response_prefix"] = (
                "Точная АС не указана. Сначала соберу орг-контекст и покажу полный список доступных вам АС."
            )
            state = self.search_repository.get_slot_state(session_id)
            entities.pop("system_raw", None)

        if (
            pending_question.get("kind") == "slot_request"
            and pending_question.get("topic") == "requested_entitlement"
            and interpretation.intent_type not in {"INSTRUCTION_LOOKUP", "ROLE_DISCOVERY"}
            and interpretation.context_shift not in {"CHANGE_SYSTEM_FOCUS", "CHANGE_ORG_CONTEXT", "SWITCH_GOAL", "RESET_CONTEXT"}
        ):
            entities["requested_entitlement_raw"] = raw_text.strip()
            for org_key in ("position_raw", "city_raw", "department_raw"):
                entities.pop(org_key, None)

        system_raw = self._clean_slot_text(entities.get("system_raw"))
        if system_raw:
            resolution_mode = mixed_result.get("system_resolution_mode") or self._infer_system_resolution_mode(system_raw)
            if resolution_mode == "BROWSE":
                browse_candidates = self.search_repository.browse_system_candidates(
                    query_text=system_raw,
                    city=state.get("city_raw"),
                    department=state.get("department_raw"),
                    position=state.get("position_raw"),
                    limit=5,
                )
                best_browse_score = float(browse_candidates[0].get("score") or 0.0) if browse_candidates else 0.0
                self.search_repository.log_tool_call(
                    session_id,
                    ToolAttempt(
                        tool_name="validate_broad_system_hint",
                        attempt_no=1,
                        input_payload={"slot_name": "system", "slot_value": system_raw},
                        result_status="success",
                        result_summary=f"{len(browse_candidates)} candidates",
                    ),
                    {"candidates": browse_candidates[:5]},
                )
                if browse_candidates and best_browse_score >= 0.45:
                    if similarity(system_raw, state.get("system_raw")) < 0.98:
                        self.search_repository.close_candidate_sets(session_id, topics=["system", "profile"])
                        self.search_repository.update_slot_state(
                            session_id,
                            system_raw=system_raw,
                            system_query_raw=system_raw,
                            system_resolution_mode="BROWSE",
                            resolved_system_id=None,
                            resolved_profile_id=None,
                            profile_candidates=None,
                            instruction_mode=None,
                            pending_question=None,
                            pending_slot=None,
                            needs_confirmation=False,
                            confirmation_topic=None,
                            confirmation_options=None,
                        )
                        self.search_repository.set_session_resolution(session_id, system_id=None, profile_id=None)
                        state = self.search_repository.get_slot_state(session_id)
                    if (state.get("active_goal") or state.get("last_intent_type")) == "SYSTEM_DISCOVERY":
                        result["suggested_intent"] = "ROLE_DISCOVERY"
                    system_raw = None
                else:
                    self._log_entity_validation_reject(
                        session_id,
                        "system",
                        system_raw,
                        "not_found_in_system_dictionary",
                    )
                    validation_messages.append(
                        f"Не нашел '{system_raw}' в справочнике АС, поэтому не записал это значение как АС."
                    )
                    result["invalid_system_raw"] = system_raw
                    entities.pop("system_raw", None)
                    system_raw = None

        if system_raw:
            resolution = self.slot_resolution_service.resolve_system(system_raw, state, SlotSourceKind.LLM_ENTITY)
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="resolve_slot_system",
                    attempt_no=1,
                    input_payload={"slot_name": "system", "slot_value": system_raw},
                    result_status="success",
                    result_summary=resolution.status.value,
                ),
                {
                    "reason": resolution.reason,
                    "confidence": resolution.confidence,
                    "candidates": resolution.candidates[:5],
                },
            )
            if resolution.status == SlotResolutionStatus.ACCEPTED:
                previous_system = state.get("system_raw")
                self.state_reducer.apply_slot_resolution(session_id, resolution)
                state = self.search_repository.get_slot_state(session_id)
                if (state.get("active_goal") or state.get("last_intent_type")) == "SYSTEM_DISCOVERY" or (
                    previous_system and similarity(system_raw, previous_system) < 0.98
                ):
                    result["suggested_intent"] = "ROLE_DISCOVERY"
            elif resolution.status == SlotResolutionStatus.CANDIDATES:
                result["system_candidates"] = resolution.candidates
            elif resolution.status == SlotResolutionStatus.REJECTED:
                self._log_entity_validation_reject(
                    session_id,
                    "system",
                    system_raw,
                    resolution.reason or "not_found_in_system_dictionary",
                )
                self.state_reducer.apply_rejected_slot(session_id, resolution)
                validation_messages.append(
                    f"Не нашел '{system_raw}' в справочнике АС, поэтому не записал это значение как АС."
                )
                result["invalid_system_raw"] = system_raw
                entities.pop("system_raw", None)
                system_raw = None

        role_raw = self._clean_slot_text(entities.get("requested_entitlement_raw"))
        if role_raw and similarity(role_raw, state.get("requested_entitlement_raw")) < 0.98:
            self.search_repository.update_slot_state(
                session_id,
                requested_entitlement_raw=role_raw,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            state = self.search_repository.get_slot_state(session_id)
        elif interpretation.intent_type == "ROLE_DISCOVERY" and state.get("requested_entitlement_raw"):
            self.search_repository.update_slot_state(
                session_id,
                requested_entitlement_raw=None,
                requested_entitlement_type_hint=None,
            )
            state = self.search_repository.get_slot_state(session_id)
        effective_goal = state.get("active_goal") or state.get("last_intent_type") or interpretation.intent_type
        effective_system = (
            self._clean_slot_text(entities.get("system_raw"))
            or self._clean_slot_text(state.get("system_raw"))
            or state.get("resolved_system_id")
        )
        if (
            effective_goal == "ROLE_ACQUISITION"
            and not self._clean_slot_text(state.get("requested_entitlement_raw"))
            and not role_raw
            and effective_system
        ):
            result["suggested_intent"] = "ROLE_DISCOVERY"

        updates: dict[str, Any] = {}
        for topic in ("position", "city", "department"):
            value = self._clean_slot_text(entities.get(f"{topic}_raw"))
            if not value:
                continue
            validation = self._validate_entity_value_for_org_slot(session_id, topic, value, state)
            if not validation["valid"]:
                self._log_entity_validation_reject(
                    session_id,
                    topic,
                    value,
                    str(validation.get("reason") or "not_found_in_slot_dictionary"),
                )
                validation_messages.append(
                    f"Не нашел '{value}' в справочнике для поля '{self._slot_label(topic)}', "
                    "поэтому не записал это значение в контекст."
                )
                continue
            value = validation.get("canonical_value") or value
            current_value = state.get(f"{topic}_raw")
            if similarity(value, current_value) < 0.98:
                updates[f"{topic}_raw"] = value
                updates[f"{topic}_normalized"] = normalize_text(value)

        if updates:
            updates.update(
                {
                    "resolved_profile_id": None,
                    "profile_candidates": None,
                    "pending_question": None,
                    "pending_slot": None,
                    "needs_confirmation": False,
                    "confirmation_topic": None,
                    "confirmation_options": None,
                }
            )
            self.search_repository.close_candidate_sets(session_id, topics=["profile"])
            self.search_repository.update_slot_state(session_id, **updates)
            self.search_repository.set_session_resolution(session_id, profile_id=None)
        if mixed_result.get("used"):
            final_state = self.search_repository.get_slot_state(session_id)
            response_prefix = self._build_mixed_state_prefix(
                original_state,
                final_state,
                list(mixed_result.get("unresolved", [])),
            )
            if response_prefix:
                validation_messages.insert(0, response_prefix)
        if validation_messages:
            result["response_prefix"] = " ".join(validation_messages)
        return result

    def _show_more_candidates(self, session_id: str, state: dict[str, Any]) -> Optional[ChatMessageResponse]:
        pending_question = self._hydrate_pending_question(state)
        if not pending_question or pending_question.kind != "candidate_selection" or not pending_question.candidate_set_id:
            return None
        current_page = self.search_repository.get_candidate_page(int(pending_question.candidate_set_id))
        if (
            pending_question.topic == "system"
            and state.get("conversation_phase") == "BROWSE_SYSTEMS"
            and current_page
            and not current_page.get("has_more")
        ):
            prompt = (
                "Других АС по текущему запросу в справочнике больше нет. "
                "Напишите рабочее название, назначение или ключевые слова по системе."
            )
            return self._ask_for_slot(
                session_id,
                "system",
                prompt,
                state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                "SHOW_MORE",
                phase="COLLECT_SYSTEM_HINT",
            )
        previous_offset = pending_question.page_offset
        page = self.search_repository.advance_candidate_set(pending_question.candidate_set_id)
        if not page:
            return None
        assistant_text = (
            "Это все найденные варианты. Показываю список с начала."
            if int(page["page_offset"]) == 0 and previous_offset > 0
            else (
                "Показываю следующие варианты АС."
                if pending_question.topic == "system"
                else f"Показываю следующие варианты для '{pending_question.topic}'."
            )
        )
        self._sync_pending_question_compatibility(session_id, state.get("pending_question"))
        return self._store_assistant_response(
            session_id=session_id,
            assistant_text=assistant_text,
            intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
            dialog_act="SHOW_MORE",
        )

    def _apply_candidate_selection(
        self,
        session_id: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
        raw_text: str,
        silent_on_miss: bool = False,
    ) -> Optional[ChatMessageResponse]:
        pending_question = self._hydrate_pending_question(state)
        if not pending_question or pending_question.kind != "candidate_selection" or not pending_question.candidate_set_id:
            return None
        selection_number = interpretation.entities.get("selection_number")
        selection_number = int(selection_number) if isinstance(selection_number, int) else None
        selection_text = self._clean_slot_text(interpretation.entities.get("selection_text") or raw_text)
        option = self.search_repository.resolve_candidate_selection(
            pending_question.candidate_set_id,
            selection_text=selection_text,
            selection_number=selection_number,
        )
        if not option:
            if silent_on_miss:
                return None
            return self._store_assistant_response(
                session_id=session_id,
                assistant_text="Не удалось распознать вариант. Выберите один из предложенных или попросите показать еще.",
                intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                dialog_act="SELECT_OPTION",
            )

        payload = option.get("option_payload") or {}
        topic = pending_question.topic
        if topic == "system":
            system_id = payload.get("system_id")
            system_name = payload.get("system_name") or option["option_label"]
            self.search_repository.close_candidate_sets(session_id, topics=["system", "profile"])
            self.search_repository.update_slot_state(
                session_id,
                system_raw=system_name,
                system_query_raw=state.get("system_query_raw") or state.get("system_raw") or system_name,
                system_resolution_mode="DIRECT",
                resolved_system_id=system_id,
                resolved_profile_id=None,
                profile_candidates=None,
                conversation_phase="RESOLVE_PROFILE",
                instruction_mode=None,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, system_id=system_id, profile_id=None)
            return None

        if topic == "profile":
            profile_id = payload.get("profile_id")
            self.search_repository.close_candidate_sets(session_id, topics=["profile"])
            self.search_repository.update_slot_state(
                session_id,
                resolved_profile_id=profile_id,
                conversation_phase="RESOLVE_PROFILE",
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, profile_id=profile_id)
            return None
        if topic in {"city", "department", "position"}:
            selected_value = self._clean_slot_text(payload.get("value") or option.get("option_label"))
            if not selected_value:
                return self._store_assistant_response(
                    session_id=session_id,
                    assistant_text="Не удалось применить выбранное значение. Выберите вариант из списка.",
                    intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                    dialog_act="SELECT_OPTION",
                )
            self.search_repository.close_candidate_sets(session_id, topics=[topic, "profile"])
            self.search_repository.update_slot_state(
                session_id,
                **{
                    f"{topic}_raw": selected_value,
                    f"{topic}_normalized": normalize_text(selected_value),
                    "resolved_profile_id": None,
                    "profile_candidates": None,
                    "pending_question": None,
                    "pending_slot": None,
                    "needs_confirmation": False,
                    "confirmation_topic": None,
                    "confirmation_options": None,
                },
            )
            self.search_repository.set_session_resolution(session_id, profile_id=None)
            return None
        return None

    @staticmethod
    def _candidate_selection_was_applied(
        before_state: dict[str, Any],
        after_state: dict[str, Any],
        topic: object,
    ) -> bool:
        before_pending = before_state.get("pending_question") or {}
        after_pending = after_state.get("pending_question") or {}
        if before_pending.get("kind") != "candidate_selection":
            return False
        if after_pending.get("candidate_set_id") != before_pending.get("candidate_set_id"):
            return True
        topic_name = str(topic or "")
        if topic_name == "system":
            return before_state.get("resolved_system_id") != after_state.get("resolved_system_id")
        if topic_name == "profile":
            return before_state.get("resolved_profile_id") != after_state.get("resolved_profile_id")
        if topic_name in {"city", "department", "position"}:
            return before_state.get(f"{topic_name}_raw") != after_state.get(f"{topic_name}_raw")
        return False

    def _continue_structured_goal(
        self,
        session_id: str,
        state: dict[str, Any],
        text: Optional[str],
        intent_type: str,
        dialog_act: str,
    ) -> ChatMessageResponse:
        if intent_type == "SYSTEM_DISCOVERY":
            org_response = self._ensure_org_slots(session_id, state, intent_type)
            if org_response is not None:
                return org_response
            state = self.search_repository.get_slot_state(session_id)
            if state.get("resolved_system_id") or self._clean_slot_text(state.get("system_raw")):
                return self._continue_role_discovery_from_system_discovery(session_id, state, text, dialog_act)
            return self._answer_system_discovery(session_id, state, dialog_act)

        if intent_type == "ROLE_DISCOVERY":
            return self._continue_role_discovery_goal(session_id, state, text, dialog_act)

        system_response = self._ensure_system(session_id, state, source_text=text)
        if system_response is not None:
            return system_response
        state = self.search_repository.get_slot_state(session_id)

        org_response = self._ensure_org_slots(session_id, state, intent_type)
        if org_response is not None:
            return org_response
        state = self.search_repository.get_slot_state(session_id)

        profile_response = self._ensure_profile(session_id, state)
        if profile_response is not None:
            return profile_response
        state = self.search_repository.get_slot_state(session_id)

        if intent_type == "ROLE_DISCOVERY":
            return self._answer_role_discovery(session_id, state, dialog_act)
        if intent_type == "ROLE_ACQUISITION":
            if not state.get("requested_entitlement_raw"):
                return self._ask_for_slot(
                    session_id,
                    "requested_entitlement",
                    "Уточните, пожалуйста, какую именно роль или доступ нужно проверить.",
                    intent_type,
                    dialog_act,
                )
            return self._answer_role_acquisition(session_id, state, text, dialog_act)
        if intent_type == "JUSTIFICATION_LOOKUP":
            if not state.get("requested_entitlement_raw"):
                return self._ask_for_slot(
                    session_id,
                    "requested_entitlement",
                    "Уточните, пожалуйста, какую именно роль нужно обосновать.",
                    intent_type,
                    dialog_act,
                    phase="RESOLVE_PROFILE",
                )
            return self._answer_justification_lookup(session_id, state, text, dialog_act)
        return self._store_assistant_response(
            session_id=session_id,
            assistant_text="Нужна конкретизация. Уточните, пожалуйста, вопрос.",
            intent_type=intent_type,
            dialog_act=dialog_act,
        )

    def _continue_role_discovery_from_system_discovery(
        self,
        session_id: str,
        state: dict[str, Any],
        text: str,
        dialog_act: str,
    ) -> ChatMessageResponse:
        self.search_repository.update_slot_state(
            session_id,
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
        )
        self.search_repository.set_session_resolution(session_id, intent_type="ROLE_DISCOVERY")
        state = self.search_repository.get_slot_state(session_id)
        return self._continue_role_discovery_goal(session_id, state, text, dialog_act)

    def _answer_system_discovery(
        self,
        session_id: str,
        state: dict[str, Any],
        dialog_act: str,
    ) -> ChatMessageResponse:
        self.search_repository.update_slot_state(
            session_id,
            conversation_phase="ANSWER_SYSTEM_DISCOVERY",
            instruction_mode=None,
            resolved_system_id=None,
        )
        state = self.search_repository.get_slot_state(session_id)
        systems = self.system_discovery_service.list_systems(session_id, state)
        position = state.get("position_raw") or "не указана"
        city = state.get("city_raw") or "не указан"
        department = state.get("department_raw") or "не указан"
        if not systems:
            return self._store_answer(
                session_id,
                self.system_discovery_service.build_answer(state, systems),
                dialog_act,
            )
        options = [
            {
                "option_key": str(item["system_id"]),
                "option_label": item["system_name_raw"],
                "option_payload": {
                    "system_id": item["system_id"],
                    "system_name": item["system_name_raw"],
                },
            }
            for item in systems
        ]
        candidate_set = self.search_repository.create_candidate_set(
            session_id=session_id,
            topic="system",
            source_query=f"{city} | {department} | {position}",
            options=options,
            page_size=PAGE_SIZE,
        )
        pending_question = {
            "kind": "candidate_selection",
            "topic": "system",
            "prompt": "Выберите АС из списка или напишите ее название.",
            "candidate_set_id": candidate_set.candidate_set_id,
        }
        self.search_repository.update_slot_state(
            session_id,
            pending_question=pending_question,
            pending_slot=None,
            needs_confirmation=True,
            confirmation_topic="system",
            conversation_phase="ANSWER_SYSTEM_DISCOVERY",
        )
        answer = self.system_discovery_service.build_answer(state, systems)
        return self._store_answer(session_id, answer, dialog_act)

    def _continue_role_discovery_goal(
        self,
        session_id: str,
        state: dict[str, Any],
        text: Optional[str],
        dialog_act: str,
    ) -> ChatMessageResponse:
        if self._should_use_broad_system_browse(state):
            org_response = self._ensure_org_slots(session_id, state, "ROLE_DISCOVERY")
            if org_response is not None:
                return org_response
            state = self.search_repository.get_slot_state(session_id)

            system_response = self._ensure_role_discovery_system(session_id, state, source_text=text)
            if system_response is not None:
                return system_response
        else:
            system_response = self._ensure_role_discovery_system(session_id, state, source_text=text)
            if system_response is not None:
                return system_response
            state = self.search_repository.get_slot_state(session_id)

            org_response = self._ensure_org_slots(session_id, state, "ROLE_DISCOVERY")
            if org_response is not None:
                return org_response

        state = self.search_repository.get_slot_state(session_id)
        return self._answer_role_discovery(session_id, state, dialog_act)

    def _ensure_system(
        self,
        session_id: str,
        state: dict[str, Any],
        source_text: Optional[str] = None,
    ) -> Optional[ChatMessageResponse]:
        if state.get("resolved_system_id"):
            return None
        query_text = state.get("system_query_raw") or state.get("system_raw")
        candidates: list[dict[str, Any]] = []
        if query_text:
            candidates = self.search_repository.resolve_system_candidates(query_text, limit=20)
        elif source_text and self._can_suggest_system_from_source_text(state):
            candidates = self._suggest_system_candidates_from_text(source_text, limit=20)
        candidates = self._rerank_system_candidates_with_llm(
            session_id=session_id,
            query_text=query_text or source_text or "",
            candidates=candidates,
            state=state,
        )
        if not query_text and not candidates:
            return self._ask_for_slot(
                session_id,
                "system",
                "Уточните, пожалуйста, в какой автоматизированной системе нужен доступ или инструкция.",
                state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                "PROVIDE_SLOT",
                phase="COLLECT_SYSTEM_HINT",
            )

        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="resolve_system_alias",
                attempt_no=1,
                input_payload={"query_text": query_text or source_text},
                result_status="success",
                result_summary=f"{len(candidates)} candidates",
            ),
            {"candidates": candidates[:10]},
        )
        if not candidates:
            return self._ask_for_slot(
                session_id,
                "system",
                f"Не удалось распознать АС по фразе '{query_text or source_text}'. Укажите более точное название или общепринятый alias.",
                state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                "PROVIDE_SLOT",
                phase="COLLECT_SYSTEM_HINT",
            )

        best = candidates[0]
        if len(candidates) == 1 or float(best["score"]) >= 0.78:
            self.search_repository.update_slot_state(
                session_id,
                system_raw=best["system_name_raw"],
                system_query_raw=query_text or source_text or best["system_name_raw"],
                system_resolution_mode="DIRECT",
                resolved_system_id=best["system_id"],
                conversation_phase="RESOLVE_PROFILE",
                instruction_mode=None,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, system_id=best["system_id"])
            return None

        options = [
            {
                "option_key": str(candidate["system_id"]),
                "option_label": candidate["system_name_raw"],
                "option_payload": {
                    "system_id": candidate["system_id"],
                    "system_name": candidate["system_name_raw"],
                },
            }
            for candidate in candidates
        ]
        return self._prompt_candidate_question(
            session_id=session_id,
            topic="system",
            prompt="Найдено несколько похожих АС. Подтвердите нужный вариант.",
            source_query=query_text or source_text or "",
            options=options,
            intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
            profile_candidates=None,
            phase="BROWSE_SYSTEMS",
        )

    @staticmethod
    def _can_suggest_system_from_source_text(state: dict[str, Any]) -> bool:
        pending_question = state.get("pending_question") or {}
        return pending_question.get("topic") == "system"

    def _ensure_role_discovery_system(
        self,
        session_id: str,
        state: dict[str, Any],
        source_text: Optional[str] = None,
    ) -> Optional[ChatMessageResponse]:
        if state.get("resolved_system_id"):
            return None
        if self._should_use_broad_system_browse(state):
            return self._browse_role_discovery_systems(session_id, state)
        return self._ensure_system(session_id, state, source_text=source_text)

    def _browse_role_discovery_systems(
        self,
        session_id: str,
        state: dict[str, Any],
    ) -> Optional[ChatMessageResponse]:
        query_text = state.get("system_query_raw") or state.get("system_raw")
        if not query_text:
            return self._ask_for_slot(
                session_id,
                "system",
                "Уточните, пожалуйста, по какой АС или группе АС нужен подбор ролей.",
                state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                "PROVIDE_SLOT",
                phase="COLLECT_SYSTEM_HINT",
            )
        candidates = self.search_repository.browse_system_candidates(
            query_text=query_text,
            city=state.get("city_raw"),
            department=state.get("department_raw"),
            position=state.get("position_raw"),
            limit=25,
        )
        candidates = self._rerank_system_candidates_with_llm(
            session_id=session_id,
            query_text=query_text,
            candidates=candidates,
            state=state,
        )
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="browse_system_candidates",
                attempt_no=1,
                input_payload={
                    "query_text": query_text,
                    "city": state.get("city_raw"),
                    "department": state.get("department_raw"),
                    "position": state.get("position_raw"),
                },
                result_status="success",
                result_summary=f"{len(candidates)} candidates",
            ),
            {"candidates": candidates[:10]},
        )
        if not candidates:
            return self._ask_for_slot(
                session_id,
                "system",
                (
                    f"Не удалось подобрать АС по запросу '{query_text}'. "
                    "Напишите рабочее название, назначение или ключевые слова."
                ),
                state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                "PROVIDE_SLOT",
                phase="COLLECT_SYSTEM_HINT",
            )
        if self._should_autoselect_browse_candidate(candidates):
            best = candidates[0]
            self.search_repository.update_slot_state(
                session_id,
                system_raw=best["system_name_raw"],
                system_query_raw=query_text,
                system_resolution_mode="DIRECT",
                resolved_system_id=best["system_id"],
                conversation_phase="RESOLVE_PROFILE",
                instruction_mode=None,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, system_id=best["system_id"])
            return None
        options = [
            {
                "option_key": str(candidate["system_id"]),
                "option_label": candidate["system_name_raw"],
                "option_payload": {
                    "system_id": candidate["system_id"],
                    "system_name": candidate["system_name_raw"],
                },
            }
            for candidate in candidates
        ]
        prompt = (
            f"Я нашел несколько АС по запросу '{query_text}'. "
            "Показываю первые варианты. Выберите нужную АС или попросите показать еще."
        )
        return self._prompt_candidate_question(
            session_id=session_id,
            topic="system",
            prompt=prompt,
            source_query=query_text,
            options=options,
            intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
            profile_candidates=None,
            phase="BROWSE_SYSTEMS",
        )

    def _should_use_broad_system_browse(self, state: dict[str, Any]) -> bool:
        if state.get("resolved_system_id"):
            return False
        resolution_mode = state.get("system_resolution_mode")
        if resolution_mode == "DIRECT":
            return False
        if resolution_mode == "BROWSE":
            return True
        query_text = state.get("system_query_raw") or state.get("system_raw")
        return self._infer_system_resolution_mode(query_text) == "BROWSE"

    def _should_autoselect_browse_candidate(self, candidates: list[dict[str, Any]]) -> bool:
        if not candidates:
            return False
        if len(candidates) == 1:
            return True
        best = candidates[0]
        second = candidates[1]
        best_score = float(best.get("score") or 0.0)
        second_score = float(second.get("score") or 0.0)
        return bool(best.get("has_profile_access")) and best_score >= 0.70 and (best_score - second_score) >= 0.12

    def _ensure_org_slots(
        self,
        session_id: str,
        state: dict[str, Any],
        intent_type: str,
    ) -> Optional[ChatMessageResponse]:
        local_state = state
        for slot_name, prompt in (
            ("position", "Укажите вашу должность по штатной структуре."),
            ("city", "Укажите ваш город по штатной структуре."),
            ("department", "Укажите ваш отдел или подразделение."),
        ):
            if not local_state.get(f"{slot_name}_raw"):
                return self._ask_for_slot(
                    session_id,
                    slot_name,
                    prompt,
                    intent_type,
                    "PROVIDE_SLOT",
                    phase=PHASE_BY_SLOT[slot_name],
                )
            slot_validation = self._validate_slot_value(session_id, local_state, slot_name, intent_type)
            if slot_validation is not None:
                return slot_validation
            local_state = self.search_repository.get_slot_state(session_id)
        return None

    def _validate_slot_value(
        self,
        session_id: str,
        state: dict[str, Any],
        slot_name: str,
        intent_type: str,
    ) -> Optional[ChatMessageResponse]:
        slot_value = self._clean_slot_text(state.get(f"{slot_name}_raw"))
        if not slot_value:
            return None
        if slot_name == "city":
            candidates = self.search_repository.find_city_candidates(slot_value, limit=8)
            tool_name = "find_city_candidates"
        elif slot_name == "department":
            candidates = self.search_repository.find_department_candidates(
                slot_value,
                city=state.get("city_raw"),
                position=state.get("position_raw"),
                limit=8,
            )
            tool_name = "find_department_candidates"
        elif slot_name == "position":
            candidates = self.search_repository.find_position_candidates(
                slot_value,
                city=state.get("city_raw"),
                department=state.get("department_raw"),
                limit=8,
            )
            if self._looks_like_echo_candidate_set(slot_value, candidates):
                fallback_candidates = self.search_repository.find_position_candidates(
                    slot_value,
                    city=None,
                    department=None,
                    limit=8,
                )
                if fallback_candidates and not self._looks_like_echo_candidate_set(slot_value, fallback_candidates):
                    candidates = fallback_candidates
            tool_name = "find_position_candidates"
        else:
            return None
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name=tool_name,
                attempt_no=1,
                input_payload={
                    "slot_name": slot_name,
                    "slot_value": slot_value,
                    "city": state.get("city_raw"),
                    "department": state.get("department_raw"),
                },
                result_status="success",
                result_summary=f"{len(candidates)} candidates",
            ),
            {"candidates": candidates[:8]},
        )
        if not candidates:
            return self._ask_for_slot(
                session_id,
                slot_name,
                self._slot_not_found_prompt(slot_name, slot_value),
                intent_type,
                "PROVIDE_SLOT",
                phase=PHASE_BY_SLOT.get(slot_name),
            )
        slot_value_norm = normalize_text(slot_value)
        exact_candidate = next(
            (
                candidate
                for candidate in candidates
                if normalize_text(str(candidate.get("value") or "")) == slot_value_norm
            ),
            None,
        )
        if exact_candidate:
            canonical_exact = self._clean_slot_text(exact_candidate.get("value"))
            if (
                slot_name == "position"
                and canonical_exact
                and slot_value != canonical_exact
                and self._position_input_is_ambiguous(slot_value, candidates)
            ):
                options = [
                    {
                        "option_key": candidate.get("value") or str(index),
                        "option_label": candidate.get("value") or str(candidate.get("value") or ""),
                        "option_payload": {"value": candidate.get("value")},
                    }
                    for index, candidate in enumerate(candidates, start=1)
                    if candidate.get("value")
                ]
                if options:
                    return self._prompt_candidate_question(
                        session_id=session_id,
                        topic=slot_name,
                        prompt=(
                            "Нашел несколько похожих должностей. "
                            "Выберите точный вариант по штатной структуре."
                        ),
                        source_query=slot_value,
                        options=options,
                        intent_type=intent_type,
                        profile_candidates=None,
                        phase=PHASE_BY_SLOT.get(slot_name),
                    )
            if canonical_exact and slot_value != canonical_exact:
                self.search_repository.close_candidate_sets(session_id, topics=[slot_name, "profile"])
                self.search_repository.update_slot_state(
                    session_id,
                    **{
                        f"{slot_name}_raw": canonical_exact,
                        f"{slot_name}_normalized": normalize_text(canonical_exact),
                        "resolved_profile_id": None,
                        "profile_candidates": None,
                    },
                )
                self.search_repository.set_session_resolution(session_id, profile_id=None)
            return None

        best = candidates[0]
        best_score = float(best.get("score") or 0.0)
        second_score = float(candidates[1].get("score") or 0.0) if len(candidates) > 1 else 0.0
        canonical_value = self._clean_slot_text(best.get("value"))
        if canonical_value and best_score >= SLOT_AUTOFILL_SCORE.get(slot_name, 0.9) and (
            len(candidates) == 1 or best_score - second_score >= SLOT_CONFIRM_GAP
        ):
            if similarity(slot_value, canonical_value) < 0.98:
                self.search_repository.close_candidate_sets(session_id, topics=[slot_name, "profile"])
                self.search_repository.update_slot_state(
                    session_id,
                    **{
                        f"{slot_name}_raw": canonical_value,
                        f"{slot_name}_normalized": normalize_text(canonical_value),
                        "resolved_profile_id": None,
                        "profile_candidates": None,
                    },
                )
                self.search_repository.set_session_resolution(session_id, profile_id=None)
            return None

        options = [
            {
                "option_key": candidate.get("value") or str(index),
                "option_label": candidate.get("value") or str(candidate.get("value") or ""),
                "option_payload": {"value": candidate.get("value")},
            }
            for index, candidate in enumerate(candidates, start=1)
            if candidate.get("value")
        ]
        if not options:
            return self._ask_for_slot(
                session_id,
                slot_name,
                self._slot_not_found_prompt(slot_name, slot_value),
                intent_type,
                "PROVIDE_SLOT",
                phase=PHASE_BY_SLOT.get(slot_name),
            )
        return self._prompt_candidate_question(
            session_id=session_id,
            topic=slot_name,
            prompt=self._slot_candidates_prompt(slot_name, slot_value),
            source_query=slot_value,
            options=options,
            intent_type=intent_type,
            profile_candidates=None,
            phase=PHASE_BY_SLOT.get(slot_name),
        )

    def _ensure_profile(self, session_id: str, state: dict[str, Any]) -> Optional[ChatMessageResponse]:
        if state.get("resolved_profile_id"):
            return None
        city = state.get("city_raw")
        department = state.get("department_raw")
        position = state.get("position_raw")
        system_id = state.get("resolved_system_id")
        candidates = self.search_repository.find_candidate_profiles(
            city,
            department,
            position,
            system_id=system_id,
            limit=20,
        )
        candidates = self._rerank_profile_candidates_with_llm(
            session_id=session_id,
            state=state,
            candidates=candidates,
        )
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="find_candidate_profiles",
                attempt_no=1,
                input_payload={
                    "city": city,
                    "department": department,
                    "position": position,
                    "system_id": system_id,
                },
                result_status="success",
                result_summary=f"{len(candidates)} candidates",
            ),
            {"candidates": candidates[:10]},
        )
        if not candidates:
            return self._store_assistant_response(
                session_id=session_id,
                assistant_text=(
                    "Не удалось определить профиль по указанным городу, отделу и должности. "
                    "Проверьте формулировки по штатной структуре."
                ),
                intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
                dialog_act="PROVIDE_SLOT",
            )
        active_goal = state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN"
        if active_goal == "ROLE_DISCOVERY" and len(candidates) > 1:
            self.search_repository.update_slot_state(
                session_id,
                resolved_profile_id=None,
                profile_candidates=candidates,
                conversation_phase="RESOLVE_PROFILE",
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, profile_id=None)
            return None
        best = candidates[0]
        second_score = float(candidates[1]["match_score"]) if len(candidates) > 1 else 0.0
        best_score = float(best["match_score"])
        score_gap = best_score - second_score
        best_department_score = float(best.get("department_score") or 0.0)
        best_position_score = float(best.get("position_score") or 0.0)
        auto_threshold = 0.72
        gap_threshold = 0.12
        if active_goal in {"ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"}:
            auto_threshold = 0.62
            gap_threshold = 0.04
        strong_role_discovery_profile = (
            active_goal == "ROLE_DISCOVERY"
            and best_score >= 0.78
            and best_department_score >= 0.95
            and best_position_score >= 0.55
            and score_gap >= 0.05
        )
        if len(candidates) == 1 or strong_role_discovery_profile or (
            best_score >= auto_threshold and (len(candidates) == 1 or score_gap >= gap_threshold)
        ):
            self.search_repository.update_slot_state(
                session_id,
                resolved_profile_id=best["profile_id"],
                profile_candidates=candidates,
                conversation_phase="RESOLVE_PROFILE",
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, profile_id=best["profile_id"])
            return None

        options = [
            {
                "option_key": str(candidate["profile_id"]),
                "option_label": f"{candidate['profile_name']} ({candidate['profile_code']})",
                "option_payload": {
                    "profile_id": candidate["profile_id"],
                    "profile_code": candidate["profile_code"],
                    "profile_name": candidate["profile_name"],
                    "profile_type": candidate.get("profile_type"),
                },
            }
            for candidate in candidates
        ]
        system_name = state.get("system_raw") or "выбранной АС"
        return self._prompt_candidate_question(
            session_id=session_id,
            topic="profile",
            prompt=f"Для {system_name} найдено несколько близких профилей. Выберите подходящий вариант.",
            source_query=f"{city} | {department} | {position}",
            options=options,
            intent_type=state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN",
            profile_candidates=candidates,
            phase="RESOLVE_PROFILE",
        )

    def _prompt_candidate_question(
        self,
        session_id: str,
        topic: str,
        prompt: str,
        source_query: str,
        options: list[dict[str, Any]],
        intent_type: str,
        profile_candidates: Optional[list[dict[str, Any]]],
        phase: Optional[str] = None,
    ) -> ChatMessageResponse:
        candidate_set = self.search_repository.create_candidate_set(
            session_id=session_id,
            topic=topic,
            source_query=source_query,
            options=options,
            page_size=PAGE_SIZE,
        )
        pending_question = {
            "kind": "candidate_selection",
            "topic": topic,
            "prompt": prompt,
            "candidate_set_id": candidate_set.candidate_set_id,
        }
        updates: dict[str, Any] = {
            "pending_question": pending_question,
            "pending_slot": None,
            "needs_confirmation": True,
            "confirmation_topic": topic,
            "conversation_phase": phase,
        }
        if profile_candidates is not None:
            updates["profile_candidates"] = profile_candidates
        self.search_repository.update_slot_state(session_id, **updates)
        state = self.search_repository.get_slot_state(session_id)
        self._sync_pending_question_compatibility(session_id, pending_question)
        return self._store_assistant_response(
            session_id=session_id,
            assistant_text=prompt,
            intent_type=intent_type,
            dialog_act="ASK_HELP",
        )

    def _ask_for_slot(
        self,
        session_id: str,
        slot_name: str,
        prompt: str,
        intent_type: str,
        dialog_act: str,
        phase: Optional[str] = None,
    ) -> ChatMessageResponse:
        pending_question = {
            "kind": "slot_request",
            "topic": slot_name,
            "prompt": prompt,
        }
        self.search_repository.update_slot_state(
            session_id,
            pending_question=pending_question,
            pending_slot=slot_name,
            conversation_phase=phase,
            needs_confirmation=False,
            confirmation_topic=None,
            confirmation_options=None,
        )
        return self._store_assistant_response(
            session_id=session_id,
            assistant_text=prompt,
            intent_type=intent_type,
            dialog_act=dialog_act,
        )

    def _answer_role_discovery(
        self,
        session_id: str,
        state: dict[str, Any],
        dialog_act: str,
    ) -> ChatMessageResponse:
        self.search_repository.update_slot_state(
            session_id,
            conversation_phase="ANSWER_ROLE_DISCOVERY",
            instruction_mode=None,
            resolved_profile_id=None,
            profile_candidates=None,
        )
        state = self.search_repository.get_slot_state(session_id)
        rows, matched_profiles = self.role_discovery_service.list_access(session_id, state)
        system = self.search_repository.get_active_system(state["resolved_system_id"])
        system_name = system["system_name_raw"] if system else state.get("system_raw") or "выбранная АС"
        answer = self.role_discovery_service.build_answer(
            state=state,
            system_name=system_name,
            rows=rows,
            matched_profiles=matched_profiles,
            list_limit=ROLE_DISCOVERY_LIST_LIMIT,
        )
        if answer.default_accesses or answer.request_accesses:
            pending_question = {
                "kind": "instruction_offer",
                "topic": "instruction",
                "prompt": INSTRUCTION_OFFER_PROMPT,
                "options": [
                    {
                        "id": "instruction_yes",
                        "label": "Да, подскажи",
                        "payload": {"action": "ACCEPT"},
                    },
                    {
                        "id": "instruction_no",
                        "label": "Нет, спасибо",
                        "payload": {"action": "DECLINE"},
                    },
                ],
            }
            self.search_repository.update_slot_state(
                session_id,
                pending_question=pending_question,
                pending_slot=None,
                needs_confirmation=True,
                confirmation_topic="instruction",
                confirmation_options=None,
                conversation_phase="ANSWER_ROLE_DISCOVERY",
            )
        else:
            self._clear_pending_question(session_id)
        return self._store_answer(session_id, answer, dialog_act)

    def _answer_role_acquisition(
        self,
        session_id: str,
        state: dict[str, Any],
        raw_text: str,
        dialog_act: str,
    ) -> ChatMessageResponse:
        entitlement_raw = state.get("requested_entitlement_raw")
        match = self.search_repository.check_entitlement_access(
            state["resolved_profile_id"],
            state["resolved_system_id"],
            entitlement_raw,
            state.get("requested_entitlement_type_hint"),
        )
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="check_entitlement_access",
                attempt_no=1,
                input_payload={"entitlement_name": entitlement_raw},
                result_status="success",
                result_summary="matched" if match else "not_found",
            ),
            match or {},
        )
        profile = self._profile_payload(state)
        profile_name = profile.get("profile_name") or f"#{state['resolved_profile_id']}"
        if not match:
            answer = SearchAnswer(
                answer_type="ROLE_ACQUISITION",
                summary_text=f"Для профиля {profile_name} роль '{entitlement_raw}' в выбранной АС не найдена.",
                profile=profile,
            )
            return self._store_answer(session_id, answer, dialog_act)
        access_level = int(match["access_level"])
        instruction = None
        citations = []
        support_recommendation = None
        if access_level == 2:
            instruction_result = self.rag_service.answer_from_inline_doc(
                f"{raw_text} {match['system_name']} {match['entitlement_name']}",
                context={
                    "intent_type": "ROLE_ACQUISITION",
                    "system_name": match["system_name"],
                },
            )
            if instruction_result:
                instruction = instruction_result["instruction"]
                citations = instruction_result["citations"]
            else:
                retrieved = self.rag_service.search_instructions(
                    f"{raw_text} {match['system_name']} {match['entitlement_name']}"
                )
                rag_answer = self.rag_service.answer_with_rag(raw_text, retrieved, answer_style="steps")
                instruction = rag_answer["instruction"]
                citations = rag_answer["citations"]
        else:
            support_recommendation = SUPPORT_RECOMMENDATION
        answer = SearchAnswer(
            answer_type="ROLE_ACQUISITION",
            summary_text=(
                f"Для профиля {profile_name} роль '{match['entitlement_name']}' "
                f"в АС {match['system_name']} доступна "
                f"{'по умолчанию' if access_level == 1 else 'по требованию'}."
            ),
            profile=profile,
            default_accesses=[self._access_payload(match)] if access_level == 1 else [],
            request_accesses=[self._access_payload(match)] if access_level == 2 else [],
            instruction=instruction,
            citations=citations,
            support_recommendation=support_recommendation,
        )
        return self._store_answer(session_id, answer, dialog_act)

    def _answer_justification_lookup(
        self,
        session_id: str,
        state: dict[str, Any],
        raw_text: str,
        dialog_act: str,
    ) -> ChatMessageResponse:
        justification = self.search_repository.get_system_justification(
            state["resolved_profile_id"],
            state["resolved_system_id"],
            entitlement_name=state.get("requested_entitlement_raw"),
        )
        profile = self._profile_payload(state)
        profile_name = profile.get("profile_name") or f"#{state['resolved_profile_id']}"
        summary = (
            f"Для профиля {profile_name} доступно следующее обоснование: {justification}"
            if justification
            else f"Для профиля {profile_name} обоснование по выбранной АС не найдено."
        )
        answer = SearchAnswer(
            answer_type="JUSTIFICATION_LOOKUP",
            summary_text=summary,
            profile=profile,
            justification=justification,
        )
        return self._store_answer(session_id, answer, dialog_act)

    def _answer_instruction_lookup(
        self,
        session_id: str,
        state: dict[str, Any],
        raw_text: str,
        interpretation: TurnInterpretation,
    ) -> ChatMessageResponse:
        if state.get("system_raw") and not state.get("resolved_system_id"):
            system_response = self._ensure_system(session_id, state, source_text=raw_text)
            if system_response is not None:
                return system_response
            state = self.search_repository.get_slot_state(session_id)
        self.policy_service.save_resume_point(session_id, state)
        result = self.instruction_service.answer(
            session_id=session_id,
            state=state,
            raw_text=raw_text,
            references_pending_question=interpretation.references_pending_question,
            resolved_system_name=self._resolved_payload(state).get("system_name"),
        )
        self.search_repository.update_slot_state(
            session_id,
            conversation_phase="ANSWER_INSTRUCTION",
            instruction_mode=result.instruction_mode,
        )
        self._restore_previous_goal(session_id, state)
        return self._store_answer(session_id, result.answer, interpretation.dialog_act)

    def _restore_previous_goal(self, session_id: str, state: dict[str, Any]) -> None:
        self.policy_service.restore_previous_goal(session_id, state)

    def _reset_context(self, session_id: str) -> None:
        self.policy_service.reset_context(session_id)

    def _sync_pending_question_compatibility(self, session_id: str, pending_question: Any) -> None:
        if not pending_question:
            self.search_repository.update_slot_state(
                session_id,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            return
        state = self.search_repository.get_slot_state(session_id)
        hydrated = self._hydrate_pending_question(state)
        confirmation = self._pending_question_to_confirmation(hydrated)
        self.search_repository.update_slot_state(
            session_id,
            needs_confirmation=confirmation is not None,
            confirmation_topic=confirmation.topic if confirmation else None,
            confirmation_options=confirmation.model_dump() if confirmation else None,
        )

    def _hydrate_pending_question(self, state: dict[str, Any]) -> Optional[PendingQuestionPayload]:
        raw = state.get("pending_question")
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "").strip()
        topic = str(raw.get("topic") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        candidate_set_id = raw.get("candidate_set_id")
        raw_options = raw.get("options") if isinstance(raw.get("options"), list) else []
        normalized_options: list[ConfirmationOption] = []
        for index, item in enumerate(raw_options, start=1):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            option_id = str(item.get("id") or f"opt_{index}")
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            normalized_options.append(
                ConfirmationOption(
                    id=option_id,
                    label=label,
                    payload=payload,
                )
            )
        if kind == "candidate_selection" and candidate_set_id is not None:
            page = self.search_repository.get_candidate_page(int(candidate_set_id))
            if not page:
                return None
            options = [
                ConfirmationOption(
                    id=str(index),
                    label=option["option_label"],
                    payload=option.get("option_payload") or {},
                )
                for index, option in enumerate(page["options"], start=1)
            ]
            return PendingQuestionPayload(
                kind=kind,
                topic=topic,
                prompt=prompt,
                candidate_set_id=int(candidate_set_id),
                options=options,
                has_more=bool(page["has_more"]),
                page_offset=int(page["page_offset"]),
                page_size=int(page["page_size"]),
                total_options=int(page["total_options"]),
            )
        return PendingQuestionPayload(kind=kind, topic=topic, prompt=prompt, options=normalized_options)

    def _pending_question_to_confirmation(
        self,
        pending_question: Optional[PendingQuestionPayload],
    ) -> Optional[ConfirmationPayload]:
        if not pending_question:
            return None
        if pending_question.kind == "candidate_selection":
            options = list(pending_question.options)
            if pending_question.has_more:
                options.append(
                    ConfirmationOption(
                        id="MORE",
                        label="Показать еще варианты",
                        payload={"action": "SHOW_MORE"},
                    )
                )
            return ConfirmationPayload(topic=pending_question.topic, prompt=pending_question.prompt, options=options)
        if pending_question.options:
            return ConfirmationPayload(
                topic=pending_question.topic,
                prompt=pending_question.prompt,
                options=list(pending_question.options),
            )
        return None

    def _store_answer(
        self,
        session_id: str,
        answer: SearchAnswer,
        dialog_act: str,
    ) -> ChatMessageResponse:
        payload = SearchAnswerPayload(
            answer_type=answer.answer_type,
            summary_text=answer.summary_text,
            profile=answer.profile,
            systems=answer.systems,
            default_accesses=answer.default_accesses,
            request_accesses=answer.request_accesses,
            justification=answer.justification,
            instruction=answer.instruction,
            citations=[
                {
                    "source_id": item.source_id,
                    "chunk_id": item.chunk_id,
                    "source_title": item.source_title,
                    "slide_no": item.slide_no,
                    "citation_label": item.citation_label,
                    "locator_text": item.locator_text,
                }
                for item in answer.citations
            ],
            support_recommendation=answer.support_recommendation,
        )
        return self._store_assistant_response(
            session_id=session_id,
            assistant_text=answer.summary_text,
            intent_type=answer.answer_type if answer.answer_type in ALL_INTENTS else "UNKNOWN",
            dialog_act=dialog_act,
            answer=payload,
        )

    def _store_assistant_response(
        self,
        session_id: str,
        assistant_text: str,
        intent_type: str,
        dialog_act: str,
        answer: Optional[SearchAnswerPayload] = None,
    ) -> ChatMessageResponse:
        response_prefix = self._response_prefixes.pop(session_id, "").strip()
        if response_prefix:
            assistant_text = f"{response_prefix}\n\n{assistant_text}"
        state = self.search_repository.get_slot_state(session_id)
        context = self._build_context(state)
        if context != (state.get("context_snapshot") or {}):
            self.search_repository.update_slot_state(session_id, context_snapshot=context)
            state = self.search_repository.get_slot_state(session_id)
            context = self._build_context(state)
        pending_question = self._hydrate_pending_question(state)
        confirmation = self._pending_question_to_confirmation(pending_question)
        suggested_actions = self._build_suggested_actions(state, pending_question)
        resolved = self._resolved_payload(state)
        structured_payload = {
            "intent_type": intent_type,
            "dialog_act": dialog_act,
            "resolved": resolved,
            "context": context,
            "active_goal": state.get("active_goal") or state.get("last_intent_type"),
            "context_shift": state.get("context_shift"),
            "conversation_phase": state.get("conversation_phase"),
            "resume_goal": state.get("resume_goal"),
            "resume_phase": state.get("resume_phase"),
            "instruction_mode": state.get("instruction_mode"),
            "pending_question": pending_question.model_dump() if pending_question else None,
            "state_revision": int(state.get("state_revision") or 0),
            "suggested_actions": [item.model_dump() for item in suggested_actions],
            "requires_confirmation": confirmation is not None,
            "confirmation": confirmation.model_dump() if confirmation else None,
            "answer": answer.model_dump() if answer else None,
        }
        message_id = self.search_repository.add_message(
            session_id=session_id,
            role="ASSISTANT",
            message_text=assistant_text,
            structured_payload=structured_payload,
        )
        return ChatMessageResponse(
            message_id=message_id,
            session_id=session_id,
            assistant_text=assistant_text,
            intent_type=self._normalize_intent_type(intent_type),
            dialog_act=dialog_act,
            requires_confirmation=confirmation is not None,
            confirmation=confirmation,
            resolved=resolved,
            context=context,
            active_goal=state.get("active_goal") or state.get("last_intent_type"),
            context_shift=state.get("context_shift"),
            conversation_phase=state.get("conversation_phase"),
            resume_goal=state.get("resume_goal"),
            resume_phase=state.get("resume_phase"),
            instruction_mode=state.get("instruction_mode"),
            pending_question=pending_question,
            state_revision=int(state.get("state_revision") or 0),
            suggested_actions=suggested_actions,
            answer=answer,
        )

    def _build_context(self, state: dict[str, Any]) -> dict[str, Any]:
        context = {
            "system": None,
            "position": state.get("position_raw"),
            "city": state.get("city_raw"),
            "department": state.get("department_raw"),
            "profile": None,
            "requested_role": None,
        }
        active_goal = state.get("active_goal") or state.get("last_intent_type")
        if active_goal in {"ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"}:
            context["requested_role"] = state.get("requested_entitlement_raw")
        system_id = state.get("resolved_system_id")
        if system_id:
            system = self.search_repository.get_active_system(int(system_id))
            if system:
                context["system"] = {
                    "system_id": system["id"],
                    "system_name": system["system_name_raw"],
                    "ci_code": system.get("ci_code"),
                }
        profile = self._profile_payload(state)
        if profile:
            context["profile"] = profile
        pending_question = state.get("pending_question") or {}
        if pending_question.get("kind") == "candidate_selection":
            topic = pending_question.get("topic")
            if topic in {"position", "city", "department", "profile"}:
                context[topic] = None
        return context

    def _build_suggested_actions(
        self,
        state: dict[str, Any],
        pending_question: Optional[PendingQuestionPayload],
    ) -> list[SuggestedActionPayload]:
        actions: list[SuggestedActionPayload] = []
        if pending_question and pending_question.kind == "candidate_selection" and pending_question.has_more:
            actions.append(
                SuggestedActionPayload(
                    id="show_more",
                    label="Показать еще",
                    text="Показать еще варианты",
                )
            )
        if state.get("system_raw") or state.get("resolved_system_id"):
            actions.append(
                SuggestedActionPayload(
                    id="change_system",
                    label="Сменить АС",
                    text="Хочу выбрать другую АС",
                )
            )
        if state.get("requested_entitlement_raw"):
            active_goal = state.get("active_goal") or state.get("last_intent_type")
            if active_goal in {"ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"}:
                actions.append(
                    SuggestedActionPayload(
                        id="change_role",
                        label="Сменить роль",
                        text="Хочу проверить другую роль",
                    )
                )
        actions.append(
            SuggestedActionPayload(
                id="reset_context",
                label="Сбросить контекст",
                text="Сбросить контекст",
            )
        )
        return actions

    def _resolved_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        resolved = {
            "system_raw": state.get("system_raw"),
            "system_id": state.get("resolved_system_id"),
            "city": state.get("city_raw"),
            "department": state.get("department_raw"),
            "position": state.get("position_raw"),
            "profile_id": state.get("resolved_profile_id"),
        }
        if state.get("resolved_system_id"):
            system = self.search_repository.get_active_system(int(state["resolved_system_id"]))
            if system:
                resolved["system_name"] = system["system_name_raw"]
        elif state.get("system_raw"):
            display_system = self._resolve_system_display_from_raw(state.get("system_raw"))
            if display_system:
                resolved["system_name"] = display_system.get("system_name")
                if display_system.get("system_id") is not None:
                    resolved["system_id_hint"] = display_system.get("system_id")
        profile = self._profile_payload(state)
        if profile:
            resolved["profile_name"] = profile.get("profile_name")
            resolved["profile_code"] = profile.get("profile_code")
        active_goal = state.get("active_goal") or state.get("last_intent_type")
        if state.get("requested_entitlement_raw") and active_goal in {"ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"}:
            resolved["requested_entitlement_raw"] = state["requested_entitlement_raw"]
        return resolved

    def _resolve_system_display_from_raw(self, system_raw: Any) -> Optional[dict[str, Any]]:
        raw_value = self._clean_slot_text(system_raw)
        if not raw_value:
            return None
        try:
            candidates = self.search_repository.resolve_system_candidates(raw_value, limit=3)
        except Exception:
            return None
        if not candidates:
            return None
        best = candidates[0]
        second_score = float(candidates[1].get("score") or 0.0) if len(candidates) > 1 else 0.0
        best_score = float(best.get("score") or 0.0)
        raw_norm = normalize_text(raw_value)
        best_name_norm = normalize_text(best.get("system_name_raw"))
        best_alias_norm = normalize_text(best.get("alias_text"))
        exact_signal = raw_norm in {best_name_norm, best_alias_norm}
        confident = exact_signal or len(candidates) == 1 or (best_score - second_score >= SLOT_CONFIRM_GAP)
        if not confident:
            return None
        return {
            "system_id": best.get("system_id"),
            "system_name": best.get("system_name_raw") or raw_value,
            "ci_code": best.get("ci_code"),
        }

    def _detect_direct_system_entity(self, raw_text: Any) -> Optional[str]:
        raw_value = self._clean_slot_text(raw_text)
        if not raw_value:
            return None
        try:
            candidates = self.search_repository.resolve_system_candidates(raw_value, limit=3)
        except Exception:
            return None
        if not candidates:
            return None
        best = candidates[0]
        second_score = float(candidates[1].get("score") or 0.0) if len(candidates) > 1 else 0.0
        best_score = float(best.get("score") or 0.0)
        raw_norm = normalize_text(raw_value)
        best_name_norm = normalize_text(best.get("system_name_raw"))
        best_alias_norm = normalize_text(best.get("alias_text"))
        exact_signal = raw_norm in {best_name_norm, best_alias_norm}
        confident = (
            exact_signal
            or len(candidates) == 1
            or best_score >= 0.88
            or (best_score >= 0.70 and (best_score - second_score) >= SLOT_CONFIRM_GAP)
        )
        if not confident:
            return None
        return self._clean_slot_text(best.get("system_name_raw"))

    def _profile_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        profile_id = state.get("resolved_profile_id")
        if not profile_id:
            return {}
        candidates = state.get("profile_candidates") or []
        for candidate in candidates:
            if int(candidate["profile_id"]) == int(profile_id):
                return {
                    "profile_id": candidate["profile_id"],
                    "profile_code": candidate.get("profile_code"),
                    "profile_name": candidate.get("profile_name"),
                    "profile_type": candidate.get("profile_type"),
                }
        profile = self.search_repository.get_active_profile(int(profile_id))
        if profile:
            return {
                "profile_id": profile["profile_id"],
                "profile_code": profile.get("profile_code"),
                "profile_name": profile.get("profile_name"),
                "profile_type": profile.get("profile_type"),
            }
        return {"profile_id": profile_id}

    def _suggest_system_candidates_from_text(self, source_text: str, limit: int = 20) -> list[dict[str, Any]]:
        normalized = normalize_text(source_text)
        if not normalized:
            return []
        stop_words = {
            "какая",
            "какие",
            "какой",
            "нужна",
            "нужен",
            "нужно",
            "роль",
            "роли",
            "доступ",
            "получить",
            "как",
            "мне",
            "для",
            "ас",
            "автоматизированная",
            "система",
            "системе",
            "по",
            "на",
        }
        variants: list[str] = [source_text]
        words = [word for word in normalized.split() if len(word) >= 3 and word not in stop_words]
        if len(words) >= 2:
            variants.append(" ".join(words[:2]))
        variants.extend(words[:4])
        scored: dict[int, dict[str, Any]] = {}
        seen_variants: set[str] = set()
        for variant in variants:
            variant_norm = normalize_text(variant)
            if not variant_norm or variant_norm in seen_variants:
                continue
            seen_variants.add(variant_norm)
            for candidate in self.search_repository.resolve_system_candidates(variant, limit=20):
                score = float(candidate.get("score") or 0)
                if score < 0.35:
                    continue
                current = scored.get(int(candidate["system_id"]))
                if current is None or score > float(current.get("score") or 0):
                    scored[int(candidate["system_id"])] = candidate
        return sorted(scored.values(), key=lambda item: float(item.get("score") or 0), reverse=True)[:limit]

    def _rerank_system_candidates_with_llm(
        self,
        session_id: str,
        query_text: str,
        candidates: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if len(candidates) < 2 or not self.config.gigachat_use_for_intent or not self.gigachat.enabled:
            return candidates
        shortlist = candidates[:10]
        payload = [
            {
                "index": index + 1,
                "system_id": item.get("system_id"),
                "name": item.get("system_name_raw"),
                "score": float(item.get("score") or 0.0),
                "has_profile_access": bool(item.get("has_profile_access")),
            }
            for index, item in enumerate(shortlist)
        ]
        system_prompt = (
            "Ты реранкер кандидатов АС. Верни JSON: {ordered_indexes:[...]} где индексы — порядок релевантности. "
            "Учитывай орг-контекст и то, что нужен лучший кандидат для текущего диалога."
        )
        user_prompt = (
            f"query_text={query_text}\n"
            f"context={{city:{state.get('city_raw')},department:{state.get('department_raw')},position:{state.get('position_raw')}}}\n"
            f"candidates={payload}"
        )
        try:
            result = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chat_model,
                max_tokens=250,
            )
        except Exception:
            return candidates
        ordered = result.get("ordered_indexes") if isinstance(result, dict) else None
        if not isinstance(ordered, list):
            return candidates
        ordered_values = [item for item in ordered if isinstance(item, int)]
        if not ordered_values:
            return candidates
        index_to_candidate = {index + 1: item for index, item in enumerate(shortlist)}
        seen: set[int] = set()
        reranked: list[dict[str, Any]] = []
        for index in ordered_values:
            candidate = index_to_candidate.get(index)
            if candidate is None or index in seen:
                continue
            seen.add(index)
            reranked.append(candidate)
        for index, candidate in index_to_candidate.items():
            if index not in seen:
                reranked.append(candidate)
        if len(candidates) > len(shortlist):
            reranked.extend(candidates[len(shortlist) :])
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="rerank_system_candidates",
                attempt_no=1,
                input_payload={"query_text": query_text, "candidate_count": len(candidates)},
                result_status="success",
                result_summary=f"reranked {len(shortlist)}",
            ),
            {"ordered_indexes": ordered_values},
        )
        return reranked

    def _rerank_profile_candidates_with_llm(
        self,
        session_id: str,
        state: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(candidates) < 2 or not self.config.gigachat_use_for_intent or not self.gigachat.enabled:
            return candidates
        shortlist = candidates[:10]
        payload = [
            {
                "index": index + 1,
                "profile_id": item.get("profile_id"),
                "profile_name": item.get("profile_name"),
                "profile_code": item.get("profile_code"),
                "match_score": float(item.get("match_score") or 0.0),
                "department_score": float(item.get("department_score") or 0.0),
                "position_score": float(item.get("position_score") or 0.0),
            }
            for index, item in enumerate(shortlist)
        ]
        system_prompt = (
            "Ты реранкер профилей. Верни JSON: {ordered_indexes:[...]} где индексы — порядок релевантности. "
            "Приоритет: точное соответствие должности, затем отдел и город."
        )
        user_prompt = (
            f"context={{system:{state.get('system_raw')},city:{state.get('city_raw')},department:{state.get('department_raw')},position:{state.get('position_raw')}}}\n"
            f"candidates={payload}"
        )
        try:
            result = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chat_model,
                max_tokens=250,
            )
        except Exception:
            return candidates
        ordered = result.get("ordered_indexes") if isinstance(result, dict) else None
        if not isinstance(ordered, list):
            return candidates
        ordered_values = [item for item in ordered if isinstance(item, int)]
        if not ordered_values:
            return candidates
        index_to_candidate = {index + 1: item for index, item in enumerate(shortlist)}
        seen: set[int] = set()
        reranked: list[dict[str, Any]] = []
        for index in ordered_values:
            candidate = index_to_candidate.get(index)
            if candidate is None or index in seen:
                continue
            seen.add(index)
            reranked.append(candidate)
        for index, candidate in index_to_candidate.items():
            if index not in seen:
                reranked.append(candidate)
        if len(candidates) > len(shortlist):
            reranked.extend(candidates[len(shortlist) :])
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="rerank_profile_candidates",
                attempt_no=1,
                input_payload={"candidate_count": len(candidates)},
                result_status="success",
                result_summary=f"reranked {len(shortlist)}",
            ),
            {"ordered_indexes": ordered_values},
        )
        return reranked

    @staticmethod
    def _slot_key_for_topic(topic: Optional[str]) -> Optional[str]:
        mapping = {
            "system": "system_raw",
            "position": "position_raw",
            "city": "city_raw",
            "department": "department_raw",
            "requested_entitlement": "requested_entitlement_raw",
        }
        return mapping.get(str(topic or "").strip())

    def _entity_value_is_valid_for_system(
        self,
        session_id: str,
        raw_value: str,
        state: dict[str, Any],
    ) -> bool:
        value = self._clean_slot_text(raw_value)
        if not value:
            return False
        try:
            candidates = self.search_repository.resolve_system_candidates(value, limit=5)
            if not candidates:
                candidates = self.search_repository.browse_system_candidates(
                    query_text=value,
                    city=state.get("city_raw"),
                    department=state.get("department_raw"),
                    position=state.get("position_raw"),
                    limit=5,
                )
        except Exception as exc:
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="validate_entity_slot",
                    attempt_no=1,
                    input_payload={"slot_name": "system", "slot_value": value},
                    result_status="error",
                    result_summary=type(exc).__name__,
                ),
                {"error": str(exc)},
            )
            return False
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="validate_entity_slot",
                attempt_no=1,
                input_payload={"slot_name": "system", "slot_value": value},
                result_status="success",
                result_summary=f"{len(candidates)} candidates",
            ),
            {"candidates": candidates[:5]},
        )
        if not candidates:
            return False

        value_norm = normalize_text(value)
        best = candidates[0]
        best_score = float(best.get("score") or 0.0)
        exact_system = any(
            value_norm
            in {
                normalize_text(candidate.get("system_name_raw")),
                normalize_text(candidate.get("alias_text")),
            }
            for candidate in candidates
        )
        if exact_system:
            return True

        return best_score >= SYSTEM_ENTITY_MIN_SCORE

    def _validate_entity_value_for_org_slot(
        self,
        session_id: str,
        slot_name: str,
        raw_value: str,
        state: dict[str, Any],
        log_probe: bool = True,
    ) -> dict[str, Any]:
        value = self._clean_slot_text(raw_value)
        if not value:
            return {"valid": False, "reason": "empty"}
        try:
            if slot_name == "city":
                candidates = self.search_repository.find_city_candidates(value, limit=5)
            elif slot_name == "department":
                candidates = self.search_repository.find_department_candidates(
                    value,
                    city=state.get("city_raw"),
                    position=state.get("position_raw"),
                    limit=5,
                )
            elif slot_name == "position":
                candidates = self.search_repository.find_position_candidates(
                    value,
                    city=state.get("city_raw"),
                    department=state.get("department_raw"),
                    limit=5,
                )
                if self._looks_like_echo_candidate_set(value, candidates):
                    fallback_candidates = self.search_repository.find_position_candidates(
                        value,
                        city=None,
                        department=None,
                        limit=5,
                    )
                    if fallback_candidates and not self._looks_like_echo_candidate_set(value, fallback_candidates):
                        candidates = fallback_candidates
            else:
                return {"valid": False, "reason": "unknown_slot"}
        except Exception as exc:
            if log_probe:
                self.search_repository.log_tool_call(
                    session_id,
                    ToolAttempt(
                        tool_name="validate_entity_slot",
                        attempt_no=1,
                        input_payload={"slot_name": slot_name, "slot_value": value},
                        result_status="error",
                        result_summary=type(exc).__name__,
                    ),
                    {"error": str(exc)},
                )
            return {"valid": False, "reason": "dictionary_error"}

        if log_probe:
            self.search_repository.log_tool_call(
                session_id,
                ToolAttempt(
                    tool_name="validate_entity_slot",
                    attempt_no=1,
                    input_payload={
                        "slot_name": slot_name,
                        "slot_value": value,
                        "city": state.get("city_raw"),
                        "department": state.get("department_raw"),
                    },
                    result_status="success",
                    result_summary=f"{len(candidates)} candidates",
                ),
                {"candidates": candidates[:5]},
            )
        if not candidates:
            return {"valid": False, "reason": "no_dictionary_candidate"}

        value_norm = normalize_text(value)
        exact_candidate = next(
            (
                candidate
                for candidate in candidates
                if normalize_text(str(candidate.get("value") or "")) == value_norm
            ),
            None,
        )
        if exact_candidate:
            if slot_name == "position" and self._position_input_is_ambiguous(value, candidates):
                return {
                    "valid": True,
                    "exact": True,
                    "canonical_value": None,
                }
            return {
                "valid": True,
                "exact": True,
                "canonical_value": self._clean_slot_text(exact_candidate.get("value")) or value,
            }

        best = candidates[0]
        best_score = float(best.get("score") or 0.0)
        if best_score < SLOT_ENTITY_MIN_SCORE.get(slot_name, 0.45):
            return {"valid": False, "reason": "low_dictionary_score", "best_score": best_score}

        return {
            "valid": True,
            "exact": False,
            "canonical_value": None,
            "best_score": best_score,
        }

    def _log_entity_validation_reject(
        self,
        session_id: str,
        slot_name: str,
        raw_value: str,
        reason: str,
    ) -> None:
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="reject_invalid_entity_slot",
                attempt_no=1,
                input_payload={"slot_name": slot_name, "slot_value": raw_value},
                result_status="success",
                result_summary=reason,
            ),
            {"reason": reason},
        )

    @staticmethod
    def _slot_not_found_prompt(slot_name: str, raw_value: str) -> str:
        label = ChatAgent._slot_label(slot_name)
        return (
            f"Не удалось найти '{raw_value}' в справочнике для поля '{label}'. "
            "Уточните формулировку по штатной структуре."
        )

    @staticmethod
    def _slot_label(slot_name: str) -> str:
        labels = {
            "system": "АС",
            "city": "город",
            "department": "отдел",
            "position": "должность",
        }
        return labels.get(slot_name, slot_name)

    @staticmethod
    def _slot_candidates_prompt(slot_name: str, raw_value: str) -> str:
        labels = {
            "city": "города",
            "department": "отдела",
            "position": "должности",
        }
        label = labels.get(slot_name, slot_name)
        return (
            f"Введенное значение '{raw_value}' не найдено однозначно для {label}. "
            "Выберите подходящий вариант из списка."
        )

    @staticmethod
    def _normalize_intent_type(value: object) -> str:
        intent = str(value or "UNKNOWN").strip().upper()
        return intent if intent in ALL_INTENTS else "UNKNOWN"

    @staticmethod
    def _is_unknown_system_signal(value: object) -> bool:
        normalized = normalize_text(value)
        if not normalized:
            return False
        exact = {
            "не знаю",
            "не помню",
            "не уверен",
            "не знаю название",
            "не знаю названия",
            "не помню название",
            "не помню названия",
            "не знаю точного названия",
            "не помню точного названия",
        }
        return normalized in exact or normalized.startswith("не знаю ") or normalized.startswith("не помню ")

    @staticmethod
    def _normalize_dialog_act(value: object) -> str:
        dialog_act = str(value or "UNKNOWN").strip().upper()
        return dialog_act if dialog_act in ALL_DIALOG_ACTS else "UNKNOWN"

    @staticmethod
    def _normalize_context_shift(value: object) -> str:
        context_shift = str(value or "NONE").strip().upper()
        return context_shift if context_shift in ALL_CONTEXT_SHIFTS else "NONE"

    @staticmethod
    def _normalize_confidence(value: object) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))

    def _infer_context_shift(
        self,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
        text_value: str,
    ) -> str:
        del text_value
        if interpretation.dialog_act == "RESET_CONTEXT":
            return "RESET_CONTEXT"
        if interpretation.goal_transition == "SWITCH":
            return "SWITCH_GOAL"
        system_raw = self._clean_slot_text(interpretation.entities.get("system_raw"))
        if system_raw:
            current_system = self._clean_slot_text(state.get("system_raw"))
            if state.get("resolved_system_id") or (current_system and similarity(current_system, system_raw) < 0.98):
                return "CHANGE_SYSTEM_FOCUS"
        for slot_name in ("position", "city", "department"):
            incoming = self._clean_slot_text(interpretation.entities.get(f"{slot_name}_raw"))
            current = self._clean_slot_text(state.get(f"{slot_name}_raw"))
            if incoming and current and similarity(incoming, current) < 0.98:
                return "CHANGE_ORG_CONTEXT"
        return "NONE"

    def _parse_mixed_slot_input(
        self,
        raw_text: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> dict[str, Any]:
        if interpretation.intent_type == "INSTRUCTION_LOOKUP":
            return {"used": False}
        segments = self._split_mixed_segments(raw_text)
        if len(segments) < 2:
            return {"used": False}
        meaningful_segments = [segment for segment in segments if self._is_meaningful_mixed_segment(segment)]
        if len(meaningful_segments) < 2:
            return {"used": False}
        parsed_entities: dict[str, str] = {}
        recognized: list[tuple[str, str]] = []
        ambiguous: list[tuple[str, str]] = []
        unresolved: list[str] = []
        system_resolution_mode: Optional[str] = None
        preferred_slots = [
            slot
            for slot in ("system", "position", "city", "department")
            if not state.get(f"{slot}_raw") and (slot != "system" or not state.get("resolved_system_id"))
        ]
        if not preferred_slots:
            preferred_slots = ["system", "position", "city", "department"]

        for index, segment in enumerate(segments):
            slot_match = self._classify_mixed_segment(segment, index, state, parsed_entities, preferred_slots)
            if not slot_match:
                unresolved.append(segment)
                continue
            slot_name = slot_match["slot"]
            entity_key = "system_raw" if slot_name == "system" else f"{slot_name}_raw"
            if entity_key in parsed_entities:
                unresolved.append(segment)
                continue
            parsed_entities[entity_key] = slot_match["value"]
            if slot_name == "system":
                system_resolution_mode = slot_match.get("resolution_mode") or system_resolution_mode
            if slot_match["status"] == "resolved":
                recognized.append((slot_name, slot_match["display"]))
            else:
                ambiguous.append((slot_name, segment))

        recognized_count = len(recognized) + len(ambiguous)
        if not parsed_entities or recognized_count == 0 or len(parsed_entities) < 2:
            return {"used": False}

        active_goal = state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN"
        suggested_intent = None
        if active_goal == "UNKNOWN" and interpretation.intent_type == "UNKNOWN":
            if "system_raw" in parsed_entities or recognized_count >= 2:
                suggested_intent = "ROLE_DISCOVERY"

        return {
            "used": True,
            "entities": parsed_entities,
            "system_resolution_mode": system_resolution_mode,
            "suggested_intent": suggested_intent,
            "unresolved": unresolved,
        }

    @staticmethod
    def _split_mixed_segments(raw_text: str) -> list[str]:
        parts = re.split(r"[,;\n]+", str(raw_text or ""))
        return [part.strip(" .") for part in parts if part and part.strip(" .")]

    @staticmethod
    def _is_meaningful_mixed_segment(segment: str) -> bool:
        tokens = [token for token in normalize_text(segment).split() if len(token) >= 3 or token.isdigit()]
        return bool(tokens)

    def _classify_mixed_segment(
        self,
        segment: str,
        segment_index: int,
        state: dict[str, Any],
        parsed_entities: dict[str, str],
        preferred_slots: list[str],
    ) -> Optional[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for slot_name in preferred_slots:
            match = self._mixed_slot_candidate(slot_name, segment, segment_index, state, parsed_entities)
            if match is not None:
                candidates.append(match)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item["score"], item["priority"]), reverse=True)
        best = candidates[0]
        if (
            len(candidates) > 1
            and candidates[1]["score"] >= 0.55
            and best["score"] - candidates[1]["score"] < 0.08
            and best["priority"] <= candidates[1]["priority"]
        ):
            return None
        return best

    def _mixed_slot_candidate(
        self,
        slot_name: str,
        segment: str,
        segment_index: int,
        state: dict[str, Any],
        parsed_entities: dict[str, str],
    ) -> Optional[dict[str, Any]]:
        if slot_name == "system":
            direct = self.search_repository.resolve_system_candidates(segment, limit=5)
            browse = self.search_repository.browse_system_candidates(
                query_text=segment,
                city=parsed_entities.get("city_raw") or state.get("city_raw"),
                department=parsed_entities.get("department_raw") or state.get("department_raw"),
                position=parsed_entities.get("position_raw") or state.get("position_raw"),
                limit=5,
            )
            pool = direct or browse
            if not pool:
                return None
            best = pool[0]
            second_score = float(pool[1].get("score") or 0.0) if len(pool) > 1 else 0.0
            score = float(best.get("score") or 0.0)
            exact = normalize_text(segment) in {
                normalize_text(best.get("system_name_raw")),
                normalize_text(best.get("alias_text")),
            }
            if exact:
                score = max(score, 1.05)
            segment_norm = normalize_text(segment)
            if (
                segment_index == 0
                and len(segment_norm.split()) == 1
                and 3 <= len(segment_norm) <= 8
                and score >= 0.75
            ):
                score = max(score, 0.9)
            status = "resolved" if exact or score >= 0.78 or (len(pool) == 1 and score >= 0.55) else "ambiguous"
            if status == "ambiguous" and score < 0.45:
                return None
            return {
                "slot": slot_name,
                "status": status,
                "score": score if status == "resolved" else max(score, second_score),
                "priority": 6 if segment_index == 0 else 4,
                "value": best["system_name_raw"] if status == "resolved" else segment,
                "display": best["system_name_raw"] if status == "resolved" else segment,
                "resolution_mode": "DIRECT" if direct else "BROWSE",
            }
        if slot_name == "city":
            pool = self.search_repository.find_city_candidates(segment, limit=5)
        elif slot_name == "department":
            pool = self.search_repository.find_department_candidates(
                segment,
                city=parsed_entities.get("city_raw") or state.get("city_raw"),
                position=parsed_entities.get("position_raw") or state.get("position_raw"),
                limit=5,
            )
        elif slot_name == "position":
            pool = self.search_repository.find_position_candidates(
                segment,
                city=parsed_entities.get("city_raw") or state.get("city_raw"),
                department=parsed_entities.get("department_raw") or state.get("department_raw"),
                limit=5,
            )
        else:
            return None
        if not pool:
            return None
        best = pool[0]
        best_value = self._clean_slot_text(best.get("value"))
        if not best_value:
            return None
        second_score = float(pool[1].get("score") or 0.0) if len(pool) > 1 else 0.0
        score = float(best.get("score") or 0.0)
        exact = normalize_text(segment) == normalize_text(best_value)
        strong_threshold = {"city": 0.88, "department": 0.86, "position": 0.86}[slot_name]
        status = "resolved" if exact or score >= strong_threshold or (len(pool) == 1 and score >= 0.55) else "ambiguous"
        if status == "ambiguous" and score < 0.45:
            return None
        return {
            "slot": slot_name,
            "status": status,
            "score": score if status == "resolved" else max(score, second_score),
            "priority": {"city": 3, "position": 2, "department": 1}[slot_name],
            "value": best_value if status == "resolved" else segment,
            "display": best_value if status == "resolved" else segment,
        }

    def _build_mixed_parse_prefix(
        self,
        recognized: list[tuple[str, str]],
        ambiguous: list[tuple[str, str]],
        unresolved: list[str],
    ) -> str:
        labels = {
            "system": "АС",
            "position": "должность",
            "city": "город",
            "department": "отдел",
        }
        parts: list[str] = []
        if recognized:
            recognized_text = ", ".join(f"{labels[slot]} = {value}" for slot, value in recognized)
            parts.append(f"Удалось определить: {recognized_text}.")
        if ambiguous:
            ambiguous_text = ", ".join(f"{labels[slot]} = {value}" for slot, value in ambiguous)
            parts.append(f"Нужно уточнить: {ambiguous_text}.")
        if unresolved:
            parts.append(f"Не удалось определить: {', '.join(unresolved)}.")
        return " ".join(parts).strip()

    def _build_mixed_state_prefix(
        self,
        original_state: dict[str, Any],
        final_state: dict[str, Any],
        unresolved: list[str],
    ) -> str:
        labels = {
            "system": "АС",
            "position": "должность",
            "city": "город",
            "department": "отдел",
        }
        recognized: list[str] = []
        for slot_name, state_key in (
            ("system", "system_raw"),
            ("position", "position_raw"),
            ("city", "city_raw"),
            ("department", "department_raw"),
        ):
            final_value = self._clean_slot_text(final_state.get(state_key))
            original_value = self._clean_slot_text(original_state.get(state_key))
            if slot_name == "system" and final_state.get("resolved_system_id"):
                system = self.search_repository.get_active_system(int(final_state["resolved_system_id"]))
                if system and system.get("system_name_raw"):
                    final_value = self._clean_slot_text(system.get("system_name_raw"))
            if final_value and similarity(final_value, original_value) < 0.98:
                recognized.append(f"{labels[slot_name]} = {final_value}")
        parts: list[str] = []
        if recognized:
            parts.append(f"Удалось определить: {', '.join(recognized)}.")
        if unresolved:
            parts.append(f"Не удалось определить: {', '.join(unresolved)}.")
        return " ".join(parts).strip()

    def _normalize_entities(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key in (
            "system_raw",
            "position_raw",
            "city_raw",
            "department_raw",
            "requested_entitlement_raw",
            "selection_text",
        ):
            cleaned = self._clean_slot_text(value.get(key))
            if cleaned:
                normalized[key] = cleaned
        selection_number = value.get("selection_number")
        if isinstance(selection_number, int):
            normalized["selection_number"] = selection_number
        elif isinstance(selection_number, str) and selection_number.strip().isdigit():
            normalized["selection_number"] = int(selection_number.strip())
        return normalized

    def _normalize_slot_candidates(self, value: object) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, list[dict[str, Any]]] = {}
        for slot_name, raw_candidates in value.items():
            if slot_name not in {"system", "position", "city", "department", "profile"}:
                continue
            if not isinstance(raw_candidates, list):
                continue
            items: list[dict[str, Any]] = []
            for item in raw_candidates:
                if not isinstance(item, dict):
                    continue
                candidate_value = self._clean_slot_text(item.get("value"))
                if not candidate_value:
                    continue
                items.append(
                    {
                        "value": candidate_value,
                        "score": self._normalize_confidence(item.get("score")),
                    }
                )
            if items:
                normalized[slot_name] = items
        return normalized

    @staticmethod
    def _normalize_goal_transition(value: object) -> Optional[str]:
        normalized = str(value or "").strip().upper()
        if normalized in {"START", "SWITCH", "STAY"}:
            return normalized
        return None

    @staticmethod
    def _looks_like_echo_candidate_set(slot_value: str, candidates: list[dict[str, Any]]) -> bool:
        if not candidates:
            return True
        if len(candidates) != 1:
            return False
        candidate_value = " ".join(str(candidates[0].get("value") or "").strip().lower().split())
        source_value = " ".join(str(slot_value or "").strip().lower().split())
        return candidate_value == source_value

    @staticmethod
    def _clean_slot_text(value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.lower() in {"null", "none", "n/a", "не указано"}:
            return None
        return text

    def _infer_system_resolution_mode(self, query_text: Optional[str]) -> Optional[str]:
        normalized = normalize_text(query_text)
        if not normalized:
            return None
        if re.search(r"\bci\d+\b", normalized, flags=re.IGNORECASE):
            return "DIRECT"
        tokens = normalized.split()
        has_digit = any(ch.isdigit() for ch in normalized)
        if len(tokens) <= 2 and not has_digit:
            return "BROWSE"
        return "DIRECT"

    def _coerce_generic_access_issue_to_system(
        self,
        interpretation: TurnInterpretation,
        entities: dict[str, Any],
        state: dict[str, Any],
    ) -> Optional[str]:
        raw_entitlement = self._clean_slot_text(entities.get("requested_entitlement_raw"))
        if not raw_entitlement:
            return None
        if entities.get("system_raw") or state.get("resolved_system_id") or state.get("system_raw"):
            return None
        if interpretation.intent_type not in {"ROLE_ACQUISITION", "UNKNOWN"}:
            return None
        entitlement_norm = normalize_text(raw_entitlement)
        tokens = [token for token in entitlement_norm.split() if token]
        if len(tokens) > 6:
            return None
        if "роль" in entitlement_norm or "полномоч" in entitlement_norm:
            return None
        generic_tokens = {
            "нет",
            "не",
            "доступ",
            "доступа",
            "к",
            "в",
            "по",
            "для",
            "мне",
            "мой",
            "моя",
            "мои",
            "получить",
            "нужен",
            "нужна",
            "нужны",
            "нужное",
            "нужной",
            "нужную",
            "открыть",
            "войти",
            "вход",
            "есть",
            "нужен",
            "нужна",
            "нужно",
            "нужны",
        }
        signal_tokens = [token for token in tokens if token not in generic_tokens and len(token) >= 2]
        if not signal_tokens:
            return None
        query_variants: list[str] = [raw_entitlement]
        compact_signal_query = " ".join(signal_tokens)
        if compact_signal_query and normalize_text(compact_signal_query) != entitlement_norm:
            query_variants.append(compact_signal_query)
        best: Optional[dict[str, Any]] = None
        best_score = 0.0
        for query in query_variants:
            candidates = self.search_repository.resolve_system_candidates(query, limit=3)
            if not candidates:
                continue
            candidate = candidates[0]
            score = float(candidate.get("score") or 0.0)
            if best is None or score > best_score:
                best = candidate
                best_score = score
        if best is None:
            return None
        min_score = 0.45 if len(signal_tokens) <= 2 else 0.60
        if best_score < min_score:
            return None
        text_tokens = set(signal_tokens)
        alias_norm = normalize_text(best.get("alias_text"))
        name_norm = normalize_text(best.get("system_name_raw"))
        alias_tokens = {token for token in alias_norm.split() if len(token) >= 2}
        name_tokens = {token for token in name_norm.split() if len(token) >= 2}
        alias_coverage = (len(alias_tokens & text_tokens) / float(len(alias_tokens))) if alias_tokens else 0.0
        name_coverage = (len(name_tokens & text_tokens) / float(len(name_tokens))) if name_tokens else 0.0
        if max(alias_coverage, name_coverage) < 0.5:
            return None
        return self._clean_slot_text(best.get("system_name_raw"))

    def _coerce_system_from_role_like_reference(
        self,
        interpretation: TurnInterpretation,
        entities: dict[str, Any],
        state: dict[str, Any],
    ) -> Optional[str]:
        raw_entitlement = self._clean_slot_text(entities.get("requested_entitlement_raw"))
        if not raw_entitlement:
            return None
        if interpretation.intent_type not in {"ROLE_ACQUISITION", "ROLE_DISCOVERY", "UNKNOWN"}:
            return None
        entitlement_norm = normalize_text(raw_entitlement)
        tokens = [token for token in entitlement_norm.split() if token]
        if not tokens or len(tokens) > 3:
            return None
        if "роль" in entitlement_norm or "полномоч" in entitlement_norm:
            return None
        candidate = self._clean_slot_text(entities.get("system_raw")) or self._clean_slot_text(state.get("system_raw"))
        if candidate:
            candidate_norm = normalize_text(candidate)
            if entitlement_norm == candidate_norm or entitlement_norm in candidate_norm:
                return candidate
        candidates = self.search_repository.resolve_system_candidates(raw_entitlement, limit=3)
        if not candidates:
            return None
        best = candidates[0]
        best_name = normalize_text(best.get("system_name_raw"))
        best_alias = normalize_text(best.get("alias_text"))
        if entitlement_norm == best_alias or entitlement_norm in best_name:
            return self._clean_slot_text(best.get("system_name_raw"))
        if len(tokens) == 1:
            token = tokens[0]
            if token in set(best_name.split()) or token in set(best_alias.split()):
                return self._clean_slot_text(best.get("system_name_raw"))
        best_score = float(best.get("score") or 0.0)
        if best_score < 0.35:
            return None
        return None

    @staticmethod
    def _position_input_is_ambiguous(slot_value: str, candidates: list[dict[str, Any]]) -> bool:
        normalized_value = normalize_text(slot_value)
        tokens = [token for token in normalized_value.split() if token]
        if len(tokens) == 0 or len(tokens) > 2:
            return False
        if len(candidates) < 2:
            return False
        top_score = float(candidates[0].get("score") or 0.0)
        close_candidates = [
            candidate
            for candidate in candidates
            if float(candidate.get("score") or 0.0) >= max(0.0, top_score - 0.08)
        ]
        if len(close_candidates) < 2:
            return False
        unique_labels = {
            normalize_text(str(candidate.get("value") or ""))
            for candidate in close_candidates
            if str(candidate.get("value") or "").strip()
        }
        return len(unique_labels) >= 2

    @staticmethod
    def _access_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "system_id": row["system_id"],
            "system_name": row["system_name"],
            "entitlement_id": row["entitlement_id"],
            "entitlement_type": row["entitlement_type"],
            "entitlement_name": row["entitlement_name"],
            "access_level": row["access_level"],
            "justification_text": row.get("justification_text"),
            "inline_comment": row.get("inline_comment"),
        }

    @staticmethod
    def _format_access_details(accesses: list[dict[str, Any]], limit: int) -> str:
        if not accesses:
            return "нет"
        labels: list[str] = []
        for item in accesses[:limit]:
            ent_type = str(item.get("entitlement_type") or "").strip()
            ent_name = str(item.get("entitlement_name") or "").strip()
            if ent_type and ent_name:
                labels.append(f"{ent_type}: {ent_name}")
            elif ent_name:
                labels.append(ent_name)
        if not labels:
            return "нет"
        remainder = len(accesses) - len(labels)
        if remainder > 0:
            labels.append(f"и еще {remainder}")
        return "; ".join(labels)
