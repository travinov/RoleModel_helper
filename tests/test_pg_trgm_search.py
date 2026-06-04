from __future__ import annotations

import re
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from app.config import AppConfig


REPO_ROOT = Path(__file__).resolve().parents[1]


class PgTrgmSearchTest(unittest.TestCase):
    def test_config_defaults_to_dba_extension_schema(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig.from_env()

        self.assertEqual(config.pg_trgm_schema, "ext")

    def test_runtime_system_search_uses_pg_trgm_similarity(self) -> None:
        repository = (REPO_ROOT / "app" / "repositories" / "search_repository.py").read_text(encoding="utf-8")

        self.assertIn("pg_trgm_similarity", repository)
        self.assertIn("{similarity}(ar.alias_normalized", repository)
        self.assertIn("{similarity}(lower(s.system_name_raw)", repository)
        self.assertNotIn("search_document", repository)
        self.assertNotIn("search_ngram", repository)
        self.assertNotIn("build_search_ngrams", repository)
        self.assertNotIn("app_similarity", repository)

    def test_schema_does_not_create_application_search_index_or_pg_trgm_extension(self) -> None:
        schema = (REPO_ROOT / "rolemodel_etl" / "sql" / "schema.sql").read_text(encoding="utf-8").lower()

        self.assertNotIn("create extension if not exists pg_trgm", schema)
        self.assertNotIn("create table if not exists search_document", schema)
        self.assertNotIn("create table if not exists search_ngram", schema)
        self.assertNotIn("create or replace function app_similarity", schema)

    def test_loader_no_longer_rebuilds_application_search_index(self) -> None:
        loader = (REPO_ROOT / "rolemodel_etl" / "loader.py").read_text(encoding="utf-8")

        self.assertNotIn("rebuild_system_search_index", loader)

    def test_no_runtime_pg_trgm_percent_operator_requirement(self) -> None:
        repository = (REPO_ROOT / "app" / "repositories" / "search_repository.py").read_text(encoding="utf-8")

        self.assertIsNone(re.search(r"\s%%\s%s", repository))


if __name__ == "__main__":
    unittest.main()
