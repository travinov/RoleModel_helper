from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.models.domain import RetrievedChunk, SearchAnswer, ToolAttempt


ROLE_DISCOVERY_FOLLOWUP = "Если хотите, подскажу, как проверить или запросить доступ к этой или другим АС."
SYSTEM_DISCOVERY_FOLLOWUP = "Если нужна конкретная АС, напишите ее название или выберите ее, и я сразу покажу роли."
INSTRUCTION_SCORE_THRESHOLD = 0.08


@dataclass
class InstructionResult:
    answer: SearchAnswer
    instruction_mode: str


class SystemDiscoveryService:
    def __init__(self, search_repository) -> None:
        self.search_repository = search_repository

    def list_systems(self, session_id: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        systems = self.search_repository.list_systems_by_confirmed_context(
            city=state.get("city_raw") or "",
            department=state.get("department_raw") or "",
            position=state.get("position_raw") or "",
        )
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="list_systems_by_confirmed_context",
                attempt_no=1,
                input_payload={
                    "city": state.get("city_raw"),
                    "department": state.get("department_raw"),
                    "position": state.get("position_raw"),
                },
                result_status="success",
                result_summary=f"{len(systems)} systems",
            ),
            {"systems": systems[:50]},
        )
        return systems

    def build_answer(self, state: dict[str, Any], systems: list[dict[str, Any]]) -> SearchAnswer:
        position = state.get("position_raw") or "не указана"
        city = state.get("city_raw") or "не указан"
        department = state.get("department_raw") or "не указан"
        if not systems:
            return SearchAnswer(
                answer_type="SYSTEM_DISCOVERY",
                summary_text=(
                    f"По подтвержденному контексту: должность {position}, город {city}, отдел {department} "
                    "доступные АС не найдены. Проверьте корректность должности, города или отдела по штатной структуре."
                ),
            )
        systems_payload = [
            {
                "system_id": int(item["system_id"]),
                "system_name": item["system_name_raw"],
                "ci_code": item.get("ci_code"),
            }
            for item in systems
        ]
        summary = "\n".join(
            [
                (
                    f"Для вас, на основании должности {position}, города {city} и отдела {department}, "
                    "доступны следующие АС:"
                ),
                *[f"{index}. {item['system_name']}" for index, item in enumerate(systems_payload, start=1)],
                SYSTEM_DISCOVERY_FOLLOWUP,
            ]
        )
        return SearchAnswer(
            answer_type="SYSTEM_DISCOVERY",
            summary_text=summary,
            systems=systems_payload,
        )


