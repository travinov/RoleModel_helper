from __future__ import annotations

import re
from collections.abc import Iterable


def normalize_index_text(value: str | None) -> str:
    normalized = (value or "").lower().replace("ё", "е").strip()
    normalized = re.sub(r"\[ci\d+\]", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\(и\d+\)", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("(пром)", "")
    normalized = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def build_search_ngrams(value: str | None) -> tuple[str, ...]:
    normalized = normalize_index_text(value or "")
    if not normalized:
        return tuple()
    padded = f"  {normalized} "
    grams = {padded[index : index + 3] for index in range(0, max(len(padded) - 2, 0))}
    return tuple(sorted(grams))


def _insert_search_document(
    cursor,
    *,
    snapshot_id: int,
    system_id: int,
    document_text: str,
    normalized_text: str,
    source_kind: str,
    alias_source: str | None,
    alias_class: str,
    collision_count: int,
) -> int | None:
    grams = build_search_ngrams(normalized_text)
    if not normalized_text or not grams:
        return None
    cursor.execute(
        """
        INSERT INTO search_document (
            snapshot_id,
            entity_type,
            entity_id,
            document_text,
            normalized_text,
            source_kind,
            alias_source,
            alias_class,
            collision_count,
            gram_count
        )
        VALUES (%s, 'SYSTEM', %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (snapshot_id, entity_type, entity_id, source_kind, normalized_text)
        DO UPDATE SET
            document_text = EXCLUDED.document_text,
            alias_source = EXCLUDED.alias_source,
            alias_class = EXCLUDED.alias_class,
            collision_count = EXCLUDED.collision_count,
            gram_count = EXCLUDED.gram_count
        RETURNING id
        """,
        (
            snapshot_id,
            system_id,
            document_text,
            normalized_text,
            source_kind,
            alias_source,
            alias_class,
            collision_count,
            len(grams),
        ),
    )
    document_id = int(cursor.fetchone()[0])
    _insert_search_ngrams(cursor, document_id, grams)
    return document_id


def _insert_search_ngrams(cursor, document_id: int, grams: Iterable[str]) -> None:
    cursor.execute("DELETE FROM search_ngram WHERE document_id = %s", (document_id,))
    cursor.executemany(
        """
        INSERT INTO search_ngram (document_id, gram)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        [(document_id, gram) for gram in grams],
    )


def rebuild_system_search_index(cursor, snapshot_id: int) -> None:
    cursor.execute("DELETE FROM search_document WHERE snapshot_id = %s", (snapshot_id,))
    cursor.execute(
        """
        SELECT id, system_name_raw
        FROM system
        WHERE snapshot_id = %s
        """,
        (snapshot_id,),
    )
    for system_id, system_name_raw in cursor.fetchall():
        document_text = str(system_name_raw or "")
        normalized_text = normalize_index_text(document_text)
        _insert_search_document(
            cursor,
            snapshot_id=snapshot_id,
            system_id=int(system_id),
            document_text=document_text,
            normalized_text=normalized_text,
            source_kind="SYSTEM_NAME",
            alias_source=None,
            alias_class="SAFE",
            collision_count=1,
        )

    cursor.execute(
        """
        SELECT sa.system_id, sa.alias_text, sa.alias_normalized, sa.alias_source
        FROM system_alias sa
        JOIN system s ON s.id = sa.system_id
        WHERE s.snapshot_id = %s
          AND sa.is_active = TRUE
        """,
        (snapshot_id,),
    )
    for system_id, alias_text, alias_normalized, alias_source in cursor.fetchall():
        _insert_search_document(
            cursor,
            snapshot_id=snapshot_id,
            system_id=int(system_id),
            document_text=str(alias_text or ""),
            normalized_text=str(alias_normalized or ""),
            source_kind="SYSTEM_ALIAS",
            alias_source=str(alias_source or "MANUAL"),
            alias_class="SAFE",
            collision_count=1,
        )

    cursor.execute(
        """
        SELECT system_id, alias_text, alias_normalized, alias_source, alias_class, collision_count
        FROM system_alias_candidate
        WHERE snapshot_id = %s
        """,
        (snapshot_id,),
    )
    for system_id, alias_text, alias_normalized, alias_source, alias_class, collision_count in cursor.fetchall():
        _insert_search_document(
            cursor,
            snapshot_id=snapshot_id,
            system_id=int(system_id),
            document_text=str(alias_text or ""),
            normalized_text=str(alias_normalized or ""),
            source_kind="SYSTEM_ALIAS_CANDIDATE",
            alias_source=str(alias_source or "AUTO_EXTRACTED"),
            alias_class=str(alias_class or "SAFE"),
            collision_count=int(collision_count or 1),
        )
