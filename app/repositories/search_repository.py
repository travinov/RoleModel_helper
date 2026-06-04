from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any, Optional

from psycopg2 import sql
from psycopg2.extras import Json

from app.models.domain import CandidateSet, ToolAttempt, TurnInterpretation
from app.services.text import normalize_text, similarity

from ..config import AppConfig
from ..db import db_cursor


_UNSET = object()
_JSON_STATE_FIELDS = {
    "profile_candidates",
    "confirmation_options",
    "goal_stack",
    "pending_question",
    "context_snapshot",
}
_MIN_TOKEN_LEN = 3
_CITY_TOKEN_COVERAGE_THRESHOLD = 1.0
_DEPARTMENT_MIN_SIMILARITY_FLOOR = 0.20
_DEPARTMENT_SIMILARITY_THRESHOLD = 0.45
_POSITION_MIN_SIMILARITY_FLOOR = 0.30
_POSITION_SIMILARITY_THRESHOLD = 0.60
_DEPARTMENT_TOKEN_COVERAGE_THRESHOLD = 0.25
_POSITION_TOKEN_COVERAGE_THRESHOLD = 0.5
_SLOT_CANDIDATE_SCORE_THRESHOLD = 0.30
_CITY_PATTERN = re.compile(r"\b(?:г|город)\.?\s*([a-zA-Zа-яА-Я-]+)", re.IGNORECASE)
_CITY_ALIASES = {
    "мск": "москва",
    "москоу": "москва",
    "спб": "санкт",
    "питер": "санкт",
}
_SYSTEM_STOP_TOKENS = {
    "ас",
    "автоматизированная",
    "система",
    "пром",
    "и2",
    "и3",
    "ci",
    "пкап",
}


def _pg_trgm_similarity(config: AppConfig) -> sql.Composed:
    return sql.SQL("{}.similarity").format(sql.Identifier(config.pg_trgm_schema))


def _token_set(value: str | None) -> set[str]:
    normalized = normalize_text(value)
    return {
        token
        for token in normalized.split()
        if len(token) >= _MIN_TOKEN_LEN or token.isdigit()
    }


def _numeric_tokens(value: str | None) -> set[str]:
    normalized = normalize_text(value)
    return set(re.findall(r"\b\d+\b", normalized))


def _token_coverage(query: str | None, haystack: str | None) -> float:
    query_tokens = _token_set(query)
    haystack_tokens = _token_set(haystack)
    if not query_tokens or not haystack_tokens:
        return 0.0
    return len(query_tokens & haystack_tokens) / float(len(query_tokens))


def _soft_token_coverage(query: str | None, haystack: str | None) -> float:
    query_tokens = _token_set(query)
    haystack_tokens = _token_set(haystack)
    if not query_tokens or not haystack_tokens:
        return 0.0
    matched = 0
    for query_token in query_tokens:
        prefix_len = 5 if len(query_token) >= 7 else 3
        query_prefix = query_token[:prefix_len]
        if any(
            query_token == haystack_token
            or haystack_token.startswith(query_prefix)
            or query_token.startswith(haystack_token[:prefix_len])
            or similarity(query_token, haystack_token) >= 0.72
            for haystack_token in haystack_tokens
        ):
            matched += 1
    return matched / float(len(query_tokens))


def _meaningful_system_tokens(value: str | None) -> set[str]:
    return {
        token
        for token in _token_set(re.sub(r"\bci\d+\b", " ", normalize_text(value), flags=re.IGNORECASE))
        if token not in _SYSTEM_STOP_TOKENS and not re.fullmatch(r"и\d+", token)
    }


def _bracket_terms(value: str | None) -> set[str]:
    terms: set[str] = set()
    for group in re.findall(r"\(([^)]*)\)", str(value or "")):
        for part in re.split(r"[,;/]+", group):
            normalized = normalize_text(part)
            if normalized:
                terms.add(normalized)
    return terms


def _score_system_candidate(query_text: str, system_name: str, alias_text: str | None = None) -> dict[str, Any]:
    query_norm = normalize_text(query_text)
    name_norm = normalize_text(system_name)
    alias_norm = normalize_text(alias_text)
    matched_by: list[str] = []
    scores = [
        similarity(query_norm, name_norm),
        similarity(query_norm, alias_norm),
    ]
    if query_norm and (query_norm in name_norm or (alias_norm and query_norm in alias_norm)):
        scores.append(0.92)
        matched_by.append("substring")

    query_tokens = _meaningful_system_tokens(query_text)
    name_tokens = _meaningful_system_tokens(system_name)
    alias_tokens = _meaningful_system_tokens(alias_text)
    if query_tokens:
        name_coverage = max(
            len(query_tokens & name_tokens) / float(len(query_tokens)) if name_tokens else 0.0,
            _soft_token_coverage(query_text, system_name),
        )
        alias_coverage = max(
            len(query_tokens & alias_tokens) / float(len(query_tokens)) if alias_tokens else 0.0,
            _soft_token_coverage(query_text, alias_text),
        )
        coverage = max(name_coverage, alias_coverage)
        if coverage > 0:
            scores.append(0.30 + 0.62 * coverage)
            matched_by.append("token_coverage")

    bracket_terms = _bracket_terms(system_name) | _bracket_terms(alias_text)
    if query_norm and query_norm in bracket_terms:
        scores.append(0.95)
        matched_by.append("bracket_abbreviation")

    exact = query_norm and query_norm in {name_norm, alias_norm}
    if exact:
        scores.append(1.0)
        matched_by.append("exact")

    score = max(scores) if scores else 0.0
    if not matched_by and score > 0:
        matched_by.append("trigram")
    return {
        "score": round(score, 4),
        "matched_by": sorted(set(matched_by)),
        "token_coverage": round(max(scores[2:] or [0.0]), 4) if len(scores) > 2 else 0.0,
    }


