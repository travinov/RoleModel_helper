from __future__ import annotations

from typing import Any

from app.models.domain import TurnInterpretation


ALL_INTENTS = {
    "SYSTEM_DISCOVERY",
    "ROLE_DISCOVERY",
    "ROLE_ACQUISITION",
    "JUSTIFICATION_LOOKUP",
    "INSTRUCTION_LOOKUP",
    "UNKNOWN",
}
STRUCTURED_INTENTS = {"SYSTEM_DISCOVERY", "ROLE_DISCOVERY", "ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"}


class ConversationPolicyService:
    def __init__(self, search_repository) -> None:
        self.search_repository = search_repository

    def apply_goal_transition(
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
        goal_transition = str(interpretation.goal_transition or "STAY").upper()

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
        elif goal_transition == "STAY" and next_goal in STRUCTURED_INTENTS and interpreted_goal == "INSTRUCTION_LOOKUP":
            goal_stack = (goal_stack + [next_goal])[-3:]
            next_goal = interpreted_goal
        elif goal_transition == "STAY" and next_goal == "INSTRUCTION_LOOKUP" and interpreted_goal in STRUCTURED_INTENTS:
            next_goal = interpreted_goal
        elif goal_transition == "SWITCH" and next_goal in STRUCTURED_INTENTS and interpreted_goal == "INSTRUCTION_LOOKUP":
            goal_stack = (goal_stack + [next_goal])[-3:]
            next_goal = interpreted_goal

        self.search_repository.update_slot_state(
            session_id,
            **self._goal_state_updates(
                next_goal=next_goal,
                goal_stack=goal_stack,
                interpreted_goal=interpreted_goal,
                goal_transition=goal_transition,
                context_shift=interpretation.context_shift,
            ),
        )
        if (
            next_goal == "SYSTEM_DISCOVERY"
            and interpreted_goal == "SYSTEM_DISCOVERY"
            and goal_transition in {"START", "SWITCH"}
        ):
            self.search_repository.set_session_resolution(
                session_id,
                intent_type=next_goal,
                system_id=None,
                profile_id=None,
            )
            return
        self.search_repository.set_session_resolution(session_id, intent_type=next_goal)

    def _goal_state_updates(
        self,
        *,
        next_goal: str,
        goal_stack: list[str],
        interpreted_goal: str,
        goal_transition: str,
        context_shift: str,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "active_goal": next_goal,
            "goal_stack": goal_stack,
            "last_intent_type": next_goal,
            "context_shift": context_shift,
        }
        # Entering SYSTEM_DISCOVERY should always rebuild the system scope from org context.
        if (
            next_goal == "SYSTEM_DISCOVERY"
            and interpreted_goal == "SYSTEM_DISCOVERY"
            and goal_transition in {"START", "SWITCH"}
        ):
            updates.update(
                {
                    "system_raw": None,
                    "system_query_raw": None,
                    "system_resolution_mode": None,
                    "resolved_system_id": None,
                    "resolved_profile_id": None,
                    "profile_candidates": None,
                    "requested_entitlement_raw": None,
                    "requested_entitlement_type_hint": None,
                    "pending_question": None,
                    "pending_slot": None,
                    "needs_confirmation": False,
                    "confirmation_topic": None,
                    "confirmation_options": None,
                    "conversation_phase": None,
                }
            )
        return updates

    def apply_context_shift(
        self,
        session_id: str,
        state: dict[str, Any],
        interpretation: TurnInterpretation,
    ) -> dict[str, Any]:
        shift = str(interpretation.context_shift or "NONE").upper()
        if shift == "NONE":
            self.search_repository.update_slot_state(session_id, context_shift="NONE")
            return {}

        if shift == "CHANGE_SYSTEM_FOCUS":
            had_system_context = bool(state.get("resolved_system_id") or state.get("system_raw"))
            if not had_system_context:
                self.search_repository.update_slot_state(session_id, context_shift="NONE")
                interpretation.context_shift = "NONE"
                return {}
            self.search_repository.close_candidate_sets(session_id, topics=["system", "profile"])
            self.search_repository.update_slot_state(
                session_id,
                system_raw=None,
                system_query_raw=None,
                system_resolution_mode=None,
                resolved_system_id=None,
                resolved_profile_id=None,
                profile_candidates=None,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
                instruction_mode=None,
                conversation_phase=None,
                context_shift=shift,
            )
            self.search_repository.set_session_resolution(session_id, system_id=None, profile_id=None)
            return {"response_prefix": "Переключаюсь на другую АС. Сохранил должность, город и отдел."}

        if shift == "CHANGE_ORG_CONTEXT":
            self.search_repository.close_candidate_sets(session_id, topics=["profile"])
            self.search_repository.update_slot_state(
                session_id,
                resolved_profile_id=None,
                profile_candidates=None,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
                instruction_mode=None,
                context_shift=shift,
            )
            self.search_repository.set_session_resolution(session_id, profile_id=None)
            return {"response_prefix": "Принял обновление орг-контекста. Пересобираю подбор по новым данным."}

        if shift == "SWITCH_GOAL":
            self.search_repository.update_slot_state(session_id, context_shift=shift)
            return {}

        self.search_repository.update_slot_state(session_id, context_shift=shift)
        return {}

    def save_resume_point(self, session_id: str, state: dict[str, Any]) -> None:
        active_goal = state.get("active_goal") or state.get("last_intent_type") or "UNKNOWN"
        if active_goal not in STRUCTURED_INTENTS:
            return
        self.search_repository.update_slot_state(
            session_id,
            resume_goal=active_goal,
            resume_phase=state.get("conversation_phase"),
        )

    def restore_previous_goal(self, session_id: str, state: dict[str, Any]) -> None:
        resume_goal = state.get("resume_goal")
        resume_phase = state.get("resume_phase")
        if resume_goal:
            self.search_repository.update_slot_state(
                session_id,
                active_goal=resume_goal,
                last_intent_type=resume_goal,
                conversation_phase=resume_phase,
                resume_goal=None,
                resume_phase=None,
            )
            self.search_repository.set_session_resolution(session_id, intent_type=resume_goal)
            return

        goal_stack = list(state.get("goal_stack") or [])
        if not goal_stack:
            return
        previous_goal = goal_stack[-1]
        remaining = goal_stack[:-1]
        self.search_repository.update_slot_state(
            session_id,
            active_goal=previous_goal,
            goal_stack=remaining,
            last_intent_type=previous_goal,
        )
        self.search_repository.set_session_resolution(session_id, intent_type=previous_goal)

    def reset_context(self, session_id: str) -> None:
        self.search_repository.close_candidate_sets(session_id)
        self.search_repository.update_slot_state(
            session_id,
            city_raw=None,
            city_normalized=None,
            department_raw=None,
            department_normalized=None,
            position_raw=None,
            position_normalized=None,
            system_raw=None,
            resolved_system_id=None,
            profile_candidates=None,
            resolved_profile_id=None,
            needs_confirmation=False,
            confirmation_topic=None,
            confirmation_options=None,
            pending_slot=None,
            requested_entitlement_raw=None,
            requested_entitlement_type_hint=None,
            last_intent_type="UNKNOWN",
            active_goal="UNKNOWN",
            resume_goal=None,
            conversation_phase=None,
            resume_phase=None,
            context_shift="RESET_CONTEXT",
            system_query_raw=None,
            system_resolution_mode=None,
            instruction_mode=None,
            goal_stack=[],
            pending_question=None,
            context_snapshot={},
        )
        self.search_repository.set_session_resolution(
            session_id,
            intent_type="UNKNOWN",
            system_id=None,
            profile_id=None,
        )
