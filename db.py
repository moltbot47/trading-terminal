"""Postgres abstraction layer for trading-terminal cloud database.

Uses psycopg v3 with connection pooling.
Set DATABASE_URL env var to your Postgres connection string.
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lazy imports — psycopg only needed when DATABASE_URL is set
psycopg = None  # type: Any
psycopg_pool = None  # type: Any
Jsonb = None  # type: Any


def _ensure_imports() -> None:
    """Import psycopg on first use (avoids crash when not installed locally)."""
    global psycopg, psycopg_pool, Jsonb
    if psycopg is None:
        import psycopg as _psycopg
        import psycopg_pool as _psycopg_pool
        from psycopg.types.json import Jsonb as _Jsonb
        psycopg = _psycopg
        psycopg_pool = _psycopg_pool
        Jsonb = _Jsonb

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool = None


def get_pool():
    """Lazy-init and return the global connection pool."""
    _ensure_imports()
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. Export it before using the Postgres layer."
            )
        try:
            _pool = psycopg_pool.ConnectionPool(
                conninfo=DATABASE_URL,
                min_size=2,
                max_size=10,
                open=True,
            )
            logger.info("Postgres connection pool created (min=2, max=10)")
        except Exception:
            logger.exception("Failed to create Postgres connection pool")
            raise
    return _pool


@contextmanager
def get_conn():
    """Context manager yielding a connection from the pool."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def pg_query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute SQL and return results as a list of dicts."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    return []
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
    except Exception:
        logger.exception("pg_query failed: %s", sql[:120])
        raise


def read_json_pg(key: str) -> dict | list | None:
    """Read a JSON value from the json_state table.

    Strips .json extension from key if present, so callers can pass
    filenames like 'heartbeat.json' directly.
    """
    clean_key = key.replace(".json", "")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM json_state WHERE key = %s", (clean_key,)
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:
        logger.exception("read_json_pg failed for key=%s", clean_key)
        return None


def write_json_pg(key: str, value) -> None:
    """Upsert a JSON value into the json_state table.

    Strips .json extension from key if present.
    """
    _ensure_imports()
    clean_key = key.replace(".json", "")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO json_state (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key)
                       DO UPDATE SET value = EXCLUDED.value,
                                     updated_at = NOW()""",
                    (clean_key, Jsonb(value)),
                )
            conn.commit()
    except Exception:
        logger.exception("write_json_pg failed for key=%s", clean_key)
        raise


def init_schema() -> None:
    """Read schema.sql and execute it to create all tables."""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"schema.sql not found at {schema_path}")

    sql = schema_path.read_text()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        logger.info("Schema initialized successfully from %s", schema_path)
    except Exception:
        logger.exception("init_schema failed")
        raise
