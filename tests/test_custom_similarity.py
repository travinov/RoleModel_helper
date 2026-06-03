from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CustomSimilarityTest(unittest.TestCase):
    def test_schema_installs_application_owned_similarity_function(self) -> None:
        schema = (REPO_ROOT / "rolemodel_etl" / "sql" / "schema.sql").read_text(encoding="utf-8").lower()

        self.assertIn("create or replace function app_similarity", schema)
        self.assertNotIn("create extension if not exists pg_trgm", schema)

    def test_system_search_sql_does_not_call_pg_trgm_runtime_functions(self) -> None:
        repository = (REPO_ROOT / "app" / "repositories" / "search_repository.py").read_text(encoding="utf-8")

        self.assertIn("app_similarity(ar.alias_normalized", repository)
        self.assertIn("app_similarity(lower(s.system_name_raw)", repository)
        self.assertIn("app_similarity(coalesce(ar.alias_normalized, '')", repository)
        self.assertIsNone(re.search(r"(?<!_)similarity\(ar\.alias_normalized", repository))
        self.assertIsNone(re.search(r"(?<!_)similarity\(lower\(s\.system_name_raw\)", repository))
        self.assertIsNone(re.search(r"(?<!_)similarity\(coalesce\(ar\.alias_normalized, ''\)", repository))
        self.assertNotIn(" %% %s", repository)


if __name__ == "__main__":
    unittest.main()
