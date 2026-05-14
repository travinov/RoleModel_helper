from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from .config import DBConfig
from .db import get_connection, read_schema_sql
from .models import ParseResult


def file_sha256(path: str | Path) -> str:
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_alias(value: str) -> str:
    normalized = value.lower().strip()
    normalized = re.sub(r"\[ci\d+\]", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\(и\d+\)", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("(пром)", "")
    normalized = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def build_system_aliases(system_name: str) -> list[str]:
    aliases = {system_name.strip()}
    aliases.add(re.sub(r"\s*\[CI\d+\]", "", system_name, flags=re.IGNORECASE).strip())
    aliases.add(re.sub(r"\s*\(И\d+\)", "", system_name, flags=re.IGNORECASE).strip())
    aliases.add(re.sub(r"\s*\(ПРОМ\)", "", system_name, flags=re.IGNORECASE).strip())
    aliases.add(
        re.sub(r"\s*\(ПРОМ\)\s*", " ", re.sub(r"\s*\(И\d+\)", "", system_name, flags=re.IGNORECASE), flags=re.IGNORECASE).strip()
    )
    cleaned: set[str] = set()
    for alias in aliases:
        alias = re.sub(r"\s+", " ", alias).strip(" -")
        if alias:
            cleaned.add(alias)
    return sorted(cleaned)


def _set_search_path(cursor, schema: str) -> None:
    from psycopg2 import sql

    cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))


def init_db(config: DBConfig) -> None:
    sql_script = read_schema_sql()
    conn = get_connection(config)
    try:
        conn.autocommit = True
        with conn.cursor() as cursor:
            _set_search_path(cursor, config.schema)
            cursor.execute(sql_script)
    finally:
        conn.close()


def _create_run(conn, config: DBConfig, parsed: ParseResult, file_hash: str) -> int:
    with conn.cursor() as cursor:
        _set_search_path(cursor, config.schema)
        cursor.execute(
            """
            INSERT INTO etl_run (
                status,
                source_file,
                file_sha256,
                sheet_name,
                rows_read,
                rows_loaded,
                errors_count
            )
            VALUES ('RUNNING', %s, %s, %s, %s, 0, 0)
            RETURNING id
            """,
            (parsed.source_file, file_hash, parsed.sheet_name, parsed.rows_read),
        )
        run_id = cursor.fetchone()[0]
    return run_id


def _mark_run_failed(conn, config: DBConfig, run_id: int, errors_count: int) -> None:
    conn.autocommit = True
    with conn.cursor() as cursor:
        _set_search_path(cursor, config.schema)
        cursor.execute(
            """
            UPDATE etl_run
            SET status = 'FAILED',
                finished_at = now(),
                errors_count = %s
            WHERE id = %s
            """,
            (errors_count, run_id),
        )


