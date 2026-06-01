from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "install_rolemodel_helper_server.sh"
REMOTE_DEPLOY_SCRIPT_PATH = REPO_ROOT / "scripts" / "deploy_rolemodel_helper_remote.sh"
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

    def test_python39_offline_requirements_include_marker_dependencies(self) -> None:
        requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8")

        self.assertIn("exceptiongroup", requirements)


if __name__ == "__main__":
    unittest.main()
