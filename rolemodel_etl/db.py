from __future__ import annotations

from pathlib import Path

from .config import DBConfig


def get_connection(config: DBConfig):
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2 is required for DB operations. Install dependencies from requirements.txt."
        ) from exc

    return psycopg2.connect(
        host=config.host,
        port=config.port,
        dbname=config.dbname,
        user=config.user,
        password=config.password,
    )


def read_schema_sql() -> str:
    schema_path = Path(__file__).resolve().parent / "sql" / "schema.sql"
    return schema_path.read_text(encoding="utf-8")

