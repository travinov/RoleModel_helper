# Specs

Use this folder for lightweight, reusable implementation specs tied to real RoleModel Helper behavior.

## Scope In This Repository

Specs should reference the current implementation areas when relevant:
- `rolemodel_etl/`: Excel snapshot parsing/loading and workbook validation.
- `app/`: FastAPI endpoints, chat agent flow, slot/state orchestration.
- `app/models/domain.py` and `app/models/api.py`: canonical state, pending questions, response payloads.
- `app/services/static_instruction.py` and `app/services/instruction_answer.py`: static instruction upload and answer paths.
- `rolemodel_etl/sql/schema.sql`: PostgreSQL schema and invariants.
- `tests/`: targeted unittests and dialogue benchmark/replay fixtures.

## When To Create Or Update A Spec

Create or update a spec when work changes behavior, interfaces, data flow, validation, or operational verification.

No spec needed for strictly docs-only wording/format edits that do not change behavior expectations.

## Change Category Checklist

Mark applicable categories in each spec:
- [ ] ETL/workbook parsing or validation behavior.
- [ ] PostgreSQL schema/data contract/query behavior.
- [ ] Chat API contract (`ChatMessageResponse`, pending question payloads, intent fields).
- [ ] Agent state machine/slots/phase transitions/context shifts.
- [ ] Static instruction upload/answer behavior.
- [ ] Dialogue benchmark/replay expectations.
- [ ] Operational checks (snapshot activation, ETL run status, error accounting).

If two unrelated categories are large, split into separate specs.

## Domain Invariants To Preserve

Call out which invariants are touched; unchanged invariants should be explicitly stated:
- Only one active snapshot at a time (`snapshot.is_active = TRUE` unique).
- ETL run lifecycle remains coherent (`RUNNING -> SUCCESS/FAILED`, row/error counters non-negative).
- Access level semantics remain `1|2` where enforced in schema.
- Slot state/pending question fields remain structurally compatible with `app/models/domain.py` and `app/models/api.py`.
- `state_revision` remains monotonic for session state updates.
- Intent/phase transitions remain explicit and replayable (no hidden side channels).
- Instruction answers preserve citation payload compatibility when static instruction mode is used.

## Spec Template

Copy into `docs/specs/YYYY-MM-DD-short-name.md`.

```md
# <Spec Title>

## Context
- Problem:
- Why now:
- Related files/services:

## Goal
- Primary outcome:

## Non-Goals
- Explicitly out of scope:

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [ ] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [ ] Dialogue benchmark/replay expectations
- [ ] Operational checks

## Requirements
- Functional:
- Technical constraints:
- Operational constraints:

## Inputs / Outputs
- Inputs:
- Outputs:
- Side effects:

## Domain Invariants
- Preserved:
- Intentionally changed (with migration/compatibility notes):

## Acceptance Criteria
1. ...
2. ...
3. ...

## Test Plan (TDD)
- RED command:
- Expected RED failure:
- GREEN command:
- Regression checks:

## Benchmark / Replay Plan (if dialogue behavior changes)
- Fixture(s):
- Expected pass/fail signal:
- Report artifact path:

## DB / Snapshot Verification
- Queries or checks to prove data/snapshot correctness:

## Rollout / Verification
- Local verification steps:
- Runtime/monitoring checks:
- Rollback approach:

## Change Notes
- Key decisions:
- Open questions / risks:
```

## Acceptance Criteria Examples

Use concrete, testable criteria. Examples:
1. Given an active snapshot and resolved profile context, system-discovery responses include only systems reachable for that profile.
2. Given a request for instruction lookup, response contains `answer.answer_type = "INSTRUCTION_LOOKUP"` and at least one citation when retrieval mode requires citations.
3. Given an unresolved slot request, response returns `pending_question` with stable `topic/kind/options/page_offset/page_size/total_options`.
4. Given ETL workbook headers missing required fragments, parse result records `HEADER_MISMATCH` issue(s) and load is blocked by validation policy.

## Test Matrix (Targeted First)

Run only the narrow checks for changed scope, then broaden if needed:

- Parser unit behavior:
```bash
/usr/bin/python3 -m unittest tests.test_parser_unit
```

- Parser + loader integration:
```bash
/usr/bin/python3 -m unittest tests.test_parser_integration
```

- Agent/state/intent behavior:
```bash
/usr/bin/python3 -m unittest tests.test_agent_logic
```

- Dialogue export payload/contract:
```bash
/usr/bin/python3 -m unittest tests.test_dialogue_export
```

- Static instruction upload/answer behavior:
```bash
/usr/bin/python3 -m unittest tests.test_static_instruction
```

- Full regression (only when scope justifies):
```bash
/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"
```

## Benchmark And Replay Guidance

For dialogue behavior, add fixture-driven verification to the spec:
- Benchmark runner:
```bash
/usr/bin/python3 tests/run_dialogue_benchmark.py
```
- Prefer existing fixtures in `tests/fixtures/` and document which fixture/report pair is the source of truth for the change.
- Include expected thresholds or critical failure rules in acceptance criteria when changing dialogue policy.
- For incident regressions, reference replay fixtures (for example `dialogue_incident_replay*.json`) and require pass criteria to be explicit.

## DB And Snapshot Verification Guidance

Every ETL/schema-affecting spec should include post-change verification such as:
- Active snapshot uniqueness and expected `snapshot.id` selection.
- ETL run status/counters (`rows_read`, `rows_loaded`, `errors_count`) consistency.
- Expected row presence in `profile`, `system`, `entitlement`, and access tables for the loaded workbook.
- `etl_error` entries are explainable and match validation outcomes.
- Alias candidate seeding behavior (`system_alias_candidate`, `department_alias_candidate`) if alias logic changed.

## Required Workbook Validation Themes (Project Memory)

When a spec touches workbook-driven behavior, explicitly cover these four themes in acceptance criteria and tests:
1. AS alias resolution: abbreviated/variant AS names resolve to the intended system where mapping is unambiguous.
2. Accessible AS list for current profile context: returned system set is constrained by current profile context, not global catalog.
3. Justification lookup: system/profile justification retrieval is returned from loaded snapshot data when requested.
4. Stale/missing AS rejection: unknown or outdated AS names are rejected with a clear fallback or clarification path.

## Conventions

- Keep specs short and implementation-oriented.
- One spec maps to one coherent change set.
- Update acceptance criteria when scope changes.
- Tie each acceptance criterion to at least one concrete check (unittest command, benchmark, replay, or DB query).
