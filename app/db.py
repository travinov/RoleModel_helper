from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from psycopg2.extras import RealDictCursor

from rolemodel_etl.db import get_connection as get_rolemodel_connection

from .config import AppConfig


@contextmanager
def db_cursor(config: AppConfig, autocommit: bool = False) -> Iterator:
    conn = get_rolemodel_connection(config.db)
    conn.autocommit = autocommit
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {config.db.schema}")
            cursor.execute(f"SET search_path TO {config.db.schema}, public")
            yield conn, cursor
            if not autocommit:
                conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

