# Agent Systemic Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the RoleModel chat agent by replacing scattered intent/slot/candidate side effects with a single planned turn pipeline: `Interpretation -> TurnPlan -> SlotResolution -> StateReducer -> ScenarioService -> Answer`.

**Architecture:** Keep the current FastAPI contracts and existing DB schema usable, but move decision-making into typed, testable services. The first milestone blocks harmful false system resolution and adds replay tests; the second milestone extracts slot resolution and state reduction; the third milestone simplifies `ChatAgent` into an orchestrator.

**Tech Stack:** Python, FastAPI, PostgreSQL, existing `SearchRepository`, current GigaChat interpreter, `unittest`/benchmark runner.

---

## File Structure

**Create:**
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/turn_plan.py`  
  Typed objects for `TurnPlan`, `PlannedAction`, `SlotResolution`, `SlotResolutionStatus`, `SlotSourceKind`.
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/slot_resolution.py`  
  Single resolver for `system`, `city`, `position`, `department`, including strict validation and candidate generation.
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/state_reducer.py`  
  Single side-effect layer for applying accepted slots, rejected slots, candidate selections, context shifts, and pending questions.
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/fixtures/dialogue_incident_replay.json`  
  Regression fixture for real incident patterns: invalid AS, invalid city/position, weak AS candidates, mid-flow AS correction.

**Modify:**
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`  
  Gradually remove direct slot commits and route through `TurnPlan`, `SlotResolutionService`, `StateReducer`.
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/policy.py`  
  Keep goal/context transition rules, but return a typed decision instead of mutating state directly where possible.
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/repositories/search_repository.py`  
  Split strict resolution from fuzzy suggestions for systems; expose explicit matched metadata.
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`  
  Add state-invariant tests and reduce dependency on fake keyword routing.
- `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/run_dialogue_benchmark.py`  
  Add `state_expect`, `state_forbid`, and `context_expect` checks.

---

## Milestone 1: Stop Harmful Resolution And Pin Regressions

### Task 1: Extend Benchmark To Validate State

**Files:**
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/run_dialogue_benchmark.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/run_dialogue_benchmark.py`

- [ ] **Step 1: Add helpers for nested response checks**

Add functions near `_contains_all()`:

```python
def _get_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _matches_expected(actual: Any, expected: Any) -> bool:
    if expected == "__NULL__":
        return actual is None
    if expected == "__NOT_NULL__":
        return actual is not None
    if isinstance(expected, list):
        return actual in expected
    return actual == expected
```

- [ ] **Step 2: Add `state_expect` and `state_forbid` checks**

Inside `_evaluate_expectation()`, after the citation check block, add:

```python
        for path, expected_value in (expect.get("state_expect") or {}).items():
            checks_total += 1
            actual_value = _get_path(response, path)
            if _matches_expected(actual_value, expected_value):
                checks_passed += 1
            else:
                failures.append(f"state_expect {path} expected {expected_value!r}, got {actual_value!r}")
                if "wrong_context_state" in self.critical_rules:
                    critical_hits.append("wrong_context_state")

        for path, forbidden_value in (expect.get("state_forbid") or {}).items():
            checks_total += 1
            actual_value = _get_path(response, path)
            if not _matches_expected(actual_value, forbidden_value):
                checks_passed += 1
            else:
                failures.append(f"state_forbid {path} unexpectedly matched {forbidden_value!r}")
                if "wrong_context_state" in self.critical_rules:
                    critical_hits.append("wrong_context_state")
