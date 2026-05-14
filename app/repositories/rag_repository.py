from __future__ import annotations

from typing import Any, Optional

from psycopg2.extras import Json

from app.services.embeddings import vector_literal

from ..config import AppConfig
from ..db import db_cursor


class RagRepository:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def create_source(
        self,
        title: str,
        file_path: str,
        source_type: str,
        source_hash: str,
        system_id: Optional[int] = None,
    ) -> dict[str, Any]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO rag_source (title, file_path, source_type, source_hash, system_id, status)
                VALUES (%s, %s, %s, %s, %s, 'NEW')
                RETURNING *
                """,
                (title, file_path, source_type, source_hash, system_id),
            )
            return dict(cursor.fetchone())

    def get_source(self, source_id: int) -> Optional[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute("SELECT * FROM rag_source WHERE id = %s", (source_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_source_chunks(self, source_id: int) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                SELECT rc.*, rs.title AS source_title
                FROM rag_source rs
                JOIN rag_document rd ON rd.source_id = rs.id
                JOIN rag_chunk rc ON rc.document_id = rd.id
                WHERE rs.id = %s
                ORDER BY rc.chunk_no
                """,
                (source_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def mark_source_status(self, source_id: int, status: str, last_error: Optional[str] = None) -> None:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                UPDATE rag_source
                SET status = %s, updated_at = now(), last_error = %s
                WHERE id = %s
                """,
                (status, last_error, source_id),
            )

    def create_ingest_run(self, source_id: int) -> int:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO rag_ingest_run (source_id, status)
                VALUES (%s, 'RUNNING')
                RETURNING id
                """,
                (source_id,),
            )
            return int(cursor.fetchone()["id"])

    def mark_ingest_run(
        self,
        ingest_run_id: int,
        status: str,
        slides_processed: int,
        fragments_created: int,
        chunks_created: int,
        errors_count: int,
    ) -> None:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                UPDATE rag_ingest_run
                SET status = %s,
                    finished_at = now(),
                    slides_processed = %s,
                    fragments_created = %s,
                    chunks_created = %s,
                    errors_count = %s
                WHERE id = %s
                """,
                (status, slides_processed, fragments_created, chunks_created, errors_count, ingest_run_id),
            )

    def add_ingest_error(
        self,
        ingest_run_id: int,
        stage: str,
        error_code: str,
        error_message: str,
        slide_no: Optional[int] = None,
        fragment_ref: Optional[str] = None,
        raw_context: Optional[str] = None,
    ) -> None:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO rag_ingest_error (
                    ingest_run_id, stage, slide_no, fragment_ref, error_code, error_message, raw_context
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (ingest_run_id, stage, slide_no, fragment_ref, error_code, error_message, raw_context),
            )

    def replace_document(
        self,
        source_id: int,
        document_title: str,
        document_metadata: dict[str, Any],
        fragments: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
    ) -> dict[str, int]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute("DELETE FROM rag_document WHERE source_id = %s", (source_id,))
            cursor.execute(
                """
                INSERT INTO rag_document (source_id, document_title, document_metadata)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (source_id, document_title, Json(document_metadata)),
            )
            document_id = int(cursor.fetchone()["id"])
            fragment_id_map: dict[int, int] = {}
            for fragment in fragments:
                cursor.execute(
                    """
                    INSERT INTO rag_fragment (
                        document_id, fragment_no, fragment_type, slide_no, fragment_text, fragment_metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        document_id,
                        fragment["fragment_no"],
                        fragment["fragment_type"],
                        fragment.get("slide_no"),
                        fragment["fragment_text"],
                        Json(fragment.get("fragment_metadata", {})),
                    ),
                )
                fragment_id_map[fragment["fragment_no"]] = int(cursor.fetchone()["id"])

            for chunk in chunks:
                fragment_id = fragment_id_map.get(chunk.get("fragment_no"))
                cursor.execute(
                    """
                    INSERT INTO rag_chunk (
                        document_id, fragment_id, chunk_no, slide_no, chunk_type, chunk_text,
                        chunk_tokens_est, chunk_metadata, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    RETURNING id
                    """,
                    (
                        document_id,
                        fragment_id,
                        chunk["chunk_no"],
                        chunk.get("slide_no"),
                        chunk["chunk_type"],
                        chunk["chunk_text"],
                        chunk["chunk_tokens_est"],
                        Json(chunk.get("chunk_metadata", {})),
                        vector_literal(chunk["embedding"]),
                    ),
                )
                chunk_id = int(cursor.fetchone()["id"])
                cursor.execute(
                    """
                    INSERT INTO rag_citation (chunk_id, citation_label, locator_text)
                    VALUES (%s, %s, %s)
                    """,
                    (chunk_id, chunk["citation_label"], chunk["locator_text"]),
                )
        return {
            "document_id": document_id,
            "fragments_created": len(fragments),
            "chunks_created": len(chunks),
        }

    def search_chunks(self, embedding: list[float], query_text: str, top_k: int = 5) -> list[dict[str, Any]]:
        with db_cursor(self.config) as (_, cursor):
            cursor.execute(
                """
                WITH ranked AS (
                    SELECT
                        rc.id AS chunk_id,
                        rs.id AS source_id,
                        rs.title AS source_title,
                        rc.slide_no,
                        rc.chunk_type,
                        rc.chunk_text,
                        cit.citation_label,
                        cit.locator_text,
                        (1 - (rc.embedding <=> %s::vector)) AS vector_score,
                        ts_rank_cd(rc.tsv, plainto_tsquery('russian', %s)) AS text_score,
                        similarity(rc.search_text, lower(%s)) AS trigram_score
                    FROM rag_chunk rc
                    JOIN rag_document rd ON rd.id = rc.document_id
                    JOIN rag_source rs ON rs.id = rd.source_id
                    LEFT JOIN rag_citation cit ON cit.chunk_id = rc.id
                    WHERE rs.status = 'READY'
                )
                SELECT *,
                    (COALESCE(vector_score, 0) * 0.50 + COALESCE(text_score, 0) * 0.30 + COALESCE(trigram_score, 0) * 0.20) AS score
                FROM ranked
                WHERE COALESCE(vector_score, 0) > 0
                   OR COALESCE(text_score, 0) > 0
                   OR COALESCE(trigram_score, 0) > 0
                ORDER BY score DESC, source_title, slide_no, chunk_id
                LIMIT %s
                """,
                (vector_literal(embedding), query_text, query_text, top_k),
            )
            return [dict(row) for row in cursor.fetchall()]
