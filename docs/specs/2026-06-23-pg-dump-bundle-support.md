# Bundled pg_dump Support

## Context
- Problem: role model upload fails on the corporate app server because `pg_dump` is not installed system-wide and the user cannot run privileged package installation.
- Why now: a new workbook upload with department aliases cannot proceed because the backup safety gate fails before ETL starts.
- Related files/services: `app/api/server.py`, `scripts/install_rolemodel_helper_server.sh`, `scripts/update_rolemodel_helper_app_remote.sh`, `vendor/`.

## Goal
- Primary outcome: upload backup can use a user-space PostgreSQL client binary supplied by deployment, without requiring sudo.

## Non-Goals
- Explicitly out of scope: disabling the database backup gate; changing ETL parsing/loading semantics; installing OS packages as root.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [ ] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [ ] Dialogue benchmark/replay expectations
- [x] Operational checks

## Requirements
- Functional:
  - Backup resolution checks `RM_PG_DUMP_PATH`.
  - Backup resolution checks `vendor/pgsql-client-el9-x86_64/bin/pg_dump` under the app checkout.
  - Backup resolution still supports `pg_dump` from `PATH`.
  - Missing `pg_dump` returns an actionable upload error and does not continue to ETL.
- Technical constraints:
  - Keep upload behavior read/write sequence unchanged after backup succeeds.
  - Preserve app-only update behavior; do not touch DB during update.
- Operational constraints:
  - The server may not have sudo or external internet access.
  - Bundled binaries, when present, must survive app-only deployment.

## Inputs / Outputs
- Inputs: `RM_PG_DUMP_PATH`, optional `vendor/pgsql-client-el9-x86_64/` client bundle, upload request body.
- Outputs: backup dump path on successful backup; HTTP 500 with remediation hint when no usable `pg_dump` exists.
- Side effects: backup file creation only after a usable `pg_dump` is found.

## Domain Invariants
- Preserved: upload still creates a backup before parsing/loading a workbook.
- Intentionally changed: backup binary discovery now supports user-space deployment paths.

## Acceptance Criteria
1. Given `RM_PG_DUMP_PATH` points to an executable file, backup uses that path even when `pg_dump` is absent from `PATH`.
2. Given no env override and `vendor/pgsql-client-el9-x86_64/bin/pg_dump` is executable, backup uses the bundled path.
3. Given neither env, bundled path, nor PATH has `pg_dump`, upload returns a clear error mentioning both `RM_PG_DUMP_PATH` and the bundled vendor path.
4. App-only deployment preserves `vendor/` when the ZIP includes it.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_rolemodel_upload_backup`
- Expected RED failure: helper functions for `RM_PG_DUMP_PATH` and bundled pg_dump do not exist.
- GREEN command: `/usr/bin/python3 -m unittest tests.test_rolemodel_upload_backup`
- Regression checks: `/usr/bin/python3 -m unittest tests.test_static_instruction`

## Rollout / Verification
- Local verification steps: targeted unittests and shell syntax checks for deployment scripts.
- Runtime/monitoring checks: on server, verify `.env.server` contains `RM_PG_DUMP_PATH` when bundle exists and run `"$RM_PG_DUMP_PATH" --version`.
- Rollback approach: app-only update back to previous branch; no DB migration involved.

## Change Notes
- Key decisions: keep backup mandatory; support user-space binary discovery instead of requiring OS package installation.
- Open questions / risks: the actual EL9 `pg_dump` binary bundle must be supplied separately if it is not committed into `vendor/`.
