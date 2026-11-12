"""Independent data checks (qaseed/data_checks.py). Each check must catch a deliberately broken database."""

from datetime import date

import pytest
from sqlalchemy import text

from qaseed.data_checks import BUSINESS_RULES, CheckReport, run_checks
from qaseed.generator import generate, mock_plan

AS_OF = date(2026, 11, 11)


@pytest.fixture
def flagship():
    plan = mock_plan("S01")
    dataset = generate(plan, seed=42, as_of=AS_OF)
    yield plan, dataset
    dataset.close()


def tamper(dataset, sql, foreign_keys_off=False):
    with dataset.engine.begin() as conn:
        if foreign_keys_off:
            conn.execute(text("PRAGMA foreign_keys = OFF"))
        conn.execute(text(sql))
    if foreign_keys_off:  # SQLite ignores this pragma inside a transaction, so restore it after the commit
        with dataset.engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys = ON"))


def failed(plan, dataset):
    return [r.description for r in run_checks(dataset.engine, plan, AS_OF).failed()]


def test_clean_flagship_passes_every_group(flagship):
    plan, dataset = flagship
    report = run_checks(dataset.engine, plan, AS_OF)
    assert report.passed and report.passed_count == len(report.results)
    groups = {r.group for r in report.results}
    assert groups == {"plan", "integrity", "business_rule"}
    assert sum(r.group == "plan" for r in report.results) == len(plan.verification_checks) == 12
    assert sum(r.group == "business_rule" for r in report.results) == len(BUSINESS_RULES)


def test_wrong_condition_count_is_caught(flagship):
    plan, dataset = flagship
    tamper(dataset, "UPDATE subscriptions SET status = 'active', canceled_on = NULL WHERE subscription_id IN (SELECT subscription_id FROM subscriptions WHERE status = 'canceled' LIMIT 1)")
    assert failed(plan, dataset) == ["exactly 10 subscriptions rows have status = 'canceled'"]


def test_missing_row_is_caught(flagship):
    plan, dataset = flagship
    tamper(dataset, "DELETE FROM payments WHERE payment_id = (SELECT MAX(payment_id) FROM payments WHERE status = 'succeeded')")
    assert failed(plan, dataset) == ["payments has exactly 50 rows"]


def test_orphan_row_is_caught_by_plan_check_and_sqlite(flagship):
    plan, dataset = flagship
    tamper(dataset, "UPDATE invoices SET subscription_id = 9999 WHERE invoice_id = 1", foreign_keys_off=True)
    problems = failed(plan, dataset)
    assert "0 invoices rows whose subscription_id has no matching subscriptions.subscription_id" in problems
    assert "0 rows reported by SQLite PRAGMA foreign_key_check" in problems


def test_foreign_key_enforcement_blocks_orphans_by_default(flagship):
    _, dataset = flagship
    with pytest.raises(Exception, match="FOREIGN KEY"):
        tamper(dataset, "UPDATE invoices SET subscription_id = 9999 WHERE invoice_id = 1")


def test_duplicate_value_is_caught(flagship):
    plan, dataset = flagship
    # The UNIQUE constraint blocks duplicates, so copy the table without it to simulate a broken database.
    with dataset.engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = OFF"))
        conn.execute(text(
            "CREATE TABLE c2 (customer_id INTEGER PRIMARY KEY NOT NULL, email TEXT NOT NULL, display_name TEXT NOT NULL, "
            "country_code TEXT NOT NULL, status TEXT NOT NULL, created_on TEXT NOT NULL)"
        ))
        conn.execute(text("INSERT INTO c2 SELECT * FROM customers"))
        conn.execute(text("DROP TABLE customers"))
        conn.execute(text("ALTER TABLE c2 RENAME TO customers"))
        conn.execute(text("UPDATE customers SET email = (SELECT email FROM customers WHERE customer_id = 1) WHERE customer_id = 2"))
    with dataset.engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
    assert "0 duplicate customers.email values" in failed(plan, dataset)


