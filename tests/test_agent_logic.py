from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from app.agent.service import ChatAgent
from app.config import AppConfig
from app.repositories.search_repository import (
    _score_department_candidate,
    _score_position_candidate,
    _score_system_candidate,
)
from app.services.text import normalize_text
from app.models.domain import RetrievedChunk, TurnInterpretation
from rolemodel_etl.config import DBConfig


class FakeGiga:
    enabled = True

    @staticmethod
    def _extract_system_entity(normalized: str) -> str | None:
        if "sberhelp" in normalized:
            return "SberHelp"
        if "аск" in normalized:
            return "АСК"
        if "ефс" in normalized and "риск" in normalized:
            return "ЕФС Риск Решения"
        if "ефс" in normalized and "цкр" in normalized:
            return "ЕФС ЕРМ ЦКР"
        if "ефс" in normalized:
            return "ЕФС"
        return None

    def complete_json(self, system_prompt: str, user_prompt: str, model=None, max_tokens=None) -> dict:
        text = ""
        context = {}
        context_match = re.search(r"Контекст:\s*(\{.*\})\s*Сообщение пользователя:", user_prompt, flags=re.DOTALL)
        if context_match:
            try:
                context = ast.literal_eval(context_match.group(1))
            except Exception:
                context = {}
        text_match = re.search(r"Сообщение пользователя:\s*(.*?)\s*Верни JSON\.", user_prompt, flags=re.DOTALL)
        if text_match:
            text = text_match.group(1).strip()
        normalized = normalize_text(text)
        if "детектор сценария запроса" in system_prompt:
            if "ас" in normalized and any(
                token in normalized
                for token in ("какие", "каким", "доступные", "список", "перечень", "положен доступ")
            ):
                return {"target": "SYSTEM_LIST", "confidence": 0.9, "reason": "systems_scope"}
            if "роль" in normalized or "роли" in normalized:
                return {"target": "ROLE_LIST", "confidence": 0.8, "reason": "roles_scope"}
            return {"target": "OTHER", "confidence": 0.6, "reason": "unknown_scope"}
        pending = context.get("pending_question") or {}
        active_goal = str(context.get("active_goal") or "UNKNOWN").upper()

        result = {
            "dialog_act": "ASK_HELP",
            "intent_type": active_goal if active_goal != "UNKNOWN" else "UNKNOWN",
            "entities": {},
            "slot_candidates": {},
            "context_shift": "NONE",
            "confidence": 0.8,
            "goal_transition": "STAY",
            "needs_clarification": False,
            "references_pending_question": bool(pending),
            "user_correction": False,
            "reasoning_trace_short": "fake_llm_test",
        }

        if any(token in normalized for token in ("сброс", "новый чат", "новый запрос")):
            result["dialog_act"] = "RESET_CONTEXT"
            result["intent_type"] = "UNKNOWN"
            result["context_shift"] = "RESET_CONTEXT"
            result["goal_transition"] = "START"
            return result

        if pending.get("kind") == "candidate_selection":
            if any(token in normalized for token in ("еще", "дальше", "следующ", "ок", "нет нужной", "тут нет")):
                result["dialog_act"] = "SHOW_MORE"
                return result
            system_hint = self._extract_system_entity(normalized)
            if pending.get("topic") == "system" and system_hint:
                result["dialog_act"] = "CHANGE_SYSTEM"
                result["entities"]["system_raw"] = system_hint
                result["context_shift"] = "CHANGE_SYSTEM_FOCUS"
                return result
            number_match = re.fullmatch(r"\s*(\d+)\s*", text)
            if number_match:
                result["dialog_act"] = "SELECT_OPTION"
                result["entities"]["selection_number"] = int(number_match.group(1))
                return result
            if pending.get("topic") != "system" and any(token in normalized for token in ("ефс", "sberhelp", "аск", "риск реш")):
                result["dialog_act"] = "CHANGE_SYSTEM"
                result["entities"]["system_raw"] = self._extract_system_entity(normalized) or text.strip()
                result["context_shift"] = "CHANGE_SYSTEM_FOCUS"
                return result
            result["dialog_act"] = "SELECT_OPTION"
            result["entities"]["selection_text"] = text.strip()
            return result

        if pending.get("kind") == "slot_request":
            topic = str(pending.get("topic") or "")
            slot_map = {
                "system": "system_raw",
                "position": "position_raw",
                "city": "city_raw",
                "department": "department_raw",
                "requested_entitlement": "requested_entitlement_raw",
            }
            if topic == "requested_entitlement" and any(
                token in normalized for token in ("какие роли", "какие у меня роли", "что мне доступно")
            ):
                result["dialog_act"] = "SWITCH_INTENT"
                result["intent_type"] = "ROLE_DISCOVERY"
                result["context_shift"] = "SWITCH_GOAL"
                result["goal_transition"] = "SWITCH"
                return result
            if topic == "system" and any(token in normalized for token in ("не знаю", "не помню", "не уверен")):
                result["dialog_act"] = "SWITCH_INTENT"
                result["intent_type"] = "SYSTEM_DISCOVERY"
                result["context_shift"] = "SWITCH_GOAL"
                result["goal_transition"] = "SWITCH"
                return result
            result["dialog_act"] = "PROVIDE_SLOT"
            slot_key = slot_map.get(topic)
            if slot_key:
                result["entities"][slot_key] = text.strip()
            if active_goal == "UNKNOWN":
                result["intent_type"] = "SYSTEM_DISCOVERY" if "какие ас" in normalized else "ROLE_DISCOVERY"
                result["goal_transition"] = "START"
            return result

        segments = [segment.strip(" .") for segment in re.split(r"[,;\n]+", text) if segment.strip(" .")]
        if len(segments) >= 2:
            for segment in segments:
                seg_norm = normalize_text(segment)
                system_value = self._extract_system_entity(seg_norm)
                if system_value and "system_raw" not in result["entities"]:
                    result["entities"]["system_raw"] = system_value
                    continue
                if any(token in seg_norm for token in ("москва", "мск", "новосибирск", "екатеринбург")) and "city_raw" not in result["entities"]:
                    result["entities"]["city_raw"] = "Москва" if "мск" in seg_norm else segment
                    continue
                if any(token in seg_norm for token in ("менеджер", "начальник")) and "position_raw" not in result["entities"]:
                    result["entities"]["position_raw"] = segment
                    continue
                if any(token in seg_norm for token in ("отдел", "экспертиз")) and "department_raw" not in result["entities"]:
                    result["entities"]["department_raw"] = segment
            if len(result["entities"]) >= 2:
                result["intent_type"] = "ROLE_DISCOVERY"
                result["dialog_act"] = "PROVIDE_SLOT"
                result["goal_transition"] = "START" if active_goal == "UNKNOWN" else "STAY"
                return result

        if active_goal in {"ROLE_DISCOVERY", "ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"} and normalized in {"да", "да подскажи", "подскажи", "да подскажите"}:
            result["intent_type"] = "INSTRUCTION_LOOKUP"
            result["dialog_act"] = "ASK_HELP"
            result["context_shift"] = "SWITCH_GOAL"
            result["goal_transition"] = "SWITCH"
            result["references_pending_question"] = True
            return result

        if any(token in normalized for token in ("как проверить", "как войти", "что делать", "доступ по запросу", "как применить")):
            result["intent_type"] = "INSTRUCTION_LOOKUP"
            result["dialog_act"] = "ASK_HELP"
            result["context_shift"] = "SWITCH_GOAL"
            result["goal_transition"] = "SWITCH" if active_goal not in {"UNKNOWN", "INSTRUCTION_LOOKUP"} else "START"
            return result
        if any(token in normalized for token in ("какие ас", "какие системы", "список ас", "доступные ас")):
            result["intent_type"] = "SYSTEM_DISCOVERY"
            result["context_shift"] = "SWITCH_GOAL" if active_goal not in {"UNKNOWN", "SYSTEM_DISCOVERY"} else "NONE"
            result["goal_transition"] = "SWITCH" if active_goal not in {"UNKNOWN", "SYSTEM_DISCOVERY"} else "START"
        if "обоснован" in normalized:
            result["intent_type"] = "JUSTIFICATION_LOOKUP"
            result["context_shift"] = "SWITCH_GOAL" if active_goal not in {"UNKNOWN", "JUSTIFICATION_LOOKUP"} else "NONE"
            result["goal_transition"] = "SWITCH" if active_goal not in {"UNKNOWN", "JUSTIFICATION_LOOKUP"} else "START"
        elif any(token in normalized for token in ("получить роль", "проверить роль")):
            result["intent_type"] = "ROLE_ACQUISITION"
            result["context_shift"] = "SWITCH_GOAL" if active_goal not in {"UNKNOWN", "ROLE_ACQUISITION"} else "NONE"
            result["goal_transition"] = "SWITCH" if active_goal not in {"UNKNOWN", "ROLE_ACQUISITION"} else "START"
        elif (
            (
                any(token in normalized for token in ("роль", "роли"))
                and any(token in normalized for token in ("какая", "какие", "нуж", "доступ", "есть", "подскаж"))
            )
            or any(token in normalized for token in ("нет доступа", "роли доступны", "есть доступ", "нужен доступ"))
        ):
            result["intent_type"] = "ROLE_DISCOVERY"
            result["context_shift"] = "SWITCH_GOAL" if active_goal not in {"UNKNOWN", "ROLE_DISCOVERY"} else "NONE"
            result["goal_transition"] = "SWITCH" if active_goal not in {"UNKNOWN", "ROLE_DISCOVERY"} else "START"

        system_entity = self._extract_system_entity(normalized)
        if system_entity:
            result["entities"]["system_raw"] = system_entity
            if pending.get("topic") != "system":
                result["dialog_act"] = "CHANGE_SYSTEM"
                result["context_shift"] = "CHANGE_SYSTEM_FOCUS" if active_goal in {"ROLE_DISCOVERY", "SYSTEM_DISCOVERY", "ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"} else result["context_shift"]
                return result

        result["dialog_act"] = "PROVIDE_SLOT" if result["intent_type"] in {"SYSTEM_DISCOVERY", "ROLE_DISCOVERY", "ROLE_ACQUISITION", "JUSTIFICATION_LOOKUP"} else "ASK_HELP"
        return result


class FakeRagService:
    def load_inline_instruction_pack(self, source_id=None, file_path=None) -> dict:
        return {
            "title": "Памятка",
            "slides_count": 5,
            "text_length": 1200,
            "sections": [],
        }

    def answer_from_inline_doc(self, query_text: str, context=None) -> dict | None:
        return {
            "instruction": f"Inline-инструкция: {query_text}",
            "citations": [
                RetrievedChunk(
                    chunk_id=-3,
                    source_id=0,
                    source_title="Памятка",
                    slide_no=3,
                    chunk_type="INLINE_SLIDE",
                    chunk_text=f"Inline-инструкция: {query_text}",
                    score=0.88,
                    citation_label="Памятка, слайд 3",
                    locator_text="слайд 3",
                )
            ],
            "summary_text": f"Inline-инструкция: {query_text}",
            "instruction_mode": "INLINE_DOC",
        }

    def search_instructions(self, query_text: str) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id=1,
                source_id=1,
                source_title="Памятка",
                slide_no=3,
                chunk_type="SLIDE_TEXT",
                chunk_text=f"Инструкция по запросу: {query_text}",
                score=0.42,
                citation_label="Памятка, слайд 3",
                locator_text="слайд 3",
            )
        ]

    def answer_with_rag(self, query_text: str, retrieved_chunks: list[RetrievedChunk], answer_style: str = "steps") -> dict:
        return {
            "instruction": f"Шаги для запроса доступа: {query_text}",
            "citations": retrieved_chunks,
            "summary_text": f"Шаги для запроса доступа: {query_text}",
        }


class DictionaryResolutionScoringTestCase(unittest.TestCase):
    def test_system_scoring_matches_partial_official_name_and_bracket_abbreviation(self) -> None:
        official_name = "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]"

        partial = _score_system_candidate("Анализ состояния клиента", official_name)
        abbreviation = _score_system_candidate("АСК", official_name)
        composite = _score_system_candidate("ПКАП АСК", official_name)
        unrelated = _score_system_candidate("ПКАП АСК", "OneKIB (АС ПКАП СС360) (И3) [CI03259775]")

        self.assertGreaterEqual(partial["score"], 0.80)
        self.assertIn("token_coverage", partial["matched_by"])
        self.assertGreaterEqual(abbreviation["score"], 0.90)
        self.assertIn("bracket_abbreviation", abbreviation["matched_by"])
        self.assertGreater(composite["score"], unrelated["score"])

    def test_department_context_boosts_ranking_without_hiding_global_text_match(self) -> None:
        query = "дочерние банки"
        department = "Отдел экспертизы кредитных рисков корпоративных клиентов дочерних банков"

        incompatible = _score_department_candidate(
            query,
            department,
            city="Самара",
            city_haystack="г. Москва",
            position="Риск-менеджер",
            position_haystack="Риск-менеджер",
        )
        compatible = _score_department_candidate(
            query,
            department,
            city="Москва",
            city_haystack="г. Москва",
            position="Риск-менеджер",
            position_haystack="Риск-менеджер Главный риск-менеджер",
        )

        self.assertIsNotNone(incompatible)
        self.assertIsNotNone(compatible)
        self.assertGreater(compatible["score"], incompatible["score"])
        self.assertGreaterEqual(incompatible["department_text_score"], 0.30)

    def test_position_context_boosts_ranking_but_keeps_matching_position_visible(self) -> None:
        query = "риск менеджер"

        incompatible = _score_position_candidate(
            query,
            "Риск-менеджер",
            city="Москва",
            city_haystack="г. Самара",
            department="Отдел дочерних банков",
            department_haystack="Отдел контроля качества",
        )
        compatible = _score_position_candidate(
            query,
            "Риск-менеджер",
            city="Москва",
            city_haystack="г. Москва",
            department="Отдел дочерних банков",
            department_haystack="Отдел экспертизы кредитных рисков корпоративных клиентов дочерних банков",
        )

        self.assertIsNotNone(incompatible)
        self.assertIsNotNone(compatible)
        self.assertGreater(compatible["score"], incompatible["score"])
        self.assertGreaterEqual(incompatible["position_text_score"], 0.70)


