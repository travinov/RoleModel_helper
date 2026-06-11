from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app.models.domain import RetrievedChunk
from app.services.gigachat import GigaChatClient
from app.services.static_instruction import STATIC_INSTRUCTION_TITLE, read_static_instruction, static_instruction_path
from app.services.text import normalize_text, similarity

from ..config import AppConfig


class InstructionAnswerUnavailableError(RuntimeError):
    """Raised when instruction answer synthesis requires GigaChat but it is unavailable."""


class StaticInstructionAnswerService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.gigachat = GigaChatClient(config)
        self._pack_cache: dict[str, dict] = {}

    def clear_cache(self) -> None:
        self._pack_cache.clear()

    def load_instruction_pack(self, file_path: Optional[str] = None) -> Optional[dict]:
        candidate_path = Path(file_path) if file_path else static_instruction_path()
        text = read_static_instruction(candidate_path)
        if not text:
            return None

        cache_key = f"{candidate_path.resolve()}:{candidate_path.stat().st_mtime_ns}"
        cached = self._pack_cache.get(cache_key)
        if cached:
            return cached

        pack = {
            "title": STATIC_INSTRUCTION_TITLE,
            "file_path": str(candidate_path),
            "sections_count": 1,
            "text_length": len(text),
            "sections": [
                {
                    "section_no": 1,
                    "chunk_text": text,
                    "citation_label": STATIC_INSTRUCTION_TITLE,
                    "locator_text": "статичный текст",
                }
            ],
        }
        self._pack_cache[cache_key] = pack
        return pack

    def answer_from_static_instruction(
        self,
        query_text: str,
        context: Optional[dict] = None,
    ) -> Optional[dict]:
        pack = self.load_instruction_pack()
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

        selected = self._static_instruction_chunks(pack)
        if not selected:
            selected = self._select_sections(pack, retrieval_query)
        if not selected:
            selected = self._select_sections(pack, query_text)
        if not selected:
            return None

        summary_text = self._answer_with_gigachat(query_text, selected)
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

    def _select_sections(self, pack: dict, query_text: str) -> list[RetrievedChunk]:
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
            section_no = int(section["section_no"])
            scored.append(
                RetrievedChunk(
                    chunk_id=-section_no,
                    source_id=0,
                    source_title=pack["title"],
                    slide_no=None,
                    chunk_type="STATIC_TEXT",
                    chunk_text=chunk_text,
                    score=round(score, 4),
                    citation_label=section["citation_label"],
                    locator_text=section["locator_text"],
                )
            )
        return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:3]

    def _answer_with_gigachat(self, query_text: str, selected: list[RetrievedChunk]) -> Optional[str]:
        if not (self.gigachat.enabled and self.config.gigachat_use_for_instruction_answer):
            raise InstructionAnswerUnavailableError("GigaChat instruction answer synthesis is unavailable")
        citations_block = "\n".join(
            f"[{index}] {chunk.citation_label}: {chunk.chunk_text}"
            for index, chunk in enumerate(selected, start=1)
        )
        system_prompt = (
            "Ты отвечаешь на вопрос по короткой инструкции. "
            "Используй только предоставленный текст. "
            "Не выдумывай шаги. Если инструкция не покрывает вопрос, скажи это прямо. "
            "Сформулируй короткий ответ по-русски и добавь ссылки вида [1], [2] там, где используешь текст."
        )
        user_prompt = (
            f"Вопрос: {query_text}\n"
            f"Текст инструкции:\n{citations_block}\n"
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
        except Exception as exc:
            raise InstructionAnswerUnavailableError("GigaChat instruction answer synthesis failed") from exc
        cleaned = re.sub(r"\s+\n", "\n", str(text or "")).strip()
        if not cleaned:
            raise InstructionAnswerUnavailableError("GigaChat instruction answer synthesis returned empty text")
        return cleaned
