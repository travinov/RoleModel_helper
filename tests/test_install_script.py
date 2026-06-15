from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "install_rolemodel_helper_server.sh"
REMOTE_DEPLOY_SCRIPT_PATH = REPO_ROOT / "scripts" / "deploy_rolemodel_helper_remote.sh"
REMOTE_APP_UPDATE_SCRIPT_PATH = REPO_ROOT / "scripts" / "update_rolemodel_helper_app_remote.sh"
QUALITY_BENCHMARK_SCRIPT_PATH = REPO_ROOT / "scripts" / "run_dialogue_quality_benchmark.sh"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"


class InstallScriptTest(unittest.TestCase):
    def test_ci_server_installer_exists_and_uses_external_db_defaults(self) -> None:
        self.assertTrue(SCRIPT_PATH.exists(), "missing ZIP-contained server installer")
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("10.135.162.149", script)
        self.assertIn("5433", script)
        self.assertIn("bdtest", script)
        self.assertIn("rolemodel_helper", script)
        self.assertIn("CI09479675-pg-travinov", script)
        self.assertIn("tsles-assai0001.esrt.sber.ru", script)
        self.assertIn("CI09479675-lnx-travinov", script)
        self.assertIn("rolemodel_etl db.init", script)
        self.assertIn("rolemodel_etl validate", script)
        self.assertIn("rolemodel_etl load", script)
        self.assertIn("--reset-db", script)
        self.assertIn("RM_WHEELHOUSE_DIR", script)
        self.assertIn("--no-index", script)
        self.assertIn("--find-links", script)
        self.assertIn("printf 'RM_DB_HOST=%s", script)
        self.assertNotIn("printf 'export RM_DB_HOST=%s", script)
        self.assertIn("DROP SCHEMA IF EXISTS", script)
        self.assertIn("information_schema.tables", script)
        self.assertIn("missing_tables", script)
        self.assertIn("python -m app", script)
        self.assertNotIn("docker compose up", script)
        self.assertNotIn("|Cyt;p22hhA*[b.kFXhWn&+8", script)

    def test_ci_server_installer_is_bash_syntax_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT_PATH)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_deploy_script_runs_installer_on_app_server(self) -> None:
        self.assertTrue(REMOTE_DEPLOY_SCRIPT_PATH.exists(), "missing local-to-server deploy script")
        script = REMOTE_DEPLOY_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru", script)
        self.assertIn("RoleModelHelper2", script)
        self.assertIn("ssh", script)
        self.assertIn("tar", script)
        self.assertIn("scripts/install_rolemodel_helper_server.sh", script)
        self.assertIn("10.135.162.149", script)
        self.assertIn("5433", script)
        self.assertIn("bdtest", script)
        self.assertIn("rolemodel_helper", script)
        self.assertIn("CI09479675-pg-travinov", script)
        self.assertIn("RM_DB_PASSWORD=$(cat)", script)
        self.assertIn("--reset-db", script)
        self.assertIn("pip download", script)
        self.assertIn("--platform", script)
        self.assertIn("manylinux2014_x86_64", script)
        self.assertIn(".rolemodel_wheelhouse", script)
        self.assertNotIn("docker compose up", script)
        self.assertNotIn("|Cyt;p22hhA*[b.kFXhWn&+8", script)

    def test_remote_deploy_script_is_bash_syntax_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(REMOTE_DEPLOY_SCRIPT_PATH)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_app_update_script_does_not_touch_database(self) -> None:
        self.assertTrue(REMOTE_APP_UPDATE_SCRIPT_PATH.exists(), "missing app-only update script")
        script = REMOTE_APP_UPDATE_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru", script)
        self.assertIn("RoleModelHelper2", script)
        self.assertIn("scripts/update_rolemodel_helper_app_remote.sh", script)
        self.assertIn("systemctl --user restart rolemodel-helper.service", script)
        self.assertIn("certs/gigachat", script)
        self.assertIn("reports/", script)
        self.assertIn("mkdir -p reports/dialogue_quality", script)
        self.assertIn(".env.server", script)
        self.assertIn("pip install", script)
        self.assertIn("--no-index", script)
        self.assertIn("--find-links", script)
        self.assertNotIn("RM_DB_PASSWORD", script)
        self.assertNotIn("rolemodel_etl db.init", script)
        self.assertNotIn("rolemodel_etl load", script)
        self.assertNotIn("DROP SCHEMA", script)

    def test_remote_app_update_script_is_bash_syntax_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(REMOTE_APP_UPDATE_SCRIPT_PATH)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dialogue_quality_benchmark_script_exists_and_supports_modes(self) -> None:
        self.assertTrue(QUALITY_BENCHMARK_SCRIPT_PATH.exists(), "missing server-side dialogue quality benchmark script")
        script = QUALITY_BENCHMARK_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("tests/run_dialogue_benchmark.py", script)
        self.assertIn("--combined-success-fixture", script)
        self.assertIn("--db-evidence", script)
        self.assertIn("--session-limit", script)
        self.assertIn("--random-session-limit", script)
        self.assertIn("all", script)
        self.assertIn("reports/dialogue_quality", script)
        self.assertIn("source .env.server", script)

    def test_dialogue_quality_benchmark_script_is_bash_syntax_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(QUALITY_BENCHMARK_SCRIPT_PATH)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python39_offline_requirements_include_marker_dependencies(self) -> None:
        requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8")

        self.assertIn("exceptiongroup", requirements)


if __name__ == "__main__":
    unittest.main()