class FakeSearchRepository:
    def __init__(self) -> None:
        self._message_id = 0
        self._candidate_set_id = 0
        self._candidate_option_id = 0
        self.sessions: dict[str, dict] = {
            "s1": {
                "id": "s1",
                "status": "ACTIVE",
                "current_intent_type": None,
                "resolved_system_id": None,
                "resolved_profile_id": None,
            }
        }
        self.states: dict[str, dict] = {
            "s1": {
                "session_id": "s1",
                "state_revision": 0,
                "goal_stack": [],
                "context_snapshot": {},
                "pending_question": None,
                "active_goal": "UNKNOWN",
                "last_intent_type": "UNKNOWN",
                "conversation_phase": None,
                "resume_goal": None,
                "resume_phase": None,
                "context_shift": None,
                "system_query_raw": None,
                "system_resolution_mode": None,
                "instruction_mode": None,
                "needs_confirmation": False,
            }
        }
        self.messages: dict[str, list[dict]] = {"s1": []}
        self.candidate_sets: dict[int, dict] = {}
        self.candidate_options: dict[int, list[dict]] = {}
        self.tool_calls: list[dict] = []
        self.turns: list[dict] = []
        self.system_candidates_map: dict[str, list[dict]] = {}
        self.system_browse_map: dict[str, list[dict]] = {}
        self.systems: dict[int, dict] = {}
        self.profile_candidates_result: list[dict] = []
        self.profiles: dict[int, dict] = {}
        self.access_map: dict[tuple[int, int], list[dict]] = {}
        self.context_system_map: dict[tuple[str, str, str], list[dict]] = {}
        self.context_access_map: dict[tuple[str, str, str, int], tuple[list[dict], list[dict]]] = {}
        self.justifications: dict[tuple[int, int], str] = {}
        self.city_candidates_map: dict[str, list[dict]] = {}
        self.department_candidates_map: dict[tuple[str, str | None], list[dict]] = {}
        self.position_candidates_map: dict[tuple[str, str | None, str | None], list[dict]] = {}

    def create_session(self) -> str:
        return "s1"

    def get_session(self, session_id: str) -> dict | None:
        return self.sessions.get(session_id)

    def list_messages(self, session_id: str) -> list[dict]:
        return list(self.messages.get(session_id, []))

    def add_message(self, session_id: str, role: str, message_text: str, structured_payload: dict | None = None) -> int:
        self._message_id += 1
        self.messages.setdefault(session_id, []).append(
            {
                "id": self._message_id,
                "role": role,
                "message_text": message_text,
                "structured_payload": structured_payload,
            }
        )
        return self._message_id

    def get_slot_state(self, session_id: str) -> dict:
        return dict(self.states[session_id])

    def update_slot_state(self, session_id: str, **fields) -> None:
        state = self.states[session_id]
        state.update(fields)
        state["state_revision"] = int(state.get("state_revision") or 0) + 1

    def update_session_resolution(self, session_id: str, intent_type=None, system_id=None, profile_id=None) -> None:
        session = self.sessions[session_id]
        if intent_type is not None:
            session["current_intent_type"] = intent_type
        if system_id is not None:
            session["resolved_system_id"] = system_id
        if profile_id is not None:
            session["resolved_profile_id"] = profile_id

    def set_session_resolution(self, session_id: str, intent_type=None, system_id=None, profile_id=None) -> None:
        session = self.sessions[session_id]
        if intent_type is not None:
            session["current_intent_type"] = intent_type
        if system_id is not None or system_id is None:
            session["resolved_system_id"] = system_id
        if profile_id is not None or profile_id is None:
            session["resolved_profile_id"] = profile_id

    def log_tool_call(self, session_id: str, attempt, output_payload=None) -> None:
        self.tool_calls.append(
            {
                "session_id": session_id,
                "tool_name": attempt.tool_name,
                "result_summary": attempt.result_summary,
                "output_payload": output_payload,
            }
        )

    def add_turn_interpretation(self, session_id: str, message_id: int, interpretation) -> None:
        self.turns.append(
            {
                "session_id": session_id,
                "message_id": message_id,
                "dialog_act": interpretation.dialog_act,
                "intent_type": interpretation.intent_type,
                "entities": interpretation.entities,
            }
        )

    def close_candidate_sets(self, session_id: str, topics=None) -> None:
        for candidate_set in self.candidate_sets.values():
            if candidate_set["session_id"] != session_id:
                continue
            if topics and candidate_set["topic"] not in topics:
                continue
            candidate_set["status"] = "CLOSED"

    def create_candidate_set(self, session_id: str, topic: str, source_query: str, options: list[dict], page_size: int = 5):
        self.close_candidate_sets(session_id, topics=[topic])
        self._candidate_set_id += 1
        candidate_set = {
            "id": self._candidate_set_id,
            "session_id": session_id,
            "topic": topic,
            "source_query": source_query,
            "page_size": page_size,
            "current_offset": 0,
            "status": "ACTIVE",
        }
        self.candidate_sets[self._candidate_set_id] = candidate_set
        self.candidate_options[self._candidate_set_id] = []
        for rank_no, option in enumerate(options, start=1):
            self._candidate_option_id += 1
            self.candidate_options[self._candidate_set_id].append(
                {
                    "id": self._candidate_option_id,
                    "candidate_set_id": self._candidate_set_id,
                    "option_key": option["option_key"],
                    "option_label": option["option_label"],
                    "option_payload": option.get("option_payload", {}),
                    "rank_no": rank_no,
                }
            )
        return type(
            "CandidateSetLike",
            (),
            {
                "candidate_set_id": self._candidate_set_id,
                "topic": topic,
                "source_query": source_query,
                "page_size": page_size,
                "current_offset": 0,
                "status": "ACTIVE",
            },
        )()

    def get_candidate_set(self, candidate_set_id: int) -> dict | None:
        return self.candidate_sets.get(candidate_set_id)

    def get_candidate_options(self, candidate_set_id: int) -> list[dict]:
        return list(self.candidate_options.get(candidate_set_id, []))

    def get_candidate_page(self, candidate_set_id: int) -> dict | None:
        candidate_set = self.candidate_sets.get(candidate_set_id)
        if not candidate_set:
            return None
        all_options = self.candidate_options.get(candidate_set_id, [])
        offset = candidate_set["current_offset"]
        page_size = candidate_set["page_size"]
        return {
            "candidate_set": candidate_set,
            "options": all_options[offset : offset + page_size],
            "page_offset": offset,
            "page_size": page_size,
            "total_options": len(all_options),
            "has_more": offset + page_size < len(all_options),
        }

    def advance_candidate_set(self, candidate_set_id: int) -> dict | None:
        page = self.get_candidate_page(candidate_set_id)
        if not page:
            return None
        total = page["total_options"]
        next_offset = page["page_offset"] + page["page_size"]
        if total == 0 or next_offset >= total:
            next_offset = 0
        self.candidate_sets[candidate_set_id]["current_offset"] = next_offset
        return self.get_candidate_page(candidate_set_id)

    def resolve_candidate_selection(self, candidate_set_id: int, selection_text=None, selection_number=None) -> dict | None:
        page = self.get_candidate_page(candidate_set_id)
        if not page:
            return None
        if selection_number is not None and 1 <= selection_number <= len(page["options"]):
            return page["options"][selection_number - 1]
        if selection_text:
            for option in self.candidate_options.get(candidate_set_id, []):
                if option["option_label"] == selection_text:
                    return option
        return None

    def resolve_system_candidates(self, query_text: str, limit: int = 5) -> list[dict]:
        direct = self.system_candidates_map.get(query_text)
        if direct is not None:
            return list(direct)[:limit]
        normalized = normalize_text(query_text)
        for key, value in self.system_candidates_map.items():
            if normalize_text(key) == normalized:
                return list(value)[:limit]
        for value in self.system_candidates_map.values():
            for candidate in value:
                if normalized in normalize_text(candidate.get("system_name_raw")):
                    return list(value)[:limit]
        return []

    def browse_system_candidates(self, query_text: str, city=None, department=None, position=None, limit: int = 25) -> list[dict]:
        direct = self.system_browse_map.get(query_text)
        if direct is not None:
            return list(direct)[:limit]
        normalized = normalize_text(query_text)
        for key, value in self.system_browse_map.items():
            if normalize_text(key) == normalized:
                return list(value)[:limit]
        return self.resolve_system_candidates(query_text, limit=limit)

    def get_active_system(self, system_id: int) -> dict | None:
        return self.systems.get(system_id)

    def get_active_profile(self, profile_id: int) -> dict | None:
        return self.profiles.get(profile_id)

    def find_candidate_profiles(self, city: str, department: str, position: str, system_id=None, limit: int = 20) -> list[dict]:
        return list(self.profile_candidates_result)[:limit]

    def list_profile_access(self, profile_id: int, system_id: int) -> list[dict]:
        return list(self.access_map.get((profile_id, system_id), []))

    def list_systems_by_confirmed_context(self, city: str, department: str, position: str) -> list[dict]:
        key = (city or "", department or "", position or "")
        if key in self.context_system_map:
            return list(self.context_system_map[key])
        deduped: dict[int, dict] = {}
        for (profile_id, system_id), access_rows in self.access_map.items():
            del profile_id, access_rows
            system = self.systems.get(system_id)
            if not system:
                continue
            deduped[int(system_id)] = {
                "system_id": int(system_id),
                "system_name_raw": system["system_name_raw"],
                "ci_code": system.get("ci_code"),
            }
        return list(deduped.values())

    def list_access_by_confirmed_context(
        self,
        city: str,
        department: str,
        position: str,
        system_id: int,
    ) -> tuple[list[dict], list[dict]]:
        key = (city or "", department or "", position or "", int(system_id))
        if key in self.context_access_map:
            rows, profiles = self.context_access_map[key]
            return list(rows), list(profiles)
        rows: list[dict] = []
        profiles_map: dict[int, dict] = {}
        for (profile_id, current_system_id), access_rows in self.access_map.items():
            if int(current_system_id) != int(system_id):
                continue
            profile = self.profiles.get(profile_id, {})
            profiles_map[profile_id] = {
                "profile_id": profile_id,
                "profile_code": profile.get("profile_code", f"P{profile_id}"),
                "profile_name": profile.get("profile_name", f"Профиль {profile_id}"),
                "profile_type": profile.get("profile_type"),
            }
            for row in access_rows:
                payload = dict(row)
                payload.setdefault("profile_id", profile_id)
                payload.setdefault("profile_name", profiles_map[profile_id]["profile_name"])
                payload.setdefault("profile_code", profiles_map[profile_id]["profile_code"])
                rows.append(payload)
        return rows, list(profiles_map.values())

    def check_entitlement_access(self, profile_id: int, system_id: int, entitlement_name: str, entitlement_type_hint=None) -> dict | None:
        for row in self.list_profile_access(profile_id, system_id):
            if row["entitlement_name"] == entitlement_name:
                result = dict(row)
                result["match_score"] = 1.0
                return result
        return None

    def get_system_justification(self, profile_id: int, system_id: int, access_level=None, entitlement_name=None) -> str | None:
        return self.justifications.get((profile_id, system_id))

    def find_city_candidates(self, query_text: str, limit: int = 10) -> list[dict]:
        return list(self.city_candidates_map.get(query_text, [{"value": query_text, "score": 1.0}]))[:limit]

    def find_department_candidates(self, query_text: str, city=None, position=None, limit: int = 10) -> list[dict]:
        key = (query_text, city)
        fallback = [{"value": query_text, "score": 1.0}] if query_text else []
        return list(self.department_candidates_map.get(key, fallback))[:limit]

    def find_position_candidates(self, query_text: str, city=None, department=None, limit: int = 10) -> list[dict]:
        key = (query_text, city, department)
        fallback = [{"value": query_text, "score": 1.0}] if query_text else []
        return list(self.position_candidates_map.get(key, fallback))[:limit]


class AgentDialogRefactorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FakeSearchRepository()
        self.agent = ChatAgent(
            AppConfig(
                db=DBConfig(
                    host="localhost",
                    port=5432,
                    dbname="rolemodel",
                    user="rolemodel",
                    password="rolemodel",
                )
            ),
            search_repository=self.repo,
            rag_service=FakeRagService(),
            gigachat=FakeGiga(),
        )
        self.fixtures_dir = Path(__file__).resolve().parent / "fixtures"

    def test_turn_plan_types_importable(self) -> None:
        from app.agent.turn_plan import PlannedAction, SlotResolutionStatus, TurnPlan

        plan = TurnPlan(action=PlannedAction.CONTINUE, intent_type="ROLE_DISCOVERY", dialog_act="PROVIDE_SLOT")
        self.assertEqual(plan.action.value, "CONTINUE")
        self.assertEqual(SlotResolutionStatus.ACCEPTED.value, "ACCEPTED")

    def test_turn_planner_maps_instruction_to_instruction_action(self) -> None:
        from app.agent.turn_plan import PlannedAction

        interpretation = TurnInterpretation(
            dialog_act="ASK_HELP",
            intent_type="INSTRUCTION_LOOKUP",
            entities={},
            slot_candidates={},
            confidence=0.9,
            goal_transition="SWITCH",
            needs_clarification=False,
            references_pending_question=False,
            user_correction=False,
            context_shift="SWITCH_GOAL",
            reasoning_trace_short="test",
        )
        plan = self.agent.turn_planner.plan(self.repo.get_slot_state("s1"), interpretation)
        self.assertEqual(plan.action, PlannedAction.ANSWER_INSTRUCTION)

    def test_slot_resolution_rejects_full_utterance_system_fallback(self) -> None:
        from app.agent.turn_plan import SlotResolutionStatus, SlotSourceKind

        self.repo.system_candidates_map["я ракетчик в афганистане какие у меня доступы"] = [
            {
                "system_id": 99,
                "system_name_raw": "АС ЕФС. Наш бизнес (ПРОМ) (И3) [CI00000099]",
                "ci_code": "[CI00000099]",
                "score": 0.43,
                "matched_by": ["trigram"],
            }
        ]

        result = self.agent.slot_resolution_service.resolve_system(
            "я ракетчик в Афганистане, какие у меня доступы?",
            self.repo.get_slot_state("s1"),
            SlotSourceKind.FULL_UTTERANCE_FALLBACK,
        )
        self.assertEqual(result.status, SlotResolutionStatus.MISSING)

    def test_state_reducer_applies_system_resolution_once(self) -> None:
        from app.agent.turn_plan import SlotResolution, SlotResolutionStatus, SlotSourceKind

        resolution = SlotResolution(
            slot_name="system",
            raw_value="АСК",
            status=SlotResolutionStatus.ACCEPTED,
            canonical_value="АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
            canonical_id=12,
            confidence=1.0,
            source_kind=SlotSourceKind.LLM_ENTITY,
        )
        self.agent.state_reducer.apply_slot_resolution("s1", resolution)
        state = self.repo.get_slot_state("s1")
        self.assertEqual(state.get("resolved_system_id"), 12)
        self.assertEqual(state.get("system_raw"), resolution.canonical_value)

    def _seed_broad_system_dialog_data(self) -> None:
        self.repo.system_browse_map["ЕФС"] = [
            {
                "system_id": 11,
                "system_name_raw": "АС ЕФС. Наш бизнес (И2) [CI02040474]",
                "score": 0.77,
                "has_profile_access": False,
            },
            {
                "system_id": 12,
                "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "score": 0.76,
                "has_profile_access": True,
            },
            {
                "system_id": 13,
                "system_name_raw": "ЕФС ЕРМ ЦКР (И2) [CI01982477]",
                "score": 0.75,
                "has_profile_access": False,
            },
            {
                "system_id": 14,
                "system_name_raw": "ЕФС - Сотрудники. Кредитная машина (ПРОМ) (И3) [CI02637239]",
                "score": 0.74,
                "has_profile_access": False,
            },
            {
                "system_id": 15,
                "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "score": 0.73,
                "has_profile_access": False,
            },
        ]
        self.repo.system_browse_map["SberHelp"] = [
            {
                "system_id": 12,
                "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "score": 0.92,
                "has_profile_access": True,
            }
        ]
        self.repo.system_candidates_map["SberHelp"] = list(self.repo.system_browse_map["SberHelp"])
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        self.repo.profiles[501] = {
            "profile_id": 501,
            "profile_code": "P00050108",
            "profile_name": "Доступы у Контроля качества НСК (функционал РМ)",
            "profile_type": "Дополнительный - Совмещение полномочий",
        }
        self.repo.profile_candidates_result = [
            {
                "profile_id": 501,
                "profile_code": "P00050108",
                "profile_name": "Доступы у Контроля качества НСК (функционал РМ)",
                "profile_type": "Дополнительный - Совмещение полномочий",
                "match_score": 0.94,
            }
        ]
        self.repo.access_map[(501, 12)] = [
            {
                "system_id": 12,
                "system_name": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "entitlement_id": 7001,
                "entitlement_type": "Полномочия",
                "entitlement_name": "ЕРМ.ЦКР SberHelp Читатель SberHelp",
                "access_level": 1,
            }
        ]

    def test_pending_question_maps_to_confirmation(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="profile",
            source_query="query",
            options=[
                {"option_key": str(index), "option_label": f"Профиль {index}", "option_payload": {"profile_id": index}}
                for index in range(1, 7)
            ],
            page_size=5,
        )
        self.repo.update_slot_state(
            "s1",
            pending_question={
                "kind": "candidate_selection",
                "topic": "profile",
                "prompt": "Выберите профиль",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )
        pending_question = self.agent._hydrate_pending_question(self.repo.get_slot_state("s1"))
        confirmation = self.agent._pending_question_to_confirmation(pending_question)
        self.assertIsNotNone(confirmation)
        self.assertEqual(len(confirmation.options), 6)
        self.assertEqual(confirmation.options[-1].label, "Показать еще варианты")

    def test_context_hides_unconfirmed_candidate_slot(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="department",
            source_query="Фрод экспертиза",
            options=[
                {
                    "option_key": "1",
                    "option_label": "Отдел экспертизы кредитных рисков корпоративных клиентов №1",
                    "option_payload": {"value": "Отдел экспертизы кредитных рисков корпоративных клиентов №1"},
                }
            ],
            page_size=5,
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="SYSTEM_DISCOVERY",
            last_intent_type="SYSTEM_DISCOVERY",
            position_raw="Риск-менеджер",
            city_raw="Москва",
            department_raw="Фрод экспертиза",
            pending_question={
                "kind": "candidate_selection",
                "topic": "department",
                "prompt": "Выберите отдел",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )

        response = self.agent._store_assistant_response(
            session_id="s1",
            assistant_text="Выберите отдел",
            intent_type="SYSTEM_DISCOVERY",
            dialog_act="PROVIDE_SLOT",
        )

        self.assertEqual(response.context["position"], "Риск-менеджер")
        self.assertEqual(response.context["city"], "Москва")
        self.assertIsNone(response.context["department"])

    def test_pending_department_does_not_rewrite_system_from_answer_text(self) -> None:
        self.repo.systems[21] = {
            "id": 21,
            "system_name_raw": "АС Залоги (ПРОМ) (И2) [CI00000021]",
            "ci_code": "[CI00000021]",
        }
        self.repo.systems[22] = {
            "id": 22,
            "system_name_raw": "АС Мониторинг и анализ финансовых институтов (MAFIN) (И2) [CI00000022]",
            "ci_code": "[CI00000022]",
        }
        self.repo.system_candidates_map["Финансовые институты"] = [
            {
                "system_id": 22,
                "system_name_raw": self.repo.systems[22]["system_name_raw"],
                "ci_code": "[CI00000022]",
                "alias_text": "Финансовые институты",
                "score": 0.93,
            }
        ]
        self.repo.department_candidates_map[("Финансовые институты", "Москва")] = [
            {
                "value": "Отдел экспертизы финансовых институтов",
                "score": 0.94,
            }
        ]
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw=self.repo.systems[21]["system_name_raw"],
            resolved_system_id=21,
            position_raw="Андеррайтер",
            city_raw="Москва",
            pending_question={
                "kind": "slot_request",
                "topic": "department",
                "prompt": "Укажите отдел",
            },
        )

        def forced_department_answer(*_args, **_kwargs):
            return {
                "dialog_act": "PROVIDE_SLOT",
                "intent_type": "ROLE_DISCOVERY",
                "entities": {
                    "system_raw": "Финансовые институты",
                    "department_raw": "Финансовые институты",
                },
                "slot_candidates": {},
                "confidence": 0.82,
                "goal_transition": "STAY",
                "context_shift": "CHANGE_SYSTEM_FOCUS",
                "needs_clarification": False,
                "references_pending_question": True,
                "user_correction": False,
                "reasoning_trace_short": "department_answer_with_system_like_text",
            }

        self.agent.gigachat.complete_json = forced_department_answer
        self.agent.handle_message("s1", "Финансовые институты")

        state = self.repo.get_slot_state("s1")
        self.assertEqual(state.get("resolved_system_id"), 21)
        self.assertEqual(state.get("system_raw"), self.repo.systems[21]["system_name_raw"])
        self.assertEqual(state.get("department_raw"), "Отдел экспертизы финансовых институтов")

    def test_pending_department_extracts_expected_slot_from_mixed_reply(self) -> None:
        self.repo.systems[21] = {
            "id": 21,
            "system_name_raw": "АС Залоги (ПРОМ) (И2) [CI00000021]",
            "ci_code": "[CI00000021]",
        }
        self.repo.system_candidates_map["АС Залоги"] = [
            {
                "system_id": 21,
                "system_name_raw": self.repo.systems[21]["system_name_raw"],
                "ci_code": "[CI00000021]",
                "score": 0.95,
            }
        ]
        self.repo.position_candidates_map[("андеррайтер", "Москва", None)] = [
            {"value": "Андеррайтер", "score": 0.96}
        ]
        self.repo.department_candidates_map[("отдел ФИ", "Москва")] = [
            {
                "value": "Отдел экспертизы финансовых институтов",
                "score": 0.91,
                "alias_text": "ФИ",
                "alias_class": "SAFE",
            }
        ]
        self.repo.department_candidates_map[("ФИ", "Москва")] = list(
            self.repo.department_candidates_map[("отдел ФИ", "Москва")]
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw=self.repo.systems[21]["system_name_raw"],
            resolved_system_id=21,
            position_raw="Андеррайтер",
            city_raw="Москва",
            pending_question={
                "kind": "slot_request",
                "topic": "department",
                "prompt": "Укажите отдел",
            },
        )

        def forced_mixed_answer(*_args, **_kwargs):
            return {
                "dialog_act": "PROVIDE_SLOT",
                "intent_type": "ROLE_DISCOVERY",
                "entities": {
                    "system_raw": "АС Залоги",
                    "position_raw": "андеррайтер",
                    "department_raw": "отдел ФИ",
                },
                "slot_candidates": {},
                "confidence": 0.84,
                "goal_transition": "STAY",
                "context_shift": "NONE",
                "needs_clarification": False,
                "references_pending_question": True,
                "user_correction": False,
                "reasoning_trace_short": "mixed_department_answer",
            }

        self.agent.gigachat.complete_json = forced_mixed_answer
        self.agent.handle_message("s1", "АС Залоги, андеррайтер, отдел ФИ")

        state = self.repo.get_slot_state("s1")
        self.assertEqual(state.get("resolved_system_id"), 21)
        self.assertEqual(state.get("system_raw"), self.repo.systems[21]["system_name_raw"])
        self.assertEqual(state.get("position_raw"), "Андеррайтер")
        self.assertEqual(state.get("department_raw"), "Отдел экспертизы финансовых институтов")

    def test_change_system_during_department_candidate_selection_clears_unconfirmed_department(self) -> None:
        self.repo.systems[21] = {
            "id": 21,
            "system_name_raw": "АС Залоги (ПРОМ) (И2) [CI00000021]",
            "ci_code": "[CI00000021]",
        }
        self.repo.systems[89] = {
            "id": 89,
            "system_name_raw": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
            "ci_code": "[CI02319693]",
        }
        self.repo.system_candidates_map["АСК"] = [
            {
                "system_id": 89,
                "system_name_raw": self.repo.systems[89]["system_name_raw"],
                "ci_code": "[CI02319693]",
                "alias_text": "АСК",
                "score": 1.0,
                "alias_class": "SAFE",
            }
        ]
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="department",
            source_query="поменяй АС на АСК",
            options=[
                {
                    "option_key": "1",
                    "option_label": "Отдел Контроля качества",
                    "option_payload": {"value": "Отдел Контроля качества"},
                }
            ],
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw=self.repo.systems[21]["system_name_raw"],
            resolved_system_id=21,
            position_raw="Менеджер направления",
            city_raw="Москва",
            department_raw="поменяй АС на АСК",
            pending_question={
                "kind": "candidate_selection",
                "topic": "department",
                "prompt": "Выберите отдел",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )

        def forced_change_system(*_args, **_kwargs):
            return {
                "dialog_act": "CHANGE_SYSTEM",
                "intent_type": "ROLE_DISCOVERY",
                "entities": {"system_raw": "АСК"},
                "slot_candidates": {},
                "confidence": 0.0,
                "goal_transition": "STAY",
                "context_shift": "NONE",
                "needs_clarification": False,
                "references_pending_question": True,
                "user_correction": False,
                "reasoning_trace_short": "change_system_while_department_selection_pending",
            }

        self.agent.gigachat.complete_json = forced_change_system
        response = self.agent.handle_message("s1", "Хочу сменить АС на АСК")

        state = self.repo.get_slot_state("s1")
        self.assertEqual(state.get("resolved_system_id"), 89)
        self.assertEqual(state.get("system_raw"), self.repo.systems[89]["system_name_raw"])
        self.assertIsNone(state.get("department_raw"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "department")

    def test_interpretation_detects_system_change_during_profile_confirmation(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            pending_question={
                "kind": "candidate_selection",
                "topic": "profile",
                "prompt": "Выберите профиль",
                "candidate_set_id": 1,
            },
        )
        interpretation = self.agent._interpret_turn_heuristic(
            "уточню, в ЕФС Риск Решения",
            self.repo.get_slot_state("s1"),
        )
        self.assertEqual(interpretation.dialog_act, "CHANGE_SYSTEM")
        self.assertIn("system_raw", interpretation.entities)

    def test_interpretation_guardrails_normalize_invalid_payload_values(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            pending_question={
                "kind": "slot_request",
                "topic": "city",
                "prompt": "Укажите город",
            },
        )
        raw = TurnInterpretation(
            dialog_act="INVALID",
            intent_type="INVALID",
            entities={"city_raw": "  Москва  "},
            slot_candidates={"city": [{"value": "  Москва  ", "score": "0.95"}]},
            confidence="1.2",
            goal_transition="INVALID",
            needs_clarification=False,
            references_pending_question=False,
            user_correction=False,
            reasoning_trace_short="  trace  ",
        )
        normalized = self.agent._apply_interpretation_guardrails(raw, "Москва", self.repo.get_slot_state("s1"))
        self.assertEqual(normalized.dialog_act, "PROVIDE_SLOT")
        self.assertEqual(normalized.intent_type, "ROLE_DISCOVERY")
        self.assertEqual(normalized.entities.get("city_raw"), "Москва")
        self.assertEqual(normalized.goal_transition, "STAY")
        self.assertEqual(normalized.confidence, 1.0)
        self.assertEqual(normalized.reasoning_trace_short, "trace")

    def test_candidate_selection_guardrail_coerces_non_select_dialog_act(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="position",
            source_query="риск менеджер",
            options=[
                {"option_key": "Риск-менеджер", "option_label": "Риск-менеджер", "option_payload": {"value": "Риск-менеджер"}},
                {"option_key": "Главный риск-менеджер", "option_label": "Главный риск-менеджер", "option_payload": {"value": "Главный риск-менеджер"}},
            ],
            page_size=5,
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            pending_question={
                "kind": "candidate_selection",
                "topic": "position",
                "prompt": "Выберите должность",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )
        raw = TurnInterpretation(
            dialog_act="PROVIDE_SLOT",
            intent_type="ROLE_DISCOVERY",
            entities={},
            confidence=0.6,
        )
        normalized = self.agent._apply_interpretation_guardrails(raw, "1. Риск-менеджер", self.repo.get_slot_state("s1"))
        self.assertEqual(normalized.dialog_act, "SELECT_OPTION")
        self.assertEqual(normalized.entities.get("selection_text"), "1. Риск-менеджер")

    def test_fallback_interpreter_does_not_route_by_keywords_without_state(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="UNKNOWN",
            last_intent_type="UNKNOWN",
            pending_question=None,
        )
        interpretation = self.agent._interpret_turn_fallback(
            "Какие роли доступны в ЕФС?",
            self.repo.get_slot_state("s1"),
        )
        self.assertEqual(interpretation.dialog_act, "ASK_HELP")
        self.assertEqual(interpretation.intent_type, "UNKNOWN")
        self.assertEqual(interpretation.entities, {})

    def test_role_discovery_paraphrases_keep_same_initial_route(self) -> None:
        self.repo.system_browse_map["ЕФС"] = [
            {
                "system_id": 18,
                "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "score": 0.9,
                "has_profile_access": True,
            }
        ]
        paraphrases = [
            "Какая роль нужна в ЕФС?",
            "Какие роли есть в ЕФС?",
            "Какие роли доступны в системе ЕФС?",
        ]
        for text in paraphrases:
            self.agent._reset_context("s1")
            response = self.agent.handle_message("s1", text)
            self.assertEqual(response.intent_type, "ROLE_DISCOVERY")
            self.assertIsNotNone(response.pending_question)
            self.assertEqual(response.pending_question.topic, "position")

    def test_instruction_response_keeps_resolved_system_context(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="INSTRUCTION_LOOKUP",
            last_intent_type="INSTRUCTION_LOOKUP",
            system_raw="ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            resolved_system_id=12,
        )
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        response = self.agent.handle_message("s1", "Как получить доступ?")
        self.assertEqual(response.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertIn("SberHelp", response.assistant_text)

    def test_show_more_works_for_candidate_sets(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="profile",
            source_query="query",
            options=[
                {"option_key": str(index), "option_label": f"Профиль {index}", "option_payload": {"profile_id": index}}
                for index in range(1, 8)
            ],
            page_size=5,
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            pending_question={
                "kind": "candidate_selection",
                "topic": "profile",
                "prompt": "Выберите профиль",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )
        response = self.agent.handle_message("s1", "Показать еще варианты")
        self.assertEqual(response.dialog_act, "SHOW_MORE")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.page_offset, 5)

    def test_change_system_while_profile_confirmation_continues_without_loop(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="profile",
            source_query="query",
            options=[
                {"option_key": "11", "option_label": "Старый профиль", "option_payload": {"profile_id": 11}},
            ],
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС",
            position_raw="риск-менеджер",
            position_normalized="риск менеджер",
            city_raw="москва",
            city_normalized="москва",
            department_raw="отдел кредитования номер 2",
            department_normalized="отдел кредитования номер 2",
            pending_question={
                "kind": "candidate_selection",
                "topic": "profile",
                "prompt": "Выберите профиль",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )
        self.repo.system_candidates_map["ЕФС Риск Решения"] = [
            {
                "system_id": 18,
                "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "score": 0.91,
            }
        ]
        self.repo.systems[18] = {
            "id": 18,
            "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
            "ci_code": "[CI04206161]",
        }
        self.repo.profile_candidates_result = [
            {
                "profile_id": 54,
                "profile_code": "P00017425",
                "profile_name": "Риск-менеджер Москва",
                "profile_type": "Дополнительный",
                "match_score": 0.88,
            }
        ]
        self.repo.profiles[54] = {
            "profile_id": 54,
            "profile_code": "P00017425",
            "profile_name": "Риск-менеджер Москва",
            "profile_type": "Дополнительный",
        }
        self.repo.access_map[(54, 18)] = [
            {
                "system_id": 18,
                "system_name": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "entitlement_id": 1,
                "entitlement_type": "Роль",
                "entitlement_name": "Рабочее место",
                "access_level": 1,
            }
        ]
        response = self.agent.handle_message("s1", "уточню, в ЕФС Риск Решения")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertNotIn("Не удалось распознать", response.assistant_text)
        self.assertEqual(self.repo.get_slot_state("s1")["resolved_system_id"], 18)

    def test_generic_instruction_lookup_works_without_system(self) -> None:
        response = self.agent.handle_message("s1", "Как мне получить доступ по запросу?")
        self.assertEqual(response.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertTrue(response.answer.instruction)
        self.assertEqual(len(response.answer.citations), 1)
        self.assertEqual(response.instruction_mode, "INLINE_DOC")

    def test_access_issue_with_system_starts_structured_collection(self) -> None:
        self.repo.system_candidates_map["АСК"] = [
            {
                "system_id": 71,
                "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "ci_code": "[CI90000001]",
                "alias_text": "АСК",
                "score": 0.95,
            }
        ]
        self.repo.systems[71] = {
            "id": 71,
            "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            "ci_code": "[CI90000001]",
        }
        response = self.agent.handle_message("s1", "У меня нет доступа к АСК")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")
        self.assertEqual(response.intent_type, "ROLE_DISCOVERY")

    def test_initial_safe_system_alias_starts_role_discovery_without_change_prefix(self) -> None:
        system_name = "Пуаро 2.0 (И2) [CI03345728]"
        self.repo.system_candidates_map["Пуаро"] = [
            {
                "system_id": 55,
                "system_name_raw": system_name,
                "ci_code": "[CI03345728]",
                "alias_text": "Пуаро",
                "alias_class": "SAFE",
                "score": 1.0,
                "matched_by": ["exact"],
            }
        ]
        self.repo.systems[55] = {
            "id": 55,
            "system_name_raw": system_name,
            "ci_code": "[CI03345728]",
        }

        response = self.agent.handle_message("s1", "Пуаро")
        state = self.repo.get_slot_state("s1")

        self.assertEqual(response.intent_type, "ROLE_DISCOVERY")
        self.assertEqual(response.pending_question.topic, "position")
        self.assertEqual(state.get("resolved_system_id"), 55)
        self.assertNotIn("Переключаюсь на другую АС", response.assistant_text)

    def test_unsafe_system_alias_is_not_auto_accepted(self) -> None:
        from app.agent.turn_plan import SlotResolutionStatus, SlotSourceKind

        self.repo.system_candidates_map["АС"] = [
            {
                "system_id": 55,
                "system_name_raw": "Пуаро 2.0 (И2) [CI03345728]",
                "alias_text": "АС",
                "alias_class": "UNSAFE",
                "score": 0.95,
            }
        ]

        result = self.agent.slot_resolution_service.resolve_system(
            "АС",
            self.repo.get_slot_state("s1"),
            SlotSourceKind.LLM_ENTITY,
        )
        self.assertEqual(result.status, SlotResolutionStatus.REJECTED)
        self.assertEqual(result.reason, "unsafe_system_alias")

    def test_ambiguous_system_alias_returns_candidates(self) -> None:
        from app.agent.turn_plan import SlotResolutionStatus, SlotSourceKind

        self.repo.system_candidates_map["ЕФС"] = [
            {
                "system_id": 1,
                "system_name_raw": "ЕФС. Первая система (И2) [CI00000001]",
                "alias_text": "ЕФС",
                "alias_class": "AMBIGUOUS",
                "score": 0.95,
            },
            {
                "system_id": 2,
                "system_name_raw": "ЕФС. Вторая система (И2) [CI00000002]",
                "alias_text": "ЕФС",
                "alias_class": "AMBIGUOUS",
                "score": 0.94,
            },
        ]

        result = self.agent.slot_resolution_service.resolve_system(
            "ЕФС",
            self.repo.get_slot_state("s1"),
            SlotSourceKind.LLM_ENTITY,
        )
        self.assertEqual(result.status, SlotResolutionStatus.CANDIDATES)
        self.assertEqual(result.reason, "ambiguous_system_alias")

    def test_department_alias_can_resolve_short_abbreviation(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="SYSTEM_DISCOVERY",
            last_intent_type="SYSTEM_DISCOVERY",
            position_raw="Риск-менеджер",
            city_raw="Москва",
            pending_question={
                "kind": "slot_request",
                "topic": "department",
                "prompt": "Укажите отдел",
            },
        )
        department = "Отдел экспертизы кредитных рисков финансовых институтов (10331828); - ОЭКРФИ, ФИ"
        self.repo.department_candidates_map[("ФИ", "Москва")] = [
            {
                "value": department,
                "score": 0.96,
                "alias_text": "ФИ",
                "alias_class": "SAFE",
            }
        ]

        response = self.agent.handle_message("s1", "ФИ")
        state = self.repo.get_slot_state("s1")

        self.assertEqual(state.get("department_raw"), department)
        self.assertIsNotNone(response.answer)
        self.assertEqual(response.answer.answer_type, "SYSTEM_DISCOVERY")

    def test_mixed_slot_ambiguous_system_alias_is_not_resolved(self) -> None:
        self.repo.system_candidates_map["ЕФС"] = [
            {
                "system_id": 1,
                "system_name_raw": "ЕФС. Первая система (И2) [CI00000001]",
                "alias_text": "ЕФС",
                "alias_class": "AMBIGUOUS",
                "score": 0.95,
            }
        ]
        candidate = self.agent._mixed_slot_candidate(
            "system",
            "ЕФС",
            0,
            self.repo.get_slot_state("s1"),
            {},
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["status"], "ambiguous")
        self.assertEqual(candidate["value"], "ЕФС")

    def test_ambiguous_department_alias_prompts_candidates_instead_of_autofill(self) -> None:
        department = "Отдел дочерних банков"
        self.repo.department_candidates_map[("ДБ", None)] = [
            {
                "value": department,
                "score": 0.82,
                "alias_text": "ДБ",
                "alias_class": "AMBIGUOUS",
            }
        ]
        validation = self.agent._validate_entity_value_for_org_slot(
            "s1",
            "department",
            "ДБ",
            self.repo.get_slot_state("s1"),
            log_probe=False,
        )
        self.assertTrue(validation["valid"])
        self.assertIsNone(validation["canonical_value"])

        mixed = self.agent._mixed_slot_candidate(
            "department",
            "ДБ",
            2,
            self.repo.get_slot_state("s1"),
            {},
        )
        self.assertIsNotNone(mixed)
        self.assertEqual(mixed["status"], "ambiguous")
        self.assertEqual(mixed["value"], "ДБ")

    def test_initial_change_system_focus_is_downgraded_to_start_context_shift(self) -> None:
        interpretation = TurnInterpretation(
            dialog_act="CHANGE_SYSTEM",
            intent_type="ROLE_DISCOVERY",
            entities={"system_raw": "Пуаро"},
            confidence=0.9,
            goal_transition="START",
            context_shift="CHANGE_SYSTEM_FOCUS",
        )
        self.agent.policy_service.apply_context_shift("s1", self.repo.get_slot_state("s1"), interpretation)
        self.agent.policy_service.apply_goal_transition("s1", self.repo.get_slot_state("s1"), interpretation)
        state = self.repo.get_slot_state("s1")
        self.assertEqual(state.get("context_shift"), "NONE")

    def test_system_discovery_collects_org_slots_without_system(self) -> None:
        response = self.agent.handle_message("s1", "Какие АС мне доступны?")
        self.assertEqual(response.intent_type, "SYSTEM_DISCOVERY")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")
        self.assertEqual(response.conversation_phase, "COLLECT_POSITION")

    def test_system_discovery_question_can_override_role_acquisition_guess(self) -> None:
        response = self.agent.handle_message("s1", "К каким АС мне положен доступ?")
        self.assertEqual(response.intent_type, "SYSTEM_DISCOVERY")
        self.assertEqual(response.active_goal, "SYSTEM_DISCOVERY")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")

    def test_rejects_system_entity_that_is_not_in_system_dictionary(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            pending_question={
                "kind": "slot_request",
                "topic": "system",
                "prompt": "Укажите АС",
            },
        )
        self.repo.system_candidates_map["Москва"] = []

        response = self.agent.handle_message("s1", "Москва")
        state = self.repo.get_slot_state("s1")

        self.assertIsNone(state.get("system_raw"))
        self.assertIsNone(state.get("resolved_system_id"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")
        self.assertTrue(
            any(
                call["tool_name"] == "reject_invalid_entity_slot"
                and call["result_summary"] == "not_found_in_system_dictionary"
                for call in self.repo.tool_calls
            )
        )

    def test_invalid_explicit_system_stops_role_flow_instead_of_autoselecting_from_full_text(self) -> None:
        def scripted_complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
            if "детектор сценария запроса" in system_prompt:
                return {"target": "ROLE_LIST", "confidence": 0.9, "reason": "role_scope"}
            return {
                "dialog_act": "ASK_HELP",
                "intent_type": "ROLE_DISCOVERY",
                "entities": {"system_raw": "ЭФИР", "position_raw": "я риск менеджер"},
                "slot_candidates": {},
                "confidence": 0.8,
                "goal_transition": "START",
                "needs_clarification": False,
                "references_pending_question": False,
                "user_correction": False,
                "reasoning_trace_short": "explicit_unknown_system",
            }

        self.agent.gigachat.complete_json = scripted_complete_json
        self.repo.system_candidates_map["ЭФИР"] = []
        self.repo.system_candidates_map["я риск менеджер, какие роли мне доступны в ЭФИРЕ?"] = [
            {
                "system_id": 54,
                "system_name_raw": "Кредитные риски КИБ (И2) [CI00767391]",
                "ci_code": "[CI00767391]",
                "score": 0.92,
            }
        ]

        response = self.agent.handle_message("s1", "я риск менеджер, какие роли мне доступны в ЭФИРЕ?")
        state = self.repo.get_slot_state("s1")

        self.assertIsNone(state.get("resolved_system_id"))
        self.assertIsNone(state.get("system_raw"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")
        self.assertIn("ЭФИР", response.assistant_text)
        self.assertNotIn("Кредитные риски КИБ", response.assistant_text)

    def test_explicit_unknown_system_does_not_resolve_from_low_score_full_utterance_candidate(self) -> None:
        def scripted_complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
            if "детектор сценария запроса" in system_prompt:
                return {"target": "ROLE_LIST", "confidence": 0.9, "reason": "role_scope"}
            return {
                "dialog_act": "ASK_HELP",
                "intent_type": "ROLE_DISCOVERY",
                "entities": {"system_raw": "ЭФИР"},
                "slot_candidates": {},
                "confidence": 0.8,
                "goal_transition": "START",
                "needs_clarification": False,
                "references_pending_question": False,
                "user_correction": False,
                "reasoning_trace_short": "explicit_unknown_system_low_score_fallback",
            }

        self.agent.gigachat.complete_json = scripted_complete_json
        self.repo.system_candidates_map["ЭФИР"] = []
        self.repo.system_candidates_map["я ракетчик в афганистане какие у меня доступы"] = [
            {
                "system_id": 99,
                "system_name_raw": "АС ЕФС. Наш бизнес (ПРОМ) (И3) [CI00000099]",
                "ci_code": "[CI00000099]",
                "score": 0.43,
                "matched_by": ["trigram"],
            }
        ]

        response = self.agent.handle_message("s1", "я ракетчик в Афганистане, какие у меня доступы?")
        state = self.repo.get_slot_state("s1")

        self.assertIsNone(state.get("resolved_system_id"))
        self.assertIsNone(state.get("system_raw"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")

    def test_candidate_selection_is_applied_before_entity_binding(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="system",
            source_query="АСК",
            options=[
                {
                    "option_key": "12",
                    "option_label": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
                    "option_payload": {
                        "system_id": 12,
                        "system_name": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
                    },
                }
            ],
            page_size=5,
        )
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
            "ci_code": "[CI02319693]",
        }
        self.repo.update_slot_state(
            "s1",
            active_goal="SYSTEM_DISCOVERY",
            last_intent_type="SYSTEM_DISCOVERY",
            position_raw="Риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел экспертизы кредитных рисков корпоративных клиентов №7",
            pending_question={
                "kind": "candidate_selection",
                "topic": "system",
                "prompt": "Выберите АС",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
            conversation_phase="ANSWER_SYSTEM_DISCOVERY",
        )

        response = self.agent.handle_message("s1", "1")
        state = self.repo.get_slot_state("s1")

        self.assertEqual(state.get("resolved_system_id"), 12)
        self.assertEqual(state.get("system_raw"), self.repo.systems[12]["system_name_raw"])
        self.assertIsNotNone(response.answer)
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")

    def test_org_candidate_selection_number_is_not_reparsed_as_system(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="position",
            source_query="риск-менеджер",
            options=[
                {"option_key": "1", "option_label": "Главный риск-менеджер", "option_payload": {"value": "Главный риск-менеджер"}},
                {"option_key": "2", "option_label": "Отраслевой риск-менеджер", "option_payload": {"value": "Отраслевой риск-менеджер"}},
                {"option_key": "3", "option_label": "Риск-менеджер", "option_payload": {"value": "Риск-менеджер"}},
            ],
            page_size=5,
        )
        self.repo.system_candidates_map["3"] = [
            {
                "system_id": 77,
                "system_name_raw": "OneKIB (АС ПКАП СС360) (И3) [CI03259775]",
                "ci_code": "[CI03259775]",
                "score": 0.95,
            }
        ]
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС",
            system_query_raw="ЕФС",
            system_resolution_mode="BROWSE",
            pending_question={
                "kind": "candidate_selection",
                "topic": "position",
                "prompt": "Выберите должность",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )

        response = self.agent.handle_message("s1", "3")
        state = self.repo.get_slot_state("s1")

        self.assertEqual(state.get("position_raw"), "Риск-менеджер")
        self.assertEqual(state.get("system_raw"), "ЕФС")
        self.assertIsNone(state.get("resolved_system_id"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "city")

    def test_single_low_score_system_candidate_is_rejected(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            pending_question={
                "kind": "slot_request",
                "topic": "system",
                "prompt": "Укажите АС",
            },
        )
        self.repo.system_candidates_map["Эфирк"] = [
            {
                "system_id": 51,
                "system_name_raw": "ЕФС ЕРМ ЦКР (И2) [CI01982477]",
                "ci_code": "[CI01982477]",
                "score": 0.375,
            }
        ]

        response = self.agent.handle_message("s1", "Эфирк")
        state = self.repo.get_slot_state("s1")

        self.assertIsNone(state.get("system_raw"))
        self.assertIsNone(state.get("resolved_system_id"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")

    def test_org_slot_answer_does_not_autodetect_system_from_slot_text(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            position_raw="Главный риск-менеджер",
            city_raw="Москва",
            pending_question={
                "kind": "slot_request",
                "topic": "department",
                "prompt": "Укажите отдел",
            },
        )
        self.repo.system_candidates_map["отдел 2"] = [
            {
                "system_id": 55,
                "system_name_raw": "Пуаро 2.0 (И2) [CI03345728]",
                "ci_code": "[CI03345728]",
                "score": 1.0,
            }
        ]

        response = self.agent.handle_message("s1", "отдел 2")
        state = self.repo.get_slot_state("s1")

        self.assertIsNone(state.get("system_raw"))
        self.assertIsNone(state.get("resolved_system_id"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")
        self.assertNotIn("Пуаро", response.assistant_text)

    def test_invalid_system_in_specific_access_question_keeps_system_pending_after_org_collection(self) -> None:
        def scripted_complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
            if "детектор сценария запроса" in system_prompt:
                return {"target": "ROLE_LIST", "confidence": 0.9, "reason": "specific_system_access"}
            return {
                "dialog_act": "PROVIDE_SLOT",
                "intent_type": "SYSTEM_DISCOVERY",
                "entities": {"system_raw": "Херон"},
                "slot_candidates": {},
                "confidence": 0.8,
                "goal_transition": "START",
                "needs_clarification": False,
                "references_pending_question": False,
                "user_correction": False,
                "reasoning_trace_short": "specific_access_unknown_system",
            }

        self.agent.gigachat.complete_json = scripted_complete_json
        self.repo.system_candidates_map["Херон"] = []

        response = self.agent.handle_message("s1", "какие у меня доступы в Хероне?")
        state = self.repo.get_slot_state("s1")

        self.assertEqual(response.intent_type, "ROLE_DISCOVERY")
        self.assertEqual(state.get("active_goal"), "ROLE_DISCOVERY")
        self.assertIsNone(state.get("system_raw"))
        self.assertIsNone(state.get("resolved_system_id"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")

    def test_rejects_position_entity_that_is_not_in_position_dictionary(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="SYSTEM_DISCOVERY",
            last_intent_type="SYSTEM_DISCOVERY",
            pending_question={
                "kind": "slot_request",
                "topic": "position",
                "prompt": "Укажите должность",
            },
        )
        self.repo.position_candidates_map[("АСК", None, None)] = []

        response = self.agent.handle_message("s1", "АСК")
        state = self.repo.get_slot_state("s1")

        self.assertIsNone(state.get("position_raw"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")
        self.assertTrue(
            any(
                call["tool_name"] == "reject_invalid_entity_slot"
                and call["result_summary"] == "no_dictionary_candidate"
                for call in self.repo.tool_calls
            )
        )

    def test_low_confidence_role_acquisition_is_forced_to_system_discovery(self) -> None:
        def scripted_complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
            if "детектор сценария запроса" in system_prompt:
                return "INVALID"
            return {
                "dialog_act": "PROVIDE_SLOT",
                "intent_type": "ROLE_ACQUISITION",
                "entities": {},
                "slot_candidates": {},
                "confidence": 0.0,
                "goal_transition": "START",
                "needs_clarification": False,
                "references_pending_question": False,
                "user_correction": False,
                "reasoning_trace_short": "low_conf_role_acq",
            }

        self.agent.gigachat.complete_json = scripted_complete_json
        response = self.agent.handle_message("s1", "К каким АС мне положен доступ?")
        self.assertEqual(response.intent_type, "SYSTEM_DISCOVERY")
        self.assertEqual(response.active_goal, "SYSTEM_DISCOVERY")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")

    def test_role_discovery_without_system_can_switch_to_system_discovery(self) -> None:
        first = self.agent.handle_message("s1", "Какие роли мне доступны?")
        self.assertEqual(first.pending_question.topic, "system")
        second = self.agent.handle_message("s1", "Не знаю")
        self.assertEqual(second.intent_type, "SYSTEM_DISCOVERY")
        self.assertIsNotNone(second.pending_question)
        self.assertEqual(second.pending_question.topic, "position")

    def test_structured_goal_asks_system_before_profile(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            position_raw="риск-менеджер",
            city_raw="москва",
            department_raw="отдел кредитования номер 2",
        )
        response = self.agent.handle_message("s1", "продолжим")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")
        self.assertEqual(response.conversation_phase, "COLLECT_SYSTEM_HINT")

    def test_profile_followup_does_not_trigger_system_change(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
            resolved_system_id=18,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел экспертизы кредитных рисков финансовых институтов",
            resolved_profile_id=49,
            profile_candidates=[
                {
                    "profile_id": 49,
                    "profile_code": "P00017430",
                    "profile_name": "Отраслевой Риск-менеджер ОЭКРКК ФИ Москва (№: P00017430)",
                    "profile_type": "Дополнительный",
                }
            ],
        )
        self.repo.systems[18] = {
            "id": 18,
            "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
            "ci_code": "[CI04206161]",
        }
        self.repo.access_map[(49, 18)] = [
            {
                "system_id": 18,
                "system_name": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "entitlement_id": 101,
                "entitlement_type": "Роль",
                "entitlement_name": "Рабочее место",
                "access_level": 1,
            }
        ]
        response = self.agent.handle_message("s1", "какие роли входят в этот профиль?")
        self.assertNotEqual(response.dialog_act, "CHANGE_SYSTEM")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")

    def test_role_discovery_summary_contains_access_names(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
            resolved_system_id=18,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел экспертизы кредитных рисков финансовых институтов",
            resolved_profile_id=49,
            profile_candidates=[
                {
                    "profile_id": 49,
                    "profile_code": "P00017430",
                    "profile_name": "Отраслевой Риск-менеджер ОЭКРКК ФИ Москва (№: P00017430)",
                    "profile_type": "Дополнительный",
                }
            ],
        )
        self.repo.systems[18] = {
            "id": 18,
            "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
            "ci_code": "[CI04206161]",
        }
        self.repo.access_map[(49, 18)] = [
            {
                "system_id": 18,
                "system_name": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "entitlement_id": 101,
                "entitlement_type": "Роль",
                "entitlement_name": "Рабочее место РКИБ",
                "access_level": 1,
            },
            {
                "system_id": 18,
                "system_name": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "entitlement_id": 102,
                "entitlement_type": "Группа",
                "entitlement_name": "Группа согласования",
                "access_level": 2,
            },
        ]
        response = self.agent.handle_message("s1", "какие роли доступны?")
        self.assertIn("Рабочее место РКИБ", response.assistant_text)
        self.assertIn("Группа согласования", response.assistant_text)

    def test_department_unknown_prompts_candidate_selection(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
            resolved_system_id=18,
            position_raw="риск-менеджер",
            city_raw="москва",
            department_raw="отдел кредитования 2",
        )
        self.repo.department_candidates_map[("отдел кредитования 2", "москва")] = [
            {"value": "Отдел экспертизы кредитных рисков корпоративных клиентов №2", "score": 0.71},
            {"value": "Отдел экспертизы кредитных рисков корпоративных клиентов №3", "score": 0.67},
        ]
        response = self.agent.handle_message("s1", "продолжим")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "department")
        self.assertEqual(response.pending_question.kind, "candidate_selection")
        self.assertGreaterEqual(len(response.pending_question.options), 2)

    def test_department_candidate_selection_updates_slot(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
            resolved_system_id=18,
            position_raw="риск-менеджер",
            city_raw="москва",
            department_raw="неверный отдел",
        )
        self.repo.department_candidates_map[("неверный отдел", "москва")] = [
            {"value": "Отдел экспертизы кредитных рисков корпоративных клиентов №2", "score": 0.74},
        ]
        self.repo.profile_candidates_result = [
            {
                "profile_id": 54,
                "profile_code": "P00017425",
                "profile_name": "Риск-менеджер Москва",
                "profile_type": "Дополнительный",
                "match_score": 0.91,
            }
        ]
        self.repo.profiles[54] = {
            "profile_id": 54,
            "profile_code": "P00017425",
            "profile_name": "Риск-менеджер Москва",
            "profile_type": "Дополнительный",
        }
        self.repo.access_map[(54, 18)] = [
            {
                "system_id": 18,
                "system_name": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "entitlement_id": 1,
                "entitlement_type": "Роль",
                "entitlement_name": "Рабочее место",
                "access_level": 1,
            }
        ]
        first_response = self.agent.handle_message("s1", "продолжим")
        self.assertIsNotNone(first_response.pending_question)
        self.assertEqual(first_response.pending_question.topic, "department")
        second_response = self.agent.handle_message("s1", "1")
        self.assertEqual(second_response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertEqual(
            self.repo.get_slot_state("s1")["department_raw"],
            "Отдел экспертизы кредитных рисков корпоративных клиентов №2",
        )

    def test_generic_position_prompts_candidate_selection_for_variants(self) -> None:
        self.repo.system_browse_map["ЕФС"] = [
            {
                "system_id": 18,
                "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "score": 0.9,
                "has_profile_access": True,
            }
        ]
        self.repo.position_candidates_map[("риск менеджер", None, None)] = [
            {"value": "Риск-менеджер", "score": 0.95},
            {"value": "Главный риск-менеджер", "score": 0.93},
            {"value": "Старший риск-менеджер", "score": 0.9},
        ]
        first = self.agent.handle_message("s1", "Какие роли доступны в ЕФС?")
        self.assertIsNotNone(first.pending_question)
        self.assertEqual(first.pending_question.topic, "position")
        second = self.agent.handle_message("s1", "риск менеджер")
        self.assertIsNotNone(second.pending_question)
        self.assertEqual(second.pending_question.topic, "position")
        self.assertIn("похожих должностей", second.assistant_text.lower())

    def test_selected_position_variant_does_not_loop_back_to_position(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС",
            system_query_raw="ЕФС",
            system_resolution_mode="BROWSE",
            position_raw="Риск-менеджер",
        )
        self.repo.position_candidates_map[("Риск-менеджер", None, None)] = [
            {"value": "Риск-менеджер", "score": 0.96},
            {"value": "Главный риск-менеджер", "score": 0.92},
            {"value": "Старший риск-менеджер", "score": 0.9},
        ]
        response = self.agent.handle_message("s1", "продолжим")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "city")

    def test_broad_system_role_discovery_collects_position_first(self) -> None:
        self._seed_broad_system_dialog_data()
        response = self.agent.handle_message("s1", "Какая мне необходима роль в АС ЕФС?")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")
        self.assertEqual(response.conversation_phase, "COLLECT_POSITION")
        self.assertEqual(self.repo.get_slot_state("s1")["system_query_raw"], "ЕФС")
        self.assertEqual(self.repo.get_slot_state("s1")["system_resolution_mode"], "BROWSE")

    def test_system_discovery_returns_full_list_by_confirmed_context(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="SYSTEM_DISCOVERY",
            last_intent_type="SYSTEM_DISCOVERY",
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
        )
        self.repo.context_system_map[("Москва", "Отд КК НСК_Риск-менеджер", "риск-менеджер")] = [
            {
                "system_id": 12,
                "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "ci_code": "[CI06055442]",
            },
            {
                "system_id": 18,
                "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "ci_code": "[CI04206161]",
            },
        ]
        response = self.agent.handle_message("s1", "Продолжим")
        self.assertEqual(response.answer.answer_type, "SYSTEM_DISCOVERY")
        self.assertEqual(response.conversation_phase, "ANSWER_SYSTEM_DISCOVERY")
        self.assertEqual(len(response.answer.systems), 2)
        self.assertIn("доступны следующие АС", response.assistant_text)
        self.assertIsNone(response.context.get("system"))
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")

    def test_system_discovery_question_with_stale_system_context_resets_to_system_list(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС ЕРМ ЦКР (И2) [CI01982477]",
            system_query_raw="ЕФС",
            system_resolution_mode="DIRECT",
            resolved_system_id=10,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
            conversation_phase="ANSWER_ROLE_DISCOVERY",
        )
        self.repo.systems[10] = {
            "id": 10,
            "system_name_raw": "ЕФС ЕРМ ЦКР (И2) [CI01982477]",
            "ci_code": "[CI01982477]",
        }
        self.repo.context_system_map[("Москва", "Отд КК НСК_Риск-менеджер", "риск-менеджер")] = [
            {
                "system_id": 12,
                "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "ci_code": "[CI06055442]",
            },
            {
                "system_id": 18,
                "system_name_raw": "ЕФС.Сотрудники.Риск-решения (ПРОМ) (И2) [CI04206161]",
                "ci_code": "[CI04206161]",
            },
        ]
        response = self.agent.handle_message("s1", "К каким АС мне положен доступ?")
        self.assertEqual(response.answer.answer_type, "SYSTEM_DISCOVERY")
        self.assertIsNone(response.context.get("system"))
        self.assertEqual(response.active_goal, "SYSTEM_DISCOVERY")
        self.assertEqual(response.context_shift, "SWITCH_GOAL")
        self.assertIn("доступны следующие АС", response.assistant_text)

    def test_system_discovery_selection_transitions_to_role_discovery(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="SYSTEM_DISCOVERY",
            last_intent_type="SYSTEM_DISCOVERY",
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
        )
        self.repo.system_candidates_map["SberHelp"] = [
            {
                "system_id": 12,
                "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "ci_code": "[CI06055442]",
                "alias_text": "SberHelp",
                "score": 0.95,
            }
        ]
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        self.repo.context_access_map[("Москва", "Отд КК НСК_Риск-менеджер", "риск-менеджер", 12)] = (
            [
                {
                    "system_id": 12,
                    "system_name": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                    "profile_id": 501,
                    "profile_name": "Профиль 501",
                    "entitlement_id": 7001,
                    "entitlement_type": "Полномочия",
                    "entitlement_name": "ЕРМ.ЦКР SberHelp Читатель SberHelp",
                    "access_level": 1,
                }
            ],
            [
                {
                    "profile_id": 501,
                    "profile_code": "P00050108",
                    "profile_name": "Профиль 501",
                    "profile_type": "Основной",
                }
            ],
        )
        response = self.agent.handle_message("s1", "SberHelp")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertEqual(self.repo.get_slot_state("s1")["active_goal"], "ROLE_DISCOVERY")
        self.assertEqual(response.context["system"]["system_id"], 12)

    def test_system_discovery_selection_by_full_official_name_with_comma_transitions_to_role_discovery(self) -> None:
        official_name = "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]"
        self.repo.update_slot_state(
            "s1",
            active_goal="SYSTEM_DISCOVERY",
            last_intent_type="SYSTEM_DISCOVERY",
            position_raw="Главный риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел экспертизы кредитных рисков корпоративных клиентов №7",
        )
        self.repo.system_candidates_map[official_name] = [
            {
                "system_id": 1,
                "system_name_raw": official_name,
                "ci_code": "[CI02319693]",
                "alias_text": "АСК",
                "score": 0.99,
            }
        ]
        self.repo.systems[1] = {
            "id": 1,
            "system_name_raw": official_name,
            "ci_code": "[CI02319693]",
        }
        self.repo.context_access_map[(
            "Москва",
            "Отдел экспертизы кредитных рисков корпоративных клиентов №7",
            "Главный риск-менеджер",
            1,
        )] = (
            [
                {
                    "system_id": 1,
                    "system_name": official_name,
                    "profile_id": 3,
                    "profile_name": "Главный риск-менеджер ЦЭКРКК Москва",
                    "entitlement_id": 1001,
                    "entitlement_type": "Роли",
                    "entitlement_name": "Корректная роль",
                    "access_level": 1,
                }
            ],
            [
                {
                    "profile_id": 3,
                    "profile_code": "P00017432",
                    "profile_name": "Главный риск-менеджер ЦЭКРКК Москва",
                    "profile_type": "Дополнительный",
                }
            ],
        )
        response = self.agent.handle_message("s1", official_name)
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertEqual(response.context["system"]["system_name"], official_name)
        self.assertIn("Корректная роль", response.assistant_text)

    def test_broad_system_role_discovery_enters_system_browse_after_org_slots(self) -> None:
        self._seed_broad_system_dialog_data()
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС",
            system_query_raw="ЕФС",
            system_resolution_mode="BROWSE",
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
        )
        response = self.agent.handle_message("s1", "продолжим")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "system")
        self.assertEqual(response.conversation_phase, "BROWSE_SYSTEMS")
        self.assertEqual(len(response.pending_question.options), 5)
        self.assertIn("Я нашел несколько АС", response.assistant_text)

    def test_browse_system_show_more_without_more_candidates_requests_hint(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="system",
            source_query="ЕФС",
            options=[
                {"option_key": str(index), "option_label": f"ЕФС система {index}", "option_payload": {"system_id": index}}
                for index in range(1, 6)
            ],
            page_size=5,
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            conversation_phase="BROWSE_SYSTEMS",
            pending_question={
                "kind": "candidate_selection",
                "topic": "system",
                "prompt": "Выберите АС",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
        )
        response = self.agent.handle_message("s1", "Ок")
        self.assertEqual(response.dialog_act, "SHOW_MORE")
        self.assertEqual(response.conversation_phase, "COLLECT_SYSTEM_HINT")
        self.assertEqual(response.pending_question.topic, "system")
        self.assertIn("Других АС", response.assistant_text)

    def test_change_system_hint_during_browse_preserves_org_slots(self) -> None:
        self._seed_broad_system_dialog_data()
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС",
            system_query_raw="ЕФС",
            system_resolution_mode="BROWSE",
            conversation_phase="BROWSE_SYSTEMS",
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
            pending_question={
                "kind": "candidate_selection",
                "topic": "system",
                "prompt": "Выберите АС",
                "candidate_set_id": self.repo.create_candidate_set(
                    session_id="s1",
                    topic="system",
                    source_query="ЕФС",
                    options=[
                        {
                            "option_key": str(candidate["system_id"]),
                            "option_label": candidate["system_name_raw"],
                            "option_payload": {"system_id": candidate["system_id"], "system_name": candidate["system_name_raw"]},
                        }
                        for candidate in self.repo.system_browse_map["ЕФС"]
                    ],
                    page_size=5,
                ).candidate_set_id,
            },
        )
        response = self.agent.handle_message("s1", "Посмотри SberHelp")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertEqual(self.repo.get_slot_state("s1")["city_raw"], "Москва")
        self.assertEqual(self.repo.get_slot_state("s1")["department_raw"], "Отд КК НСК_Риск-менеджер")
        self.assertEqual(self.repo.get_slot_state("s1")["resolved_system_id"], 12)
        self.assertIn("ЕРМ.ЦКР SberHelp Читатель SberHelp", response.assistant_text)

    def test_requested_entitlement_prompt_allows_switch_to_role_discovery(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_ACQUISITION",
            last_intent_type="ROLE_ACQUISITION",
            system_raw="АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            resolved_system_id=71,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел Фрод экспертизы",
            resolved_profile_id=501,
            profile_candidates=[
                {
                    "profile_id": 501,
                    "profile_code": "P00050108",
                    "profile_name": "Доступы у Контроля качества НСК (функционал РМ)",
                    "profile_type": "Дополнительный - Совмещение полномочий",
                }
            ],
            pending_question={
                "kind": "slot_request",
                "topic": "requested_entitlement",
                "prompt": "Уточните, пожалуйста, какую именно роль или доступ нужно проверить.",
            },
        )
        self.repo.systems[71] = {
            "id": 71,
            "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            "ci_code": "[CI90000001]",
        }
        self.repo.access_map[(501, 71)] = [
            {
                "system_id": 71,
                "system_name": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "entitlement_id": 7101,
                "entitlement_type": "Полномочия",
                "entitlement_name": "Риск-менеджер",
                "access_level": 1,
                "justification_text": "Обоснование для роли по умолчанию",
            },
            {
                "system_id": 71,
                "system_name": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "entitlement_id": 7102,
                "entitlement_type": "Полномочия",
                "entitlement_name": "Риск-менеджер расширенный",
                "access_level": 2,
                "justification_text": "Обоснование для роли по запросу",
            },
        ]
        response = self.agent.handle_message("s1", "Какие роли мне доступны?")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertNotIn("не найдена", response.assistant_text.lower())
        self.assertIsNone(self.repo.get_slot_state("s1").get("requested_entitlement_raw"))
        self.assertIsNone(response.context.get("requested_role"))
        self.assertEqual(
            response.answer.default_accesses[0].get("justification_text"),
            "Обоснование для роли по умолчанию",
        )
        self.assertEqual(
            response.answer.request_accesses[0].get("justification_text"),
            "Обоснование для роли по запросу",
        )
        self.assertIn("Если хотите, подскажу", response.assistant_text)

    def test_generic_access_question_starts_role_discovery_without_requested_role(self) -> None:
        self.repo.system_candidates_map["АСК"] = [
            {
                "system_id": 71,
                "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "ci_code": "[CI90000001]",
                "alias_text": "АСК",
                "score": 0.96,
            }
        ]
        self.repo.system_candidates_map["АСК Риск-менеджмент (ПРОМ) [CI90000001]"] = list(
            self.repo.system_candidates_map["АСК"]
        )
        self.repo.systems[71] = {
            "id": 71,
            "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            "ci_code": "[CI90000001]",
        }
        response = self.agent.handle_message("s1", "У меня есть доступ к АСК?")
        state = self.repo.get_slot_state("s1")
        self.assertEqual(state.get("active_goal"), "ROLE_DISCOVERY")
        self.assertIsNone(state.get("requested_entitlement_raw"))
        self.assertEqual(state.get("system_raw"), "АСК Риск-менеджмент (ПРОМ) [CI90000001]")
        self.assertIsNone(response.context.get("requested_role"))
        self.assertEqual(response.pending_question.topic, "position")

    def test_access_issue_phrase_is_treated_as_system_not_role(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_ACQUISITION",
            last_intent_type="ROLE_ACQUISITION",
            pending_question={
                "kind": "slot_request",
                "topic": "requested_entitlement",
                "prompt": "Уточните, пожалуйста, какую именно роль или доступ нужно проверить.",
            },
        )
        self.repo.system_candidates_map["АСК"] = [
            {
                "system_id": 71,
                "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "ci_code": "[CI90000001]",
                "alias_text": "АСК",
                "score": 0.94,
            }
        ]
        self.repo.systems[71] = {
            "id": 71,
            "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            "ci_code": "[CI90000001]",
        }
        response = self.agent.handle_message("s1", "Нет доступа к АСК")
        state = self.repo.get_slot_state("s1")
        self.assertEqual(state["active_goal"], "ROLE_DISCOVERY")
        self.assertIsNone(state.get("requested_entitlement_raw"))
        self.assertEqual(state.get("system_raw"), "АСК Риск-менеджмент (ПРОМ) [CI90000001]")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")

    def test_system_alias_in_requested_role_slot_does_not_pollute_requested_role(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_ACQUISITION",
            last_intent_type="ROLE_ACQUISITION",
            pending_question={
                "kind": "slot_request",
                "topic": "requested_entitlement",
                "prompt": "Уточните, пожалуйста, какую именно роль или доступ нужно проверить.",
            },
        )
        self.repo.system_candidates_map["АСК"] = [
            {
                "system_id": 71,
                "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "ci_code": "[CI90000001]",
                "alias_text": "АСК",
                "score": 0.96,
            }
        ]
        self.repo.systems[71] = {
            "id": 71,
            "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            "ci_code": "[CI90000001]",
        }
        response = self.agent.handle_message("s1", "АСК")
        state = self.repo.get_slot_state("s1")
        self.assertIsNone(state.get("requested_entitlement_raw"))
        self.assertEqual(state.get("active_goal"), "ROLE_DISCOVERY")
        self.assertEqual(state.get("system_raw"), "АСК Риск-менеджмент (ПРОМ) [CI90000001]")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "position")

    def test_role_discovery_aggregates_roles_for_multiple_profiles(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            resolved_system_id=71,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел Фрод экспертизы",
        )
        self.repo.systems[71] = {
            "id": 71,
            "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            "ci_code": "[CI90000001]",
        }
        self.repo.profile_candidates_result = [
            {
                "profile_id": 501,
                "profile_code": "P00050108",
                "profile_name": "Профиль 501",
                "profile_type": "Основной",
                "match_score": 0.71,
                "department_score": 0.98,
                "position_score": 0.61,
            },
            {
                "profile_id": 502,
                "profile_code": "P00050209",
                "profile_name": "Профиль 502",
                "profile_type": "Дополнительный",
                "match_score": 0.69,
                "department_score": 0.96,
                "position_score": 0.58,
            },
        ]
        self.repo.access_map[(501, 71)] = [
            {
                "system_id": 71,
                "system_name": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "entitlement_id": 7101,
                "entitlement_type": "Полномочия",
                "entitlement_name": "Роль A",
                "access_level": 1,
                "justification_text": "Обоснование A",
            }
        ]
        self.repo.access_map[(502, 71)] = [
            {
                "system_id": 71,
                "system_name": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "entitlement_id": 7202,
                "entitlement_type": "Полномочия",
                "entitlement_name": "Роль B",
                "access_level": 2,
                "justification_text": "Обоснование B",
            }
        ]

        response = self.agent.handle_message("s1", "Какие роли мне доступны?")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.kind, "instruction_offer")
        self.assertEqual(response.pending_question.topic, "instruction")
        self.assertIn("найдено несколько контуров доступа", response.assistant_text)
        default_names = {item["entitlement_name"] for item in response.answer.default_accesses}
        request_names = {item["entitlement_name"] for item in response.answer.request_accesses}
        self.assertEqual(default_names, {"Роль A"})
        self.assertEqual(request_names, {"Роль B"})
        self.assertIsNone(response.context.get("requested_role"))

    def test_role_discovery_uses_strict_confirmed_context(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
            resolved_system_id=1,
            position_raw="Главный риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел экспертизы кредитных рисков корпоративных клиентов №7",
        )
        self.repo.systems[1] = {
            "id": 1,
            "system_name_raw": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
            "ci_code": "[CI02319693]",
        }
        strict_key = (
            "Москва",
            "Отдел экспертизы кредитных рисков корпоративных клиентов №7",
            "Главный риск-менеджер",
            1,
        )
        strict_rows = [
            {
                "system_id": 1,
                "system_name": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
                "profile_id": 3,
                "profile_name": "Главный риск-менеджер ЦЭКРКК Москва",
                "entitlement_id": 1001,
                "entitlement_type": "Роли",
                "entitlement_name": "Корректная роль",
                "access_level": 1,
            }
        ]
        strict_profiles = [
            {
                "profile_id": 3,
                "profile_code": "P00017432",
                "profile_name": "Главный риск-менеджер ЦЭКРКК Москва",
                "profile_type": "Дополнительный",
            }
        ]
        self.repo.context_access_map[strict_key] = (strict_rows, strict_profiles)
        self.repo.access_map[(999, 1)] = [
            {
                "system_id": 1,
                "system_name": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
                "entitlement_id": 9999,
                "entitlement_type": "Роли",
                "entitlement_name": "Лишняя роль из fuzzy-профиля",
                "access_level": 1,
            }
        ]

        response = self.agent.handle_message("s1", "Какие роли мне доступны?")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        names = {item["entitlement_name"] for item in response.answer.default_accesses}
        self.assertIn("Корректная роль", names)
        self.assertNotIn("Лишняя роль из fuzzy-профиля", names)

    def test_mixed_slot_input_populates_known_slots_and_asks_for_department(self) -> None:
        self.repo.system_candidates_map["Аск"] = [
            {
                "system_id": 71,
                "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
                "ci_code": "[CI90000001]",
                "alias_text": "АСК",
                "score": 0.92,
            }
        ]
        self.repo.system_candidates_map["АСК Риск-менеджмент (ПРОМ) [CI90000001]"] = list(self.repo.system_candidates_map["Аск"])
        self.repo.systems[71] = {
            "id": 71,
            "system_name_raw": "АСК Риск-менеджмент (ПРОМ) [CI90000001]",
            "ci_code": "[CI90000001]",
        }
        self.repo.city_candidates_map["Аск"] = []
        self.repo.city_candidates_map["риск менеджер"] = []
        self.repo.city_candidates_map["Москва"] = [{"value": "Москва", "score": 1.0}]
        self.repo.position_candidates_map[("Аск", None, None)] = []
        self.repo.position_candidates_map[("риск менеджер", None, None)] = [
            {"value": "риск-менеджер", "score": 0.94}
        ]
        self.repo.position_candidates_map[("Москва", None, None)] = []
        self.repo.department_candidates_map[("Аск", None)] = []
        self.repo.department_candidates_map[("риск менеджер", None)] = []
        self.repo.department_candidates_map[("Москва", None)] = []
        self.repo.department_candidates_map[("Москва", "Москва")] = []
        self.repo.department_candidates_map[("риск менеджер", "Москва")] = []
        response = self.agent.handle_message("s1", "Аск, риск менеджер, Москва")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "department")
        self.assertIn("Удалось определить", response.assistant_text)
        self.assertIn("АС", response.assistant_text)
        self.assertIn("город", response.assistant_text)
        state = self.repo.get_slot_state("s1")
        self.assertEqual(state["active_goal"], "ROLE_DISCOVERY")
        self.assertEqual(state["city_raw"], "Москва")
        self.assertEqual(state["position_raw"], "риск-менеджер")
        self.assertEqual(state["resolved_system_id"], 71)

    def test_mixed_slot_input_reports_unresolved_segments(self) -> None:
        self.repo.system_candidates_map["SberHelp"] = [
            {
                "system_id": 12,
                "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "ci_code": "[CI06055442]",
                "alias_text": "SberHelp",
                "score": 0.96,
            }
        ]
        self.repo.system_candidates_map["ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]"] = list(self.repo.system_candidates_map["SberHelp"])
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        self.repo.city_candidates_map["SberHelp"] = []
        self.repo.city_candidates_map["риск менеджер"] = []
        self.repo.city_candidates_map["Москва"] = [{"value": "Москва", "score": 1.0}]
        self.repo.city_candidates_map["???"] = []
        self.repo.position_candidates_map[("SberHelp", None, None)] = []
        self.repo.position_candidates_map[("риск менеджер", None, None)] = [
            {"value": "риск-менеджер", "score": 0.94}
        ]
        self.repo.position_candidates_map[("Москва", None, None)] = []
        self.repo.position_candidates_map[("???", None, None)] = []
        self.repo.department_candidates_map[("SberHelp", None)] = []
        self.repo.department_candidates_map[("риск менеджер", None)] = []
        self.repo.department_candidates_map[("Москва", None)] = []
        self.repo.department_candidates_map[("Москва", "Москва")] = []
        self.repo.department_candidates_map[("риск менеджер", "Москва")] = []
        self.repo.department_candidates_map[("???", "Москва")] = []
        response = self.agent.handle_message("s1", "SberHelp, риск менеджер, Москва, ???")
        self.assertIsNotNone(response.pending_question)
        self.assertEqual(response.pending_question.topic, "department")
        self.assertIn("Не удалось определить", response.assistant_text)

    def test_context_does_not_expose_unresolved_system_before_selection(self) -> None:
        self.repo.system_candidates_map["АСК"] = [
            {
                "system_id": 1,
                "system_name_raw": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
                "ci_code": "[CI02319693]",
                "alias_text": "АСК",
                "score": 0.91,
            }
        ]
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="АСК",
            resolved_system_id=None,
        )
        context = self.agent._build_context(self.repo.get_slot_state("s1"))
        self.assertIsNone(context["system"])

    def test_instruction_interrupt_works_while_candidate_selection_pending(self) -> None:
        candidate_set = self.repo.create_candidate_set(
            session_id="s1",
            topic="profile",
            source_query="Москва | Отдел | Риск-менеджер",
            options=[
                {
                    "option_key": "101",
                    "option_label": "Профиль 101",
                    "option_payload": {"profile_id": 101, "profile_name": "Профиль 101"},
                },
                {
                    "option_key": "102",
                    "option_label": "Профиль 102",
                    "option_payload": {"profile_id": 102, "profile_name": "Профиль 102"},
                },
            ],
            page_size=5,
        )
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            resolved_system_id=12,
            pending_question={
                "kind": "candidate_selection",
                "topic": "profile",
                "prompt": "Выберите профиль",
                "candidate_set_id": candidate_set.candidate_set_id,
            },
            conversation_phase="RESOLVE_PROFILE",
        )
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }

        def forced_instruction(*_args, **_kwargs):
            return {
                "dialog_act": "ASK_HELP",
                "intent_type": "INSTRUCTION_LOOKUP",
                "entities": {},
                "slot_candidates": {},
                "confidence": 0.93,
                "goal_transition": "STAY",
                "needs_clarification": False,
                "references_pending_question": False,
                "user_correction": False,
                "reasoning_trace_short": "forced_instruction_interrupt",
            }

        self.agent.gigachat.complete_json = forced_instruction
        response = self.agent.handle_message("s1", "Нужна инструкция")
        self.assertEqual(response.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertEqual(response.instruction_mode, "INLINE_DOC")
        self.assertIn("Inline-инструкция", response.assistant_text)

    def test_yes_followup_after_role_answer_uses_inline_doc(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            system_query_raw="SberHelp",
            system_resolution_mode="DIRECT",
            resolved_system_id=12,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
            resolved_profile_id=501,
            profile_candidates=[
                {
                    "profile_id": 501,
                    "profile_code": "P00050108",
                    "profile_name": "Доступы у Контроля качества НСК (функционал РМ)",
                    "profile_type": "Дополнительный - Совмещение полномочий",
                }
            ],
        )
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        self.repo.access_map[(501, 12)] = [
            {
                "system_id": 12,
                "system_name": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "entitlement_id": 7001,
                "entitlement_type": "Полномочия",
                "entitlement_name": "ЕРМ.ЦКР SberHelp Читатель SberHelp",
                "access_level": 1,
            }
        ]
        first_response = self.agent.handle_message("s1", "какие роли доступны?")
        self.assertIn("Если хотите, подскажу", first_response.assistant_text)
        second_response = self.agent.handle_message("s1", "Да, подскажи")
        self.assertEqual(second_response.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertEqual(second_response.instruction_mode, "INLINE_DOC")
        self.assertIn("Inline-инструкция", second_response.assistant_text)

    def test_yes_after_role_answer_with_select_option_interpretation_goes_to_instruction(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            system_query_raw="SberHelp",
            system_resolution_mode="DIRECT",
            resolved_system_id=12,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
            resolved_profile_id=501,
            profile_candidates=[
                {
                    "profile_id": 501,
                    "profile_code": "P00050108",
                    "profile_name": "Доступы у Контроля качества НСК (функционал РМ)",
                    "profile_type": "Дополнительный - Совмещение полномочий",
                }
            ],
        )
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        self.repo.access_map[(501, 12)] = [
            {
                "system_id": 12,
                "system_name": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
                "entitlement_id": 7001,
                "entitlement_type": "Полномочия",
                "entitlement_name": "ЕРМ.ЦКР SberHelp Читатель SberHelp",
                "access_level": 1,
            }
        ]
        first_response = self.agent.handle_message("s1", "какие роли доступны?")
        self.assertEqual(first_response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertIsNotNone(first_response.pending_question)
        self.assertEqual(first_response.pending_question.kind, "instruction_offer")

        def scripted_complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
            if "детектор инструкционного запроса" in system_prompt:
                return {"is_instruction_request": False, "confidence": 0.42, "reason": "simulated_miss"}
            if "классификатор ответа пользователя на вопрос ассистента о необходимости инструкции" in system_prompt:
                return {"decision": "ACCEPT", "confidence": 0.92, "reason": "explicit_yes_to_offer"}
            if "интерпретатор реплик пользователя" in system_prompt:
                return {
                    "dialog_act": "SELECT_OPTION",
                    "intent_type": "ROLE_DISCOVERY",
                    "entities": {},
                    "slot_candidates": {},
                    "context_shift": "NONE",
                    "confidence": 1.0,
                    "goal_transition": "STAY",
                    "needs_clarification": False,
                    "references_pending_question": False,
                    "user_correction": False,
                    "reasoning_trace_short": "simulated_select_option_yes",
                }
            return {
                "dialog_act": "ASK_HELP",
                "intent_type": "UNKNOWN",
                "entities": {},
                "slot_candidates": {},
                "context_shift": "NONE",
                "confidence": 0.0,
                "goal_transition": "STAY",
                "needs_clarification": True,
                "references_pending_question": False,
                "user_correction": False,
                "reasoning_trace_short": "fallback",
            }

        self.agent.gigachat.complete_json = scripted_complete_json
        second_response = self.agent.handle_message("s1", "Да")
        self.assertEqual(second_response.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertEqual(second_response.instruction_mode, "INLINE_DOC")
        self.assertIn("Inline-инструкция", second_response.assistant_text)

    def test_instruction_offer_decline_is_handled_before_reset_context(self) -> None:
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            resolved_system_id=12,
            position_raw="Риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел экспертизы кредитных рисков корпоративных клиентов №1",
            conversation_phase="ANSWER_ROLE_DISCOVERY",
            pending_question={
                "kind": "instruction_offer",
                "topic": "instruction",
                "prompt": "Нужна инструкция?",
                "options": [
                    {"id": "instruction_yes", "label": "Да, подскажи", "payload": {"action": "ACCEPT"}},
                    {"id": "instruction_no", "label": "Нет, спасибо", "payload": {"action": "DECLINE"}},
                ],
            },
        )

        def scripted_complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
            if "классификатор ответа пользователя на вопрос ассистента о необходимости инструкции" in system_prompt:
                return {"decision": "DECLINE", "confidence": 0.95, "reason": "explicit_decline"}
            if "интерпретатор реплик пользователя" in system_prompt:
                return {
                    "dialog_act": "RESET_CONTEXT",
                    "intent_type": "ROLE_DISCOVERY",
                    "entities": {},
                    "slot_candidates": {},
                    "context_shift": "RESET_CONTEXT",
                    "confidence": 1.0,
                    "goal_transition": "STAY",
                    "needs_clarification": False,
                    "references_pending_question": True,
                    "user_correction": False,
                    "reasoning_trace_short": "simulated_wrong_reset_for_decline",
                }
            return {"is_instruction_request": False, "confidence": 0.9, "reason": "not_instruction"}

        self.agent.gigachat.complete_json = scripted_complete_json
        response = self.agent.handle_message("s1", "Нет, спасибо")
        state = self.repo.get_slot_state("s1")

        self.assertIn("продолжаем без инструкции", response.assistant_text)
        self.assertEqual(state.get("active_goal"), "ROLE_DISCOVERY")
        self.assertEqual(state.get("position_raw"), "Риск-менеджер")
        self.assertEqual(state.get("city_raw"), "Москва")
        self.assertEqual(state.get("department_raw"), "Отдел экспертизы кредитных рисков корпоративных клиентов №1")
        self.assertEqual(state.get("resolved_system_id"), 12)
        self.assertIsNone(state.get("pending_question"))

    def test_instruction_followup_with_comma_does_not_trigger_mixed_slot(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
            resolved_system_id=1,
            position_raw="Главный риск-менеджер",
            city_raw="Москва",
            department_raw="Отдел экспертизы кредитных рисков корпоративных клиентов №7",
            conversation_phase="ANSWER_ROLE_DISCOVERY",
        )
        self.repo.systems[1] = {
            "id": 1,
            "system_name_raw": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]",
            "ci_code": "[CI02319693]",
        }
        self.repo.system_candidates_map["Да"] = [
            {
                "system_id": 35,
                "system_name_raw": "АС Zeus: Оперативное хранилище данных для управления лимитами на финансовых рынках CIB (И2) [CI01530064]",
                "ci_code": "[CI01530064]",
                "alias_text": "Zeus",
                "score": 0.82,
            }
        ]
        self.repo.add_message(
            "s1",
            "ASSISTANT",
            "Если хотите, подскажу, как проверить или запросить доступ к этой или другим АС.",
            structured_payload={
                "answer": {
                    "answer_type": "ROLE_DISCOVERY",
                    "summary_text": "Роли выданы",
                }
            },
        )

        def scripted_complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
            if "детектор инструкционного запроса" in system_prompt:
                return {"is_instruction_request": True, "confidence": 0.93, "reason": "followup_to_role_answer"}
            return {
                "dialog_act": "SWITCH_INTENT",
                "intent_type": "ROLE_ACQUISITION",
                "entities": {},
                "slot_candidates": {},
                "confidence": 0.95,
                "goal_transition": "SWITCH",
                "needs_clarification": False,
                "references_pending_question": False,
                "user_correction": False,
                "reasoning_trace_short": "role_acq_like_message",
            }

        self.agent.gigachat.complete_json = scripted_complete_json
        response = self.agent.handle_message("s1", "Да, как запросить доступ?")
        self.assertEqual(response.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertEqual(response.context["system"]["system_name"], self.repo.systems[1]["system_name_raw"])
        state = self.repo.get_slot_state("s1")
        self.assertEqual(state["system_raw"], self.repo.systems[1]["system_name_raw"])
        self.assertEqual(state["department_raw"], "Отдел экспертизы кредитных рисков корпоративных клиентов №7")

    def test_change_system_focus_preserves_org_context(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            system_raw="ЕФС ЕРМ ЦКР (И2) [CI01982477]",
            system_query_raw="ЕФС ЦКР",
            system_resolution_mode="DIRECT",
            resolved_system_id=10,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
            conversation_phase="ANSWER_ROLE_DISCOVERY",
        )
        self.repo.systems[10] = {
            "id": 10,
            "system_name_raw": "ЕФС ЕРМ ЦКР (И2) [CI01982477]",
            "ci_code": "[CI01982477]",
        }
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        self.repo.system_candidates_map["SberHelp"] = [
            {
                "system_id": 12,
                "system_name_raw": self.repo.systems[12]["system_name_raw"],
                "ci_code": "[CI06055442]",
                "alias_text": "SberHelp",
                "score": 0.92,
            }
        ]
        self.repo.context_access_map[("Москва", "Отд КК НСК_Риск-менеджер", "риск-менеджер", 12)] = (
            [
                {
                    "profile_id": 501,
                    "system_id": 12,
                    "system_name": self.repo.systems[12]["system_name_raw"],
                    "entitlement_id": 7001,
                    "entitlement_type": "Полномочия",
                    "entitlement_name": "ЕРМ.ЦКР SberHelp Читатель SberHelp",
                    "access_level": 1,
                }
            ],
            [
                {
                    "profile_id": 501,
                    "profile_code": "P00050108",
                    "profile_name": "Доступы у Контроля качества НСК",
                    "profile_type": "Совмещение",
                }
            ],
        )
        response = self.agent.handle_message("s1", "Посмотри SberHelp")
        self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
        self.assertEqual(response.context["city"], "Москва")
        self.assertEqual(response.context["department"], "Отд КК НСК_Риск-менеджер")
        self.assertEqual(response.context["position"], "риск-менеджер")
        self.assertEqual(response.context["system"]["system_name"], self.repo.systems[12]["system_name_raw"])
        self.assertEqual(response.context_shift, "CHANGE_SYSTEM_FOCUS")
        self.assertIn("Переключаюсь на другую АС", response.assistant_text)

    def test_instruction_interrupt_stores_and_restores_resume_goal(self) -> None:
        self.repo.update_slot_state(
            "s1",
            active_goal="ROLE_DISCOVERY",
            last_intent_type="ROLE_DISCOVERY",
            conversation_phase="ANSWER_ROLE_DISCOVERY",
            system_raw="ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            resolved_system_id=12,
            position_raw="риск-менеджер",
            city_raw="Москва",
            department_raw="Отд КК НСК_Риск-менеджер",
        )
        self.repo.systems[12] = {
            "id": 12,
            "system_name_raw": "ЕФС База знаний SberHelp (ПРОМ) (И3) [CI06055442]",
            "ci_code": "[CI06055442]",
        }
        response = self.agent.handle_message("s1", "Как проверить доступ?")
        self.assertEqual(response.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertEqual(response.instruction_mode, "INLINE_DOC")
        self.assertEqual(response.active_goal, "ROLE_DISCOVERY")
        self.assertEqual(response.resume_goal, None)
        self.assertEqual(response.context_shift, "SWITCH_GOAL")
        state = self.repo.get_slot_state("s1")
        self.assertEqual(state["active_goal"], "ROLE_DISCOVERY")
        self.assertIsNone(state.get("resume_goal"))

    def test_dialog_fixture_broad_role_discovery(self) -> None:
        self._seed_broad_system_dialog_data()
        fixture_path = self.fixtures_dir / "dialogue_role_discovery_broad_system.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        responses = []
        for turn in fixture["turns"]:
            response = self.agent.handle_message("s1", turn["user"])
            responses.append(response)
            expected = turn["expect"]
            if "conversation_phase" in expected:
                self.assertEqual(response.conversation_phase, expected["conversation_phase"])
            if "pending_topic" in expected:
                self.assertIsNotNone(response.pending_question)
                self.assertEqual(response.pending_question.topic, expected["pending_topic"])
            for token in expected.get("contains", []):
                self.assertIn(token, response.assistant_text)
        self.assertEqual(responses[-1].answer.answer_type, "ROLE_DISCOVERY")
        self.assertIn("ЕРМ.ЦКР SberHelp Читатель SberHelp", responses[-1].assistant_text)


if __name__ == "__main__":
    unittest.main()
