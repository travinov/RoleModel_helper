# Dialogue Quality Benchmark Runbook

This runbook describes the app-only deployment and dialogue quality benchmark flow for the corporate server.

## Scope

- Distribution source: GitHub ZIP from branch `codex/dialogue-quality-distribution-20260611`.
- Server app directory: `/home/CI09479675-lnx-travinov/RoleModelHelper2`.
- App server: `CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru`.
- Benchmark report directory on server: `~/RoleModelHelper2/reports/dialogue_quality`.
- Database must not be reset or reloaded during this flow.

Do not run:

```bash
bash scripts/deploy_rolemodel_helper_remote.sh --reset-db
bash scripts/install_rolemodel_helper_server.sh --reset-db
python -m rolemodel_etl load
```

## 1. Download ZIP On Local Computer

Run on the local working computer, not inside the server SSH session:

```bash
cd ~/Downloads
curl -L "https://github.com/travinov/RoleModel_helper/archive/refs/heads/codex/dialogue-quality-distribution-20260611.zip" -o rolemodel_helper_quality.zip
```

Unpack the ZIP and replace/update the local working copy used for deployment, for example:

```bash
unzip -o ~/Downloads/rolemodel_helper_quality.zip -d ~/Downloads/rolemodel_helper_quality
```

If you maintain a fixed local deployment directory, copy the unpacked files into it before the next step.

## 2. Upload App-Only Update To Server

Run from the local project directory that contains `scripts/update_rolemodel_helper_app_remote.sh`:

```bash
cd "/Users/travinov-sv/SBRF/Агентные решение/PythonProject/RoleModelHelper2"
bash scripts/update_rolemodel_helper_app_remote.sh
```

The update script preserves server-side `.env.server`, `.venv/`, `logs/`, `reports/`, `uploads/`, `backups/`, and `certs/`. It does not ask for the database password, does not initialize schema, does not reset schema, and does not load the workbook.

## 3. Connect To Server And Verify App

Run:

```bash
ssh CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
cd ~/RoleModelHelper2
curl -s http://127.0.0.1:8000/api/v1/health
```

Expected: JSON with `"status":"ok"` and GigaChat flags enabled.

## 4. Run Benchmark On 100 Dialogues

Run on the server:

```bash
cd ~/RoleModelHelper2
bash scripts/run_dialogue_quality_benchmark.sh 100
```

The script loads `.env.server`, creates `reports/dialogue_quality`, runs the combined successful-dialogue fixture with DB evidence enabled, and writes:

```text
reports/dialogue_quality/combined_success_report.first100.json
```

During execution the terminal should show progress lines starting with:

```text
[dialogue-benchmark]
```

## 5. Check Report On Server

Run on the server:

```bash
ls -lh reports/dialogue_quality/combined_success_report.first100.json
```

Optional quick summary:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

p = Path("reports/dialogue_quality/combined_success_report.first100.json")
r = json.loads(p.read_text(encoding="utf-8"))
print("selected_session_count:", r.get("selected_session_count"))
print("actual_success_rate_percent:", r.get("actual_success_rate_percent"))
print("meets_quality_gate:", r.get("meets_quality_gate"))
print("critical_failures_summary:", r.get("critical_failures_summary"))
print("failed_sessions:", sum(1 for s in r.get("sessions", []) if not s.get("meets_target")))
PY
```

## 6. Download Report To Local Computer

Run on the local working computer, not inside the server SSH session:

```bash
scp 'CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru:/home/CI09479675-lnx-travinov/RoleModelHelper2/reports/dialogue_quality/combined_success_report.first100.json' ~/Downloads/
```

Verify:

```bash
ls -lh ~/Downloads/combined_success_report.first100.json
```

## Other Run Modes

Run on the server from `~/RoleModelHelper2`:

```bash
bash scripts/run_dialogue_quality_benchmark.sh 10
bash scripts/run_dialogue_quality_benchmark.sh 20
bash scripts/run_dialogue_quality_benchmark.sh all
bash scripts/run_dialogue_quality_benchmark.sh random10
bash scripts/run_dialogue_quality_benchmark.sh random 100 42
```

Report names:

- `10` -> `combined_success_report.first10.json`
- `20` -> `combined_success_report.first20.json`
- `100` -> `combined_success_report.first100.json`
- `all` -> `combined_success_report.all.json`
- `random10` -> `combined_success_report.random10.json`
- `random 100 42` -> `combined_success_report.random100.json`