@pytest.mark.parametrize("sql,rule", [
    ("UPDATE subscriptions SET canceled_on = NULL WHERE status = 'canceled' AND subscription_id = (SELECT MIN(subscription_id) FROM subscriptions WHERE status = 'canceled')",
     "0 subscriptions where canceled status and cancel date disagree"),
    ("UPDATE invoices SET due_on = '2099-01-01' WHERE invoice_id = (SELECT MIN(invoice_id) FROM invoices WHERE status = 'overdue')",
     "0 overdue invoices that are not yet past due"),
    ("UPDATE payments SET attempted_on = '2000-01-01' WHERE payment_id = 1",
     "0 payments attempted before their invoice was issued or after the as-of date"),
    ("UPDATE customers SET created_on = '2099-01-01' WHERE customer_id = 1",
     "0 customers created after the as-of date"),
    ("UPDATE invoices SET status = 'void' WHERE invoice_id = (SELECT MIN(invoice_id) FROM payments WHERE status = 'succeeded')",
     "0 invoices with a succeeded payment that are not paid"),
    ("UPDATE invoices SET status = 'paid' WHERE invoice_id = (SELECT MIN(invoice_id) FROM payments WHERE status = 'failed')",
     "0 invoices with only failed or pending payments that are not unpaid (open or overdue)"),
])
def test_business_rule_violations_are_caught(flagship, sql, rule):
    plan, dataset = flagship
    tamper(dataset, sql)
    assert rule in failed(plan, dataset)


def test_rules_for_absent_tables_are_skipped():
    plan = mock_plan("S03")  # customers + subscriptions only
    dataset = generate(plan, seed=1, as_of=AS_OF)
    try:
        report = run_checks(dataset.engine, plan, AS_OF)
        assert report.passed
        assert not any("invoice" in r.description or "payment" in r.description for r in report.results if r.group == "business_rule")
    finally:
        dataset.close()


def test_empty_report_is_not_a_pass():
    assert CheckReport().passed is False


# ---- Day 6: the payment/invoice rule was rewritten from correlated EXISTS to one GROUP BY ----------

ONLY_FAILED_RULE = "0 invoices with only failed or pending payments that are not unpaid (open or overdue)"


def test_paid_invoice_with_a_failed_and_a_succeeded_payment_is_not_flagged(flagship):
    plan, dataset = flagship
    # Move one failed payment onto an invoice that already has a succeeded payment (that invoice is paid).
    tamper(dataset, "UPDATE payments SET invoice_id = (SELECT MIN(invoice_id) FROM payments WHERE status = 'succeeded') "
                    "WHERE payment_id = (SELECT MIN(payment_id) FROM payments WHERE status = 'failed')")
    assert ONLY_FAILED_RULE not in failed(plan, dataset)


def test_invoice_without_payments_is_not_flagged_by_the_payment_rule(flagship):
    plan, dataset = flagship
    tamper(dataset, "DELETE FROM payments WHERE invoice_id = (SELECT MIN(invoice_id) FROM payments WHERE status = 'failed')")
    tamper(dataset, "UPDATE invoices SET status = 'paid' WHERE invoice_id NOT IN (SELECT invoice_id FROM payments)")
    assert ONLY_FAILED_RULE not in failed(plan, dataset)


def test_every_invoice_with_only_failed_payments_is_counted(flagship):
    plan, dataset = flagship
    tamper(dataset, "UPDATE invoices SET status = 'paid' WHERE invoice_id IN (SELECT invoice_id FROM payments "
                    "GROUP BY invoice_id HAVING SUM(status = 'succeeded') = 0)")
    with dataset.engine.connect() as conn:
        expected = conn.execute(text("SELECT COUNT(DISTINCT invoice_id) FROM payments WHERE invoice_id NOT IN "
                                     "(SELECT invoice_id FROM payments WHERE status = 'succeeded')")).scalar_one()
    rule = next(r for r in run_checks(dataset.engine, plan, AS_OF).results if r.description == ONLY_FAILED_RULE)
    assert expected > 0 and rule.actual == expected
