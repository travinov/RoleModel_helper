from __future__ import annotations

import unittest
from pathlib import Path

from app.config import AppConfig
from app.rag.pptx_extractor import extract_pptx_document
from app.rag.service import RagService
from rolemodel_etl.config import DBConfig


class RagPptxExtractionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AppConfig(
            db=DBConfig(
                host="localhost",
                port=5432,
                dbname="rolemodel",
                user="rolemodel",
                password="rolemodel",
            )
        )
        self.presentation = (
            Path(__file__).resolve().parents[1]
            / "Doc"
            / "Памятка по работе с ролевой моделью Риск-менеджера.pptx"
        )
        if not self.presentation.exists():
            self.skipTest(f"Presentation not found: {self.presentation}")

    def test_extract_pptx_document(self) -> None:
        extracted = extract_pptx_document(
            self.presentation,
            tesseract_cmd=self.config.tesseract_cmd,
            tesseract_langs=self.config.tesseract_langs,
        )
        self.assertEqual(extracted["document_metadata"]["slides_count"], 5)
        self.assertEqual(len(extracted["slides"]), 5)
        image_count = sum(len(slide["images"]) for slide in extracted["slides"])
        self.assertEqual(image_count, 3)
        self.assertIn("Что такое ролевая модель", extracted["slides"][1]["slide_text"])

    def test_build_fragments_and_chunks(self) -> None:
        service = RagService(self.config)
        extracted = extract_pptx_document(
            self.presentation,
            tesseract_cmd=self.config.tesseract_cmd,
            tesseract_langs=self.config.tesseract_langs,
        )
        fragments, chunks = service._build_fragments_and_chunks(extracted)
        self.assertGreaterEqual(len(fragments), 8)
        self.assertGreaterEqual(len(chunks), 8)
        self.assertEqual(len({chunk["slide_no"] for chunk in chunks if chunk.get("slide_no")}), 5)

    def test_small_doc_inline_pack_is_available_for_presentation(self) -> None:
        service = RagService(self.config)
        pack = service.load_inline_instruction_pack(file_path=str(self.presentation))
        self.assertIsNotNone(pack)
        assert pack is not None
        self.assertEqual(pack["slides_count"], 5)
        self.assertLessEqual(pack["text_length"], 15000)
        self.assertGreaterEqual(len(pack["sections"]), 5)


if __name__ == "__main__":
    unittest.main()
