# Weak System Browse Autoselect Guard

## Context
- Problem: In session `522490bd-7a4d-458c-a251-c6eb91b6d694`, user asked for `АС ОКК`, but the agent resolved the only weak browse candidate `АС CF` and generated a Cash Flow instruction.
- Why now: `ОКК` is not an official alias, so weak trigram candidates must not be committed as a direct system resolution.
- Related files/services: `app/agent/service.py`, `tests/test_agent_logic.py`.

## Goal
- Primary outcome: A single weak system browse candidate is not auto-selected unless it is an exact or strong system match.

## Non-Goals
- Do not add `ОКК` as an alias.
- Do not change ETL alias generation.
- Do not change static instruction synthesis.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [x] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [x] Dialogue benchmark/replay expectations
- [ ] Operational checks

## Requirements
- Functional: Given one browse candidate with `score=0.5455`, `exact_match=false`, and `strong_match=false`, the agent must not set `resolved_system_id`.
- Functional: Exact official aliases and strong matches may still be auto-selected.
- Technical constraints: Keep the change scoped to browse autoselection logic.
- Operational constraints: Existing candidate selection flow must remain available for ambiguous or weak system hints.

## Inputs / Outputs
- Inputs: System hint text and browse candidates from `browse_system_candidates`.
- Outputs: Either direct system resolution for exact/strong candidates or a pending system candidate/clarification flow.
- Side effects: Weak single candidates no longer update `system_raw` to another AS.

## Domain Invariants
- Preserved: Slot state fields remain structurally compatible.
- Preserved: `state_revision` remains monotonic through existing repository updates.
- Intentionally changed: `len(candidates) == 1` alone is no longer sufficient for `DIRECT` system resolution.

## Acceptance Criteria
1. A single weak browse candidate `АС ОКК -> АС CF` is not auto-selected and does not set `resolved_system_id=45`.
2. A single exact safe browse candidate remains eligible for auto-selection.
3. Targeted agent tests pass.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_single_weak_browse_system_candidate_is_not_auto_selected`
- Expected RED failure: state incorrectly contains `resolved_system_id=45`.
- GREEN command: same as RED command.
- Regression checks: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_single_weak_browse_system_candidate_is_not_auto_selected tests.test_agent_logic.AgentDialogRefactorTestCase.test_initial_safe_system_alias_starts_role_discovery_without_change_prefix`

## Benchmark / Replay Plan
- Fixture(s): Production session `522490bd-7a4d-458c-a251-c6eb91b6d694` evidence from diagnostics.
- Expected pass/fail signal: `АС ОКК` is not resolved to Cash Flow when only weak trigram candidates exist.
- Report artifact path: none for this scoped code fix.

## DB / Snapshot Verification
- No DB migration required.

## Rollout / Verification
- Local verification steps: Run targeted unittest checks.
- Runtime/monitoring checks: Re-run session-style diagnostic after deployment if needed.
- Rollback approach: Revert the scoped change in `_should_autoselect_browse_candidate`.

## Change Notes
- Key decisions: Do not raise broad search thresholds; instead require exact or strong evidence before direct autoselect.
- Open questions / risks: Some previously auto-selected single weak candidates will now ask for clarification.