def _score_department_candidate(
    query_text: str,
    department_name: str,
    city: str | None = None,
    city_haystack: str | None = None,
    position: str | None = None,
    position_haystack: str | None = None,
) -> Optional[dict[str, Any]]:
    sim_score = similarity(query_text, department_name)
    coverage_score = max(_token_coverage(query_text, department_name), _soft_token_coverage(query_text, department_name))
    query_numbers = _numeric_tokens(query_text)
    if query_numbers:
        candidate_numbers = _numeric_tokens(department_name)
        if query_numbers & candidate_numbers:
            sim_score = min(1.0, sim_score + 0.2)
            coverage_score = min(1.0, coverage_score + 0.2)
        else:
            sim_score *= 0.6
            coverage_score *= 0.6
    text_score = max(sim_score, coverage_score)
    if text_score < _SLOT_CANDIDATE_SCORE_THRESHOLD:
        return None
    city_score = max(_token_coverage(city, city_haystack), _soft_token_coverage(city, city_haystack)) if city else 0.0
    position_score = max(
        similarity(position, position_haystack),
        _token_coverage(position, position_haystack),
        _soft_token_coverage(position, position_haystack),
    ) if position else 0.0
    total = 0.70 * text_score + 0.15 * city_score + 0.15 * position_score
    return {
        "score": round(total, 4),
        "department_text_score": round(text_score, 4),
        "city_compatibility": round(city_score, 4),
        "position_compatibility": round(position_score, 4),
    }


def _score_position_candidate(
    query_text: str,
    position_name: str,
    city: str | None = None,
    city_haystack: str | None = None,
    department: str | None = None,
    department_haystack: str | None = None,
) -> Optional[dict[str, Any]]:
    query_tokens = _token_set(query_text)
    if 0 < len(query_tokens) <= 2 and _soft_token_coverage(query_text, position_name) < 1.0:
        return None
    text_score = max(
        similarity(query_text, position_name),
        _token_coverage(query_text, position_name),
        _soft_token_coverage(query_text, position_name),
    )
    if text_score < _SLOT_CANDIDATE_SCORE_THRESHOLD:
        return None
    city_score = max(_token_coverage(city, city_haystack), _soft_token_coverage(city, city_haystack)) if city else 0.0
    department_score = max(
        similarity(department, department_haystack),
        _token_coverage(department, department_haystack),
        _soft_token_coverage(department, department_haystack),
    ) if department else 0.0
    total = 0.70 * text_score + 0.15 * city_score + 0.15 * department_score
    return {
        "score": round(total, 4),
        "position_text_score": round(text_score, 4),
        "city_compatibility": round(city_score, 4),
        "department_compatibility": round(department_score, 4),
    }


