# Role List Query Overrides Instruction Mode

## Context
- Problem: In session `91b407e6`, after an initial instruction answer for `ЕФС Риск-Решения`, repeated user questions `какие роли...` were still classified as `INSTRUCTION_LOOKUP`, so the bot repeated "Мои доступы" instructions instead of collecting org slots for role discovery.
- Why now: This is a negatively rated production dialogue where the system was resolved correctly, but the goal was not switched from instruction lookup to role discovery.
- Related files/services: `app/agent/service.py`, `tests/test_agent_logic.py`.

## Goal
- Primary outcome: Explicit role-list questions such as `какие роли мне доступны` or `какие роли мне нужно запросить` route to `ROLE_DISCOVERY`, even when the LLM classified the turn as `INSTRUCTION_LOOKUP`.

## Non-Goals
- Do not change instruction behavior for process questions like `как запросить роль`, `как получить доступ`, or access-error troubleshooting.
- Do not add new AS aliases or change system resolution scoring.
- Do not change role discovery result formatting.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [x] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [x] Dialogue benchmark/replay expectations
- [ ] Operational checks

## Requirements
- Functional: If a user explicitly asks which roles are available/needed/requested, the turn must continue as `ROLE_DISCOVERY`.
- Functional: If a concrete AS is already resolved or named in the same message, that AS context must be preserved.
- Functional: If required org slots are missing, the bot asks the next org slot starting with position.
- Technical constraints: The guardrail must be deterministic and narrow; it must not depend on an extra LLM call.
- Operational constraints: Existing static instruction flows must continue for process-oriented wording.

## Inputs / Outputs
- Inputs: User text, current slot state, and LLM `TurnInterpretation`.
- Outputs: Possibly corrected `TurnInterpretation` before policy application.
- Side effects: Normal `ROLE_DISCOVERY` state transition and pending org-slot question.

## Domain Invariants
- Preserved: Intent/phase transitions remain explicit and replayable.
- Preserved: Slot state and pending question payloads remain structurally compatible.
- Preserved: Instruction answers keep existing citation payload behavior.
- Intentionally changed: Explicit role-list wording can override `INSTRUCTION_LOOKUP` classification.

## Acceptance Criteria
1. Given active `INSTRUCTION_LOOKUP` context and a resolved `ЕФС Риск-Решения` system, when the user asks `какие роли мне доступны для АС ЕФС Риск-решения?`, the response intent is `ROLE_DISCOVERY`.
2. The same turn asks for `position` when org slots are missing, instead of returning an instruction answer.
3. A process question such as `как запросить роль для ЕФС Риск-Решения?` remains `INSTRUCTION_LOOKUP`.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_role_list_question_overrides_instruction_lookup_after_instruction_answer`
- Expected RED failure: response remains `INSTRUCTION_LOOKUP` or returns an instruction answer.
- GREEN command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_role_list_question_overrides_instruction_lookup_after_instruction_answer`
- Regression checks: `/usr/bin/python3 -m unittest tests.test_agent_logic`

## Benchmark / Replay Plan (if dialogue behavior changes)
- Fixture(s): Unit replay for session `91b407e6` wording in `tests/test_agent_logic.py`.
- Expected pass/fail signal: explicit role-list question enters `ROLE_DISCOVERY` and asks for position.
- Report artifact path: Not required for this narrow unit change.

## DB / Snapshot Verification
- No DB schema or snapshot changes.

## Rollout / Verification
- Local verification steps: Run targeted unit test, then `tests.test_agent_logic`.
- Runtime/monitoring checks: Re-run production dialogue replay or manual session check after deployment if needed.
- Rollback approach: Revert the deterministic guardrail and its tests.

## Change Notes
- Key decisions: Treat `какие роли...` as role discovery even if words like `запросить` appear later in the same phrase.
- Open questions / risks: Very short ambiguous wording without `какие роли` remains under existing LLM routing.
