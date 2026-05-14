from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


ConversationPhase = Literal[
    "COLLECT_POSITION",
    "COLLECT_CITY",
    "COLLECT_DEPARTMENT",
    "BROWSE_SYSTEMS",
    "COLLECT_SYSTEM_HINT",
    "RESOLVE_PROFILE",
    "ANSWER_SYSTEM_DISCOVERY",
    "ANSWER_ROLE_DISCOVERY",
    "ANSWER_INSTRUCTION",
]

SystemResolutionMode = Literal["DIRECT", "BROWSE"]
InstructionMode = Literal["INLINE_DOC", "RAG"]
ContextShift = Literal[
    "NONE",
    "CHANGE_SYSTEM_FOCUS",
    "CHANGE_ORG_CONTEXT",
    "SWITCH_GOAL",
    "RESET_CONTEXT",
]


@dataclass
class ChatIntent:
    intent_type: str
    system_raw: Optional[str] = None
    entitlement_raw: Optional[str] = None
    city_raw: Optional[str] = None
    department_raw: Optional[str] = None
    position_raw: Optional[str] = None


@dataclass
class TurnInterpretation:
    dialog_act: str
    intent_type: str
    entities: dict[str, Any] = field(default_factory=dict)
    slot_candidates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    context_shift: str = "NONE"
    goal_transition: Optional[str] = None
    needs_clarification: bool = False
    reasoning_trace_short: Optional[str] = None
    confidence: float = 0.0
    references_pending_question: bool = False
    user_correction: bool = False


@dataclass
class ConversationGoal:
    intent_type: str


@dataclass
class PendingQuestion:
    kind: str
    topic: str
    prompt: str
    candidate_set_id: Optional[int] = None
    options: list[dict[str, Any]] = field(default_factory=list)
    has_more: bool = False
    page_offset: int = 0
    page_size: int = 0
    total_options: int = 0


@dataclass
class CandidateSet:
    candidate_set_id: int
    topic: str
    source_query: str
    page_size: int
    current_offset: int
    status: str
    options: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PolicyDecision:
    action: str
    intent_type: str
    message: Optional[str] = None


@dataclass
class RetrievedChunk:
    chunk_id: int
    source_id: int
    source_title: str
    slide_no: Optional[int]
    chunk_type: str
    chunk_text: str
    score: float
    citation_label: str
    locator_text: str


@dataclass
class ToolAttempt:
    tool_name: str
    attempt_no: int
    input_payload: dict
    result_status: str
    result_summary: str
    error_text: Optional[str] = None


@dataclass
class SearchAnswer:
    answer_type: str
    summary_text: str
    profile: Optional[dict] = None
    systems: list[dict] = field(default_factory=list)
    default_accesses: list[dict] = field(default_factory=list)
    request_accesses: list[dict] = field(default_factory=list)
    justification: Optional[str] = None
    instruction: Optional[str] = None
    citations: list[RetrievedChunk] = field(default_factory=list)
    support_recommendation: Optional[str] = None
