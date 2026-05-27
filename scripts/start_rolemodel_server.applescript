-- RoleModel Helper: start backend server with local environment
-- Usage:
--   osascript "/Volumes/SSD APFS/Python Project/RoleModel_helper/scripts/start_rolemodel_server.applescript"

set projectDir to "/Volumes/SSD APFS/Python Project/RoleModel_helper"
set appPort to "8011"

set launchCmd to ""
set launchCmd to launchCmd & "cd " & quoted form of projectDir & "; "
set launchCmd to launchCmd & "if [ ! -f .venv/bin/python ]; then /usr/bin/python3 -m venv .venv; fi; "
set launchCmd to launchCmd & "source .venv/bin/activate; "
set launchCmd to launchCmd & "if [ -f .env ]; then set -a; source .env; set +a; else echo '.env not found in project root'; fi; "
set launchCmd to launchCmd & "export RM_APP_PORT=" & appPort & "; "
set launchCmd to launchCmd & "echo 'Starting RoleModel server on http://127.0.0.1:" & appPort & "'; "
set launchCmd to launchCmd & "python -m app"

tell application "Terminal"
	activate
	do script launchCmd
end tell
