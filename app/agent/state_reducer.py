from __future__ import annotations

from app.agent.turn_plan import SlotResolution, SlotResolutionStatus
from app.models.domain import ToolAttempt
from app.services.text import normalize_text


class StateReducer:
    def __init__(self, search_repository) -> None:
        self.search_repository = search_repository

    def apply_slot_resolution(self, session_id: str, resolution: SlotResolution) -> None:
        if resolution.status != SlotResolutionStatus.ACCEPTED:
            return
        if resolution.slot_name == "system":
            self.search_repository.close_candidate_sets(session_id, topics=["system", "profile"])
            self.search_repository.update_slot_state(
                session_id,
                system_raw=resolution.canonical_value,
                system_query_raw=resolution.raw_value,
                system_resolution_mode="DIRECT",
                resolved_system_id=resolution.canonical_id,
                resolved_profile_id=None,
                profile_candidates=None,
                instruction_mode=None,
                pending_question=None,
                pending_slot=None,
                needs_confirmation=False,
                confirmation_topic=None,
                confirmation_options=None,
            )
            self.search_repository.set_session_resolution(session_id, system_id=resolution.canonical_id, profile_id=None)
            return

        if resolution.slot_name in {"city", "position", "department"}:
            field = f"{resolution.slot_name}_raw"
            normalized_field = f"{resolution.slot_name}_normalized"
            self.search_repository.close_candidate_sets(session_id, topics=[resolution.slot_name, "profile"])
            self.search_repository.update_slot_state(
                session_id,
                **{
                    field: resolution.canonical_value,
                    normalized_field: normalize_text(resolution.canonical_value),
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

    def apply_rejected_slot(self, session_id: str, resolution: SlotResolution) -> None:
        if resolution.status != SlotResolutionStatus.REJECTED:
            return
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="slot_resolution_rejected",
                attempt_no=1,
                input_payload={
                    "slot_name": resolution.slot_name,
                    "raw_value": resolution.raw_value,
                    "reason": resolution.reason,
                },
                result_status="success",
                result_summary=resolution.reason,
            ),
            {
                "resolution": {
                    "slot_name": resolution.slot_name,
                    "raw_value": resolution.raw_value,
                    "status": resolution.status.value,
                    "canonical_value": resolution.canonical_value,
                    "canonical_id": resolution.canonical_id,
                    "confidence": resolution.confidence,
                    "source_kind": resolution.source_kind.value,
                    "reason": resolution.reason,
                }
            },
        )
