# RoleModel Helper

Server-side chat assistant for role/access lookup on top of:

- structured PostgreSQL data loaded from the role-model Excel workbook,
- a static text instruction used for instruction answers.

## Components

- `rolemodel_etl/` - ETL from Excel into PostgreSQL.
- `app/` - FastAPI backend, server-side agent, static instruction handling, minimal chat UI.
- `tests/` - unit and integration-style tests for parser, agent logic, instruction upload, and operational checks.

## Install

```bash
/usr/bin/python3 -m pip install --user -r requirements.txt
```

## Environment

ETL commands require:

```bash
export RM_DB_HOST=127.0.0.1
export RM_DB_PORT=5432
export RM_DB_NAME=rolemodel
export RM_DB_USER=rolemodel
export RM_DB_PASSWORD=rolemodel
export RM_DB_SCHEMA=public
```

Optional app settings:

```bash
export RM_APP_HOST=127.0.0.1
export RM_APP_PORT=8000
```

GigaChat settings (for intent parsing and static instruction answer synthesis):

```bash
# Certificate authentication is the default deployment path.
# Put files into the project directory:
#   certs/gigachat/egress_sberca.crt  - client certificate
#   certs/gigachat/egress_sberca.key  - client private key
#   certs/gigachat/ca.pem             - optional CA bundle
#
# If you keep them elsewhere, set explicit absolute paths:
# export RM_GIGACHAT_CERT_FILE="/absolute/path/to/egress_sberca.crt"
# export RM_GIGACHAT_KEY_FILE="/absolute/path/to/egress_sberca.key"
# export RM_GIGACHAT_CA_BUNDLE="/absolute/path/to/ca.pem"

export RM_GIGACHAT_CHAT_MODEL=GigaChat-2-Max
export RM_GIGACHAT_BASE_URL=https://gigachat-ift.sberdevices.delta.sbrf.ru/v1
export RM_GIGACHAT_TIMEOUT_SEC=45
export RM_GIGACHAT_VERIFY_SSL=false
export RM_GIGACHAT_USE_FOR_INTENT=true
export RM_GIGACHAT_USE_FOR_INSTRUCTION_ANSWER=true
```

## ETL

Initialize schema:

```bash
/usr/bin/python3 -m rolemodel_etl db.init
```

Validate workbook:

```bash
/usr/bin/python3 -m rolemodel_etl validate --file "Doc/ЦРМ_ПЦП_ЦКРР_(ролевая).xlsx"
```

Load workbook into PostgreSQL:

```bash
/usr/bin/python3 -m rolemodel_etl load --file "Doc/ЦРМ_ПЦП_ЦКРР_(ролевая).xlsx" --snapshot-label "initial"
```

## Static instruction

Instruction answers use `Doc/static_instruction.txt`. Upload a new instruction through the UI button
`Загрузить инструкцию` or through the API:

```bash
curl -X POST \
  -H "X-File-Name: instruction.rtf" \
  --data-binary "@/absolute/path/to/instruction.rtf" \
  http://127.0.0.1:8000/api/v1/admin/instruction/upload
```

Supported upload formats are `.rtf` and `.txt`. The uploaded file is preserved under
`~/rolemodel_instruction_uploads` by default, and the active static text is replaced at
`Doc/static_instruction.txt`.

## Backend

Start the API server:

```bash
/usr/bin/python3 -m app
```

## Corporate Linux server install

From your local machine, after downloading and extracting the GitHub ZIP, run:

```bash
bash scripts/deploy_rolemodel_helper_remote.sh
```

This uploads the extracted checkout to
`CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru` over SSH and then runs
the server-side installer there. The default remote directory is
`~/RoleModelHelper2`.
Before upload, the deploy script downloads Linux Python wheels into
`.rolemodel_wheelhouse` and sends them with the app, so the app server does not
need outbound access to PyPI during installation.

If you are already logged in to the app server and the ZIP is extracted there,
run only the server-side installer:

```bash
bash scripts/install_rolemodel_helper_server.sh
```

The installer defaults to external PostgreSQL `10.135.162.149:5433`, database
`bdtest`, schema `rolemodel_helper`, database user `CI09479675-pg-travinov`,
and application port `8000`. It asks for the database password with hidden input
and does not require local container runtime or the PostgreSQL `vector` extension.
During installation it checks whether the configured PostgreSQL schema already
contains all required tables, initializes missing objects, and verifies the
schema again before loading the workbook.
Runtime system search uses DBA-managed PostgreSQL `pg_trgm` functions from the
extension schema. The default extension schema is `ext`; override it with
`RM_PG_TRGM_SCHEMA` if DBA installs `pg_trgm` elsewhere. The app calls
`<schema>.similarity(text, text)` directly and does not require `pg_trgm` to be
installed in the application schema.

For a full overwrite at the current setup stage, use:

```bash
bash scripts/deploy_rolemodel_helper_remote.sh --reset-db
```

`--reset-db` drops the configured schema `rolemodel_helper` with `CASCADE`,
recreates it, and loads the bundled workbook from scratch.

To update only the application code without touching PostgreSQL, run:

```bash
bash scripts/update_rolemodel_helper_app_remote.sh
```

This app-only update does not ask for the database password, does not run schema
initialization, and does not validate or load the workbook. It preserves the
remote `.env.server`, `logs/`, `uploads/`, `backups/`, and `certs/` directories,
including `certs/gigachat/egress_sberca.crt` and
`certs/gigachat/egress_sberca.key`.

When the app runs on the remote server, `127.0.0.1` means "localhost on that
server". From your local machine, open it through an SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
```

Then open the minimal chat UI locally:

```text
http://127.0.0.1:8000/
```

Main endpoints:

- `POST /api/v1/chat/sessions`
- `POST /api/v1/chat/sessions/{session_id}/messages`
- `GET /api/v1/chat/sessions/{session_id}`
- `POST /api/v1/admin/systems/aliases`
- `POST /api/v1/admin/instruction/upload`
- `GET /api/v1/health`

## Tests

Run all tests:

```bash
/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"
```

Run the combined successful-dialogue quality benchmark on a deployed server:

```bash
set -a
source .env.server
set +a

.venv/bin/python tests/run_dialogue_benchmark.py \
  --base-url http://127.0.0.1:8000 \
  --combined-success-fixture \
  --db-evidence \
  --report reports/dialogue_quality/combined_success_report.json \
  --strict-exit
```

Session selection:
- first 10 chats: add `--session-limit 10`
- first 20 chats: add `--session-limit 20`
- all chats: omit `--session-limit` and `--random-session-limit`
- random 10 chats: add `--random-session-limit 10 --random-seed 42`

On the deployed server, use the bundled wrapper:

```bash
bash scripts/run_dialogue_quality_benchmark.sh 10
bash scripts/run_dialogue_quality_benchmark.sh 20
bash scripts/run_dialogue_quality_benchmark.sh all
bash scripts/run_dialogue_quality_benchmark.sh random10
```

Output:
- stdout prints suite name, success rate, consecutive-session result, quality-gate result, critical failure summary, and report path.
- the JSON report contains selected/fixture session counts, selection mode, per-session and per-turn results, failures, and DB evidence from `tool_call_log` / `chat_turn_interpretation` when `--db-evidence` is enabled.
