"""Independent checks on a generated database: does the data prove the request?

Reads the database only, through SQL (SQLAlchemy). It does not import or trust
the generator, so a generator bug shows up here as a FAIL.

Three groups of checks:
1. Plan checks: every verification check the plan lists (row counts,
   requested condition counts, foreign keys, uniqueness).
2. Database integrity: foreign keys are enforced, and SQLite's own
   PRAGMA foreign_key_check reports no violations.
3. Business rules: dates agree with statuses (canceled subscriptions have a
   cancel date, overdue invoices are past due, open ones are not, nothing
   happens before its parent record or after the as-of date).

Not checked yet: cross-table story rules such as "a failed payment belongs to
an overdue invoice", and realism of names or amounts beyond the schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from qaseed.plan_models import ScenarioPlan

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class CheckResult:
    group: str  # plan | integrity | business_rule
    kind: str
    table: str
    description: str
    expected: int
    actual: int
    column: str | None = None
    value: str | None = None

    @property
    def passed(self) -> bool:
        return self.actual == self.expected


@dataclass
class CheckReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def passed_count(self) -> int:
        return sum(r.passed for r in self.results)

    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]


# Business rules: (tables that must be present, description, SQL counting violations).
# Dates are ISO text (YYYY-MM-DD), so text comparison is date comparison.
BUSINESS_RULES = [
    (("customers",), "0 customers created after the as-of date",
     "SELECT COUNT(*) FROM customers WHERE created_on > :as_of"),
    (("subscriptions",), "0 subscriptions where canceled status and cancel date disagree",
     "SELECT COUNT(*) FROM subscriptions WHERE (status = 'canceled') <> (canceled_on IS NOT NULL)"),
    (("subscriptions",), "0 subscriptions canceled before they started or after the as-of date",
     "SELECT COUNT(*) FROM subscriptions WHERE canceled_on IS NOT NULL AND (canceled_on < started_on OR canceled_on > :as_of)"),
    (("subscriptions", "customers"), "0 subscriptions starting before their customer was created",
     "SELECT COUNT(*) FROM subscriptions s JOIN customers c ON c.customer_id = s.customer_id WHERE s.started_on < c.created_on"),
    (("invoices",), "0 invoices due before they were issued",
     "SELECT COUNT(*) FROM invoices WHERE due_on < issued_on"),
    (("invoices",), "0 overdue invoices that are not yet past due",
     "SELECT COUNT(*) FROM invoices WHERE status = 'overdue' AND due_on >= :as_of"),
    (("invoices",), "0 open invoices that are already past due",
     "SELECT COUNT(*) FROM invoices WHERE status = 'open' AND due_on < :as_of"),
    (("invoices", "subscriptions"), "0 invoices issued before their subscription started",
     "SELECT COUNT(*) FROM invoices i JOIN subscriptions s ON s.subscription_id = i.subscription_id WHERE i.issued_on < s.started_on"),
    (("payments", "invoices"), "0 payments attempted before their invoice was issued or after the as-of date",
     "SELECT COUNT(*) FROM payments p JOIN invoices i ON i.invoice_id = p.invoice_id WHERE p.attempted_on < i.issued_on OR p.attempted_on > :as_of"),
]


def run_checks(engine: Engine, plan: ScenarioPlan, as_of: date) -> CheckReport:
    """Run every check against the database and return the results (nothing is raised for a FAIL)."""
    report = CheckReport()
    parents = {(r.child_table, r.child_column): (r.parent_table, r.parent_column) for r in plan.relationships}
    present = set(plan.generation_order)
    with engine.connect() as conn:

        def count(sql: str, **params) -> int:
            return int(conn.execute(text(sql), params).scalar_one())

        report.results.append(CheckResult(
            "integrity", "foreign_keys_enabled", "*", "foreign-key enforcement is on (1)", 1,
            count("PRAGMA foreign_keys"),
        ))
        for check in plan.verification_checks:
            table = _ident(check.table)
            if check.kind == "row_count":
                actual = count(f"SELECT COUNT(*) FROM {table}")
            elif check.kind == "condition_count":
                actual = count(f"SELECT COUNT(*) FROM {table} WHERE {_ident(check.column)} = :value", value=check.value)
            elif check.kind == "foreign_key":
                column = _ident(check.column)
                parent, parent_column = (_ident(x) for x in parents[(check.table, check.column)])
                actual = count(
                    f"SELECT COUNT(*) FROM {table} c LEFT JOIN {parent} p ON c.{column} = p.{parent_column} "
                    f"WHERE p.{parent_column} IS NULL"
                )
            elif check.kind == "unique":
                column = _ident(check.column)
                actual = count(f"SELECT COUNT(*) FROM (SELECT {column} FROM {table} GROUP BY {column} HAVING COUNT(*) > 1)")
            else:  # pragma: no cover - the plan contract only has the four kinds above
                raise ValueError(f"Unknown check kind {check.kind}")
            report.results.append(CheckResult(
                "plan", check.kind, check.table, check.description, check.expected, actual, check.column, check.value,
            ))
        violations = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
        report.results.append(CheckResult(
            "integrity", "foreign_key_check", "*", "0 rows reported by SQLite PRAGMA foreign_key_check", 0, len(violations),
        ))
        for tables, description, sql in BUSINESS_RULES:
            if set(tables) <= present:
                report.results.append(CheckResult(
                    "business_rule", "business_rule", tables[0], description, 0, count(sql, as_of=as_of.isoformat()),
                ))
    return report


def _ident(name: str | None) -> str:
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"Unexpected identifier: {name!r}")
    return name
