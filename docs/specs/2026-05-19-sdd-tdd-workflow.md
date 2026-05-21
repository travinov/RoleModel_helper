# SDD/TDD Workflow For RoleModel Helper

## Context
- Problem: changes to ETL, dialogue state, RAG, and workbook-derived access logic need a repeatable way to capture intent before code changes.
- Why now: the repository already has unittest coverage, dialogue benchmark fixtures, and an agent refactor plan, but specs need to be tied to the actual domain invariants.
- Related files/services: `AGENTS.md`, `docs/specs/README.md`, `rolemodel_etl/`, `app/agent/`, `app/rag/`, `app/models/api.py`, `app/models/domain.py`, `tests/`, `tests/fixtures/`.

## Goal
- Primary outcome: every non-trivial behavior change starts from a short project-specific spec and proceeds through a visible RED/GREEN TDD loop.

## Non-Goals
- Do not migrate the project from `unittest` to pytest.
- Do not require a spec for wording-only docs edits or mechanical formatting.
- Do not create a heavyweight architecture process for small fixes.

## Change Category Checklist
- [x] ETL/workbook parsing or validation behavior
- [x] PostgreSQL schema/data contract/query behavior
- [x] Chat API contract
- [x] Agent state machine/slots/phase transitions
- [x] RAG ingestion/retrieval/citation behavior
- [x] Dialogue benchmark/replay expectations
- [x] Operational checks

## Requirements
- Functional:
  - Specs must identify the impacted project area before implementation.
  - Specs must list acceptance criteria that can be verified by unittest, benchmark/replay, DB query, or runtime check.
  - Dialogue specs must include expected `conversation_phase`, `pending_question.topic`, `answer.answer_type`, or `state_expect/state_forbid` checks where applicable.
  - Workbook-driven specs must cover AS alias resolution, accessible AS scope, justification lookup, and stale/missing AS rejection when those behaviors are touched.
- Technical constraints:
  - Keep using `/usr/bin/python3 -m unittest` commands already documented in the project.
  - Preserve public API payload compatibility in `app/models/api.py`.
  - Preserve domain state compatibility in `app/models/domain.py`.
  - Keep changes small enough for targeted tests before full regression.
- Operational constraints:
  - ETL/schema specs must include active snapshot and ETL counter verification.
  - RAG specs must include citation behavior expectations.
  - Dialogue behavior specs must name fixture/report artifacts when replay coverage is required.

## Inputs / Outputs
- Inputs: user request, existing README/AGENTS guidance, current implementation files, current tests/fixtures, optional production or workbook evidence.
- Outputs: one spec under `docs/specs/YYYY-MM-DD-short-name.md`, a RED test/check command, a GREEN verification command, and scoped regression commands.
- Side effects: no production code changes before a RED check is observed for behavior changes.

## Domain Invariants
- Preserved:
  - A single active snapshot is selected for workbook-backed behavior.
  - ETL run status and row/error counters remain coherent.
  - Access-level semantics remain compatible with schema checks.
  - Chat state fields, pending questions, state revisions, active goals, phases, and context shifts remain API-compatible.
  - RAG instruction answers preserve required citation behavior.
- Intentionally changed:
  - None for the workflow itself; future feature specs must state intentional invariant changes explicitly.

## Acceptance Criteria
1. `AGENTS.md` tells agents to start non-trivial work from a spec and run TDD through targeted checks.
2. `docs/specs/README.md` contains project-specific sections for ETL, DB/schema, API, agent state, RAG, benchmark/replay, and operational checks.
3. A future dialogue behavior spec can directly name `conversation_phase`, `pending_question.topic`, `answer.answer_type`, `state_expect`, and `state_forbid` expectations.
4. A future workbook behavior spec can directly require the four validation themes: AS alias resolution, accessible AS list by current profile, justification lookup, stale/missing AS rejection.
5. Targeted test commands are documented for parser, agent, dialogue export, RAG, benchmark, and full regression scopes.

## Test Plan (TDD)
- RED command: not applicable; this is a docs/process adoption spec with no runtime behavior change.
- Expected RED failure: not applicable.
- GREEN command:
```bash
sed -n '1,260p' AGENTS.md
sed -n '1,360p' docs/specs/README.md
sed -n '1,260p' docs/specs/2026-05-19-sdd-tdd-workflow.md
```
- Regression checks: no application tests required unless production code or tests are changed.

## Benchmark / Replay Plan
- Fixture(s): none for this workflow spec.
- Expected pass/fail signal: future dialogue specs should use `tests/run_dialogue_benchmark.py` and fixture expectations when behavior changes.
- Report artifact path: future specs should name the report path when benchmark output is part of verification.

## DB / Snapshot Verification
- No DB verification required for this workflow spec.
- Future ETL/schema specs must include active snapshot uniqueness, `etl_run` status/counters, relevant loaded rows, and explainable `etl_error` outcomes.

## Rollout / Verification
- Local verification steps:
  - Read `AGENTS.md`.
  - Read `docs/specs/README.md`.
  - Confirm this spec has no placeholders and maps to the actual repository structure.
- Runtime/monitoring checks: none.
- Rollback approach: remove or revert the docs-only files.

## Change Notes
- Key decisions:
  - Keep SDD lightweight and close to implementation.
  - Keep `unittest` as the documented test runner.
  - Treat dialogue benchmark fixtures as acceptance checks for agent behavior, not as optional reports.
- Open questions / risks:
  - Exact workbook examples must be retied to the active snapshot before use, because workbook contents can change.
