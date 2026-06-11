from __future__ import annotations

import os
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agent.scenario_services import InstructionService
from app.config import AppConfig
from app.services.instruction_answer import InstructionAnswerUnavailableError, StaticInstructionAnswerService
from rolemodel_etl.config import DBConfig


def _test_config() -> AppConfig:
    return AppConfig(
        db=DBConfig(
            host="localhost",
            port=5432,
            dbname="rolemodel",
            user="rolemodel",
            password="rolemodel",
        ),
        gigachat_use_for_instruction_answer=False,
    )


class DummyAgent:
    pass


class DummySearchRepository:
    def __init__(self, config: AppConfig) -> None:
        self.config = config


def _build_test_client(config: AppConfig) -> TestClient:
    sys.modules.pop("app.api.server", None)
    with patch("rolemodel_etl.loader.init_db"), patch("app.agent.service.ChatAgent", return_value=DummyAgent()):
        server = importlib.import_module("app.api.server")
    server.init_db = lambda db_config: None
    server.ChatAgent = lambda app_config, instruction_answer_service=None: DummyAgent()
    server.SearchRepository = DummySearchRepository
    return TestClient(server.build_app(config))


class StaticInstructionUploadTestCase(unittest.TestCase):
    def test_upload_rtf_converts_to_static_instruction_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            static_path = Path(tmpdir) / "static_instruction.txt"
            upload_dir = Path(tmpdir) / "uploads"
            env = {
                "RM_STATIC_INSTRUCTION_PATH": str(static_path),
                "RM_INSTRUCTION_UPLOAD_DIR": str(upload_dir),
            }
            with patch.dict(os.environ, env, clear=False):
                client = _build_test_client(_test_config())
                response = client.post(
                    "/api/v1/admin/instruction/upload",
                    content=(
                        r"{\rtf1\ansi\uc0 "
                        r"\u1055 \u1088 \u1080 \u1074 \u1077 \u1090  "
                        r"\u1080 \u1085 \u1089 \u1090 \u1088 \u1091 \u1082 \u1094 \u1080 \u1103 "
                        "\\\n"
                        r"1. \u1064 \u1072 \u1075 "
                        r"}"
                    ).encode("utf-8"),
                    headers={"X-File-Name": "instruction.rtf", "Content-Type": "application/rtf"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "SUCCESS")
            self.assertEqual(Path(payload["static_instruction_path"]), static_path)
            self.assertTrue(Path(payload["uploaded_file"]).exists())
            self.assertGreater(payload["text_length"], 0)
            saved_text = static_path.read_text(encoding="utf-8")
            self.assertIn("Привет инструкция", saved_text)
            self.assertIn("\n1. Шаг", saved_text)

    def test_upload_rejects_empty_body_and_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            static_path = Path(tmpdir) / "static_instruction.txt"
            env = {
                "RM_STATIC_INSTRUCTION_PATH": str(static_path),
                "RM_INSTRUCTION_UPLOAD_DIR": str(Path(tmpdir) / "uploads"),
            }
            with patch.dict(os.environ, env, clear=False):
                client = _build_test_client(_test_config())
                empty = client.post(
                    "/api/v1/admin/instruction/upload",
                    content=b"",
                    headers={"X-File-Name": "instruction.txt"},
                )
                wrong_ext = client.post(
                    "/api/v1/admin/instruction/upload",
                    content=b"text",
                    headers={"X-File-Name": "instruction.docx"},
                )

            self.assertEqual(empty.status_code, 400)
            self.assertEqual(wrong_ext.status_code, 400)
            self.assertFalse(static_path.exists())


class StaticInstructionServiceTestCase(unittest.TestCase):
    def test_static_instruction_pack_uses_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            static_path = Path(tmpdir) / "static_instruction.txt"
            static_path.write_text("Шаг 1. Откройте Мои доступы.\nШаг 2. Выберите ролевую модель.", encoding="utf-8")
            with patch.dict(os.environ, {"RM_STATIC_INSTRUCTION_PATH": str(static_path)}, clear=False):
                config = _test_config()
                config.gigachat_use_for_instruction_answer = True
                config.gigachat_access_token = "test-token"
                service = StaticInstructionAnswerService(config)
                service.gigachat.chat_completion = lambda *args, **kwargs: "Откройте Мои доступы [1]."
                pack = service.load_instruction_pack()
                answer = service.answer_from_static_instruction("как получить доступ")

        self.assertIsNotNone(pack)
        assert pack is not None
        self.assertEqual(pack["title"], "Статичная инструкция")
        self.assertEqual(pack["sections_count"], 1)
        self.assertGreaterEqual(len(pack["sections"]), 1)
        self.assertIsNotNone(answer)
        assert answer is not None
        self.assertEqual(answer["instruction_mode"], "INLINE_DOC")
        self.assertIn("Мои доступы", answer["summary_text"])
        self.assertEqual(answer["citations"][0].source_title, "Статичная инструкция")

    def test_instruction_answer_does_not_fallback_when_gigachat_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            static_path = Path(tmpdir) / "static_instruction.txt"
            static_path.write_text("Шаг 1. Откройте Мои доступы.", encoding="utf-8")
            with patch.dict(os.environ, {"RM_STATIC_INSTRUCTION_PATH": str(static_path)}, clear=False):
                service = StaticInstructionAnswerService(_test_config())
                with self.assertRaises(InstructionAnswerUnavailableError):
                    service.answer_from_static_instruction("как получить доступ")

    def test_instruction_service_returns_clear_message_when_static_instruction_missing(self) -> None:
        class MissingStaticInstruction:
            def answer_from_static_instruction(self, query_text: str, context=None) -> None:
                return None

        class SearchRepo:
            def log_tool_call(self, *args, **kwargs) -> None:
                raise AssertionError("tool calls must not be logged for static instruction lookup")

        result = InstructionService(SearchRepo(), MissingStaticInstruction()).answer(
            session_id="s1",
            state={},
            raw_text="как получить доступ",
            references_pending_question=False,
            resolved_system_name=None,
        )

        self.assertEqual(result.instruction_mode, "INLINE_DOC")
        self.assertEqual(result.answer.answer_type, "INSTRUCTION_LOOKUP")
        self.assertIn("Инструкция не загружена", result.answer.summary_text)
        self.assertEqual(result.answer.citations, [])

    def test_ui_has_dedicated_instruction_upload_control(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "app" / "ui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="upload-instruction"', html)
        self.assertIn('id="upload-instruction-input"', html)
        self.assertIn('accept=".rtf,.txt"', html)
        self.assertIn('/admin/instruction/upload', html)


if __name__ == "__main__":
    unittest.main()
