from __future__ import annotations

from typing import Any

from app.agent.turn_plan import SlotResolution, SlotResolutionStatus, SlotSourceKind
from app.services.text import normalize_text


STRICT_SYSTEM_ACCEPT_SCORE = 0.74
STRICT_SYSTEM_SUGGEST_SCORE = 0.45
ORG_SLOT_ACCEPT_SCORE = 0.88
ORG_SLOT_ACCEPT_GAP = 0.12


class SlotResolutionService:
    def __init__(self, search_repository) -> None:
        self.search_repository = search_repository

    def resolve_system(
        self,
        raw_value: str | None,
        state: dict[str, Any],
        source_kind: SlotSourceKind,
    ) -> SlotResolution:
        value = str(raw_value or "").strip()
        if not value:
            return SlotResolution("system", raw_value, SlotResolutionStatus.MISSING, source_kind=source_kind)

        if source_kind == SlotSourceKind.FULL_UTTERANCE_FALLBACK:
            return SlotResolution(
                slot_name="system",
                raw_value=value,
                status=SlotResolutionStatus.MISSING,
                source_kind=source_kind,
                reason="full_utterance_never_commits_system",
            )

        candidates = self.search_repository.resolve_system_candidates(value, limit=8)
        if not candidates:
            return SlotResolution(
                slot_name="system",
                raw_value=value,
                status=SlotResolutionStatus.REJECTED,
                source_kind=source_kind,
                reason="not_found_in_system_dictionary",
            )

        best = candidates[0]
        best_score = float(best.get("score") or 0.0)
        value_norm = normalize_text(value)
        alias_class = str(best.get("alias_class") or "SAFE").upper()
        exact_system_name = value_norm == normalize_text(best.get("system_name_raw"))
        exact_safe_alias = alias_class == "SAFE" and value_norm == normalize_text(best.get("alias_text"))
        exact = exact_system_name or exact_safe_alias
        matched_by = set(best.get("matched_by") or [])
        second_score = float(candidates[1].get("score") or 0.0) if len(candidates) > 1 else 0.0
        if alias_class == "UNSAFE" and not exact_system_name:
            return SlotResolution(
                slot_name="system",
                raw_value=value,
                status=SlotResolutionStatus.REJECTED,
                confidence=best_score,
                candidates=candidates,
                source_kind=source_kind,
                reason="unsafe_system_alias",
            )
        if alias_class == "AMBIGUOUS" and not exact_system_name:
            return SlotResolution(
                slot_name="system",
                raw_value=value,
                status=SlotResolutionStatus.CANDIDATES,
                confidence=best_score,
                candidates=[item for item in candidates if float(item.get("score") or 0.0) >= STRICT_SYSTEM_SUGGEST_SCORE],
                source_kind=source_kind,
                reason="ambiguous_system_alias",
            )
        strong_score_match = best_score >= STRICT_SYSTEM_ACCEPT_SCORE and (
            len(candidates) == 1 or best_score - second_score >= 0.12
        )
        strong_match = exact or (alias_class == "SAFE" and "bracket_abbreviation" in matched_by) or strong_score_match

        if strong_match:
            return SlotResolution(
                slot_name="system",
                raw_value=value,
                status=SlotResolutionStatus.ACCEPTED,
                canonical_value=str(best.get("system_name_raw") or value),
                canonical_id=int(best["system_id"]),
                confidence=best_score,
                candidates=candidates,
                source_kind=source_kind,
                reason="strict_system_match",
            )

        filtered = [item for item in candidates if float(item.get("score") or 0.0) >= STRICT_SYSTEM_SUGGEST_SCORE]
        if filtered:
            return SlotResolution(
                slot_name="system",
                raw_value=value,
                status=SlotResolutionStatus.CANDIDATES,
                confidence=best_score,
                candidates=filtered,
                source_kind=source_kind,
                reason="ambiguous_system_candidates",
            )

        return SlotResolution(
            slot_name="system",
            raw_value=value,
            status=SlotResolutionStatus.REJECTED,
            source_kind=source_kind,
            reason="weak_system_candidates_rejected",
        )

    def resolve_org_slot(
        self,
        slot_name: str,
        raw_value: str | None,
        state: dict[str, Any],
        source_kind: SlotSourceKind,
    ) -> SlotResolution:
        value = str(raw_value or "").strip()
        if not value:
            return SlotResolution(slot_name, raw_value, SlotResolutionStatus.MISSING, source_kind=source_kind)

        if slot_name == "city":
            candidates = self.search_repository.find_city_candidates(value, limit=8)
        elif slot_name == "position":
            candidates = self.search_repository.find_position_candidates(
                value,
                city=state.get("city_raw"),
                department=state.get("department_raw"),
                limit=8,
            )
        elif slot_name == "department":
            candidates = self.search_repository.find_department_candidates(
                value,
                city=state.get("city_raw"),
                position=state.get("position_raw"),
                limit=8,
            )
        else:
            return SlotResolution(slot_name, value, SlotResolutionStatus.REJECTED, source_kind=source_kind, reason="unknown_slot")

        if not candidates:
            return SlotResolution(slot_name, value, SlotResolutionStatus.REJECTED, source_kind=source_kind, reason="not_found_in_dictionary")

        best = candidates[0]
        best_score = float(best.get("score") or 0.0)
        canonical_value = str(best.get("value") or "").strip()
        if canonical_value and normalize_text(canonical_value) == normalize_text(value):
            return SlotResolution(
                slot_name,
                value,
                SlotResolutionStatus.ACCEPTED,
                canonical_value=canonical_value,
                confidence=1.0,
                candidates=candidates,
                source_kind=source_kind,
                reason="exact_org_slot_match",
            )

        second_score = float(candidates[1].get("score") or 0.0) if len(candidates) > 1 else 0.0
        if canonical_value and best_score >= ORG_SLOT_ACCEPT_SCORE and (len(candidates) == 1 or best_score - second_score >= ORG_SLOT_ACCEPT_GAP):
            return SlotResolution(
                slot_name,
                value,
                SlotResolutionStatus.ACCEPTED,
                canonical_value=canonical_value,
                confidence=best_score,
                candidates=candidates,
                source_kind=source_kind,
                reason="confident_org_slot_match",
            )

        return SlotResolution(
            slot_name,
            value,
            SlotResolutionStatus.CANDIDATES,
            confidence=best_score,
            candidates=candidates,
            source_kind=source_kind,
            reason="ambiguous_org_slot",
        )
