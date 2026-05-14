from __future__ import annotations

import unittest
from pathlib import Path

from rolemodel_etl.parser import parse_workbook


class ParserIntegrationTestCase(unittest.TestCase):
    def test_parse_known_workbook_counts(self) -> None:
        workbook = (
            Path(__file__).resolve().parents[1]
            / "Doc"
            / "ЦРМ_ПЦП_ЦКРР_(ролевая).xlsx"
        )
        if not workbook.exists():
            self.skipTest(f"Workbook not found: {workbook}")

        parsed = parse_workbook(workbook)

        self.assertEqual(parsed.sheet_name, "Полная форма")
        self.assertEqual(len(parsed.profiles), 70)
        self.assertEqual(len(parsed.systems), 36)
        self.assertEqual(len(parsed.entitlements), 131)
        self.assertEqual(parsed.access_count, 882)
        self.assertEqual(parsed.justifications_count, 365)
        self.assertEqual(parsed.errors_count, 0)


if __name__ == "__main__":
    unittest.main()

