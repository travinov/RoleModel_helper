# Remove RAG And Vector Dependency

## Context
- Problem: instruction answers now use `Doc/static_instruction.txt`, but the repository still creates RAG tables and requires the PostgreSQL `vector` extension.
- Why now: the corporate PostgreSQL server should not need `pgvector` for a static-instruction deployment.
- Related files/services: `rolemodel_etl/sql/schema.sql`, `app/api/server.py`, `app/agent/service.py`, `app/agent/scenario_services.py`, `app/services/static_instruction.py`, `README.md`, `docker-compose.yml`, tests.

## Goal
- Remove active RAG ingestion/search code, RAG admin API, RAG tables, and `vector`/`pgvector` requirements.

## Non-Goals
- Do not change Excel parsing/loading semantics.
- Do not change chat role lookup behavior.
- Do not remove static instruction upload or static instruction answers.
- Do not drop existing RAG tables from already-created databases; this change prevents creating them in new installs.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [x] PostgreSQL schema/data contract/query behavior
- [x] Chat API contract
- [ ] Agent state machine/slots/phase transitions
- [ ] Dialogue benchmark/replay expectations
- [x] Operational checks

## Requirements
- Functional:
  - Static instruction upload and lookup keep working.
  - `/api/v1/admin/instruction/upload` remains available.
  - `/api/v1/admin/rag/*` endpoints are removed.
  - New DB initialization no longer creates RAG tables or `vector` objects.
- Technical constraints:
  - `CREATE EXTENSION vector`, `VECTOR(128)`, `::vector`, and vector indexes are absent from schema/code.
  - Active app code no longer imports `app.rag` or `RagRepository`.
  - Local Docker uses plain PostgreSQL, not `pgvector`.
- Operational constraints:
  - Corporate installer continues to use external PostgreSQL `10.135.162.149:5433`.

## Inputs / Outputs
- Inputs:
  - Static instruction text file or upload body.
- Outputs:
  - Instruction answers with existing citation payload shape.
- Side effects:
  - New installs create only role model, chat, alias, and search tables/views.

## Domain Invariants
- Preserved:
  - Chat response payload remains structurally compatible.
  - `instruction_mode` remains `INLINE_DOC`.
  - Role model snapshot uniqueness and ETL run lifecycle remain unchanged.
- Intentionally changed:
  - RAG ingestion/search admin API is no longer part of the app.
  - `vector`/`pgvector` is no longer required.

## Acceptance Criteria
1. `tests.test_no_rag` proves active code/schema no longer has RAG admin endpoints, RAG modules, `pgvector`, or `vector` schema objects.
2. Static instruction upload and answer tests pass without `app.rag`.
3. Full unittest discovery passes.
4. README and installer guidance no longer instruct users to enable `pgvector`.

## Test Plan (TDD)
- RED command:
  - `/usr/bin/python3 -m unittest tests.test_no_rag`
- Expected RED failure:
  - Existing `app/rag`, RAG schema tables, and `CREATE EXTENSION vector` are still present.
- GREEN command:
  - `/usr/bin/python3 -m unittest tests.test_no_rag tests.test_static_instruction`
- Regression checks:
  - `/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"`

## Benchmark / Replay Plan
- Not applicable: no dialogue behavior change is intended.

## DB / Snapshot Verification
- Verify schema text has no `rag_`, `VECTOR`, `::vector`, or `vector_cosine_ops`.
- Existing databases may still have old tables; DBA can drop them separately if needed.

## Rollout / Verification
- Local verification steps:
  - Run targeted no-RAG/static-instruction tests.
  - Run full unittest discovery.
- Runtime/monitoring checks:
  - New corporate install should pass `rolemodel_etl db.init` without `pgvector`.
  - Smoke-test `/api/v1/health` and static instruction upload.
- Rollback approach:
  - Restore prior commit if the removed admin RAG endpoints are needed again.

## Change Notes
- Key decisions:
  - Keep static instruction answer logic under `app/services`, not under `app/rag`.
  - Keep citation payload fields for API compatibility.
- Open questions / risks:
  - Existing DBs are not automatically cleaned; this commit only stops creating and using RAG objects.
