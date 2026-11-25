"""SQLite persistence helpers for the independent Payment Provider Simulator."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_PROVIDER_DB_PATH = Path("data/provider.db")
DEFAULT_PROVIDER_SCHEMA_PATH = Path("db/schemas/provider.sql")

_PARTIAL_IDEMPOTENCY_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_tx_idempotency
    ON provider_transactions (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""


def get_connection(db_path: Path | str = DEFAULT_PROVIDER_DB_PATH) -> sqlite3.Connection:
    """Open an independent sqlite3 connection configured for safe concurrent access."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(
    db_path: Path | str = DEFAULT_PROVIDER_DB_PATH,
    schema_path: Path | str = DEFAULT_PROVIDER_SCHEMA_PATH,
) -> None:
    """Initialize data/provider.db from db/schemas/provider.sql."""
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    conn = get_connection(db_path)
    try:
        conn.executescript(schema_sql)
        conn.execute(_PARTIAL_IDEMPOTENCY_INDEX_SQL)
    finally:
        conn.close()