def _normalize_city_name(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    if text in _CITY_ALIASES:
        return _CITY_ALIASES[text]
    parts = [part for part in text.split() if part not in {"г", "город"}]
    if not parts:
        return ""
    first = parts[0]
    return _CITY_ALIASES.get(first, first)


def _city_display_name(city_name: str) -> str:
    if not city_name:
        return ""
    if city_name == "санкт":
        return "Санкт-Петербург"
    return "-".join(part.capitalize() for part in city_name.split("-"))


class SearchRepository:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def _ensure_feedback_schema(self) -> None:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_session_feedback (
                    session_id UUID PRIMARY KEY REFERENCES chat_session(id) ON DELETE CASCADE,
                    rating TEXT NOT NULL CHECK (rating IN ('UP', 'DOWN')),
                    comment TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO chat_session (id, status)
                VALUES (%s, 'ACTIVE')
                """,
                (session_id,),
            )
            cursor.execute(
                """
                INSERT INTO chat_slot_state (session_id, goal_stack, context_snapshot)
                VALUES (%s, '[]'::jsonb, '{}'::jsonb)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id,),
            )
        return session_id

    def close_active_sessions(self) -> None:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                UPDATE chat_session
                SET status = 'CLOSED',
                    updated_at = now()
                WHERE status = 'ACTIVE'
                """,
            )

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute("SELECT * FROM chat_session WHERE id = %s", (session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_session_feedback(self, session_id: str) -> Optional[dict[str, Any]]:
        self._ensure_feedback_schema()
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT rating, comment, created_at, updated_at
                FROM chat_session_feedback
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_session_feedback(self, session_id: str, rating: str, comment: Optional[str] = None) -> dict[str, Any]:
        self._ensure_feedback_schema()
        normalized_comment = (comment or "").strip() or None
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO chat_session_feedback (session_id, rating, comment)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE
                SET rating = EXCLUDED.rating,
                    comment = EXCLUDED.comment,
                    updated_at = now()
                RETURNING rating, comment, created_at, updated_at
                """,
                (session_id, rating, normalized_comment),
            )
            feedback = dict(cursor.fetchone())
            cursor.execute(
                """
                UPDATE chat_session
                SET updated_at = now()
                WHERE id = %s
                """,
                (session_id,),
            )
            return feedback

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT id, role, message_text, structured_payload, created_at
                FROM chat_message
                WHERE session_id = %s
                ORDER BY created_at, id
                """,
                (session_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_dialogue_export_sessions(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        self._ensure_feedback_schema()
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                WITH target_sessions AS (
                    SELECT DISTINCT s.id, s.created_at, s.updated_at, s.status
                    FROM chat_session s
                    WHERE s.created_at::date BETWEEN %s AND %s
                       OR EXISTS (
                           SELECT 1
                           FROM chat_message m
                           WHERE m.session_id = s.id
                             AND m.created_at::date BETWEEN %s AND %s
                       )
                       OR EXISTS (
                           SELECT 1
                           FROM chat_session_feedback f
                           WHERE f.session_id = s.id
                             AND f.created_at::date BETWEEN %s AND %s
                       )
                ),
                feedback_agg AS (
                    SELECT f.session_id,
                           jsonb_agg(
                               jsonb_build_object(
                                   'rating', f.rating,
                                   'comment', f.comment,
                                   'created_at', f.created_at
                               )
                               ORDER BY f.created_at
                           ) AS feedback
                    FROM chat_session_feedback f
                    WHERE f.session_id IN (SELECT id FROM target_sessions)
                    GROUP BY f.session_id
                ),
                messages_agg AS (
                    SELECT m.session_id,
                           jsonb_agg(
                               jsonb_build_object(
                                   'id', m.id,
                                   'role', m.role,
                                   'message_text', m.message_text,
                                   'created_at', m.created_at,
                                   'structured_payload', m.structured_payload
                               )
                               ORDER BY m.id
                           ) AS messages
                    FROM chat_message m
                    WHERE m.session_id IN (SELECT id FROM target_sessions)
                    GROUP BY m.session_id
                ),
                state_agg AS (
                    SELECT st.session_id,
                           to_jsonb(st) - 'context_snapshot' AS state
                    FROM chat_slot_state st
                    WHERE st.session_id IN (SELECT id FROM target_sessions)
                )
                SELECT ts.id AS session_id,
                       ts.created_at,
                       ts.updated_at,
                       ts.status,
                       COALESCE(f.feedback, '[]'::jsonb) AS feedback,
                       COALESCE(st.state, '{}'::jsonb) AS state,
                       COALESCE(m.messages, '[]'::jsonb) AS messages
                FROM target_sessions ts
                LEFT JOIN feedback_agg f ON f.session_id = ts.id
                LEFT JOIN messages_agg m ON m.session_id = ts.id
                LEFT JOIN state_agg st ON st.session_id = ts.id
                ORDER BY ts.created_at, ts.id
                """,
                (date_from, date_to, date_from, date_to, date_from, date_to),
            )
            return [dict(row) for row in cursor.fetchall()]

    def add_message(
        self,
        session_id: str,
        role: str,
        message_text: str,
        structured_payload: Optional[dict[str, Any]] = None,
    ) -> int:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO chat_message (session_id, role, message_text, structured_payload)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (session_id, role, message_text, Json(structured_payload) if structured_payload else None),
            )
            message_id = int(cursor.fetchone()["id"])
            cursor.execute(
                """
                UPDATE chat_session
                SET updated_at = now()
                WHERE id = %s
                """,
                (session_id,),
            )
            return message_id

    def get_slot_state(self, session_id: str) -> dict[str, Any]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute("SELECT * FROM chat_slot_state WHERE session_id = %s", (session_id,))
            row = cursor.fetchone()
            if row:
                state = dict(row)
                state.setdefault("goal_stack", [])
                state.setdefault("context_snapshot", {})
                state.setdefault("pending_question", None)
                state.setdefault("state_revision", 0)
                return state
        return {
            "session_id": session_id,
            "goal_stack": [],
            "context_snapshot": {},
            "pending_question": None,
            "state_revision": 0,
            "conversation_phase": None,
            "resume_goal": None,
            "resume_phase": None,
            "context_shift": None,
            "system_query_raw": None,
            "system_resolution_mode": None,
            "instruction_mode": None,
        }

    def update_slot_state(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "city_raw",
            "city_normalized",
            "department_raw",
            "department_normalized",
            "position_raw",
            "position_normalized",
            "system_raw",
            "resolved_system_id",
            "profile_candidates",
            "resolved_profile_id",
            "needs_confirmation",
            "confirmation_topic",
            "confirmation_options",
            "pending_slot",
            "requested_entitlement_raw",
            "requested_entitlement_type_hint",
            "last_intent_type",
            "active_goal",
            "resume_goal",
            "conversation_phase",
            "resume_phase",
            "context_shift",
            "system_query_raw",
            "system_resolution_mode",
            "instruction_mode",
            "goal_stack",
            "pending_question",
            "context_snapshot",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = []
        params: list[Any] = []
        for key, value in updates.items():
            assignments.append(f"{key} = %s")
            if key in _JSON_STATE_FIELDS and value is not None:
                params.append(Json(value))
            else:
                params.append(value)
        params.append(session_id)
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO chat_slot_state (session_id, goal_stack, context_snapshot)
                VALUES (%s, '[]'::jsonb, '{}'::jsonb)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id,),
            )
            cursor.execute(
                f"""
                UPDATE chat_slot_state
                SET {", ".join(assignments)},
                    state_revision = state_revision + 1,
                    updated_at = now()
                WHERE session_id = %s
                """,
                params,
            )

    def update_session_resolution(
        self,
        session_id: str,
        intent_type: Optional[str] = None,
        system_id: Optional[int] = None,
        profile_id: Optional[int] = None,
    ) -> None:
        assignments = []
        params: list[Any] = []
        if intent_type is not None:
            assignments.append("current_intent_type = %s")
            params.append(intent_type)
        if system_id is not None:
            assignments.append("resolved_system_id = %s")
            params.append(system_id)
        if profile_id is not None:
            assignments.append("resolved_profile_id = %s")
            params.append(profile_id)
        if not assignments:
            return
        params.append(session_id)
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                f"""
                UPDATE chat_session
                SET {", ".join(assignments)},
                    updated_at = now()
                WHERE id = %s
                """,
                params,
            )

    def set_session_resolution(
        self,
        session_id: str,
        intent_type: Any = _UNSET,
        system_id: Any = _UNSET,
        profile_id: Any = _UNSET,
    ) -> None:
        assignments = []
        params: list[Any] = []
        if intent_type is not _UNSET:
            assignments.append("current_intent_type = %s")
            params.append(intent_type)
        if system_id is not _UNSET:
            assignments.append("resolved_system_id = %s")
            params.append(system_id)
        if profile_id is not _UNSET:
            assignments.append("resolved_profile_id = %s")
            params.append(profile_id)
        if not assignments:
            return
        params.append(session_id)
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                f"""
                UPDATE chat_session
                SET {", ".join(assignments)},
                    updated_at = now()
                WHERE id = %s
                """,
                params,
            )

    def log_tool_call(self, session_id: str, attempt: ToolAttempt, output_payload: Optional[dict[str, Any]] = None) -> None:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO tool_call_log (
                    session_id, tool_name, attempt_no, input_payload, output_payload, status, result_summary, error_text
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    attempt.tool_name,
                    attempt.attempt_no,
                    Json(attempt.input_payload),
                    Json(output_payload) if output_payload is not None else None,
                    attempt.result_status,
                    attempt.result_summary,
                    attempt.error_text,
                ),
            )

    def add_turn_interpretation(self, session_id: str, message_id: int, interpretation: TurnInterpretation) -> None:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO chat_turn_interpretation (
                    session_id, message_id, dialog_act, intent_type, entities,
                    slot_candidates, goal_transition, context_shift, needs_clarification,
                    reasoning_trace_short, confidence, references_pending_question
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    message_id,
                    interpretation.dialog_act,
                    interpretation.intent_type,
                    Json(interpretation.entities),
                    Json(interpretation.slot_candidates or {}),
                    interpretation.goal_transition,
                    interpretation.context_shift,
                    interpretation.needs_clarification,
                    interpretation.reasoning_trace_short,
                    interpretation.confidence,
                    interpretation.references_pending_question,
                ),
            )

    def close_candidate_sets(self, session_id: str, topics: Optional[list[str]] = None) -> None:
        with db_cursor(self.config) as (_, cursor):
            if topics:
                cursor.execute(
                    """
                    UPDATE chat_candidate_set
                    SET status = 'CLOSED'
                    WHERE session_id = %s
                      AND status = 'ACTIVE'
                      AND topic = ANY(%s)
                    """,
                    (session_id, topics),
                )
            else:
                cursor.execute(
                    """
                    UPDATE chat_candidate_set
                    SET status = 'CLOSED'
                    WHERE session_id = %s
                      AND status = 'ACTIVE'
                    """,
                    (session_id,),
                )

    def create_candidate_set(
        self,
        session_id: str,
        topic: str,
        source_query: str,
        options: list[dict[str, Any]],
        page_size: int = 5,
    ) -> CandidateSet:
        self.close_candidate_sets(session_id, topics=[topic])
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO chat_candidate_set (session_id, topic, source_query, page_size, current_offset, status)
                VALUES (%s, %s, %s, %s, 0, 'ACTIVE')
                RETURNING id, topic, source_query, page_size, current_offset, status
                """,
                (session_id, topic, source_query, page_size),
            )
            row = dict(cursor.fetchone())
            for rank_no, option in enumerate(options, start=1):
                cursor.execute(
                    """
                    INSERT INTO chat_candidate_option (
                        candidate_set_id, option_key, option_label, option_payload, rank_no
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        row["id"],
                        str(option["option_key"]),
                        option["option_label"],
                        Json(option.get("option_payload", {})),
                        rank_no,
                    ),
                )
        return CandidateSet(
            candidate_set_id=int(row["id"]),
            topic=row["topic"],
            source_query=row["source_query"],
            page_size=int(row["page_size"]),
            current_offset=int(row["current_offset"]),
            status=row["status"],
            options=[],
        )

    def get_candidate_set(self, candidate_set_id: int) -> Optional[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT *
                FROM chat_candidate_set
                WHERE id = %s
                """,
                (candidate_set_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_candidate_options(self, candidate_set_id: int) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT id, candidate_set_id, option_key, option_label, option_payload, rank_no
                FROM chat_candidate_option
                WHERE candidate_set_id = %s
                ORDER BY rank_no
                """,
                (candidate_set_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_candidate_page(self, candidate_set_id: int) -> Optional[dict[str, Any]]:
        candidate_set = self.get_candidate_set(candidate_set_id)
        if not candidate_set:
            return None
        all_options = self.get_candidate_options(candidate_set_id)
        offset = int(candidate_set["current_offset"])
        page_size = int(candidate_set["page_size"])
        page_options = all_options[offset : offset + page_size]
        return {
            "candidate_set": candidate_set,
            "options": page_options,
            "page_offset": offset,
            "page_size": page_size,
            "total_options": len(all_options),
            "has_more": offset + page_size < len(all_options),
        }

    def advance_candidate_set(self, candidate_set_id: int) -> Optional[dict[str, Any]]:
        page = self.get_candidate_page(candidate_set_id)
        if not page:
            return None
        total_options = int(page["total_options"])
        page_size = int(page["page_size"])
        next_offset = int(page["page_offset"]) + page_size
        if total_options == 0 or next_offset >= total_options:
            next_offset = 0
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                UPDATE chat_candidate_set
                SET current_offset = %s
                WHERE id = %s
                """,
                (next_offset, candidate_set_id),
            )
        return self.get_candidate_page(candidate_set_id)

    def resolve_candidate_selection(
        self,
        candidate_set_id: int,
        selection_text: Optional[str] = None,
        selection_number: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        page = self.get_candidate_page(candidate_set_id)
        if not page:
            return None
        current_options = page["options"]
        all_options = self.get_candidate_options(candidate_set_id)
        if selection_number is not None and 1 <= selection_number <= len(current_options):
            return current_options[selection_number - 1]
        if selection_number is None and selection_text:
            number_match = re.match(r"^\s*(\d{1,3})[\).\s:-]*", str(selection_text))
            if number_match:
                parsed_number = int(number_match.group(1))
                if 1 <= parsed_number <= len(current_options):
                    return current_options[parsed_number - 1]
        normalized_selection = normalize_text(selection_text)
        if not normalized_selection:
            return None
        for pool in (current_options, all_options):
            for option in pool:
                if normalized_selection in {
                    normalize_text(option["option_key"]),
                    normalize_text(option["option_label"]),
                }:
                    return option
        best_option = None
        best_score = 0.0
        for option in all_options:
            score = max(
                similarity(normalized_selection, option["option_label"]),
                similarity(normalized_selection, option["option_key"]),
            )
            if score > best_score:
                best_option = option
                best_score = score
        if best_option and best_score >= 0.55:
            return best_option
        return None

    def upsert_alias(self, system_id: int, alias_text: str, alias_source: str = "MANUAL") -> None:
        alias_norm = normalize_text(alias_text)
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO system_alias (system_id, alias_text, alias_normalized, alias_source, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (system_id, alias_normalized)
                DO UPDATE
                SET alias_text = EXCLUDED.alias_text,
                    alias_source = EXCLUDED.alias_source,
                    is_active = TRUE
                """,
                (system_id, alias_text, alias_norm, alias_source),
            )

    def resolve_system_candidates(self, query_text: str, limit: int = 5) -> list[dict[str, Any]]:
        query_norm = normalize_text(query_text)
        if not query_norm:
            return []
        pg_trgm_similarity = _pg_trgm_similarity(self.config)
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                sql.SQL(
                """
                WITH active_snapshot AS (
                    SELECT id
                    FROM snapshot
                    WHERE is_active = TRUE
                    ORDER BY loaded_at DESC, id DESC
                    LIMIT 1
                ),
                alias_rows AS (
                    SELECT
                        sa.system_id,
                        sa.alias_text,
                        sa.alias_normalized,
                        sa.alias_source,
                        'SAFE'::text AS alias_class,
                        1::integer AS collision_count
                    FROM system_alias sa
                    WHERE sa.is_active = TRUE
                    UNION ALL
                    SELECT
                        sac.system_id,
                        sac.alias_text,
                        sac.alias_normalized,
                        sac.alias_source,
                        sac.alias_class,
                        sac.collision_count
                    FROM system_alias_candidate sac
                )
                SELECT
                    s.id AS system_id,
                    s.system_name_raw,
                    s.ci_code,
                    ar.alias_text,
                    ar.alias_class,
                    ar.collision_count,
                    ar.alias_source,
                    {similarity}(ar.alias_normalized, %s) AS alias_score,
                    {similarity}(lower(s.system_name_raw), %s) AS system_score
                FROM active_snapshot a
                JOIN system s ON s.snapshot_id = a.id
                LEFT JOIN alias_rows ar ON ar.system_id = s.id
                ORDER BY GREATEST(
                    COALESCE({similarity}(ar.alias_normalized, %s), 0),
                    {similarity}(lower(s.system_name_raw), %s)
                ) DESC,
                s.system_name_raw ASC
                """,
                ).format(similarity=pg_trgm_similarity),
                (
                    query_norm,
                    query_norm,
                    query_norm,
                    query_norm,
                ),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        deduped: dict[int, dict[str, Any]] = {}
        for row in rows:
            alias_class = str(row.get("alias_class") or "SAFE").upper()
            structured_score = _score_system_candidate(
                query_text,
                str(row.get("system_name_raw") or ""),
                str(row.get("alias_text") or ""),
            )
            score = max(
                float(row.get("alias_score") or 0),
                float(row.get("system_score") or 0),
                float(structured_score.get("score") or 0),
            )
            if alias_class == "UNSAFE":
                score = min(score, 0.29)
            elif alias_class == "AMBIGUOUS":
                score = min(max(score, 0.60), 0.72)
            if score < 0.30:
                continue
            current = deduped.get(int(row["system_id"]))
            if current is None or score > float(current["score"]):
                row["score"] = score
                row["matched_by"] = structured_score.get("matched_by") or ["pg_trgm"]
                row["alias_class"] = alias_class
                row["collision_count"] = int(row.get("collision_count") or 1)
                deduped[int(row["system_id"])] = row
        return sorted(deduped.values(), key=lambda item: item["score"], reverse=True)[:limit]

    def browse_system_candidates(
        self,
        query_text: str,
        city: Optional[str],
        department: Optional[str],
        position: Optional[str],
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        query_norm = normalize_text(query_text)
        if not query_norm:
            return []
        pg_trgm_similarity = _pg_trgm_similarity(self.config)

        profile_ids: list[int] = []
        if city and department and position:
            profile_ids = [
                int(candidate["profile_id"])
                for candidate in self.find_candidate_profiles(city, department, position, system_id=None, limit=50)
            ]

        access_counts: dict[int, int] = {}
        with db_cursor(self.config) as (_, cursor):
            if profile_ids:
                cursor.execute(
                    """
                    SELECT system_id, COUNT(DISTINCT profile_id) AS profiles_count
                    FROM v_profile_access_active
                    WHERE profile_id = ANY(%s)
                    GROUP BY system_id
                    """,
                    (profile_ids,),
                )
                access_counts = {
                    int(row["system_id"]): int(row["profiles_count"])
                    for row in cursor.fetchall()
                }
            cursor.execute(
                sql.SQL(
                """
                WITH active_snapshot AS (
                    SELECT id
                    FROM snapshot
                    WHERE is_active = TRUE
                    ORDER BY loaded_at DESC, id DESC
                    LIMIT 1
                ),
                alias_rows AS (
                    SELECT
                        sa.system_id,
                        sa.alias_text,
                        sa.alias_normalized,
                        sa.alias_source,
                        'SAFE'::text AS alias_class,
                        1::integer AS collision_count
                    FROM system_alias sa
                    WHERE sa.is_active = TRUE
                    UNION ALL
                    SELECT
                        sac.system_id,
                        sac.alias_text,
                        sac.alias_normalized,
                        sac.alias_source,
                        sac.alias_class,
                        sac.collision_count
                    FROM system_alias_candidate sac
                )
                SELECT
                    s.id AS system_id,
                    s.system_name_raw,
                    s.ci_code,
                    ar.alias_text,
                    ar.alias_class,
                    ar.collision_count,
                    ar.alias_source,
                    {similarity}(coalesce(ar.alias_normalized, ''), %s) AS alias_score,
                    {similarity}(lower(s.system_name_raw), %s) AS system_score
                FROM active_snapshot a
                JOIN system s ON s.snapshot_id = a.id
                LEFT JOIN alias_rows ar ON ar.system_id = s.id
                WHERE {similarity}(lower(s.system_name_raw), %s) >= 0.30
                   OR {similarity}(coalesce(ar.alias_normalized, ''), %s) >= 0.30
                   OR lower(s.system_name_raw) LIKE ('%%' || %s || '%%')
                   OR coalesce(ar.alias_normalized, '') LIKE ('%%' || %s || '%%')
                ORDER BY s.system_name_raw ASC
                """,
                ).format(similarity=pg_trgm_similarity),
                (
                    query_norm,
                    query_norm,
                    query_norm,
                    query_norm,
                    query_norm,
                    query_norm,
                ),
            )
            rows = [dict(row) for row in cursor.fetchall()]

        ranked: dict[int, dict[str, Any]] = {}
        for row in rows:
            system_id = int(row["system_id"])
            system_name = str(row.get("system_name_raw") or "")
            alias_text = str(row.get("alias_text") or "")
            alias_class = str(row.get("alias_class") or "SAFE").upper()
            system_name_norm = normalize_text(system_name)
            alias_norm = normalize_text(alias_text)
            structured_score = _score_system_candidate(query_text, system_name, alias_text)
            score = max(
                float(row.get("alias_score") or 0),
                float(row.get("system_score") or 0),
                float(structured_score.get("score") or 0),
            )
            if alias_class == "UNSAFE":
                score = min(score, 0.29)
            elif alias_class == "AMBIGUOUS":
                score = min(max(score, 0.60), 0.72)
            if score < 0.30:
                continue
            exact_match = query_norm == system_name_norm or (alias_class == "SAFE" and alias_norm and query_norm == alias_norm)
            safe_alias_match = alias_class == "SAFE" and alias_norm and query_norm == alias_norm
            strong_match = (
                query_norm == system_name_norm
                or safe_alias_match
                or query_norm in system_name_norm
                or (alias_class == "SAFE" and alias_norm and query_norm in alias_norm)
                or score >= 0.74
            )
            has_profile_access = access_counts.get(system_id, 0) > 0
            payload = {
                "system_id": system_id,
                "system_name_raw": system_name,
                "ci_code": row.get("ci_code"),
                "alias_text": alias_text or None,
                "alias_class": alias_class,
                "collision_count": int(row.get("collision_count") or 1),
                "alias_source": row.get("alias_source"),
                "score": round(score, 4),
                "has_profile_access": has_profile_access,
                "profiles_count": access_counts.get(system_id, 0),
                "exact_match": exact_match,
                "strong_match": strong_match,
                "matched_by": structured_score.get("matched_by") or ["pg_trgm"],
            }
            current = ranked.get(system_id)
            if current is None:
                ranked[system_id] = payload
                continue
            current_key = (
                int(current["has_profile_access"]),
                int(current["exact_match"]),
                int(current["strong_match"]),
                float(current["score"]),
            )
            payload_key = (
                int(payload["has_profile_access"]),
                int(payload["exact_match"]),
                int(payload["strong_match"]),
                float(payload["score"]),
            )
            if payload_key > current_key:
                ranked[system_id] = payload

        return sorted(
            ranked.values(),
            key=lambda item: (
                -int(item["has_profile_access"]),
                -int(item["exact_match"]),
                -int(item["strong_match"]),
                -float(item["score"]),
                item["system_name_raw"],
            ),
        )[:limit]

    def get_active_system(self, system_id: int) -> Optional[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT id, system_name_raw, ci_code
                FROM system
                WHERE id = %s
                """,
                (system_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_profile(self, profile_id: int) -> Optional[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT profile_id, profile_code, profile_name, profile_type
                FROM v_profile_catalog_active
                WHERE profile_id = %s
                LIMIT 1
                """,
                (profile_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_candidate_profiles(
        self,
        city: str,
        department: str,
        position: str,
        system_id: Optional[int] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute("SELECT * FROM v_profile_catalog_active")
            rows = [dict(row) for row in cursor.fetchall()]
            accessible_ids: set[int] = set()
            if system_id is not None:
                cursor.execute(
                    """
                    SELECT DISTINCT profile_id
                    FROM v_profile_access_active
                    WHERE system_id = %s
                    """,
                    (system_id,),
                )
                accessible_ids = {int(row["profile_id"]) for row in cursor.fetchall()}
        scored: list[dict[str, Any]] = []
        for row in rows:
            city_score = similarity(city, row.get("structure_text"))
            dept_score = similarity(department, row.get("department_text"))
            pos_score = similarity(position, row.get("position_text"))
            city_token_coverage = _token_coverage(
                city,
                f"{row.get('structure_text', '')} {row.get('department_text', '')}",
            )
            dept_token_coverage = _token_coverage(department, row.get("department_text"))
            pos_token_coverage = _token_coverage(position, row.get("position_text"))
            city_match = city_token_coverage >= _CITY_TOKEN_COVERAGE_THRESHOLD
            department_match = (
                dept_score >= _DEPARTMENT_MIN_SIMILARITY_FLOOR
                and (
                    dept_score >= _DEPARTMENT_SIMILARITY_THRESHOLD
                    or dept_token_coverage >= _DEPARTMENT_TOKEN_COVERAGE_THRESHOLD
                )
            )
            position_match = (
                pos_score >= _POSITION_MIN_SIMILARITY_FLOOR
                and (
                    pos_score >= _POSITION_SIMILARITY_THRESHOLD
                    or pos_token_coverage >= _POSITION_TOKEN_COVERAGE_THRESHOLD
                )
            )
            if not (city_match and department_match and position_match):
                continue
            has_system_access = int(row["profile_id"]) in accessible_ids if accessible_ids else False
            total = pos_score * 0.45 + dept_score * 0.35 + city_score * 0.20 + (0.15 if has_system_access else 0.0)
            if total >= 0.15:
                row["match_score"] = round(total, 4)
                row["city_score"] = round(city_score, 4)
                row["department_score"] = round(dept_score, 4)
                row["position_score"] = round(pos_score, 4)
                row["city_token_coverage"] = round(city_token_coverage, 4)
                row["department_token_coverage"] = round(dept_token_coverage, 4)
                row["position_token_coverage"] = round(pos_token_coverage, 4)
                row["has_system_access"] = has_system_access
                scored.append(row)
        if accessible_ids:
            accessible_scored = [row for row in scored if row["has_system_access"]]
            if accessible_scored:
                scored = accessible_scored
        return sorted(scored, key=lambda item: item["match_score"], reverse=True)[:limit]

    def find_city_candidates(self, query_text: str, limit: int = 10) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                WITH active_snapshot AS (
                    SELECT id
                    FROM snapshot
                    WHERE is_active = TRUE
                    ORDER BY loaded_at DESC, id DESC
                    LIMIT 1
                )
                SELECT DISTINCT pss.segment_name
                FROM active_snapshot a
                JOIN profile p ON p.snapshot_id = a.id
                JOIN profile_structure_segment pss ON pss.profile_id = p.id
                """
            )
            segments = [str(row["segment_name"] or "") for row in cursor.fetchall()]
        city_names: dict[str, str] = {}
        for segment in segments:
            for raw_city in _CITY_PATTERN.findall(segment):
                normalized_city = _normalize_city_name(raw_city)
                if not normalized_city:
                    continue
                city_names.setdefault(normalized_city, _city_display_name(normalized_city))
        query_norm = _normalize_city_name(query_text) or normalize_text(query_text)
        scored: list[dict[str, Any]] = []
        for city_norm, city_label in city_names.items():
            score = max(
                similarity(query_norm, city_norm),
                _token_coverage(query_norm, city_norm),
            )
            if score < _SLOT_CANDIDATE_SCORE_THRESHOLD:
                continue
            scored.append({"value": city_label, "score": round(score, 4)})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    def find_department_candidates(
        self,
        query_text: str,
        city: Optional[str] = None,
        position: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                WITH active_snapshot AS (
                    SELECT id
                    FROM snapshot
                    WHERE is_active = TRUE
                    ORDER BY loaded_at DESC, id DESC
                    LIMIT 1
                )
                SELECT
                    pd.department_name,
                    dac.alias_text,
                    dac.alias_class,
                    dac.collision_count,
                    coalesce(string_agg(DISTINCT pss.segment_name, ' '), '') AS structure_text,
                    coalesce(string_agg(DISTINCT pp.position_name, ' '), '') AS position_text
                FROM active_snapshot a
                JOIN profile p ON p.snapshot_id = a.id
                JOIN profile_department pd ON pd.profile_id = p.id
                LEFT JOIN department_alias_candidate dac
                    ON dac.snapshot_id = a.id
                   AND dac.department_name = pd.department_name
                LEFT JOIN profile_position pp ON pp.profile_id = p.id
                LEFT JOIN profile_structure_segment pss ON pss.profile_id = p.id
                GROUP BY p.id, pd.department_name, dac.alias_text, dac.alias_class, dac.collision_count
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            department_name = str(row.get("department_name") or "").strip()
            if not department_name:
                continue
            alias_text = str(row.get("alias_text") or "").strip()
            alias_class = str(row.get("alias_class") or "SAFE").upper()
            score_payload = _score_department_candidate(
                query_text=query_text,
                department_name=department_name,
                city=city,
                city_haystack=f"{row.get('structure_text') or ''} {department_name}",
                position=position,
                position_haystack=str(row.get("position_text") or ""),
            )
            alias_score_payload = None
            if alias_text:
                alias_score_payload = _score_department_candidate(
                    query_text=query_text,
                    department_name=alias_text,
                    city=city,
                    city_haystack=f"{row.get('structure_text') or ''} {department_name}",
                    position=position,
                    position_haystack=str(row.get("position_text") or ""),
                )
                if alias_score_payload is not None:
                    if alias_class == "UNSAFE":
                        alias_score_payload["score"] = min(float(alias_score_payload["score"]), 0.29)
                    elif alias_class == "AMBIGUOUS":
                        alias_score_payload["score"] = min(max(float(alias_score_payload["score"]), 0.60), 0.82)
                    elif normalize_text(query_text) == normalize_text(alias_text):
                        alias_score_payload["score"] = max(float(alias_score_payload["score"]), 0.96)
                        alias_score_payload["department_text_score"] = max(
                            float(alias_score_payload.get("department_text_score") or 0),
                            1.0,
                        )
                    alias_score_payload["alias_text"] = alias_text
                    alias_score_payload["alias_class"] = alias_class
                    alias_score_payload["collision_count"] = int(row.get("collision_count") or 1)
                    if float(alias_score_payload["score"]) < _SLOT_CANDIDATE_SCORE_THRESHOLD:
                        alias_score_payload = None
            if alias_score_payload is not None and (
                score_payload is None or float(alias_score_payload["score"]) > float(score_payload["score"])
            ):
                score_payload = alias_score_payload
            if score_payload is None:
                continue
            key = normalize_text(department_name)
            payload = {"value": department_name, **score_payload}
            current = deduped.get(key)
            if current is None or float(payload["score"]) > float(current["score"]):
                deduped[key] = payload
        return sorted(deduped.values(), key=lambda item: item["score"], reverse=True)[:limit]

    def find_position_candidates(
        self,
        query_text: str,
        city: Optional[str] = None,
        department: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                WITH active_snapshot AS (
                    SELECT id
                    FROM snapshot
                    WHERE is_active = TRUE
                    ORDER BY loaded_at DESC, id DESC
                    LIMIT 1
                )
                SELECT
                    pp.position_name,
                    coalesce(string_agg(DISTINCT pss.segment_name, ' '), '') AS structure_text,
                    coalesce(string_agg(DISTINCT pd.department_name, ' '), '') AS department_text
                FROM active_snapshot a
                JOIN profile p ON p.snapshot_id = a.id
                JOIN profile_position pp ON pp.profile_id = p.id
                LEFT JOIN profile_structure_segment pss ON pss.profile_id = p.id
                LEFT JOIN profile_department pd ON pd.profile_id = p.id
                GROUP BY p.id, pp.position_name
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            position_name = str(row.get("position_name") or "").strip()
            if not position_name:
                continue
            score_payload = _score_position_candidate(
                query_text=query_text,
                position_name=position_name,
                city=city,
                city_haystack=f"{row.get('structure_text') or ''} {row.get('department_text') or ''}",
                department=department,
                department_haystack=str(row.get("department_text") or ""),
            )
            if score_payload is None:
                continue
            key = normalize_text(position_name)
            payload = {"value": position_name, **score_payload}
            current = deduped.get(key)
            if current is None or float(payload["score"]) > float(current["score"]):
                deduped[key] = payload
        return sorted(deduped.values(), key=lambda item: item["score"], reverse=True)[:limit]

    def list_profile_access(self, profile_id: int, system_id: int) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT *
                FROM v_profile_access_active
                WHERE profile_id = %s AND system_id = %s
                ORDER BY entitlement_type, entitlement_name
                """,
                (profile_id, system_id),
            )
            return [dict(row) for row in cursor.fetchall()]

    def find_profiles_by_confirmed_context(
        self,
        city: str,
        department: str,
        position: str,
        system_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        city_norm = _normalize_city_name(city) or normalize_text(city)
        department_norm = normalize_text(department)
        position_norm = normalize_text(position)
        if not city_norm or not department_norm or not position_norm:
            return []
        accessible_ids: Optional[set[int]] = None
        with db_cursor(self.config) as (_, cursor):
            if system_id is not None:
                cursor.execute(
                    """
                    SELECT DISTINCT profile_id
                    FROM v_profile_access_active
                    WHERE system_id = %s
                    """,
                    (system_id,),
                )
                accessible_ids = {int(row["profile_id"]) for row in cursor.fetchall()}
                if not accessible_ids:
                    return []
            cursor.execute(
                """
                WITH active_snapshot AS (
                    SELECT id
                    FROM snapshot
                    WHERE is_active = TRUE
                    ORDER BY loaded_at DESC, id DESC
                    LIMIT 1
                )
                SELECT
                    p.id AS profile_id,
                    p.profile_code,
                    p.profile_name,
                    p.profile_type,
                    array_remove(array_agg(DISTINCT pd.department_name), NULL) AS departments,
                    array_remove(array_agg(DISTINCT pp.position_name), NULL) AS positions,
                    array_remove(array_agg(DISTINCT pss.segment_name), NULL) AS segments
                FROM active_snapshot a
                JOIN profile p ON p.snapshot_id = a.id
                LEFT JOIN profile_department pd ON pd.profile_id = p.id
                LEFT JOIN profile_position pp ON pp.profile_id = p.id
                LEFT JOIN profile_structure_segment pss ON pss.profile_id = p.id
                GROUP BY p.id, p.profile_code, p.profile_name, p.profile_type
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
        matched: list[dict[str, Any]] = []
        for row in rows:
            profile_id = int(row["profile_id"])
            if accessible_ids is not None and profile_id not in accessible_ids:
                continue
            department_values = row.get("departments") or []
            position_values = row.get("positions") or []
            segment_values = row.get("segments") or []
            departments_norm = {normalize_text(value) for value in department_values if value}
            positions_norm = {normalize_text(value) for value in position_values if value}
            city_tokens: set[str] = set()
            for segment in segment_values:
                segment_text = str(segment or "")
                for raw_city in _CITY_PATTERN.findall(segment_text):
                    normalized_city = _normalize_city_name(raw_city)
                    if normalized_city:
                        city_tokens.add(normalized_city)
                segment_norm = normalize_text(segment_text)
                if city_norm and city_norm in segment_norm.split():
                    city_tokens.add(city_norm)
            if department_norm not in departments_norm:
                continue
            if position_norm not in positions_norm:
                continue
            if city_norm not in city_tokens:
                continue
            matched.append(
                {
                    "profile_id": profile_id,
                    "profile_code": row.get("profile_code"),
                    "profile_name": row.get("profile_name"),
                    "profile_type": row.get("profile_type"),
                }
            )
        return sorted(
            matched,
            key=lambda item: (
                str(item.get("profile_name") or ""),
                str(item.get("profile_code") or ""),
            ),
        )

    def list_systems_by_confirmed_context(
        self,
        city: str,
        department: str,
        position: str,
    ) -> list[dict[str, Any]]:
        profiles = self.find_profiles_by_confirmed_context(
            city=city,
            department=department,
            position=position,
            system_id=None,
        )
        if not profiles:
            return []
        profile_ids = [int(item["profile_id"]) for item in profiles]
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT DISTINCT
                    v.system_id,
                    v.system_name AS system_name_raw,
                    s.ci_code
                FROM v_profile_access_active v
                JOIN system s ON s.id = v.system_id
                WHERE v.profile_id = ANY(%s)
                ORDER BY v.system_name
                """,
                (profile_ids,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_access_by_confirmed_context(
        self,
        city: str,
        department: str,
        position: str,
        system_id: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        profiles = self.find_profiles_by_confirmed_context(
            city=city,
            department=department,
            position=position,
            system_id=system_id,
        )
        if not profiles:
            return [], []
        profile_ids = [int(item["profile_id"]) for item in profiles]
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT *
                FROM v_profile_access_active
                WHERE system_id = %s
                  AND profile_id = ANY(%s)
                ORDER BY entitlement_type, entitlement_name
                """,
                (system_id, profile_ids),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        return rows, profiles

    def check_entitlement_access(
        self,
        profile_id: int,
        system_id: int,
        entitlement_name: str,
        entitlement_type_hint: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        accesses = self.list_profile_access(profile_id, system_id)
        best_row: Optional[dict[str, Any]] = None
        best_score = 0.0
        for row in accesses:
            score = similarity(entitlement_name, row.get("entitlement_name"))
            if entitlement_type_hint:
                score += 0.1 * similarity(entitlement_type_hint, row.get("entitlement_type"))
            if score > best_score:
                best_row = row
                best_score = score
        if best_row and best_score >= 0.35:
            best_row["match_score"] = round(best_score, 4)
            return best_row
        return None

    def get_system_justification(
        self,
        profile_id: int,
        system_id: int,
        access_level: Optional[int] = None,
        entitlement_name: Optional[str] = None,
    ) -> Optional[str]:
        if access_level is None and entitlement_name:
            match = self.check_entitlement_access(profile_id, system_id, entitlement_name)
            if match:
                access_level = int(match["access_level"])
        with db_cursor(self.config) as (_, cursor):
            if access_level is None:
                cursor.execute(
                    """
                    SELECT justification_text
                    FROM profile_system_justification
                    WHERE profile_id = %s AND system_id = %s
                    ORDER BY access_level
                    LIMIT 1
                    """,
                    (profile_id, system_id),
                )
            else:
                cursor.execute(
                    """
                    SELECT justification_text
                    FROM profile_system_justification
                    WHERE profile_id = %s AND system_id = %s AND access_level = %s
                    LIMIT 1
                    """,
                    (profile_id, system_id, access_level),
                )
            row = cursor.fetchone()
            return row["justification_text"] if row else None
