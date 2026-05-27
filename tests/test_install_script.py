from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "install_rolemodel_helper_server.sh"


class InstallScriptTest(unittest.TestCase):
    def test_ci_server_installer_exists_and_uses_external_db_defaults(self) -> None:
        self.assertTrue(SCRIPT_PATH.exists(), "missing ZIP-contained server installer")
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("10.135.162.149", script)
        self.assertIn("5433", script)
        self.assertIn("bdtest", script)
        self.assertIn("rolemodel_helper", script)
        self.assertIn("CI09479675-pg-travinov", script)
        self.assertIn("tvles-assai0001.esrt.sber.ru", script)
        self.assertIn("CI09479675-lnx-travinov", script)
        self.assertIn("rolemodel_etl db.init", script)
        self.assertIn("rolemodel_etl validate", script)
        self.assertIn("rolemodel_etl load", script)
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


if __name__ == "__main__":
    unittest.main()