```

- [ ] **Step 3: Run a syntax check**

Run:

```bash
python -m py_compile tests/run_dialogue_benchmark.py
```

Expected: no output and exit code `0`.

---

### Task 2: Add Incident Replay Fixture

**Files:**
- Create: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/fixtures/dialogue_incident_replay.json`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/run_dialogue_benchmark.py`

- [ ] **Step 1: Create fixture with state invariants**

Create JSON:

```json
{
  "suite_name": "rolemodel_incident_replay_v1",
  "scoring": {
    "target_success_rate_percent": 95,
    "consecutive_sessions_required": 4,
    "critical_failures": [
      "missing_required_slot_sequence",
      "wrong_context_state",
      "uncited_instruction_answer"
    ]
  },
  "sessions": [
    {
      "name": "invalid_system_must_not_autoresolve",
      "turns": [
        {
          "user": "Мне нужен доступ в ЭФИР",
          "expect": {
            "pending_topic": "system",
            "state_expect": {
              "context.system": "__NULL__"
            },
            "state_forbid": {
              "context.system.system_name": "ЭФИР"
            },
            "contains_all": ["АС", "не найдена"]
          }
        }
      ]
    },
    {
      "name": "nonsense_org_context_must_not_suggest_weak_as",
      "turns": [
        {
          "user": "я ракетчик в Афганистане, какие у меня доступы?",
          "expect": {
            "pending_topic": "position",
            "state_expect": {
              "context.system": "__NULL__"
            },
            "state_forbid": {
              "context.system.system_name": "АС ЕФС. Наш бизнес"
            }
          }
        }
      ]
    },
    {
      "name": "invalid_system_then_org_slots_still_wait_system",
      "turns": [
        {
          "user": "Нужны роли в Херон",
          "expect": {
            "pending_topic": "system",
            "state_expect": {
              "context.system": "__NULL__"
            }
          }
        },
        {
          "user": "риск-менеджер",
          "expect": {
            "pending_topic": "system",
            "state_expect": {
              "context.system": "__NULL__"
            }
          }
        }
      ]
    },
    {
      "name": "unknown_system_typo_must_not_be_accepted",
      "turns": [
        {
          "user": "Подбери роли в Эфирк",
          "expect": {
            "pending_topic": "system",
            "state_expect": {
              "context.system": "__NULL__"
            },
            "contains_any": ["не найдена", "уточните"]
          }
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Run fixture against local or server app**

Run against local app when available:

```bash
python tests/run_dialogue_benchmark.py \
  --base-url http://127.0.0.1:8011 \
  --fixture tests/fixtures/dialogue_incident_replay.json \
  --report tests/fixtures/dialogue_incident_replay.report.json
```

Expected before fixes: at least one failure documents current behavior. Expected after Milestone 1: `actual_success_rate_percent >= 95` and no `wrong_context_state` critical hits.

---

### Task 3: Stop Full-Utterance System Autoresolution

**Files:**
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Add unit test for weak full-text AS fallback**

Add test near the existing invalid system tests:

```python
def test_role_discovery_without_explicit_system_does_not_resolve_from_full_utterance(self) -> None:
    self.repo.system_candidates["я ракетчик в афганистане какие у меня доступы"] = [
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
    self.assertNotEqual(response.pending_question.topic if response.pending_question else None, "system")
```

- [ ] **Step 2: Change `_ensure_system()` to use fallback only for explicit system slot requests**

In `_ensure_system()`, replace:

```python
        elif source_text:
            candidates = self._suggest_system_candidates_from_text(source_text, limit=20)
```

with:

```python
        elif source_text and self._can_suggest_system_from_source_text(state):
            candidates = self._suggest_system_candidates_from_text(source_text, limit=20)
```

Add helper:

```python
    def _can_suggest_system_from_source_text(self, state: dict[str, Any]) -> bool:
        pending_question = state.get("pending_question") or {}
        if pending_question.get("topic") == "system":
            return True
        return False
```

- [ ] **Step 3: Run targeted test**

Run:

```bash
python -m unittest tests.test_agent_logic -k full_utterance
```

Expected: test passes after implementation.

---

### Task 4: Make Candidate Selection First

**Files:**
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Add regression test**

Add a test where pending candidate selection receives a number and must not be written as `department_raw` or `system_raw` before selection:

```python
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
                    "system_name": "АС ПКАП Анализ состояния клиента корпоративного бизнеса (АСК, МОКК) (И2) [CI02319693]"
                }
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
    self.assertEqual(response.answer.answer_type, "ROLE_DISCOVERY")
```

- [ ] **Step 2: Move selection block before entity application**

In `_apply_policy()`, process `SHOW_MORE` and `SELECT_OPTION` after reset/context-shift/goal-transition but before `_apply_entities()`.

New order:

```python
state = self.search_repository.get_slot_state(session_id)

if interpretation.dialog_act == "SHOW_MORE":
    response = self._show_more_candidates(session_id, state)
    if response is not None:
        return response

if interpretation.dialog_act == "SELECT_OPTION":
    response = self._apply_candidate_selection(session_id, state, interpretation, text)
    if response is not None:
        return response
    state = self.search_repository.get_slot_state(session_id)

entity_result = self._apply_entities(session_id, state, interpretation, text)
```

Keep instruction-offer handling before this block only if `pending_question.kind == "instruction_offer"`.

- [ ] **Step 3: Run targeted tests**

Run:

```bash
python -m unittest tests.test_agent_logic -k 'candidate_selection or show_more or instruction_offer'
```

Expected: all selected tests pass.

---

## Milestone 2: Extract Typed Slot Resolution

### Task 5: Add Turn Plan And Slot Resolution Types

**Files:**
- Create: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/turn_plan.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Create typed models**

Create file:

```python
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
```

- [ ] **Step 2: Add import smoke test**

Add test:

```python
def test_turn_plan_types_importable(self) -> None:
    from app.agent.turn_plan import PlannedAction, SlotResolutionStatus, TurnPlan

    plan = TurnPlan(action=PlannedAction.CONTINUE, intent_type="ROLE_DISCOVERY", dialog_act="PROVIDE_SLOT")
    self.assertEqual(plan.action.value, "CONTINUE")
    self.assertEqual(SlotResolutionStatus.ACCEPTED.value, "ACCEPTED")
```

- [ ] **Step 3: Run test**

Run:

```bash
python -m unittest tests.test_agent_logic -k turn_plan_types
```

Expected: pass.

---

### Task 6: Extract `SlotResolutionService`

**Files:**
- Create: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/slot_resolution.py`
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Create service with explicit system strictness**

Create file:

```python
from __future__ import annotations

from typing import Any, Optional

from app.agent.turn_plan import SlotResolution, SlotResolutionStatus, SlotSourceKind
from app.services.text import normalize_text


STRICT_SYSTEM_ACCEPT_SCORE = 0.74
STRICT_SYSTEM_SUGGEST_SCORE = 0.45


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
        exact = value_norm in {
            normalize_text(best.get("system_name_raw")),
            normalize_text(best.get("alias_text")),
        }
        matched_by = set(best.get("matched_by") or [])
        strong_match = exact or "exact" in matched_by or "bracket_abbreviation" in matched_by or best_score >= STRICT_SYSTEM_ACCEPT_SCORE

        if source_kind == SlotSourceKind.FULL_UTTERANCE_FALLBACK:
            return SlotResolution(
                slot_name="system",
                raw_value=value,
                status=SlotResolutionStatus.MISSING,
                candidates=[],
                source_kind=source_kind,
                reason="full_utterance_never_commits_system",
            )

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
            return SlotResolution(slot_name, value, SlotResolutionStatus.ACCEPTED, canonical_value=canonical_value, confidence=1.0, candidates=candidates, source_kind=source_kind)

        if canonical_value and best_score >= 0.88 and (len(candidates) == 1 or best_score - float(candidates[1].get("score") or 0.0) >= 0.12):
            return SlotResolution(slot_name, value, SlotResolutionStatus.ACCEPTED, canonical_value=canonical_value, confidence=best_score, candidates=candidates, source_kind=source_kind)

        return SlotResolution(slot_name, value, SlotResolutionStatus.CANDIDATES, confidence=best_score, candidates=candidates, source_kind=source_kind, reason="ambiguous_org_slot")
```

- [ ] **Step 2: Wire service in `ChatAgent.__init__`**

Add import:

```python
from app.agent.slot_resolution import SlotResolutionService
```

In constructor add:

```python
self.slot_resolution_service = SlotResolutionService(search_repository)
```

- [ ] **Step 3: Add direct unit tests for resolver**

Use existing fake repository. Add tests for:

```python
def test_slot_resolution_rejects_full_utterance_system_fallback(self) -> None:
    from app.agent.turn_plan import SlotResolutionStatus, SlotSourceKind

    self.repo.system_candidates["я ракетчик в афганистане"] = [
        {"system_id": 1, "system_name_raw": "АС ЕФС. Наш бизнес", "score": 0.5, "matched_by": ["trigram"]}
    ]
    result = self.agent.slot_resolution_service.resolve_system(
        "я ракетчик в афганистане",
        self.repo.get_slot_state("s1"),
        SlotSourceKind.FULL_UTTERANCE_FALLBACK,
    )
    self.assertEqual(result.status, SlotResolutionStatus.MISSING)
    self.assertIsNone(result.canonical_id)
```

- [ ] **Step 4: Run tests**

Run:

```bash
python -m unittest tests.test_agent_logic -k slot_resolution
```

Expected: pass.

---

## Milestone 3: Centralize State Mutations

### Task 7: Add `StateReducer`

**Files:**
- Create: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/state_reducer.py`
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Create reducer**

Create file:

```python
from __future__ import annotations

from typing import Any

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
            {"resolution": resolution.__dict__},
        )
```

- [ ] **Step 2: Wire reducer in `ChatAgent.__init__`**

Add import:

```python
from app.agent.state_reducer import StateReducer
```

Add constructor field:

```python
self.state_reducer = StateReducer(search_repository)
```

- [ ] **Step 3: Add reducer unit test**

```python
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
```

- [ ] **Step 4: Run tests**

Run:

```bash
python -m unittest tests.test_agent_logic -k state_reducer
```

Expected: pass.

---

### Task 8: Route System Entity Commit Through Resolver + Reducer

**Files:**
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Replace direct system validation in `_apply_entities()`**

Replace the block that uses `_entity_value_is_valid_for_system()` and direct `update_slot_state()` for `system_raw` with:

```python
        system_raw = self._clean_slot_text(entities.get("system_raw"))
        if system_raw:
            from app.agent.turn_plan import SlotResolutionStatus, SlotSourceKind

            resolution = self.slot_resolution_service.resolve_system(
                system_raw,
                state,
                SlotSourceKind.LLM_ENTITY,
            )
            if resolution.status == SlotResolutionStatus.ACCEPTED:
                self.state_reducer.apply_slot_resolution(session_id, resolution)
                state = self.search_repository.get_slot_state(session_id)
                if (state.get("active_goal") or state.get("last_intent_type")) == "SYSTEM_DISCOVERY":
                    result["suggested_intent"] = "ROLE_DISCOVERY"
            elif resolution.status == SlotResolutionStatus.CANDIDATES:
                result["system_candidates"] = resolution.candidates
                result["invalid_system_raw"] = None
            else:
                self.state_reducer.apply_rejected_slot(session_id, resolution)
                validation_messages.append(
                    f"Не нашел '{system_raw}' в справочнике АС, поэтому не записал это значение как АС."
                )
                result["invalid_system_raw"] = system_raw
```

- [ ] **Step 2: Keep compatibility for candidate prompting**

If `result["system_candidates"]` is present, call `_prompt_candidate_question()` before falling through to structured goal. Use existing format:

```python
options = [
    {
        "option_key": str(candidate["system_id"]),
        "option_label": candidate["system_name_raw"],
        "option_payload": {
            "system_id": candidate["system_id"],
            "system_name": candidate["system_name_raw"],
        },
    }
    for candidate in entity_result.get("system_candidates", [])
]
```

- [ ] **Step 3: Run regression tests**

Run:

```bash
python -m unittest tests.test_agent_logic -k 'invalid_system or system_entity or role_discovery'
```

Expected: invalid systems are rejected, valid systems still resolve.

---

## Milestone 4: Typed Turn Planning

### Task 9: Introduce `TurnPlanner` Without Removing Old Flow

**Files:**
- Create: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/turn_planner.py`
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Create planner skeleton**

Create file:

```python
from __future__ import annotations

from typing import Any

from app.agent.turn_plan import PlannedAction, TurnPlan
from app.models.domain import TurnInterpretation


class TurnPlanner:
    def plan(self, state: dict[str, Any], interpretation: TurnInterpretation) -> TurnPlan:
        active_goal = state.get("active_goal") or state.get("last_intent_type") or interpretation.intent_type or "UNKNOWN"
        if interpretation.dialog_act == "RESET_CONTEXT":
            return TurnPlan(
                action=PlannedAction.RESET_CONTEXT,
                intent_type="UNKNOWN",
                dialog_act="RESET_CONTEXT",
            )
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
        return TurnPlan(
            action=PlannedAction.CONTINUE,
            intent_type=active_goal,
            dialog_act=interpretation.dialog_act,
        )
```

- [ ] **Step 2: Add planner import and constructor field**

```python
from app.agent.turn_planner import TurnPlanner
```

```python
self.turn_planner = TurnPlanner()
```

- [ ] **Step 3: Add non-invasive planner test**

```python
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
```

- [ ] **Step 4: Run test**

Run:

```bash
python -m unittest tests.test_agent_logic -k turn_planner
```

Expected: pass.

---

### Task 10: Convert `_continue_structured_goal()` To Use TurnPlan Actions

**Files:**
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Add a private dispatcher**

Add method:

```python
    def _execute_turn_plan(
        self,
        session_id: str,
        state: dict[str, Any],
        text: str,
        interpretation: TurnInterpretation,
        plan: TurnPlan,
    ) -> Optional[ChatMessageResponse]:
        if plan.action.value == "ANSWER_INSTRUCTION":
            return self._answer_instruction_lookup(session_id, state, text, interpretation)
        if plan.action.value == "ANSWER_SYSTEM_DISCOVERY":
            return self._continue_structured_goal(session_id, state, text, "SYSTEM_DISCOVERY", plan.dialog_act)
        if plan.action.value == "ANSWER_ROLE_DISCOVERY":
            return self._continue_structured_goal(session_id, state, text, "ROLE_DISCOVERY", plan.dialog_act)
        return None
```

- [ ] **Step 2: Call planner after entity handling**

Near the end of `_apply_policy()`, before `return self._continue_structured_goal(...)`, add:

```python
        plan = self.turn_planner.plan(state, interpretation)
        planned_response = self._execute_turn_plan(session_id, state, source_text or text, interpretation, plan)
        if planned_response is not None:
            return planned_response
```

- [ ] **Step 3: Run role/system tests**

Run:

```bash
python -m unittest tests.test_agent_logic -k 'system_discovery or role_discovery or instruction_interrupt'
```

Expected: pass.

---

## Milestone 5: Clean Up Old Risky Paths

### Task 11: Deprecate Direct Full-Text System Suggestion

**Files:**
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/app/agent/service.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Change `_suggest_system_candidates_from_text()` to only work with explicit source kind**

Rename to:

```python
    def _suggest_system_candidates_from_explicit_hint(self, source_text: str, limit: int = 20) -> list[dict[str, Any]]:
```

Keep body, but callers must pass only text from `pending_question.topic == "system"` or explicit `system_raw`.

- [ ] **Step 2: Remove calls using raw full utterance**

Search:

```bash
rg -n "_suggest_system_candidates_from_text|source_text" app/agent/service.py
```

Expected remaining usages should only be in `_ensure_system()` behind `_can_suggest_system_from_source_text()`.

- [ ] **Step 3: Run incident replay**

Run:

```bash
python tests/run_dialogue_benchmark.py \
  --base-url http://127.0.0.1:8011 \
  --fixture tests/fixtures/dialogue_incident_replay.json \
  --report tests/fixtures/dialogue_incident_replay.report.json
```

Expected: no weak AS context pollution.

---

### Task 12: Reduce `FakeGiga` Keyword Mirror

**Files:**
- Modify: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`
- Test: `/Volumes/SSD APFS/Python Project/RoleModel_helper/tests/test_agent_logic.py`

- [ ] **Step 1: Add explicit scripted interpretation helper**

Add helper in test class:

```python
def script_interpretation(self, payload: dict[str, Any]) -> None:
    def complete_json(system_prompt: str, user_prompt: str, model=None, max_tokens=None):
        if "интерпретатор реплик пользователя" in system_prompt:
            return payload
        return self.agent.gigachat.complete_json(system_prompt, user_prompt, model=model, max_tokens=max_tokens)
    self.agent.gigachat.complete_json = complete_json
```

- [ ] **Step 2: Convert new regression tests to scripted interpretations**

For incident tests, set explicit `intent_type`, `dialog_act`, and `entities` so the test validates policy/reducer behavior, not fake keyword matching.

Example:

```python
self.script_interpretation({
    "dialog_act": "PROVIDE_SLOT",
    "intent_type": "ROLE_DISCOVERY",
    "entities": {"system_raw": "ЭФИР"},
    "slot_candidates": {},
    "context_shift": "NONE",
    "confidence": 0.9,
    "goal_transition": "START",
    "needs_clarification": False,
    "references_pending_question": False,
    "user_correction": False,
    "reasoning_trace_short": "explicit invalid system",
})
```

- [ ] **Step 3: Run full agent logic tests**

Run:

```bash
python -m unittest tests.test_agent_logic -v
```

Expected: pass.

---

## Milestone 6: Verification And Deployment

### Task 13: Full Local Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run unit tests**

```bash
python -m unittest tests.test_agent_logic tests.test_parser_unit tests.test_parser_integration tests.test_rag_pptx -v
```

Expected: all pass.

- [ ] **Step 2: Run benchmark fixtures**

```bash
python tests/run_dialogue_benchmark.py \
  --fixture tests/fixtures/dialogue_benchmark_30_sessions.json \
  --report tests/fixtures/dialogue_benchmark_30_sessions.systemic_refactor.report.json

python tests/run_dialogue_benchmark.py \
  --fixture tests/fixtures/dialogue_incident_replay.json \
  --report tests/fixtures/dialogue_incident_replay.systemic_refactor.report.json
```

Expected: target `>= 95%`, no critical `wrong_context_state`.

- [ ] **Step 3: Manual smoke via API**

```bash
curl -s http://127.0.0.1:8011/api/v1/health
```

Expected response contains healthy status.

---

### Task 14: Server Deployment Verification

**Files:**
- No code changes.

- [ ] **Step 1: Deploy to current main server**

Use the existing server:

```bash
ssh -i /Users/travinov-sv/.ssh/cloudru_v2 user1@87.242.86.193
```

From the local project root, run:

```bash
rsync -az --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.git' \
  --exclude 'Doc/backups' \
  -e "ssh -i /Users/travinov-sv/.ssh/cloudru_v2" \
  "/Volumes/SSD APFS/Python Project/RoleModel_helper/" \
  user1@87.242.86.193:/opt/rolemodel_helper/
```

Expected: rsync exits with code `0`.

- [ ] **Step 2: Restart service**

```bash
sudo systemctl restart rolemodel-helper
sudo systemctl status rolemodel-helper --no-pager
```

Expected: `active (running)`.

- [ ] **Step 3: Smoke public URL**

```bash
curl -k -s https://87.242.86.193/rolemodel/api/v1/health
```

Expected: healthy JSON response.

- [ ] **Step 4: Run incident replay against server**

```bash
python tests/run_dialogue_benchmark.py \
  --base-url https://87.242.86.193/rolemodel \
  --fixture tests/fixtures/dialogue_incident_replay.json \
  --report tests/fixtures/dialogue_incident_replay.server.report.json
```

Expected: target `>= 95%`, no critical hits.

---

## Acceptance Criteria

- Invalid AS names do not populate `context.system` or `resolved_system_id`.
- Weak trigram candidates are never auto-accepted as systems.
- Selecting an AS from a candidate list immediately transitions to `ROLE_DISCOVERY`.
- `SYSTEM_DISCOVERY`, `ROLE_DISCOVERY`, and `INSTRUCTION_LOOKUP` do not overwrite each other through hidden side effects.
- Candidate selection is handled before slot binding.
- Benchmark checks state, not just response text.
- Incident replay fixture passes locally and on server.
- Existing REST contracts remain backward-compatible.

## Non-Goals

- Do not replace GigaChat in this refactor.
- Do not change ETL schema.
- Do not redesign UI.
- Do not add DB migrations in this plan; all state changes must fit the existing additive chat-state fields.

## Execution Recommendation

Use subagent-driven implementation by milestone:

1. Worker A: Tasks 1-4, regression pinning and stop-gap safety.
2. Worker B: Tasks 5-8, typed slot resolver and reducer.
3. Worker C: Tasks 9-12, turn planner and cleanup.
4. Main agent: integration, review, deployment, server smoke.
