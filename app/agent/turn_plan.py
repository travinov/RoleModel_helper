from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PlannedAction(str, Enum):
    ASK_SLOT = "ASK_SLOT"
    PROMPT_CANDIDATES = "PROMPT_CANDIDATES"
    APPLY_SELECTION = "APPLY_SELECTION"
    ANSWER_SYSTEM_DISCOVERY = "ANSWER_SYSTEM_DISCOVERY"
    ANSWER_ROLE_DISCOVERY = "ANSWER_ROLE_DISCOVERY"
    ANSWER_INSTRUCTION = "ANSWER_INSTRUCTION"
    RESET_CONTEXT = "RESET_CONTEXT"
    CONTINUE = "CONTINUE"


class SlotResolutionStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    CANDIDATES = "CANDIDATES"
    REJECTED = "REJECTED"
    MISSING = "MISSING"


class SlotSourceKind(str, Enum):
    LLM_ENTITY = "LLM_ENTITY"
    USER_SLOT_REPLY = "USER_SLOT_REPLY"
    CANDIDATE_SELECTION = "CANDIDATE_SELECTION"
    MIXED_SEGMENT = "MIXED_SEGMENT"
    FULL_UTTERANCE_FALLBACK = "FULL_UTTERANCE_FALLBACK"


@dataclass(frozen=True)
class SlotResolution:
    slot_name: str
    raw_value: Optional[str]
    status: SlotResolutionStatus
    canonical_value: Optional[str] = None
    canonical_id: Optional[int] = None
    confidence: float = 0.0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    source_kind: SlotSourceKind = SlotSourceKind.LLM_ENTITY
    reason: str = ""


@dataclass(frozen=True)
class TurnPlan:
    action: PlannedAction
    intent_type: str
    dialog_act: str
    conversation_phase: Optional[str] = None
    slot_name: Optional[str] = None
    prompt: Optional[str] = None
    slot_resolutions: list[SlotResolution] = field(default_factory=list)
    tool_name: Optional[str] = None
    response_prefix: Optional[str] = None
