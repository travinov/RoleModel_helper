# Инструкция: Проверка Качества Диалогов На Корпоративном Сервере

Эта инструкция описывает полный рабочий сценарий:

1. скачать ZIP-дистрибутив на локальный компьютер;
2. развернуть обновление приложения на корпоративный сервер без перетирания БД;
3. подключиться к серверу;
4. запустить проверочный скрипт на нужном количестве диалогов;
5. проверить результат;
6. скачать JSON-отчет на локальный компьютер.

## Важные Ограничения

БД уже содержит данные. В этом сценарии нельзя запускать команды, которые сбрасывают схему или заново загружают workbook.

Не запускать:

```bash
bash scripts/deploy_rolemodel_helper_remote.sh --reset-db
bash scripts/install_rolemodel_helper_server.sh --reset-db
python -m rolemodel_etl load
```

Использовать только app-only обновление:

```bash
bash scripts/update_rolemodel_helper_app_remote.sh
```

Оно сохраняет на сервере:

```text
.env.server
.venv/
logs/
reports/
uploads/
backups/
certs/
```

## 1. Скачать ZIP На Локальный Компьютер

Терминал: локальный компьютер, не SSH-сессия сервера.

```bash
cd ~/Downloads
curl -L "https://github.com/travinov/RoleModel_helper/archive/refs/heads/codex/dialogue-quality-distribution-20260611.zip" -o rolemodel_helper_quality.zip
```

Проверить, что ZIP скачался:

```bash
ls -lh ~/Downloads/rolemodel_helper_quality.zip
```

## 2. Распаковать ZIP В Локальную Папку Поставки

Терминал: локальный компьютер.

Если рабочая локальная папка поставки:

```text
/Users/travinov-sv/SBRF/Агентные решение/PythonProject/RoleModelHelper2
```

то распакуйте ZIP с заменой файлов в эту папку.

После распаковки проверить, что в локальной папке есть обновленный проверочный скрипт:

```bash
cd "/Users/travinov-sv/SBRF/Агентные решение/PythonProject/RoleModelHelper2"
grep -n "dialogue-benchmark" tests/run_dialogue_benchmark.py | head
ls -l scripts/run_dialogue_quality_benchmark.sh
```

Ожидаемо должны быть строки с `dialogue-benchmark`, а файл `scripts/run_dialogue_quality_benchmark.sh` должен существовать.

## 3. Залить Обновление На Сервер

Терминал: локальный компьютер.

Запустить из локальной папки поставки:

```bash
cd "/Users/travinov-sv/SBRF/Агентные решение/PythonProject/RoleModelHelper2"
bash scripts/update_rolemodel_helper_app_remote.sh
```

Дождаться завершения. В конце должны быть строки вида:

```text
[rolemodel-update] Application update finished
[rolemodel-update] Health check on server: curl -s http://127.0.0.1:8000/api/v1/health
```

Этот шаг не трогает БД: не делает reset schema, не запускает workbook load и не спрашивает пароль БД.

## 4. Подключиться К Серверу

Терминал: локальный компьютер.

```bash
ssh CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
```

Дальше команды выполняются уже в SSH-сессии на сервере.

Перейти в каталог приложения:

```bash
cd ~/RoleModelHelper2
pwd
```

Ожидаемый путь:

```text
/home/CI09479675-lnx-travinov/RoleModelHelper2
```

## 5. Проверить, Что Обновление На Сервере Есть

Терминал: SSH-сессия сервера.

```bash
grep -n "dialogue-benchmark" tests/run_dialogue_benchmark.py | head
ls -l scripts/run_dialogue_quality_benchmark.sh
```

Ожидаемо:

- `grep` показывает строку с `[dialogue-benchmark]`;
- файл `scripts/run_dialogue_quality_benchmark.sh` существует.

## 6. Проверить, Что Приложение Работает

Терминал: SSH-сессия сервера.

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

Ожидаемо:

```json
{"status":"ok","gigachat_enabled":true,"gigachat_intent":true,"gigachat_instruction_answer":true}
```

## 7. Запустить Проверку На 100 Диалогах

