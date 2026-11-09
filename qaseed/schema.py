"""Load the sample schema and report tables, columns, and foreign keys."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from qaseed.config import SCHEMA_PATH

TABLE_ORDER = ("customers", "subscriptions", "invoices", "payments")

# Matches column-level checks such as: status TEXT NOT NULL CHECK (status IN ('a', 'b'))
_CHECK_IN_RE = re.compile(r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)\s*\)", re.IGNORECASE)
_QUOTED_RE = re.compile(r"'([^']*)'")


def create_connection(
    db_path: str | Path = ":memory:",
    schema_path: Path = SCHEMA_PATH,
) -> sqlite3.Connection:
    """Open SQLite, enable foreign keys, and apply the sample schema.

    The default path is an in-memory database, so importing or displaying
    the schema does not write a file.
    """
    if str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _enable_foreign_keys(conn)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    # executescript commits first. Set the pragma again on this connection.
    _enable_foreign_keys(conn)
    return conn


def inspect_schema(conn: sqlite3.Connection) -> dict:
    """Return foreign-key enforcement plus each table's columns and foreign keys."""
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    tables = [_table_info(conn, row["name"]) for row in rows]
    tables.sort(key=_table_sort_key)
    return {
        "foreign_keys_enabled": _foreign_keys_enabled(conn),
        "tables": tables,
    }


def load_schema(schema_path: Path = SCHEMA_PATH) -> dict:
    """Inspect the sample schema in a throwaway in-memory database."""
    conn = create_connection(schema_path=schema_path)
    try:
        return inspect_schema(conn)
    finally:
        conn.close()


def main() -> None:
    """Print a short schema summary. Used as a local smoke check."""
    conn = create_connection()
    try:
        schema = inspect_schema(conn)
    finally:
        conn.close()
    print(f"foreign_keys_enabled={schema['foreign_keys_enabled']}")
    for table in schema["tables"]:
        columns = ", ".join(column["name"] for column in table["columns"])
        print(f"{table['name']}: {columns}")
        for fk in table["foreign_keys"]:
            print(f"  {fk['from_column']} -> {fk['to_table']}.{fk['to_column']}")
        for column, values in table["allowed_values"].items():
            print(f"  {column} in ({', '.join(values)})")
        for column in table["unique_columns"]:
            print(f"  unique: {column}")


def _enable_foreign_keys(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    if not _foreign_keys_enabled(conn):
        raise RuntimeError("SQLite did not enable foreign-key enforcement")


def _foreign_keys_enabled(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _table_info(conn: sqlite3.Connection, name: str) -> dict:
    ident = _quote_ident(name)
    columns = [
        {
            "name": column["name"],
            "type": column["type"],
            "not_null": bool(column["notnull"]),
            "primary_key": bool(column["pk"]),
        }
        for column in conn.execute(f"PRAGMA table_info({ident})")
    ]
    foreign_keys = [
        {
            "from_column": fk["from"],
            "to_table": fk["table"],
            "to_column": fk["to"],
        }
        for fk in conn.execute(f"PRAGMA foreign_key_list({ident})")
    ]
    return {
        "name": name,
        "columns": columns,
        "foreign_keys": foreign_keys,
        "allowed_values": _allowed_values(conn, name),
        "unique_columns": _unique_columns(conn, ident),
    }


def _allowed_values(conn: sqlite3.Connection, name: str) -> dict[str, list[str]]:
    """Read CHECK (column IN ('a', 'b')) value lists from the table definition."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    sql = row["sql"] if row and row["sql"] else ""
    return {
        match.group(1): _QUOTED_RE.findall(match.group(2))
        for match in _CHECK_IN_RE.finditer(sql)
    }


def _unique_columns(conn: sqlite3.Connection, ident: str) -> list[str]:
    """Single-column UNIQUE constraints (primary keys are listed separately)."""
    columns = []
    for index in conn.execute(f"PRAGMA index_list({ident})"):
        if not index["unique"] or index["origin"] == "pk":
            continue
        parts = conn.execute(f"PRAGMA index_info({_quote_ident(index['name'])})").fetchall()
        if len(parts) == 1:
            columns.append(parts[0]["name"])
    return sorted(columns)


def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unexpected table name: {name}")
    return f'"{name}"'


def _table_sort_key(table: dict) -> tuple[int, str]:
    try:
        return (TABLE_ORDER.index(table["name"]), table["name"])
    except ValueError:
        return (len(TABLE_ORDER), table["name"])


if __name__ == "__main__":
    main()
