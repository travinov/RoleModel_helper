# Application Search Index

## Context
- Problem: runtime system lookup should not depend on PostgreSQL `pg_trgm` grants or an SQL similarity function.
- Why now: deployed DB exposes `pg_trgm` in the target schema but function execution is blocked for the effective runtime role.
- Related files/services: `rolemodel_etl/sql/schema.sql`, `rolemodel_etl/loader.py`, `rolemodel_etl/search_index.py`, `app/repositories/search_repository.py`.

## Goal
- Primary outcome: system lookup uses application-owned search index tables populated by `rolemodel_etl load`.

## Non-Goals
- Do not introduce external search services.
- Do not change workbook parsing semantics.
- Do not change profile, entitlement, or instruction-answer behavior.

## Change Category Checklist
- [x] ETL/workbook parsing or validation behavior
- [x] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [ ] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [ ] Dialogue benchmark/replay expectations
- [x] Operational checks

## Requirements
- Functional:
  - `rolemodel_etl load` rebuilds system search documents and n-grams for the loaded snapshot.
  - Runtime system candidate queries score indexed documents using n-gram overlap plus existing structured scoring.
  - Safe, ambiguous, and unsafe alias class handling remains preserved.
- Technical constraints:
  - No `pg_trgm`, `gin_trgm_ops`, trigram `%` operator, or `app_similarity()` runtime SQL dependency.
  - Search index tables must use ordinary PostgreSQL tables and btree indexes only.
- Operational constraints:
  - `--reset-db` can recreate the index from scratch during current rollout.

## Inputs / Outputs
- Inputs: normalized system names, seeded manual aliases, auto-extracted alias candidates, user system query text.
- Outputs: ranked system candidates with the existing response shape.
- Side effects: new `search_document` and `search_ngram` rows per snapshot.

## Domain Invariants
- Preserved:
  - Only one active snapshot at a time.
  - Existing alias class thresholds continue to cap unsafe and ambiguous matches.
  - Accessible AS filtering in `browse_system_candidates` remains unchanged.
- Intentionally changed:
  - Database similarity scoring uses application index overlap instead of function calls.

## Acceptance Criteria
1. Schema contains `search_document` and `search_ngram` tables with ordinary btree indexes.
2. Schema does not create `pg_trgm`, `gin_trgm_ops`, or `app_similarity()`.
3. `rolemodel_etl load` rebuilds search index rows after system aliases and alias candidates are seeded.
4. Runtime system lookup SQL reads `search_document`/`search_ngram` and does not call `app_similarity()` or `pg_trgm` functions/operators.
5. Full regression tests pass.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_application_search_index`
- Expected RED failure: missing `rolemodel_etl.search_index` and index schema/query assertions fail.
- GREEN command: `/usr/bin/python3 -m unittest tests.test_application_search_index tests.test_no_rag tests.test_install_script`
- Regression checks: `/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"`

## DB / Snapshot Verification
- After deploy, query `search_document` and `search_ngram` counts for the active snapshot.
- Health endpoint should remain `status=ok`.

## Rollout / Verification
- Local verification steps: targeted tests, full tests, ZIP content verification.
- Runtime/monitoring checks: deploy with `--reset-db`, then check indexed row counts.
- Rollback approach: redeploy previous ZIP with `--reset-db`.

## Change Notes
- Key decisions: use indexed document n-gram overlap so quality can be tuned in application-owned code.
- Open questions / risks: scoring thresholds may need calibration against real user queries after rollout.
