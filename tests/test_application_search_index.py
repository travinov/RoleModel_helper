from __future__ import annotations

import re
import unittest
from pathlib import Path

from rolemodel_etl.search_index import build_search_ngrams


REPO_ROOT = Path(__file__).resolve().parents[1]


class ApplicationSearchIndexTest(unittest.TestCase):
    def test_ngram_generation_is_distinct_and_padded(self) -> None:
        grams = build_search_ngrams("ЦКРР")

        self.assertEqual(len(grams), len(set(grams)))
        self.assertIn("  ц", grams)
        self.assertIn("цкр", grams)
        self.assertIn("крр", grams)
        self.assertIn("рр ", grams)

    def test_schema_uses_application_search_index_tables(self) -> None:
        schema = (REPO_ROOT / "rolemodel_etl" / "sql" / "schema.sql").read_text(encoding="utf-8").lower()

        self.assertIn("create table if not exists search_document", schema)
        self.assertIn("create table if not exists search_ngram", schema)
        self.assertIn("idx_search_document_snapshot_entity", schema)
        self.assertIn("idx_search_ngram_gram", schema)
        self.assertNotIn("create extension if not exists pg_trgm", schema)
        self.assertNotIn("create or replace function app_similarity", schema)
        self.assertNotIn("gin_trgm_ops", schema)

    def test_loader_rebuilds_index_after_aliases_are_seeded(self) -> None:
        loader = (REPO_ROOT / "rolemodel_etl" / "loader.py").read_text(encoding="utf-8")

        self.assertIn("from .search_index import rebuild_system_search_index", loader)
        self.assertGreater(loader.find("rebuild_system_search_index(cursor, snapshot_id)"), loader.find("_seed_reference_alias_candidates(cursor, snapshot_id)"))

    def test_runtime_system_search_uses_index_not_similarity_function(self) -> None:
        repository = (REPO_ROOT / "app" / "repositories" / "search_repository.py").read_text(encoding="utf-8")

        self.assertIn("search_document", repository)
        self.assertIn("search_ngram", repository)
        self.assertIn("common_grams", repository)
        self.assertNotIn("app_similarity", repository)
        self.assertIsNone(re.search(r"(?<!_)similarity\(ar\.alias_normalized", repository))
        self.assertIsNone(re.search(r"(?<!_)similarity\(lower\(s\.system_name_raw\)", repository))
        self.assertNotIn(" %% %s", repository)

    def test_manual_alias_upsert_refreshes_search_index(self) -> None:
        repository = (REPO_ROOT / "app" / "repositories" / "search_repository.py").read_text(encoding="utf-8")

        upsert_alias = repository[repository.index("def upsert_alias") : repository.index("def resolve_system_candidates")]
        self.assertIn("INSERT INTO search_document", upsert_alias)
        self.assertIn("INSERT INTO search_ngram", upsert_alias)
        self.assertIn("build_search_ngrams(alias_norm)", upsert_alias)


if __name__ == "__main__":
    unittest.main()
