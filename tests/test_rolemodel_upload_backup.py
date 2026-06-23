from __future__ import annotations

import importlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import AppConfig
from rolemodel_etl.config import DBConfig


class DummyAgent:
    pass


class DummySearchRepository:
    def __init__(self, config: AppConfig) -> None:
        self.config = config


def _load_server_module():
    sys.modules.pop("app.api.server", None)
    with patch("rolemodel_etl.loader.init_db"), patch("app.agent.service.ChatAgent", return_value=DummyAgent()):
        server = importlib.import_module("app.api.server")
    server.init_db = lambda db_config: None
    server.ChatAgent = lambda app_config, instruction_answer_service=None: DummyAgent()
    server.SearchRepository = DummySearchRepository
    return server


class RoleModelBackupPgDumpTestCase(unittest.TestCase):
    def test_resolve_pg_dump_path_uses_explicit_env_path(self) -> None:
        server = _load_server_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            pg_dump = Path(tmpdir) / "pg_dump"
            pg_dump.write_text("#!/bin/sh\n", encoding="utf-8")
            pg_dump.chmod(pg_dump.stat().st_mode | stat.S_IXUSR)

            with patch.dict(os.environ, {"RM_PG_DUMP_PATH": str(pg_dump)}, clear=False):
                with patch("app.api.server.shutil.which", return_value=None):
                    self.assertEqual(server._resolve_pg_dump_path(), str(pg_dump))

    def test_resolve_pg_dump_path_uses_bundled_vendor_path_before_path_lookup(self) -> None:
        server = _load_server_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundled = root / "vendor" / "pgsql-client-el9-x86_64" / "bin" / "pg_dump"
            bundled.parent.mkdir(parents=True)
            bundled.write_text("#!/bin/sh\n", encoding="utf-8")
            bundled.chmod(bundled.stat().st_mode | stat.S_IXUSR)

            with patch("app.api.server.PROJECT_ROOT", root):
                with patch.dict(os.environ, {}, clear=True):
                    with patch("app.api.server.shutil.which", return_value="/usr/bin/pg_dump"):
                        self.assertEqual(server._resolve_pg_dump_path(), str(bundled))

    def test_missing_pg_dump_error_mentions_env_and_vendor_path(self) -> None:
        server = _load_server_module()
        config = AppConfig(
            db=DBConfig(
                host="localhost",
                port=5432,
                dbname="rolemodel",
                user="rolemodel",
                password="rolemodel",
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.api.server.PROJECT_ROOT", Path(tmpdir)):
                with patch.dict(os.environ, {}, clear=True):
                    with patch("app.api.server.shutil.which", return_value=None):
                        with self.assertRaises(server.HTTPException) as ctx:
                            server._backup_database(config)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("RM_PG_DUMP_PATH", ctx.exception.detail)
        self.assertIn("vendor/pgsql-client-el9-x86_64/bin/pg_dump", ctx.exception.detail)

    def test_backup_dumps_only_configured_application_schema(self) -> None:
        server = _load_server_module()
        config = AppConfig(
            db=DBConfig(
                host="db.example",
                port=5433,
                dbname="bdtest",
                user="rolemodel_user",
                password="secret",
                schema="rolemodel_helper",
            )
        )
        completed = server.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            pg_dump = Path(tmpdir) / "pg_dump"
            pg_dump.write_text("#!/bin/sh\n", encoding="utf-8")
            pg_dump.chmod(pg_dump.stat().st_mode | stat.S_IXUSR)
            backup_dir = Path(tmpdir) / "backups"

            def fake_run(cmd, **_kwargs):
                Path(cmd[cmd.index("-f") + 1]).write_bytes(b"dump")
                return completed

            with patch.dict(os.environ, {"RM_DB_BACKUP_DIR": str(backup_dir)}, clear=False):
                with patch("app.api.server._resolve_pg_dump_path", return_value=str(pg_dump)):
                    with patch("app.api.server.subprocess.run", side_effect=fake_run) as run:
                        backup_path = server._backup_database(config)

        cmd = run.call_args.args[0]
        self.assertIn("-n", cmd)
        self.assertEqual(cmd[cmd.index("-n") + 1], "rolemodel_helper")
        self.assertEqual(cmd[-1], "bdtest")
        self.assertTrue(str(backup_path).startswith(str(backup_dir)))


if __name__ == "__main__":
    unittest.main()
