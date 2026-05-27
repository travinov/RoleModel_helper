from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from app.models.domain import RetrievedChunk
from app.repositories.rag_repository import RagRepository
from app.services.embeddings import embed_text
from app.services.gigachat import GigaChatClient
from app.services.static_instruction import STATIC_INSTRUCTION_TITLE, read_static_instruction, static_instruction_path
from app.services.text import normalize_text, similarity

from ..config import AppConfig
from .pptx_extractor import extract_pptx_document


class RagService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.repository = RagRepository(config)
        self.gigachat = GigaChatClient(config)
        self._inline_pack_cache: dict[str, dict] = {}

    def _source_hash(self, file_path: str | Path) -> str:
        path = Path(file_path)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def register_source(
        self,
        title: str,
        file_path: str,
        source_type: str = "PPTX",
        system_id: Optional[int] = None,
    ) -> dict:
        return self.repository.create_source(
            title=title,
            file_path=file_path,
            source_type=source_type.upper(),
            source_hash=self._source_hash(file_path),
            system_id=system_id,
        )

    def inspect_source(self, source_id: int) -> Optional[dict]:
        source = self.repository.get_source(source_id)
        if not source:
            return None
        chunks = self.repository.list_source_chunks(source_id)
        source["chunks_count"] = len(chunks)
        if chunks:
            source["slides_count"] = len({chunk["slide_no"] for chunk in chunks if chunk.get("slide_no") is not None})
        else:
            source["slides_count"] = 0
        return source

    def _chunk_slide_text(self, slide_no: int, text: str) -> list[str]:
        if not text:
            return []
        if self.gigachat.enabled and self.config.gigachat_use_for_chunking:
            llm_chunks = self._chunk_slide_text_with_llm(slide_no, text)
            if llm_chunks:
                return llm_chunks
        normalized = re.sub(r"\s+", " ", text).strip()
        parts = re.split(r"(?=(?:^|\s)\d+(?:\.\d+)?\s*[.)]?\s)", normalized)
        chunks: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) <= 700:
                chunks.append(part)
                continue
            sentences = re.split(r"(?<=[.!?])\s+", part)
            current = ""
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(current) + len(sentence) + 1 > 700 and current:
                    chunks.append(current.strip())
                    current = sentence
                else:
                    current = (current + " " + sentence).strip()
            if current:
                chunks.append(current.strip())
        return chunks

    def _chunk_slide_text_with_llm(self, slide_no: int, text: str) -> Optional[list[str]]:
        system_prompt = (
            "Ты сервис чанкинга инструкций. "
            "Разбей текст одного слайда на смысловые chunks и верни строго JSON: "
            "{\"chunks\": [\"...\", \"...\"]}. "
            "Правила: не смешивай разные шаги, не добавляй новую информацию, "
            "ориентир длины 300-700 символов, минимум 80 символов если возможно."
        )
        user_prompt = (
            f"Номер слайда: {slide_no}\n"
            f"Текст слайда:\n{text}\n"
            "Верни JSON."
        )
        try:
            payload = self.gigachat.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self.config.gigachat_chunk_model,
                max_tokens=1200,
            )
        except Exception:
            return None
        if not payload:
            return None
        candidate = payload.get("chunks")
        if not isinstance(candidate, list):
            return None
        clean_chunks: list[str] = []
        for item in candidate:
            text_value = re.sub(r"\s+", " ", str(item or "")).strip()
            if len(text_value) >= 20:
                clean_chunks.append(text_value)
        if not clean_chunks:
            return None
        return clean_chunks

    def _build_fragments_and_chunks(self, extracted: dict) -> tuple[list[dict], list[dict]]:
        fragments: list[dict] = []
        chunks: list[dict] = []
        fragment_no = 0
        chunk_no = 0
        for slide in extracted["slides"]:
            slide_no = slide["slide_no"]
            slide_text = slide["slide_text"]
            if slide_text:
                fragment_no += 1
                fragments.append(
                    {
                        "fragment_no": fragment_no,
                        "fragment_type": "SLIDE_TEXT",
                        "slide_no": slide_no,
                        "fragment_text": slide_text,
                        "fragment_metadata": {"heading": slide_text.split(" ", 12)[:12]},
                    }
                )
                for text_chunk in self._chunk_slide_text(slide_no, slide_text):
                    chunk_no += 1
                    chunks.append(
                        {
                            "fragment_no": fragment_no,
                            "chunk_no": chunk_no,
                            "slide_no": slide_no,
                            "chunk_type": "SLIDE_TEXT",
                            "chunk_text": text_chunk,
                            "chunk_tokens_est": max(1, len(text_chunk.split())),
                            "chunk_metadata": {"source_path": extracted["document_metadata"]["file_path"]},
                            "embedding": embed_text(text_chunk, self.config.embedding_dim),
                            "citation_label": f"{extracted['document_title']}, слайд {slide_no}",
                            "locator_text": f"слайд {slide_no}",
                        }
                    )
            for image in slide["images"]:
                if image["ocr_text"]:
                    fragment_no += 1
                    fragments.append(
                        {
                            "fragment_no": fragment_no,
                            "fragment_type": "SLIDE_IMAGE_OCR",
                            "slide_no": slide_no,
                            "fragment_text": image["ocr_text"],
                            "fragment_metadata": {"target": image["target"], "image_index": image["image_index"]},
                        }
                    )
                    chunk_no += 1
                    chunks.append(
                        {
                            "fragment_no": fragment_no,
                            "chunk_no": chunk_no,
                            "slide_no": slide_no,
                            "chunk_type": "SLIDE_IMAGE_OCR",
                            "chunk_text": image["ocr_text"],
                            "chunk_tokens_est": max(1, len(image["ocr_text"].split())),
                            "chunk_metadata": {"target": image["target"], "image_index": image["image_index"]},
                            "embedding": embed_text(image["ocr_text"], self.config.embedding_dim),
                            "citation_label": f"{extracted['document_title']}, слайд {slide_no}",
                            "locator_text": f"слайд {slide_no}, изображение {image['image_index']}",
                        }
                    )
                fragment_no += 1
                fragments.append(
                    {
                        "fragment_no": fragment_no,
                        "fragment_type": "SLIDE_IMAGE_SUMMARY",
                        "slide_no": slide_no,
                        "fragment_text": image["summary_text"],
                        "fragment_metadata": {"target": image["target"], "image_index": image["image_index"]},
                    }
                )
                chunk_no += 1
                chunks.append(
                    {
                        "fragment_no": fragment_no,
                        "chunk_no": chunk_no,
                        "slide_no": slide_no,
                        "chunk_type": "SLIDE_IMAGE_SUMMARY",
                        "chunk_text": image["summary_text"],
                        "chunk_tokens_est": max(1, len(image["summary_text"].split())),
                        "chunk_metadata": {"target": image["target"], "image_index": image["image_index"]},
                        "embedding": embed_text(image["summary_text"], self.config.embedding_dim),
                        "citation_label": f"{extracted['document_title']}, слайд {slide_no}",
                        "locator_text": f"слайд {slide_no}, изображение {image['image_index']}",
                    }
                )
        return fragments, chunks

    def ingest_source(
        self,
        source_id: Optional[int] = None,
        file_path: Optional[str] = None,
        title: Optional[str] = None,
        source_type: str = "PPTX",
        system_id: Optional[int] = None,
    ) -> dict:
        if source_id is None:
            if not file_path or not title:
                raise ValueError("file_path and title are required when source_id is not provided")
            source = self.register_source(title=title, file_path=file_path, source_type=source_type, system_id=system_id)
            source_id = source["id"]
        source = self.repository.get_source(source_id)
        if not source:
            raise ValueError(f"RAG source {source_id} was not found")

        ingest_run_id = self.repository.create_ingest_run(source_id)
        self.repository.mark_source_status(source_id, "INGESTING")
        try:
            if source["source_type"] != "PPTX":
                raise ValueError(f"Unsupported source type: {source['source_type']}")
            extracted = extract_pptx_document(
                file_path=source["file_path"],
                tesseract_cmd=self.config.tesseract_cmd,
                tesseract_langs=self.config.tesseract_langs,
            )
            fragments, chunks = self._build_fragments_and_chunks(extracted)
            result = self.repository.replace_document(
                source_id=source_id,
                document_title=source["title"],
                document_metadata=extracted["document_metadata"],
                fragments=fragments,
                chunks=chunks,
            )
            self.repository.mark_ingest_run(
                ingest_run_id=ingest_run_id,
                status="SUCCESS",
                slides_processed=len(extracted["slides"]),
                fragments_created=result["fragments_created"],
                chunks_created=result["chunks_created"],
                errors_count=0,
            )
            self.repository.mark_source_status(source_id, "READY")
            return {
                "source_id": source_id,
                "status": "READY",
                "slides_processed": len(extracted["slides"]),
                "fragments_created": result["fragments_created"],
                "chunks_created": result["chunks_created"],
            }
        except Exception as exc:
            self.repository.add_ingest_error(
                ingest_run_id=ingest_run_id,
                stage="INGEST",
                error_code=exc.__class__.__name__,
                error_message=str(exc),
            )
            self.repository.mark_ingest_run(
                ingest_run_id=ingest_run_id,
                status="FAILED",
                slides_processed=0,
                fragments_created=0,
                chunks_created=0,
                errors_count=1,
            )
            self.repository.mark_source_status(source_id, "FAILED", last_error=str(exc))
            raise

    def search_instructions(self, query_text: str, top_k: Optional[int] = None) -> list[RetrievedChunk]:
        rows = self.repository.search_chunks(
            embedding=embed_text(query_text, self.config.embedding_dim),
            query_text=query_text,
            top_k=top_k or self.config.rag_top_k,
        )
        return [
            RetrievedChunk(
                chunk_id=int(row["chunk_id"]),
                source_id=int(row["source_id"]),
                source_title=row["source_title"],
                slide_no=row["slide_no"],
                chunk_type=row["chunk_type"],
                chunk_text=row["chunk_text"],
                score=float(row["score"]),
                citation_label=row["citation_label"],
                locator_text=row["locator_text"],
            )
            for row in rows
        ]

    def clear_inline_instruction_cache(self) -> None:
        self._inline_pack_cache.clear()

    def load_inline_instruction_pack(
        self,
        source_id: Optional[int] = None,
        file_path: Optional[str] = None,
    ) -> Optional[dict]:
        if source_id is not None:
            return None
        candidate_path = Path(file_path) if file_path else static_instruction_path()
        text = read_static_instruction(candidate_path)
        if not text:
            return None

        cache_key = f"{candidate_path.resolve()}:{candidate_path.stat().st_mtime_ns}"
        cached = self._inline_pack_cache.get(cache_key)
        if cached:
            return cached
        pack = {
            "title": STATIC_INSTRUCTION_TITLE,
            "file_path": str(candidate_path),
            "slides_count": 1,
            "text_length": len(text),
            "sections": [
                {
                    "slide_no": 1,
                    "chunk_text": text,
                    "citation_label": STATIC_INSTRUCTION_TITLE,
                    "locator_text": "статичный текст",
                }
            ],
        }
        self._inline_pack_cache[cache_key] = pack
        return pack

    def answer_from_inline_doc(
        self,
        query_text: str,
        context: Optional[dict] = None,
    ) -> Optional[dict]:
        pack = self.load_inline_instruction_pack()
        if not pack:
            return None

        context = context or {}
        retrieval_query = " ".join(
            part.strip()
            for part in (
                query_text,
                str(context.get("system_name") or ""),
                str(context.get("intent_type") or ""),
            )
            if str(part or "").strip()
        ).strip()
        if pack.get("title") == STATIC_INSTRUCTION_TITLE:
            selected = self._static_instruction_chunks(pack)
        else:
            selected = self._select_inline_sections(pack, retrieval_query)
        if not selected:
            selected = self._select_inline_sections(pack, query_text)
        if not selected:
            return None

        summary_text = self._answer_inline_with_gigachat(query_text, selected)
        if not summary_text:
            summary_text = "\n".join(chunk.chunk_text for chunk in selected)
        return {
            "instruction": summary_text,
            "citations": selected,
            "summary_text": summary_text,
            "instruction_mode": "INLINE_DOC",
        }

    def _static_instruction_chunks(self, pack: dict) -> list[RetrievedChunk]:
        chunks: list[RetrievedChunk] = []
        for section in pack.get("sections") or []:
            chunks.append(
                RetrievedChunk(
                    chunk_id=-1,
                    source_id=0,
                    source_title=pack["title"],
                    slide_no=None,
                    chunk_type="STATIC_TEXT",
                    chunk_text=str(section["chunk_text"]),
                    score=1.0,
                    citation_label=str(section["citation_label"]),
                    locator_text=str(section["locator_text"]),
                )
            )
        return chunks

    def answer_with_rag(self, query_text: str, retrieved_chunks: list[RetrievedChunk], answer_style: str = "concise") -> dict:
        if not retrieved_chunks:
            return {
                "instruction": None,
                "citations": [],
                "summary_text": "Подходящая инструкция не найдена в загруженных документах.",
            }
        selected = retrieved_chunks[: min(3, len(retrieved_chunks))]
        summary_text = self._answer_with_gigachat(query_text, selected)
        if not summary_text:
            lines = []
            for index, chunk in enumerate(selected, start=1):
                prefix = f"{index}. " if answer_style == "steps" else ""
                lines.append(f"{prefix}{chunk.chunk_text}")
            summary_text = "\n".join(lines)
        return {
            "instruction": summary_text,
            "citations": selected,
            "summary_text": summary_text,
        }

    def _select_inline_sections(self, pack: dict, query_text: str) -> list[RetrievedChunk]:
        query_norm = normalize_text(query_text)
        if not query_norm:
            return []

        query_tokens = {token for token in query_norm.split() if len(token) >= 3}
        scored: list[RetrievedChunk] = []
        for section in pack.get("sections") or []:
            chunk_text = str(section["chunk_text"])
            chunk_norm = normalize_text(chunk_text)
            chunk_tokens = {token for token in chunk_norm.split() if len(token) >= 3}
            token_overlap = (
                len(query_tokens & chunk_tokens) / float(len(query_tokens))
                if query_tokens
                else 0.0
            )
            score = max(similarity(query_text, chunk_text), token_overlap)
            if score < 0.12:
                continue
            scored.append(
                RetrievedChunk(
                    chunk_id=-int(section["slide_no"]),
                    source_id=0,
                    source_title=pack["title"],
                    slide_no=int(section["slide_no"]),
                    chunk_type="INLINE_SLIDE",
                    chunk_text=chunk_text,
                    score=round(score, 4),
                    citation_label=section["citation_label"],
                    locator_text=section["locator_text"],
                )
            )
        return sorted(scored, key=lambda item: (-item.score, item.slide_no or 0))[:3]

    def _answer_inline_with_gigachat(self, query_text: str, selected: list[RetrievedChunk]) -> Optional[str]:
        if not (self.gigachat.enabled and self.config.gigachat_use_for_rag_answer):
            return None
        citations_block = "\n".join(
            f"[{index}] {chunk.citation_label}: {chunk.chunk_text}"
            for index, chunk in enumerate(selected, start=1)
        )
        system_prompt = (
            "Ты отвечаешь на вопрос по короткой инструкции. "
            "Используй только предоставленные фрагменты. "
            "Не выдумывай шаги. Если инструкция не покрывает вопрос, скажи это прямо. "
            "Сформулируй короткий ответ по-русски и добавь ссылки вида [1], [2] там, где используешь фрагменты."
        )
        user_prompt = (
            f"Вопрос: {query_text}\n"
            f"Фрагменты инструкции:\n{citations_block}\n"
            "Ответ:"
        )
        try:
            text = self.gigachat.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.config.gigachat_chat_model,
                temperature=0.1,
                max_tokens=700,
            )
        except Exception:
            return None
        cleaned = re.sub(r"\s+\n", "\n", str(text or "")).strip()
        return cleaned or None

    def _answer_with_gigachat(self, query_text: str, selected: list[RetrievedChunk]) -> Optional[str]:
        if not (self.gigachat.enabled and self.config.gigachat_use_for_rag_answer):
            return None
        evidence_lines = []
        for index, chunk in enumerate(selected, start=1):
            evidence_lines.append(
                f"[{index}] {chunk.citation_label}\n"
                f"{chunk.chunk_text}"
            )
        system_prompt = (
            "Ты помощник по инструкциям доступа. "
            "Отвечай только по предоставленным фрагментам. "
            "Если данных недостаточно, честно скажи об этом. "
            "Добавь ссылки вида [1], [2] в конце релевантных шагов."
        )
        user_prompt = (
            f"Вопрос пользователя:\n{query_text}\n\n"
            "Фрагменты:\n"
            + "\n\n".join(evidence_lines)
        )
        try:
            content = self.gigachat.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.config.gigachat_chat_model,
                temperature=0.1,
                max_tokens=700,
            )
        except Exception:
            return None
        normalized = content.strip()
        if not normalized:
            return None
        return normalized
