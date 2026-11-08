"""Local paths and optional .env loading. This module does not read or store secrets."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "data" / "schema.sql"
DEFAULT_DB_PATH = "data/qaseed.db"


def load_local_env() -> None:
    """Load a gitignored .env file when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def database_path(env: dict[str, str] | None = None) -> Path:
    """Return the local SQLite path. Does not create the database file."""
    values = os.environ if env is None else env
    if env is None:
        load_local_env()
    raw = values.get("QASEED_DB_PATH", DEFAULT_DB_PATH)
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path
