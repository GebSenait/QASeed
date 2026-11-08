"""Schema creation, foreign keys, and inspection."""

import sqlite3

import pytest

from qaseed.schema import create_connection, inspect_schema


@pytest.fixture
def connection():
    conn = create_connection()
    try:
        yield conn
    finally:
        conn.close()


def test_foreign_keys_are_enabled(connection):
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert inspect_schema(connection)["foreign_keys_enabled"] is True


def test_creates_four_tables_with_primary_keys(connection):
    schema = inspect_schema(connection)
    tables = {table["name"]: table for table in schema["tables"]}
    assert list(tables) == ["customers", "subscriptions", "invoices", "payments"]
    assert _primary_key(tables["customers"]) == ["customer_id"]
    assert _primary_key(tables["subscriptions"]) == ["subscription_id"]
    assert _primary_key(tables["invoices"]) == ["invoice_id"]
    assert _primary_key(tables["payments"]) == ["payment_id"]
    for table in tables.values():
        for column in table["columns"]:
            if column["primary_key"]:
                assert column["not_null"] is True


def test_foreign_key_chain(connection):
    schema = inspect_schema(connection)
    links = {
        table["name"]: {
            (fk["from_column"], fk["to_table"], fk["to_column"])
            for fk in table["foreign_keys"]
        }
        for table in schema["tables"]
    }
    assert links["customers"] == set()
    assert links["subscriptions"] == {("customer_id", "customers", "customer_id")}
    assert links["invoices"] == {("subscription_id", "subscriptions", "subscription_id")}
    assert links["payments"] == {("invoice_id", "invoices", "invoice_id")}


def test_linked_rows_can_be_inserted(connection):
    _insert_chain(connection)
    counts = {
        name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in ("customers", "subscriptions", "invoices", "payments")
    }
    assert counts == {
        "customers": 1,
        "subscriptions": 1,
        "invoices": 1,
        "payments": 1,
    }


def test_subscription_without_customer_is_rejected(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO subscriptions (
                customer_id, plan_name, status, monthly_price_cents, started_on
            ) VALUES (999, 'team', 'active', 4900, '2026-01-15')
            """
        )


def test_invoice_without_subscription_is_rejected(connection):
    _insert_customer(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO invoices (
                subscription_id, invoice_number, amount_cents, status, issued_on, due_on
            ) VALUES (999, 'INV-missing', 4900, 'open', '2026-02-01', '2026-02-15')
            """
        )


def test_payment_without_invoice_is_rejected(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO payments (invoice_id, amount_cents, status, attempted_on)
            VALUES (999, 4900, 'failed', '2026-02-16')
            """
        )


def test_delete_customer_with_subscription_is_rejected(connection):
    _insert_chain(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM customers WHERE email = 'ada@example.test'")


def test_invalid_status_is_rejected(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO customers (email, display_name, country_code, status, created_on)
            VALUES ('ada@example.test', 'Ada Tester', 'AE', 'paused', '2026-01-15')
            """
        )


def test_file_database_keeps_foreign_keys(tmp_path):
    conn = create_connection(tmp_path / "sample.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO subscriptions (
                    customer_id, plan_name, status, monthly_price_cents, started_on
                ) VALUES (999, 'team', 'active', 4900, '2026-01-15')
                """
            )
    finally:
        conn.close()


def _primary_key(table: dict) -> list[str]:
    return [column["name"] for column in table["columns"] if column["primary_key"]]


def _insert_customer(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO customers (email, display_name, country_code, status, created_on)
        VALUES ('ada@example.test', 'Ada Tester', 'AE', 'active', '2026-01-15')
        """
    )


def _insert_chain(connection: sqlite3.Connection) -> None:
    _insert_customer(connection)
    connection.execute(
        """
        INSERT INTO subscriptions (
            customer_id, plan_name, status, monthly_price_cents, started_on, canceled_on
        ) VALUES (1, 'team', 'canceled', 4900, '2026-01-15', '2026-03-01')
        """
    )
    connection.execute(
        """
        INSERT INTO invoices (
            subscription_id, invoice_number, amount_cents, status, issued_on, due_on
        ) VALUES (1, 'INV-1001', 4900, 'overdue', '2026-02-01', '2026-02-15')
        """
    )
    connection.execute(
        """
        INSERT INTO payments (invoice_id, amount_cents, status, attempted_on)
        VALUES (1, 4900, 'failed', '2026-02-16')
        """
    )
