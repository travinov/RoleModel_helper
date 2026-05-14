from __future__ import annotations

import unittest

from rolemodel_etl.parser import parse_access_value, parse_header_cell


class ParseAccessValueTestCase(unittest.TestCase):
    def test_parse_level_without_comment(self) -> None:
        parsed = parse_access_value("1")
        self.assertIsNotNone(parsed)
        level, comment, raw = parsed  # type: ignore[misc]
        self.assertEqual(level, 1)
        self.assertIsNone(comment)
        self.assertEqual(raw, "1")

    def test_parse_level_with_comment(self) -> None:
        parsed = parse_access_value("2 Проведение оценки финансового положения")
        self.assertIsNotNone(parsed)
        level, comment, raw = parsed  # type: ignore[misc]
        self.assertEqual(level, 2)
        self.assertEqual(comment, "Проведение оценки финансового положения")
        self.assertEqual(raw, "2 Проведение оценки финансового положения")

    def test_parse_empty_returns_none(self) -> None:
        self.assertIsNone(parse_access_value(""))
        self.assertIsNone(parse_access_value(None))

    def test_parse_invalid_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_access_value("ABC")


class ParseHeaderCellTestCase(unittest.TestCase):
    def test_parse_header_role(self) -> None:
        header_type, header_name = parse_header_cell("Роли:\nРиск-менеджер")
        self.assertEqual(header_type, "Роли")
        self.assertEqual(header_name, "Риск-менеджер")

    def test_parse_header_polnomochiya_example(self) -> None:
        header_type, header_name = parse_header_cell(
            "Полномочия:\nФА Заместитель руководителя подразделения риск менеджеров"
        )
        self.assertEqual(header_type, "Полномочия")
        self.assertEqual(header_name, "ФА Заместитель руководителя подразделения риск менеджеров")

    def test_parse_header_polnomochiya_single_line(self) -> None:
        header_type, header_name = parse_header_cell(
            "Полномочия: ФА Заместитель руководителя подразделения риск менеджеров"
        )
        self.assertEqual(header_type, "Полномочия")
        self.assertEqual(header_name, "ФА Заместитель руководителя подразделения риск менеджеров")

    def test_parse_header_vpn_example(self) -> None:
        header_type, header_name = parse_header_cell(
            "Доступ через VPN:\nЕРМ.ЦКР SberHelp Доступ через VPN"
        )
        self.assertEqual(header_type, "Доступ через VPN")
        self.assertEqual(header_name, "ЕРМ.ЦКР SberHelp Доступ через VPN")

    def test_parse_header_vpn_single_line(self) -> None:
        header_type, header_name = parse_header_cell(
            "Доступ через VPN: ЕРМ.ЦКР SberHelp Доступ через VPN"
        )
        self.assertEqual(header_type, "Доступ через VPN")
        self.assertEqual(header_name, "ЕРМ.ЦКР SberHelp Доступ через VPN")

    def test_parse_header_reason(self) -> None:
        header_type, header_name = parse_header_cell("Обоснование:\n")
        self.assertEqual(header_type, "Обоснование")
        self.assertEqual(header_name, "")


if __name__ == "__main__":
    unittest.main()
