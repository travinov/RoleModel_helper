# Access List Query Guardrail

## Context
- Problem: In session `97072ceb` ("Чат 204"), the user wrote `доступы в АС ККА, МСК отдел №2, Риск-менеджер`. The bot treated this as an instruction request and explained how to request access instead of listing roles/accesses.
- Why now: This is a negatively rated production dialogue. A related fix already handles explicit `какие роли...` follow-ups, but the first-turn wording `доступы в АС ...` remains ambiguous.
- Related files/services: `app/agent/service.py`, `tests/test_agent_logic.py`.

## Goal
- Primary outcome: User requests for a list of accesses/roles in a concrete AS route to `ROLE_DISCOVERY`, not `INSTRUCTION_LOOKUP`, when they are not process questions.

## Non-Goals
- Do not change real process questions such as `как получить доступ`, `как запросить доступ`, or `как оформить доступ`.
- Do not change ETL, aliases, or role answer formatting.
- Do not add LLM fallback.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [x] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [x] Dialogue benchmark/replay expectations
- [ ] Operational checks

## Requirements
- Functional: `доступы в АС ККА, МСК отдел №2, Риск-менеджер` must route to `ROLE_DISCOVERY` even if LLM or instruction detector classified it as `INSTRUCTION_LOOKUP`.
- Functional: The guardrail applies only to list-like access wording: `доступы в/для АС`, `какие доступы`, `мои доступы`, `доступные доступы`.
- Functional: Process markers such as `как получить`, `как запросить`, `оформить`, `что нажать`, `инструкция` keep `INSTRUCTION_LOOKUP`.
- Technical constraints: Deterministic guardrail after instruction detection and before policy application.

## Inputs / Outputs
- Inputs: Raw user text, current state, and `TurnInterpretation`.
- Outputs: Possibly corrected `TurnInterpretation` with `intent_type = ROLE_DISCOVERY` or `SYSTEM_DISCOVERY`.
- Side effects: Existing slot/entity application and role discovery flow continue normally.

## Domain Invariants
- Preserved: Instruction flows remain available for process questions.
- Preserved: Slot state and pending question payloads remain structurally compatible.
- Intentionally changed: Ambiguous list-like `доступы` wording is treated as structured discovery.

## Acceptance Criteria
1. Given an LLM interpretation of `INSTRUCTION_LOOKUP` for `доступы в АС ККА, МСК отдел №2, Риск-менеджер`, the guardrail switches the turn to `ROLE_DISCOVERY`.
2. Given available access rows for the resolved context, the response lists roles/accesses instead of returning an instruction.
3. Given `как получить доступ к АС ККА`, the response remains `INSTRUCTION_LOOKUP`.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_access_list_query_with_full_context_overrides_instruction_lookup`
- Expected RED failure: response remains `INSTRUCTION_LOOKUP`.
- GREEN command: same targeted test.
- Regression checks: `/usr/bin/python3 -m unittest tests.test_agent_logic`

## Benchmark / Replay Plan (if dialogue behavior changes)
- Fixture(s): Unit replay for session `97072ceb` wording in `tests/test_agent_logic.py`.
- Expected pass/fail signal: access list request enters `ROLE_DISCOVERY`.
- Report artifact path: Not required for this narrow unit change.

## DB / Snapshot Verification
- No DB schema or snapshot changes.

## Rollout / Verification
- Local verification steps: Run targeted test, then `tests.test_agent_logic`.
- Runtime/monitoring checks: Re-run the production dialogue replay manually after deployment if needed.
- Rollback approach: Revert the guardrail helper and tests.

## Change Notes
- Key decisions: Extend the existing role-list guardrail rather than relying on prompt tuning.
- Open questions / risks: Very broad access questions without a concrete AS should continue toward `SYSTEM_DISCOVERY`.
