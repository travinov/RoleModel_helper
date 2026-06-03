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
# Use either RM_GIGACHAT_AUTH_KEY or RM_GIGACHAT_CLIENT_ID + RM_GIGACHAT_CLIENT_SECRET.
export RM_GIGACHAT_AUTH_KEY="<basic auth key>"
# export RM_GIGACHAT_CLIENT_ID="<client id>"
# export RM_GIGACHAT_CLIENT_SECRET="<client secret>"

export RM_GIGACHAT_SCOPE=GIGACHAT_API_PERS
export RM_GIGACHAT_CHAT_MODEL=GigaChat-2-Pro
export RM_GIGACHAT_AUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
export RM_GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
export RM_GIGACHAT_TIMEOUT_SEC=45
export RM_GIGACHAT_VERIFY_SSL=true
# export RM_GIGACHAT_CA_BUNDLE="/absolute/path/to/ca.pem"
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
`rolemodel_etl load` builds application-owned `search_document` and
`search_ngram` tables, so runtime search does not require `pg_trgm`,
`gin_trgm_ops`, or `EXECUTE` grants on PostgreSQL trigram functions.

For a full overwrite at the current setup stage, use:

```bash
bash scripts/deploy_rolemodel_helper_remote.sh --reset-db
```

`--reset-db` drops the configured schema `rolemodel_helper` with `CASCADE`,
recreates it, and loads the bundled workbook from scratch.

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
