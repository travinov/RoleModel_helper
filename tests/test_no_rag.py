from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class NoRagSurfaceTest(unittest.TestCase):
    def test_database_schema_does_not_require_vector_or_create_rag_tables(self) -> None:
        schema = (REPO_ROOT / "rolemodel_etl" / "sql" / "schema.sql").read_text(encoding="utf-8").lower()

        self.assertNotIn("create extension if not exists vector", schema)
        self.assertIsNone(re.search(r"\bvector\s*\(", schema))
        self.assertNotIn("::vector", schema)
        self.assertNotIn("rag_", schema)
        self.assertNotIn("vector_cosine_ops", schema)

    def test_active_application_code_has_no_rag_modules_or_admin_endpoints(self) -> None:
        self.assertFalse((REPO_ROOT / "app" / "rag").exists())
        self.assertFalse((REPO_ROOT / "app" / "repositories" / "rag_repository.py").exists())
        self.assertFalse((REPO_ROOT / "app" / "services" / "embeddings.py").exists())

        active_files = [
            *list((REPO_ROOT / "app").rglob("*.py")),
            REPO_ROOT / "README.md",
        ]
        for path in active_files:
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("admin/rag", text, str(path))
            self.assertNotIn("ragservice", text, str(path))
            self.assertNotIn("ragrepository", text, str(path))
            self.assertNotIn("pgvector", text, str(path))

    def test_corporate_archive_has_no_docker_dependency(self) -> None:
        self.assertFalse((REPO_ROOT / "docker-compose.yml").exists())

        files = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "start_rolemodel_server.command",
            REPO_ROOT / "scripts" / "start_rolemodel_server.applescript",
            REPO_ROOT / "scripts" / "install_rolemodel_helper_server.sh",
        ]
        for path in files:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("docker", text, str(path))
            self.assertNotIn("docker-compose", text, str(path))
            self.assertNotIn("docker compose", text, str(path))


if __name__ == "__main__":
    unittest.main()
