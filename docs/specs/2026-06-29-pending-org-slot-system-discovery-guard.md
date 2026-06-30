# Pending Org Slot System Discovery Guard

## Context
- Problem: In session `ade856d3-a6f3-4ac5-95ee-98bc2ebdfbc0`, the agent first resolved `база знаний` to `ЕФС База знаний SberHelp`, then replaced it with `MAFIN` after the user answered the pending department question with `фин институты`.
- Why now: Answers to explicit org-slot prompts must not be reclassified as system discovery when they already contain the expected org slot.
- Related files/services: `app/agent/service.py`, `app/agent/policy.py`, `tests/test_agent_logic.py`.

## Goal
- Primary outcome: When the bot is waiting for `position`, `city`, or `department` and the current interpretation contains that expected slot, `gigachat_system_discovery_gate` must not promote the turn to `SYSTEM_DISCOVERY`.

## Non-Goals
- Do not remove explicit system change flows.
- Do not change department/system dictionaries or aliases.
- Do not change role answer rendering.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [x] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [x] Dialogue benchmark/replay expectations
- [ ] Operational checks

## Requirements
- Functional: Given selected system `База знаний`, pending `department`, and input `фин институты` interpreted as `department_raw`, the selected system remains unchanged.
- Functional: Explicit system changes outside expected org-slot answers still work.
- Technical constraints: Keep the guard local to system-discovery promotion.

## Inputs / Outputs
- Inputs: Pending question, current interpretation, user text, slot state.
- Outputs: The original interpretation is preserved when it answers the pending org slot.
- Side effects: Avoids `SYSTEM_DISCOVERY` goal transition that clears the selected system.

## Domain Invariants
- Preserved: Org slot validation still canonicalizes department/city/position.
- Preserved: Explicit `CHANGE_SYSTEM` and system candidate selection remain separate flows.
- Intentionally changed: `SYSTEM_DISCOVERY` promotion is suppressed for expected org-slot answers.

## Acceptance Criteria
1. `фин институты` as an answer to pending `department` updates only department and keeps `resolved_system_id=50`.
2. The regression test fails before the guard and passes after it.
3. Existing agent tests pass.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_pending_department_system_discovery_gate_does_not_replace_resolved_system`
- Expected RED failure: `resolved_system_id` becomes MAFIN system id.
- GREEN command: same as RED command.
- Regression checks: `/usr/bin/python3 -m unittest tests.test_agent_logic`

## Benchmark / Replay Plan
- Fixture(s): Production session `ade856d3-a6f3-4ac5-95ee-98bc2ebdfbc0` evidence from diagnostics.
- Expected pass/fail signal: department answer does not clear or replace selected system.

## DB / Snapshot Verification
- No DB migration required.

## Rollout / Verification
- Local verification steps: Run targeted and full agent unittest checks.
- Runtime/monitoring checks: Re-run production-style session after deployment if needed.
- Rollback approach: Revert the guard in `_maybe_promote_system_discovery_query`.

## Change Notes
- Key decisions: Do not rely on LLM system-discovery gate while an expected org slot has already been extracted.
- Open questions / risks: Users who intend to switch systems while answering an org-slot prompt must be explicit.
