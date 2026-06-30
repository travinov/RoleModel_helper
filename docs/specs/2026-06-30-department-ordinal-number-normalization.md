# Department Ordinal Number Normalization

## Context
- Problem: In session `8d157343` ("Чат 187"), while collecting `department`, the user answered `второй`. The bot stored `department_raw = второй`, searched the department dictionary by the literal word, and offered an irrelevant candidate `Методолог КК`.
- Why now: This is a negatively rated production dialogue. The user expected `второй` to mean department number `2`.
- Related files/services: `app/agent/service.py`, `tests/test_agent_logic.py`.

## Goal
- Primary outcome: While collecting `department`, simple ordinal/numeric department references such as `второй`, `2-й`, and `№2` are normalized deterministically and searched as department number `2`.

## Non-Goals
- No LLM fallback for text-to-number conversion.
- No broad rewrite of department candidate SQL or profile context filtering.
- No automatic handling of complex ordinals beyond simple first-to-twentieth forms.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [x] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [x] Dialogue benchmark/replay expectations
- [ ] Operational checks

## Requirements
- Functional: If pending slot is `department` and the user says `второй`, the search should try canonical variants for number `2` before literal fuzzy search.
- Functional: Simple forms `первый...двадцатый`, `2-й`, `2`, `№2`, `отдел 2`, and `отдел №2` should map to an exact numeric department marker.
- Functional: If the rule does not match, existing department search behavior remains unchanged.
- Functional: If ordinary search returns only a weak or city-incompatible candidate, the bot should ask the user to clarify instead of offering that irrelevant candidate.
- Technical constraints: Deterministic code only; no extra LLM call.

## Inputs / Outputs
- Inputs: Raw department slot text, current slot state with city/position context.
- Outputs: Canonical department candidate selection or clarification prompt.
- Side effects: Normal slot-state updates through existing validation and candidate selection flows.

## Domain Invariants
- Preserved: Slot state and pending question payloads remain structurally compatible.
- Preserved: Intent/phase transitions remain explicit and replayable.
- Intentionally changed: Numeric/ordinal department input can be rewritten for dictionary lookup.

## Acceptance Criteria
1. Given pending `department`, city `Самара`, position `Риск-менеджер`, and input `второй`, the bot searches canonical numeric variants and resolves/selects department `№2` instead of `Методолог КК`.
2. Given an unrecognized department phrase where the best candidate is weak and `city_compatibility = 0`, the bot asks to clarify instead of showing that single weak candidate.
3. Existing candidate selection behavior for genuinely ambiguous department matches is preserved.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_department_ordinal_word_resolves_numbered_department_before_weak_literal_match`
- Expected RED failure: response offers `Методолог КК` candidate or state keeps `department_raw = второй`.
- GREEN command: same targeted test.
- Regression checks: `/usr/bin/python3 -m unittest tests.test_agent_logic`

## Benchmark / Replay Plan (if dialogue behavior changes)
- Fixture(s): Unit replay for session `8d157343` wording in `tests/test_agent_logic.py`.
- Expected pass/fail signal: `второй` maps to department `№2`.
- Report artifact path: Not required for this narrow unit change.

## DB / Snapshot Verification
- No DB schema or snapshot changes.

## Rollout / Verification
- Local verification steps: Run targeted unit test, then `tests.test_agent_logic`.
- Runtime/monitoring checks: Re-run the production session replay manually after deploy if needed.
- Rollback approach: Revert ordinal normalization helper and tests.

## Change Notes
- Key decisions: Use a deterministic Russian ordinal dictionary; do not call LLM.
- Open questions / risks: Broader profile-context filtering remains a separate issue related to Чат 161.