Терминал: SSH-сессия сервера.

```bash
cd ~/RoleModelHelper2
bash scripts/run_dialogue_quality_benchmark.sh 100
```

Параметр `100` означает: проверить первые 100 диалогов из fixture.

Во время выполнения в терминале должны появляться строки:

```text
[dialogue-quality] base_url=http://127.0.0.1:8000
[dialogue-quality] mode=100
[dialogue-quality] report=reports/dialogue_quality/combined_success_report.first100.json
[dialogue-benchmark] Selected 100/241 sessions: {"mode": "first", "limit": 100}
[dialogue-benchmark] Session 1/100 start: ...
[dialogue-benchmark] Session 1/100 turn 1/... sending request
```

Если строки `[dialogue-benchmark]` не появляются, значит на сервер не попала новая версия скрипта.

## 8. Проверить, Что Отчет Создан

Терминал: SSH-сессия сервера.

```bash
ls -lh reports/dialogue_quality/combined_success_report.first100.json
```

Ожидаемо: файл существует и имеет ненулевой размер.

## 9. Посмотреть Краткую Сводку На Сервере

Терминал: SSH-сессия сервера.

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

p = Path("reports/dialogue_quality/combined_success_report.first100.json")
r = json.loads(p.read_text(encoding="utf-8"))

print("suite_name:", r.get("suite_name"))
print("selected_session_count:", r.get("selected_session_count"))
print("session_selection:", r.get("session_selection"))
print("checks_total:", r.get("checks_total"))
print("checks_passed:", r.get("checks_passed"))
print("actual_success_rate_percent:", r.get("actual_success_rate_percent"))
print("meets_quality_gate:", r.get("meets_quality_gate"))
print("critical_failures_summary:", r.get("critical_failures_summary"))

failed = [s for s in r.get("sessions", []) if not s.get("meets_target")]
print("failed_sessions:", len(failed))
for s in failed[:20]:
    print("-", s.get("name"), "session_id=", s.get("session_id"), "rate=", s.get("actual_success_rate_percent"), "critical=", s.get("critical_hits"))
PY
```

## 10. Скачать Отчет На Локальный Компьютер

Терминал: локальный компьютер, не SSH-сессия сервера.

```bash
scp 'CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru:/home/CI09479675-lnx-travinov/RoleModelHelper2/reports/dialogue_quality/combined_success_report.first100.json' ~/Downloads/
```

Проверить локальный файл:

```bash
ls -lh ~/Downloads/combined_success_report.first100.json
```

После этого файл можно отправить на анализ:

```text
~/Downloads/combined_success_report.first100.json
```

## Параметры Проверочного Скрипта

Команда запуска всегда выполняется на сервере из каталога `~/RoleModelHelper2`:

```bash
bash scripts/run_dialogue_quality_benchmark.sh <mode>
```

Доступные режимы:

```bash
bash scripts/run_dialogue_quality_benchmark.sh 10
bash scripts/run_dialogue_quality_benchmark.sh 20
bash scripts/run_dialogue_quality_benchmark.sh 100
bash scripts/run_dialogue_quality_benchmark.sh all
bash scripts/run_dialogue_quality_benchmark.sh random10
bash scripts/run_dialogue_quality_benchmark.sh random 100 42
```

Что означают параметры:

- `10`: первые 10 диалогов, отчет `combined_success_report.first10.json`.
- `20`: первые 20 диалогов, отчет `combined_success_report.first20.json`.
- `100`: первые 100 диалогов, отчет `combined_success_report.first100.json`.
- `all`: все диалоги, отчет `combined_success_report.all.json`.
- `random10`: случайные 10 диалогов с seed `42`, отчет `combined_success_report.random10.json`.
- `random 100 42`: случайные 100 диалогов с seed `42`, отчет `combined_success_report.random100.json`.

## Где Лежат Отчеты На Сервере

```text
/home/CI09479675-lnx-travinov/RoleModelHelper2/reports/dialogue_quality/
```

Посмотреть список:

```bash
cd ~/RoleModelHelper2
ls -lh reports/dialogue_quality/
```
