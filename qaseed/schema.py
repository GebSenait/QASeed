"""Load the sample schema and report tables, columns, and foreign keys."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from qaseed.config import SCHEMA_PATH

TABLE_ORDER = ("customers", "subscriptions", "invoices", "payments")


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
    return {"name": name, "columns": columns, "foreign_keys": foreign_keys}


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
