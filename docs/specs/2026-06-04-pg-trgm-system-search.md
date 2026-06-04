# Pg Trgm System Search

## Context
- Problem: the application-owned search index gave worse AS matching quality than native PostgreSQL trigram scoring.
- Why now: DBA reinstalled `pg_trgm` in schema `ext` and confirmed `as_admin` can execute `ext.similarity(text, text)`.
- Related files/services: `app/config.py`, `app/repositories/search_repository.py`, `rolemodel_etl/sql/schema.sql`, `rolemodel_etl/loader.py`.

## Goal
- Primary outcome: system lookup uses native `pg_trgm` `similarity(text, text)` from the DBA-managed extension schema.

## Non-Goals
- Do not create or reinstall `pg_trgm` from the application schema.
- Do not use the trigram `%` operator until its schema-qualified availability is explicitly verified.
- Do not change workbook parsing or database load semantics.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [x] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [ ] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [ ] Dialogue benchmark/replay expectations
- [x] Operational checks

## Requirements
- Functional:
  - `RM_PG_TRGM_SCHEMA` controls the extension schema and defaults to `ext`.
  - Runtime AS candidate SQL calls `<RM_PG_TRGM_SCHEMA>.similarity(...)`.
  - Existing SAFE/AMBIGUOUS/UNSAFE alias caps remain preserved.
- Technical constraints:
  - No application-owned `app_similarity()` function.
  - No application-owned `search_document` or `search_ngram` runtime path.
  - No mandatory use of unqualified `%` operator.
- Operational constraints:
  - DBA must keep `pg_trgm` installed in the configured extension schema.
  - Runtime role must have `EXECUTE` on `similarity(text, text)`.

## Inputs / Outputs
- Inputs: user AS query text, `system_alias`, `system_alias_candidate`, active snapshot systems.
- Outputs: ranked system candidates with the existing response shape.
- Side effects: none.

## Domain Invariants
- Preserved:
  - Only one active snapshot at a time.
  - Alias class thresholds continue to cap unsafe and ambiguous matches.
  - App-only update does not touch PostgreSQL data.
- Intentionally changed:
  - AS similarity ranking returns to native `pg_trgm` scoring.

## Acceptance Criteria
1. Config defaults `RM_PG_TRGM_SCHEMA` to `ext`.
2. Runtime system lookup SQL uses schema-qualified `similarity(...)` and does not reference `search_document` or `search_ngram`.
3. Schema does not create `pg_trgm`, `app_similarity()`, `search_document`, or `search_ngram`.
4. Loader no longer rebuilds an application-owned search index.
5. Full regression tests pass.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_pg_trgm_search tests.test_custom_similarity`
- Expected RED failure: current code still references the application search index.
- GREEN command: `/usr/bin/python3 -m unittest tests.test_pg_trgm_search tests.test_custom_similarity tests.test_install_script`
- Regression checks: `/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"`

## DB / Snapshot Verification
- On app server:
  - `SELECT ext.similarity('text','tuxt');`
  - Confirm runtime role has `EXECUTE` on `ext.similarity(text,text)`.

## Rollout / Verification
- Local verification steps: targeted tests, full tests, ZIP content verification.
- Runtime/monitoring checks: app-only update, service restart, health endpoint, then AS lookup smoke test.
- Rollback approach: redeploy previous ZIP.

## Change Notes
- Key decisions: use schema-qualified function calls instead of relying on `search_path`.
- Open questions / risks: if DBA changes extension schema, set `RM_PG_TRGM_SCHEMA` in `.env.server`.
