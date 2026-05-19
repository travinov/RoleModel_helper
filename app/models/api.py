from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


IntentType = Literal[
    "SYSTEM_DISCOVERY",
    "ROLE_DISCOVERY",
    "ROLE_ACQUISITION",
    "JUSTIFICATION_LOOKUP",
    "INSTRUCTION_LOOKUP",
    "UNKNOWN",
]


class ConfirmationOption(BaseModel):
    id: str
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConfirmationPayload(BaseModel):
    topic: str
    prompt: str
    options: list[ConfirmationOption] = Field(default_factory=list)


class PendingQuestionPayload(BaseModel):
    kind: str
    topic: str
    prompt: str
    candidate_set_id: Optional[int] = None
    options: list[ConfirmationOption] = Field(default_factory=list)
    has_more: bool = False
    page_offset: int = 0
    page_size: int = 0
    total_options: int = 0


class SuggestedActionPayload(BaseModel):
    id: str
    label: str
    text: str


class CitationPayload(BaseModel):
    source_id: int
    chunk_id: int
    source_title: str
    slide_no: Optional[int] = None
    citation_label: str
    locator_text: str


class SearchAnswerPayload(BaseModel):
    answer_type: str
    summary_text: str
    profile: Optional[dict[str, Any]] = None
    systems: list[dict[str, Any]] = Field(default_factory=list)
    default_accesses: list[dict[str, Any]] = Field(default_factory=list)
    request_accesses: list[dict[str, Any]] = Field(default_factory=list)
    justification: Optional[str] = None
    instruction: Optional[str] = None
    citations: list[CitationPayload] = Field(default_factory=list)
    support_recommendation: Optional[str] = None


class SessionFeedbackPayload(BaseModel):
    rating: Literal["UP", "DOWN"]
    comment: Optional[str] = None


class ChatMessageResponse(BaseModel):
    message_id: int
    session_id: str
    assistant_text: str
    intent_type: IntentType
    dialog_act: Optional[str] = None
    requires_confirmation: bool = False
    confirmation: Optional[ConfirmationPayload] = None
    resolved: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    active_goal: Optional[str] = None
    context_shift: Optional[str] = None
    conversation_phase: Optional[str] = None
    resume_goal: Optional[str] = None
    resume_phase: Optional[str] = None
    instruction_mode: Optional[str] = None
    pending_question: Optional[PendingQuestionPayload] = None
    state_revision: int = 0
    suggested_actions: list[SuggestedActionPayload] = Field(default_factory=list)
    answer: Optional[SearchAnswerPayload] = None


class SessionCreateResponse(BaseModel):
    session_id: str
    assistant_text: str


class SessionStateResponse(BaseModel):
    session_id: str
    status: str
    current_intent_type: Optional[str] = None
    feedback: Optional[SessionFeedbackPayload] = None
    resolved: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    active_goal: Optional[str] = None
    context_shift: Optional[str] = None
    conversation_phase: Optional[str] = None
    resume_goal: Optional[str] = None
    resume_phase: Optional[str] = None
    instruction_mode: Optional[str] = None
    pending_question: Optional[PendingQuestionPayload] = None
    state_revision: int = 0
    suggested_actions: list[SuggestedActionPayload] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class UserMessageRequest(BaseModel):
    text: str


class SessionFeedbackRequest(BaseModel):
    rating: Literal["UP", "DOWN"]
    comment: Optional[str] = None


class DialogueExportRequest(BaseModel):
    date_from: date
    date_to: date


class AliasUpsertRequest(BaseModel):
    system_id: int
    alias_text: str
    alias_source: Literal["MANUAL"] = "MANUAL"


class RagSourceCreateRequest(BaseModel):
    title: str
    file_path: str
    source_type: Literal["PPTX", "PDF", "DOCX", "HTML", "TEXT"] = "PPTX"
    system_id: Optional[int] = None


class RagIngestRequest(BaseModel):
    source_id: Optional[int] = None
    file_path: Optional[str] = None
    title: Optional[str] = None
    source_type: Literal["PPTX", "PDF", "DOCX", "HTML", "TEXT"] = "PPTX"
    system_id: Optional[int] = None
