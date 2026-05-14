from __future__ import annotations

from typing import Any

from app.agent.turn_plan import PlannedAction, TurnPlan
from app.models.domain import TurnInterpretation


class TurnPlanner:
    def plan(self, state: dict[str, Any], interpretation: TurnInterpretation) -> TurnPlan:
        active_goal = state.get("active_goal") or state.get("last_intent_type") or interpretation.intent_type or "UNKNOWN"
        if interpretation.dialog_act == "RESET_CONTEXT":
            return TurnPlan(action=PlannedAction.RESET_CONTEXT, intent_type="UNKNOWN", dialog_act="RESET_CONTEXT")
        if interpretation.intent_type == "INSTRUCTION_LOOKUP":
            return TurnPlan(
                action=PlannedAction.ANSWER_INSTRUCTION,
                intent_type="INSTRUCTION_LOOKUP",
                dialog_act=interpretation.dialog_act,
                conversation_phase="ANSWER_INSTRUCTION",
            )
        if active_goal == "SYSTEM_DISCOVERY":
            return TurnPlan(
                action=PlannedAction.ANSWER_SYSTEM_DISCOVERY,
                intent_type="SYSTEM_DISCOVERY",
                dialog_act=interpretation.dialog_act,
                conversation_phase="ANSWER_SYSTEM_DISCOVERY",
            )
        if active_goal == "ROLE_DISCOVERY":
            return TurnPlan(
                action=PlannedAction.ANSWER_ROLE_DISCOVERY,
                intent_type="ROLE_DISCOVERY",
                dialog_act=interpretation.dialog_act,
                conversation_phase="ANSWER_ROLE_DISCOVERY",
            )
        return TurnPlan(action=PlannedAction.CONTINUE, intent_type=active_goal, dialog_act=interpretation.dialog_act)
