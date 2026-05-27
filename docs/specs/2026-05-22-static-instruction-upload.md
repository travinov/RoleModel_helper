# Static Instruction Upload

## Context
- Problem: instruction answers currently depend on a bundled PPTX inline pack and can fall back to RAG search.
- Why now: the instruction should become a single static source that can be uploaded from the UI through a new API.
- Related files/services: `app/api/server.py`, `app/ui/index.html`, `app/rag/service.py`, `app/agent/scenario_services.py`, `app/models/domain.py`, `tests/test_rag_pptx.py`, new API/UI tests.

## Goal
- Store the active instruction as plain UTF-8 text and use only that static text for instruction answers.

## Non-Goals
- Do not ingest the instruction into RAG tables.
- Do not keep PPTX as fallback for instruction answers.
- Do not change role model Excel upload behavior.
- Do not add authentication or admin-token changes.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [ ] Agent state machine/slots/phase transitions
- [x] RAG ingestion/retrieval/citation behavior
- [x] Dialogue benchmark/replay expectations
- [ ] Operational checks

## Requirements
- Functional:
  - `POST /api/v1/admin/instruction/upload` accepts `.txt` and `.rtf` files.
  - Uploaded content is normalized to plain text and written to `Doc/static_instruction.txt`.
  - The original uploaded file is also saved under an upload directory for auditability.
  - The UI has a dedicated instruction upload control, separate from Excel upload.
  - Instruction lookup uses only `Doc/static_instruction.txt`.
  - If the static instruction is absent or empty, the agent returns a clear "instruction not loaded" answer.
- Technical constraints:
  - No DB schema change.
  - No new external Python dependency.
  - RTF conversion must support the RTF unicode escapes produced by the provided file.
- Operational constraints:
  - Existing `/api/v1/admin/rolemodel/upload` remains unchanged.
  - Existing generic RAG admin endpoints may remain available, but instruction-answer code must not use RAG fallback.

## Inputs / Outputs
- Inputs:
  - Raw upload body with `X-File-Name` header for `.txt` or `.rtf`.
- Outputs:
  - JSON upload response with status, message, static instruction path, uploaded file path, and text length.
- Side effects:
  - `Doc/static_instruction.txt` is replaced atomically enough for local/server use.
  - `RagService` static instruction cache is invalidated after upload.

## Domain Invariants
- Preserved:
  - Chat response payloads still use `answer.answer_type = "INSTRUCTION_LOOKUP"`.
  - Instruction answers still expose citations, now pointing to the static instruction source.
  - `instruction_mode` remains compatible with existing response fields.
- Intentionally changed:
  - Instruction lookup no longer falls back to PPTX or RAG chunks.

## Acceptance Criteria
1. Given a `.rtf` upload, `/api/v1/admin/instruction/upload` saves readable plain text to `Doc/static_instruction.txt` and returns `status = "SUCCESS"`.
2. Given an empty body or unsupported extension, the new upload API returns a validation error and does not update the static instruction.
3. Given `Doc/static_instruction.txt`, `RagService.answer_from_inline_doc()` returns an inline answer with at least one static citation.
4. Given no static instruction, `InstructionService.answer()` returns an `INSTRUCTION_LOOKUP` answer saying the instruction is not loaded and does not call RAG search.
5. The UI contains a dedicated instruction upload button wired to `/api/v1/admin/instruction/upload`.

## Test Plan (TDD)
- RED command:
  - `/usr/bin/python3 -m unittest tests.test_static_instruction`
- Expected RED failure:
  - Missing static instruction helper/API behavior and missing UI control.
- GREEN command:
  - `/usr/bin/python3 -m unittest tests.test_static_instruction`
- Regression checks:
  - `/usr/bin/python3 -m unittest tests.test_agent_logic`
  - `/usr/bin/python3 -m unittest tests.test_rag_pptx`

## Benchmark / Replay Plan
- No benchmark fixture update is required because the response contract remains `INSTRUCTION_LOOKUP` with citations.
- If dialogue wording shifts materially, run `/usr/bin/python3 tests/run_dialogue_benchmark.py`.

## DB / Snapshot Verification
- Not applicable: no schema or snapshot behavior changes.

## Rollout / Verification
- Local verification steps:
  - Run targeted unittest commands.
  - Start the local server and smoke-test the root page if UI wiring changes need visual confirmation.
- Runtime/monitoring checks:
  - Upload a `.rtf` instruction and verify `Doc/static_instruction.txt` contains readable text.
- Rollback approach:
  - Restore the previous `Doc/static_instruction.txt` or remove it to make the "instruction not loaded" state explicit.

## Change Notes
- Key decisions:
  - Static text is authoritative.
  - PPTX and RAG are not used for instruction lookup.
- Open questions / risks:
  - Existing RAG admin endpoints remain for other future uses; this change only removes them from instruction-answer fallback.