class RoleDiscoveryService:
    def __init__(self, search_repository) -> None:
        self.search_repository = search_repository

    def list_access(self, session_id: str, state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows, matched_profiles = self.search_repository.list_access_by_confirmed_context(
            city=state.get("city_raw") or "",
            department=state.get("department_raw") or "",
            position=state.get("position_raw") or "",
            system_id=int(state["resolved_system_id"]),
        )
        self.search_repository.log_tool_call(
            session_id,
            ToolAttempt(
                tool_name="list_access_by_confirmed_context",
                attempt_no=1,
                input_payload={
                    "system_id": state.get("resolved_system_id"),
                    "city": state.get("city_raw"),
                    "department": state.get("department_raw"),
                    "position": state.get("position_raw"),
                },
                result_status="success",
                result_summary=f"{len(matched_profiles)} profiles, {len(rows)} access rows",
            ),
            {"profiles": matched_profiles[:20]},
        )
        return rows, matched_profiles

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

    def build_answer(
        self,
        state: dict[str, Any],
        system_name: str,
        rows: list[dict[str, Any]],
        matched_profiles: list[dict[str, Any]],
        list_limit: int,
    ) -> SearchAnswer:
        if not matched_profiles:
            return SearchAnswer(
                answer_type="ROLE_DISCOVERY",
                summary_text=(
                    "По подтвержденному контексту (АС, должность, город, отдел) доступы не найдены. "
                    "Проверьте корректность отдела и должности по штатной структуре."
                ),
            )

        profile_name_map = {
            int(item["profile_id"]): str(item.get("profile_name") or item["profile_id"])
            for item in matched_profiles
        }
        merged_accesses: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
        for row in rows:
            payload = self._access_payload(row)
            profile_id = int(row["profile_id"])
            current_profile_name = profile_name_map.get(profile_id) or str(profile_id)
            key = (
                payload.get("entitlement_id"),
                payload.get("entitlement_type"),
                payload.get("entitlement_name"),
                int(payload.get("access_level") or 0),
            )
            current = merged_accesses.get(key)
            if current is None:
                payload["profile_names"] = [current_profile_name]
                merged_accesses[key] = payload
                continue
            current_profiles = set(current.get("profile_names") or [])
            current_profiles.add(current_profile_name)
            current["profile_names"] = sorted(current_profiles)
            if not current.get("justification_text") and payload.get("justification_text"):
                current["justification_text"] = payload.get("justification_text")
            if not current.get("inline_comment") and payload.get("inline_comment"):
                current["inline_comment"] = payload.get("inline_comment")

        accesses = sorted(
            merged_accesses.values(),
            key=lambda item: (
                str(item.get("entitlement_type") or ""),
                str(item.get("entitlement_name") or ""),
                int(item.get("access_level") or 0),
            ),
        )
        grouped_default = [item for item in accesses if int(item["access_level"]) == 1]
        grouped_request = [item for item in accesses if int(item["access_level"]) == 2]
        position = state.get("position_raw") or "не указана"
        city = state.get("city_raw") or "не указан"
        department = state.get("department_raw") or "не указан"
        default_details = self._format_access_details(grouped_default, list_limit)
        request_details = self._format_access_details(grouped_request, list_limit)
        scope_summary = f"для АС {system_name}"
        if len(profile_name_map) > 1:
            scope_summary += " найдено несколько контуров доступа, список ролей объединен."
        else:
            scope_summary += "."
        if not grouped_default and not grouped_request:
            summary_parts = [
                (
                    f"Для вас, на основании должности {position}, города {city} и отдела {department}, "
                    f"{scope_summary}"
                ),
                "Доступы в этой АС по подтвержденному контексту не найдены.",
                ROLE_DISCOVERY_FOLLOWUP,
            ]
        else:
            summary_parts = [
                (
                    f"Для вас, на основании должности {position}, города {city} и отдела {department}, "
                    f"{scope_summary}"
                ),
                f"По умолчанию доступны следующие роли: {default_details}.",
                f"По требованию доступны следующие роли: {request_details}.",
                ROLE_DISCOVERY_FOLLOWUP,
            ]
        return SearchAnswer(
            answer_type="ROLE_DISCOVERY",
            summary_text="\n".join(summary_parts),
            profile=None,
            default_accesses=grouped_default,
            request_accesses=grouped_request,
        )


class InstructionService:
    def __init__(self, search_repository, instruction_answer_service) -> None:
        self.search_repository = search_repository
        self.instruction_answer_service = instruction_answer_service

    def answer(
        self,
        session_id: str,
        state: dict[str, Any],
        raw_text: str,
        references_pending_question: bool,
        resolved_system_name: Optional[str],
    ) -> InstructionResult:
        query_text = raw_text
        if references_pending_question and len(" ".join(raw_text.split()).split()) <= 3:
            query_text = "Как проверить или запросить доступ к роли"
        if resolved_system_name:
            query_text = f"{query_text} {resolved_system_name}"

        inline_answer = self.instruction_answer_service.answer_from_static_instruction(
            query_text,
            context={
                "intent_type": "INSTRUCTION_LOOKUP",
                "system_name": resolved_system_name,
            },
        )
        if inline_answer:
            summary_text = inline_answer["summary_text"]
            return InstructionResult(
                answer=SearchAnswer(
                    answer_type="INSTRUCTION_LOOKUP",
                    summary_text=summary_text,
                    instruction=summary_text,
                    citations=inline_answer["citations"],
                ),
                instruction_mode="INLINE_DOC",
            )

        summary_text = "Инструкция не загружена. Загрузите файл .rtf или .txt через интерфейс загрузки инструкции."
        return InstructionResult(
            answer=SearchAnswer(
                answer_type="INSTRUCTION_LOOKUP",
                summary_text=summary_text,
                instruction=summary_text,
                citations=[],
            ),
            instruction_mode="INLINE_DOC",
        )
