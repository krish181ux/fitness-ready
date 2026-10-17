"""DuckDB connection helpers shared by the loader and the tools server."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = REPO_ROOT / "wearable-real.duckdb"
SCHEMA_PATH = REPO_ROOT / "loader" / "schema.sql"


def db_path() -> Path:
    """Database location. ``WEARABLE_DB`` overrides it (tests, docker)."""
    return Path(os.environ.get("WEARABLE_DB", DEFAULT_DB_PATH))


def connect(path: str | Path | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    target = Path(path) if path is not None else db_path()
    if read_only and not target.exists():
        raise FileNotFoundError(
            f"{target} does not exist. Run `python -m loader.load_garmin` first."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(target), read_only=read_only)


def apply_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_PATH.read_text())
