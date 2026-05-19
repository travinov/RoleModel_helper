from __future__ import annotations

import unittest
from datetime import date

from app.services.dialogue_export import build_dialogues_markdown


class DialogueExportMarkdownTestCase(unittest.TestCase):
    def test_builds_markdown_with_feedback_and_messages(self) -> None:
        content = build_dialogues_markdown(
            sessions=[
                {
                    "session_id": "abc12345-0000-0000-0000-000000000000",
                    "created_at": "2026-05-15T09:00:00+03:00",
                    "updated_at": "2026-05-15T09:02:00+03:00",
                    "status": "ACTIVE",
                    "feedback": [
                        {
                            "rating": "DOWN",
                            "comment": "Ожидал список ролей",
                            "created_at": "2026-05-15T09:03:00+03:00",
                        }
                    ],
                    "state": {
                        "system_raw": "АС Тест",
                        "position_raw": "Риск-менеджер",
                        "city_raw": "Москва",
                        "department_raw": "Отдел",
                        "active_goal": "ROLE_DISCOVERY",
                    },
                    "messages": [
                        {
                            "role": "ASSISTANT",
                            "created_at": "2026-05-15T09:00:00+03:00",
                            "message_text": "Опишите вопрос",
                        },
                        {
                            "role": "USER",
                            "created_at": "2026-05-15T09:01:00+03:00",
                            "message_text": "Какие роли?",
                        },
                    ],
                }
            ],
            date_from=date(2026, 5, 15),
            date_to=date(2026, 5, 18),
        )

        self.assertIn("# Диалоги за 2026-05-15 - 2026-05-18", content)
        self.assertIn("Всего сессий: 1", content)
        self.assertIn("Оценки: DOWN: 1", content)
        self.assertIn("`DOWN` `2026-05-15 09:03:00`: Ожидал список ролей", content)
        self.assertIn("Последний контекст: АС: АС Тест", content)
        self.assertIn("**Пользователь** `2026-05-15 09:01:00`", content)
        self.assertIn("Какие роли?", content)


if __name__ == "__main__":
    unittest.main()
