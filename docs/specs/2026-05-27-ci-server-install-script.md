# CI Server Install Script

## Context
- Problem: the app must be installed from a ZIP archive on a corporate Linux server and connected to an existing external PostgreSQL database.
- Why now: the target DB is reachable only by direct IP on port `5433`, so the default local Docker/PostgreSQL setup is not appropriate.
- Related files/services: `scripts/deploy_rolemodel_helper_remote.sh`, `scripts/install_rolemodel_helper_server.sh`, `requirements.txt`, `rolemodel_etl/cli.py`, `rolemodel_etl/sql/schema.sql`, `app/__main__.py`.

## Goal
- Provide a ZIP-contained deployment path that can be launched locally, copies the app to `CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru`, and configures the app there against external PostgreSQL.

## Non-Goals
- Do not provision PostgreSQL or Docker.
- Do not hardcode DB credentials.
- Do not require `sudo` for the default installation path.
- Do not change application runtime behavior.

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
  - The script can be launched from the extracted repository root.
  - A local deploy script uploads the repository over SSH and runs the server installer on the app server.
  - Defaults target DB settings to `10.135.162.149:5433`, database `bdtest`, schema `rolemodel_helper`, user `CI09479675-pg-travinov`.
  - DB password is supplied through an environment variable or hidden interactive prompt.
  - The script creates a Python virtual environment, installs `requirements.txt`, initializes DB schema, validates the bundled workbook, and loads it unless explicitly skipped.
  - The script checks whether all required schema tables exist before and after initialization.
  - The optional `--reset-db` mode drops the configured schema and recreates all DB objects from scratch.
  - The script creates an app env file and a runnable user-level service or fallback start script.
- Technical constraints:
  - Bash script only; no new dependency.
  - Keep secrets out of command-line arguments and protect the env file with mode `600`.
  - Pass the DB password from the local deploy script to the remote installer through stdin.
  - Preserve direct-IP DB host use; do not substitute hostname.
  - Require an explicit `--reset-db` flag for destructive DB overwrite.
- Operational constraints:
  - Work without local Docker.
  - If user-level systemd is unavailable, leave clear start/stop scripts in the install directory.

## Inputs / Outputs
- Inputs:
  - Extracted ZIP checkout.
  - Optional environment variables: `RM_DB_USER`, `RM_DB_PASSWORD`, `RM_DB_NAME`, `RM_DB_SCHEMA`, `RM_APP_PORT`, `RM_INSTALL_DIR`.
- Outputs:
  - Uploaded app checkout on the remote app server.
  - Installed app directory, virtual environment, `.env`, logs directory, start/stop scripts, optional user systemd unit.
- Side effects:
  - Creates schema/tables/extensions in the configured external DB.
  - Loads the bundled role model workbook when not skipped.
  - With `--reset-db`, deletes the configured schema and all contained data before re-creating it.

## Domain Invariants
- Preserved:
  - Only app operational packaging changes.
  - Existing DB schema and ETL commands remain the source of truth.
- Intentionally changed:
  - None.

## Acceptance Criteria
1. A test verifies that the installer exists, is Bash syntax-valid, contains the external DB defaults, and references the current app/ETL entrypoints.
2. A test verifies that the local deploy script exists, is Bash syntax-valid, uploads over SSH, and runs the server installer remotely.
3. The installer refuses to run without DB credentials unless they are entered interactively.
4. The installer uses `10.135.162.149` and `5433` by default and does not start local Docker.
5. The installer writes protected env files and provides a way to start the app after ZIP extraction.
6. The installer reports missing required DB tables and verifies the schema after initialization.
7. The deploy script can pass through `--reset-db` for a full schema overwrite.

## Test Plan (TDD)
- RED command:
  - `/usr/bin/python3 -m unittest tests.test_install_script`
- Expected RED failure:
  - Missing `scripts/deploy_rolemodel_helper_remote.sh`.
- GREEN command:
  - `/usr/bin/python3 -m unittest tests.test_install_script`
- Regression checks:
  - `bash -n scripts/install_rolemodel_helper_server.sh scripts/deploy_rolemodel_helper_remote.sh`

## Benchmark / Replay Plan
- Not applicable: no dialogue behavior changes.

## DB / Snapshot Verification
- Runtime script verification:
  - The DB schema check reports required/missing tables.
  - `rolemodel_etl db.init` must complete against the external DB.
  - `rolemodel_etl validate` must pass for the bundled workbook.
  - `rolemodel_etl load` must create an active snapshot.

## Rollout / Verification
- Local verification steps:
  - Run the targeted unittest for the installer.
  - Run `bash -n` against the script.
- Runtime/monitoring checks:
  - On the server, check service status or fallback PID file.
  - Smoke-test `http://127.0.0.1:<port>/api/v1/health`.
  - From a local machine, use `ssh -L <port>:127.0.0.1:<port> CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru` before opening the local browser URL.
- Rollback approach:
  - Stop the user service or fallback PID.
  - Remove the install directory and user systemd unit.

## Change Notes
- Key decisions:
  - The default path is user-local to avoid requiring `sudo` on a corporate server.
  - Credentials stay outside Git and are written only to the generated server env file.
  - `127.0.0.1` in installer output is server-local; local browser access should use an SSH tunnel unless the app port is exposed by network policy.
  - Destructive DB overwrite is explicit via `--reset-db`; the default path remains non-destructive and idempotent.
- Open questions / risks:
  - The DB user must have enough privileges for `CREATE SCHEMA` and required extensions, or extensions must be pre-created by DBA.