def _insert_snapshot(cursor, parsed: ParseResult, run_id: int, snapshot_label: Optional[str]) -> int:
    cursor.execute(
        """
        INSERT INTO snapshot (
            run_id,
            snapshot_label,
            is_active,
            source_file,
            sheet_name,
            model_code,
            model_name
        )
        VALUES (%s, %s, FALSE, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            run_id,
            snapshot_label,
            parsed.source_file,
            parsed.sheet_name,
            parsed.model_code,
            parsed.model_name,
        ),
    )
    return cursor.fetchone()[0]


def _seed_system_aliases(cursor, snapshot_id: int, system_id_by_name: dict[str, int]) -> None:
    old_manual_aliases: dict[str, list[tuple[str, str]]] = {}
    cursor.execute(
        """
        SELECT s.ci_code, s.system_name_raw, sa.alias_text, sa.alias_source
        FROM snapshot snap
        JOIN system s ON s.snapshot_id = snap.id
        JOIN system_alias sa ON sa.system_id = s.id
        WHERE snap.is_active = TRUE
          AND sa.alias_source = 'MANUAL'
          AND sa.is_active = TRUE
        """
    )
    for ci_code, system_name_raw, alias_text, alias_source in cursor.fetchall():
        lookup_key = ci_code or system_name_raw
        old_manual_aliases.setdefault(lookup_key, []).append((alias_text, alias_source))

    cursor.execute(
        "SELECT id, system_name_raw, ci_code FROM system WHERE snapshot_id = %s",
        (snapshot_id,),
    )
    for system_id, system_name_raw, ci_code in cursor.fetchall():
        for alias_text in build_system_aliases(system_name_raw):
            cursor.execute(
                """
                INSERT INTO system_alias (system_id, alias_text, alias_normalized, alias_source, is_active)
                VALUES (%s, %s, %s, 'AUTO_SEEDED', TRUE)
                ON CONFLICT (system_id, alias_normalized) DO NOTHING
                """,
                (system_id, alias_text, normalize_alias(alias_text)),
            )
        lookup_key = ci_code or system_name_raw
        for alias_text, alias_source in old_manual_aliases.get(lookup_key, []):
            cursor.execute(
                """
                INSERT INTO system_alias (system_id, alias_text, alias_normalized, alias_source, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (system_id, alias_normalized) DO NOTHING
                """,
                (system_id, alias_text, normalize_alias(alias_text), alias_source),
            )


def load_to_db(config: DBConfig, parsed: ParseResult, snapshot_label: Optional[str] = None) -> dict:
    file_hash = file_sha256(parsed.source_file)
    conn = get_connection(config)
    run_id: Optional[int] = None
    try:
        conn.autocommit = True
        run_id = _create_run(conn, config, parsed, file_hash)
        conn.autocommit = False

        with conn.cursor() as cursor:
            _set_search_path(cursor, config.schema)

            snapshot_id = _insert_snapshot(cursor, parsed, run_id, snapshot_label)

            system_id_by_name: dict[str, int] = {}
            for system in parsed.systems:
                cursor.execute(
                    """
                    INSERT INTO system (
                        snapshot_id,
                        system_name_raw,
                        ci_code,
                        source_col_start,
                        source_col_end
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        snapshot_id,
                        system.system_name_raw,
                        system.ci_code,
                        system.source_col_start,
                        system.source_col_end,
                    ),
                )
                system_id_by_name[system.system_name_raw] = cursor.fetchone()[0]

            _seed_system_aliases(cursor, snapshot_id, system_id_by_name)

            entitlement_id_by_col: dict[int, int] = {}
            for entitlement in parsed.entitlements:
                system_id = system_id_by_name[entitlement.system_name_raw]
                cursor.execute(
                    """
                    INSERT INTO entitlement (
                        snapshot_id,
                        system_id,
                        source_col,
                        entitlement_type,
                        entitlement_name,
                        header_raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        snapshot_id,
                        system_id,
                        entitlement.source_col,
                        entitlement.entitlement_type,
                        entitlement.entitlement_name,
                        entitlement.header_raw,
                    ),
                )
                entitlement_id_by_col[entitlement.source_col] = cursor.fetchone()[0]

            profile_id_by_code: dict[str, int] = {}
            for profile in parsed.profiles:
                cursor.execute(
                    """
                    INSERT INTO profile (
                        snapshot_id,
                        profile_code,
                        profile_name,
                        profile_type,
                        profile_raw,
                        profile_justification_raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        snapshot_id,
                        profile.profile_code,
                        profile.profile_name,
                        profile.profile_type,
                        profile.profile_raw,
                        profile.profile_justification_raw,
                    ),
                )
                profile_id = cursor.fetchone()[0]
                profile_id_by_code[profile.profile_code] = profile_id

                for path in profile.structure_paths:
                    cursor.execute(
                        """
                        INSERT INTO profile_structure_path (
                            snapshot_id,
                            profile_id,
                            path_order,
                            path_raw
                        )
                        VALUES (%s, %s, %s, %s)
                        """,
                        (snapshot_id, profile_id, path.path_order, path.path_raw),
                    )
                    for segment_order, segment_name in enumerate(path.segments, start=1):
                        cursor.execute(
                            """
                            INSERT INTO profile_structure_segment (
                                snapshot_id,
                                profile_id,
                                path_order,
                                segment_order,
                                segment_name
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                snapshot_id,
                                profile_id,
                                path.path_order,
                                segment_order,
                                segment_name,
                            ),
                        )

                for department in profile.departments:
                    cursor.execute(
                        """
                        INSERT INTO profile_department (
                            snapshot_id,
                            profile_id,
                            department_name,
                            department_code,
                            raw_line,
                            parse_status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            snapshot_id,
                            profile_id,
                            department.name,
                            department.code,
                            department.raw_line,
                            department.parse_status,
                        ),
                    )

                for position in profile.positions:
                    cursor.execute(
                        """
                        INSERT INTO profile_position (
                            snapshot_id,
                            profile_id,
                            position_name,
                            position_code,
                            raw_line,
                            parse_status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            snapshot_id,
                            profile_id,
                            position.name,
                            position.code,
                            position.raw_line,
                            position.parse_status,
                        ),
                    )

            justification_id_by_key: dict[tuple[str, str, int], int] = {}
            for profile in parsed.profiles:
                profile_id = profile_id_by_code[profile.profile_code]
                for justification in profile.justifications:
                    system_id = system_id_by_name[justification.system_name_raw]
                    cursor.execute(
                        """
                        INSERT INTO profile_system_justification (
                            snapshot_id,
                            profile_id,
                            system_id,
                            access_level,
                            justification_text,
                            raw_value
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (snapshot_id, profile_id, system_id, access_level)
                        DO UPDATE SET
                            justification_text = EXCLUDED.justification_text,
                            raw_value = EXCLUDED.raw_value
                        RETURNING id
                        """,
                        (
                            snapshot_id,
                            profile_id,
                            system_id,
                            justification.access_level,
                            justification.justification_text,
                            justification.raw_value,
                        ),
                    )
                    justification_id = cursor.fetchone()[0]
                    justification_id_by_key[
                        (profile.profile_code, justification.system_name_raw, justification.access_level)
                    ] = justification_id

            access_inserted = 0
            for profile in parsed.profiles:
                profile_id = profile_id_by_code[profile.profile_code]
                for access in profile.accesses:
                    entitlement_id = entitlement_id_by_col[access.source_col]
                    justification_id = justification_id_by_key.get(
                        (profile.profile_code, access.system_name_raw, access.access_level)
                    )
                    cursor.execute(
                        """
                        INSERT INTO profile_entitlement_access (
                            snapshot_id,
                            profile_id,
                            entitlement_id,
                            access_level,
                            inline_comment,
                            raw_value,
                            justification_id
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (snapshot_id, profile_id, entitlement_id)
                        DO UPDATE SET
                            access_level = EXCLUDED.access_level,
                            inline_comment = EXCLUDED.inline_comment,
                            raw_value = EXCLUDED.raw_value,
                            justification_id = EXCLUDED.justification_id
                        """,
                        (
                            snapshot_id,
                            profile_id,
                            entitlement_id,
                            access.access_level,
                            access.inline_comment,
                            access.raw_value,
                            justification_id,
                        ),
                    )
                    access_inserted += 1

            for issue in parsed.issues:
                cursor.execute(
                    """
                    INSERT INTO etl_error (
                        run_id,
                        snapshot_id,
                        stage,
                        severity,
                        sheet_name,
                        source_row,
                        source_col,
                        error_code,
                        error_message,
                        raw_value
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        snapshot_id,
                        issue.stage,
                        issue.severity,
                        issue.sheet_name,
                        issue.source_row,
                        issue.source_col,
                        issue.error_code,
                        issue.message,
                        issue.raw_value,
                    ),
                )

            cursor.execute("UPDATE snapshot SET is_active = FALSE WHERE is_active = TRUE")
            cursor.execute("UPDATE snapshot SET is_active = TRUE WHERE id = %s", (snapshot_id,))
            cursor.execute("REFRESH MATERIALIZED VIEW mv_access_search_active")
            cursor.execute(
                """
                UPDATE etl_run
                SET status = 'SUCCESS',
                    finished_at = now(),
                    rows_loaded = %s,
                    errors_count = %s
                WHERE id = %s
                """,
                (access_inserted, len(parsed.issues), run_id),
            )

        conn.commit()
        return {
            "run_id": run_id,
            "sheet_name": parsed.sheet_name,
            "rows_read": parsed.rows_read,
            "profiles": len(parsed.profiles),
            "systems": len(parsed.systems),
            "entitlements": len(parsed.entitlements),
            "rows_loaded": parsed.access_count,
            "errors_count": parsed.errors_count,
            "warnings_count": parsed.warnings_count,
        }
    except Exception:
        conn.rollback()
        if run_id is not None:
            _mark_run_failed(conn, config, run_id, len(parsed.issues) + 1)
        raise
    finally:
        conn.close()
