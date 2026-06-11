# Combined Dialogue Quality Runner

## Context
- Problem: generated successful-dialogue fixtures include GigaChat evidence expectations that the old benchmark runner ignored.
- Why now: the combined fixture should be part of the server test workflow and produce a clear pass/fail report after deployment.
- Related files/services: `tests/run_dialogue_benchmark.py`, `tests/fixtures/dialogue_success_combined_2026-06-11.json`, `tests/test_dialogue_benchmark_runner.py`, `README.md`.

## Goal
- Primary outcome: running the combined fixture on the server validates structured response behavior and optional DB evidence for successful GigaChat tool calls and absence of `fallback_without_llm`.

## Non-Goals
- Do not replay the benchmark automatically during application startup.
- Do not add API endpoints for test evidence.
- Do not mutate production data beyond creating benchmark chat sessions.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [x] Chat API contract
- [x] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [x] Dialogue benchmark/replay expectations
- [x] Operational checks

## Requirements
- Functional:
  - Runner can use the combined successful-dialogue fixture with one CLI flag.
  - Runner can limit execution to the first N sessions or a reproducible random sample.
  - Runner evaluates generated fields such as `intent_type`, `dialog_act`, `pending_kind`, min counts, and presence checks.
  - Runner can validate `expected_tool_success` and `forbid_reasoning_trace` against local DB evidence when `--db-evidence` is set.
  - Report includes session selection metadata, `meets_quality_gate`, critical failure summary, per-session results, per-turn failures, and per-turn evidence when enabled.
- Technical constraints:
  - Keep existing default fixture unchanged.
  - Keep DB evidence optional so local API-only benchmark usage still works.

## Inputs / Outputs
- Inputs: fixture JSON, live API base URL, optional app DB environment.
- Outputs: benchmark report JSON and a concise stdout summary.
- Side effects: benchmark creates chat sessions/messages in the target app DB.

## Acceptance Criteria
1. New generated fixture fields are counted as checks.
2. `expected_tool_success` fails without matching fresh tool evidence.
3. `forbid_reasoning_trace` fails when fresh evidence includes `fallback_without_llm`.
4. Combined fixture loads through `DialogueBenchmarkRunner`.
5. `--session-limit 10` checks the first 10 fixture sessions.
6. `--random-session-limit 10 --random-seed N` checks a reproducible random 10-session sample.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_dialogue_benchmark_runner`
- Expected RED failure: `_evaluate_expectation` does not accept evidence or generated fixture fields.
- GREEN command: `/usr/bin/python3 -m unittest tests.test_dialogue_benchmark_runner`
- Regression checks: `/usr/bin/python3 -m py_compile tests/run_dialogue_benchmark.py`
- Full regression: `/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"`

## Benchmark / Replay Plan
- Fixture: `tests/fixtures/dialogue_success_combined_2026-06-11.json`
- Expected pass/fail signal: `meets_quality_gate=true` in the output report.
- Optional smoke run selectors: `--session-limit 10`, `--session-limit 20`, or `--random-session-limit 10 --random-seed 42`.
- Report artifact path: caller-provided `--report`, recommended under `reports/dialogue_quality/`.

## DB / Snapshot Verification
- With `--db-evidence`, runner reads `tool_call_log` and `chat_turn_interpretation` for the new benchmark session.
- Expected evidence: at least `gigachat_interpret_turn` success for turns that require it, and no forbidden reasoning trace.

## Rollout / Verification
- Local verification steps: unit tests and JSON load check.
- Runtime/monitoring checks: run benchmark on the deployed server with `--db-evidence`.
- Rollback approach: use the previous benchmark command without `--combined-success-fixture`.

## Change Notes
- Key decision: DB evidence is optional but required for the generated fixture's GigaChat proof fields to pass.
- Key decision: all sessions remain the default; partial runs must be explicit and recorded in the report.
