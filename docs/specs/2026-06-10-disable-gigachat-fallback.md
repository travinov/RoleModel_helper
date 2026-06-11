# Disable GigaChat Fallback For Dialogue Turns

## Context
- Problem: when GigaChat intent interpretation is unavailable or returns an error, the chat silently falls back to local heuristic interpretation.
- Why now: quality checks must fail loudly when the real GigaChat integration is unavailable; users should not receive a normal-looking answer produced without the required LLM path.
- Related files/services: `app/agent/service.py`, `app/services/instruction_answer.py`, `app/services/gigachat.py`, `tests/test_agent_logic.py`, `tests/test_static_instruction.py`, `tests/run_dialogue_benchmark.py`.

## Goal
- Primary outcome: production message handling stops using fallback interpretation after a GigaChat failure and instead tells the user that the service is temporarily unavailable with an action to repeat the same request.

## Non-Goals
- Do not remove the internal `_interpret_turn_fallback` helper if existing tests still use it directly.
- Do not add an automatic retry loop in the backend in this change.
- Do not change role-model search, ETL, or GigaChat authentication.

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
  - If GigaChat is disabled, unavailable, raises an exception, or returns an invalid payload for turn interpretation, the user receives a service-unavailable assistant message.
  - If GigaChat is disabled, unavailable, raises an exception, or returns empty text for instruction answer synthesis, the user receives the same service-unavailable assistant message instead of a static-text fallback.
  - The response includes a suggested action that repeats the original user text.
  - The current dialogue state is preserved so the user can retry without losing context.
  - The turn must not be marked as `fallback_without_llm`.
- Technical constraints:
  - Keep the existing public chat response shape.
  - Preserve existing GigaChat tool-call logging for diagnostics.
  - Keep the change scoped to message handling and tests.
- Operational constraints:
  - Live e2e benchmarks should treat unavailable GigaChat as a hard failure, not a degraded pass.

## Inputs / Outputs
- Inputs: user chat message through `POST /api/v1/chat/sessions/{session_id}/messages`.
- Outputs: `ChatMessageResponse` with `intent_type="UNKNOWN"`, service-unavailable assistant text, and a repeat suggested action.
- Side effects: user message and assistant error message are stored; no slot state transition is applied.

## Domain Invariants
- Preserved:
  - Existing session state, pending question, and context remain available after the failed turn.
  - `state_revision` is not advanced by fallback interpretation.
  - Tool-call errors remain visible in `tool_call_log`.
- Intentionally changed:
  - Production turn handling no longer uses `_interpret_turn_fallback` after a failed GigaChat call.

## Acceptance Criteria
1. Given GigaChat `complete_json` raises, `handle_message` returns a visible temporary-unavailable message and a `retry_request` suggested action containing the original text.
2. Given GigaChat fails during a turn, no persisted turn interpretation has `reasoning_trace_short="fallback_without_llm"`.
3. Given GigaChat is disabled for intent interpretation, `handle_message` returns the same temporary-unavailable response instead of continuing with heuristic fallback.
4. Given GigaChat is disabled for instruction answer synthesis, `StaticInstructionAnswerService.answer_from_static_instruction` raises a typed unavailable error instead of returning extractive static text.
5. Existing direct fallback helper tests may remain, but the production `handle_message` path must not use fallback for GigaChat failures.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_gigachat_failure_returns_retryable_service_message`
- Expected RED failure: response is produced by fallback policy instead of the service-unavailable contract.
- GREEN command: `/usr/bin/python3 -m unittest tests.test_agent_logic.AgentDialogRefactorTestCase.test_gigachat_failure_returns_retryable_service_message tests.test_agent_logic.AgentDialogRefactorTestCase.test_fallback_interpreter_does_not_route_by_keywords_without_state tests.test_static_instruction.StaticInstructionServiceTestCase.test_instruction_answer_does_not_fallback_when_gigachat_disabled`
- Regression checks: `/usr/bin/python3 -m unittest tests.test_agent_logic tests.test_static_instruction tests.test_gigachat_certificate_auth`

## Benchmark / Replay Plan
- Fixture(s): existing `tests/fixtures/dialogue_benchmark_*.json`.
- Expected pass/fail signal: live e2e runs should fail hard on GigaChat unavailability, while normal unittest coverage remains deterministic.
- Report artifact path: unchanged for this patch; later live-e2e work should add explicit GigaChat evidence fields.

## DB / Snapshot Verification
- Query `chat_turn_interpretation` for the failed session and confirm no `fallback_without_llm` row was created for the failed turn.
- Query `tool_call_log` for the failed session and confirm the relevant `gigachat_*` tool has `status='error'`.

## Rollout / Verification
- Local verification steps:
  - Run the targeted agent test.
  - Run GigaChat certificate auth tests.
- Runtime/monitoring checks:
  - Temporarily disable/withhold GigaChat credentials in a non-production environment and confirm the chat shows the retryable service message.
- Rollback approach:
  - Revert `app/agent/service.py` and the matching test/spec change.

## Change Notes
- Key decision: keep fallback helper available for tests and possible offline diagnostics, but remove it from production message handling after GigaChat failures.
- Open questions / risks: a later live-e2e runner change should make missing GigaChat traces a hard benchmark failure.
