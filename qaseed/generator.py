"""Seeded generator: a validated ScenarioPlan -> linked synthetic rows in SQLite.

    python -m qaseed.generator                       # offline: plan S01 from its mock reply, generate, check
    python -m qaseed.generator --scenario-id S05     # another offline scenario
    python -m qaseed.generator --all                 # every valid offline scenario, coverage table
    python -m qaseed.generator --live "<scenario>"   # plan with live Gemini (1-4 requests), then generate
    python -m qaseed.generator --db data/demo.db     # also keep the SQLite file (gitignored *.db)
    python -m qaseed.generator --db data/demo.db --replace   # rebuild that file if it already exists

How rows are made (deterministic for a given seed, as-of date and Faker version):
- Tables are filled in the plan's generation order, parents before children.
  Child row i links to parent row (i mod parent count), so every parent is used.
- Requested conditions are exact: "10 canceled subscriptions" gives exactly
  10 canceled rows; every other row gets a normal status (active / paid /
  succeeded), never another requested value.
- Dates follow the status: an overdue invoice is past its due date, an open
  one is not, a canceled subscription has a cancel date and nothing else does.
- Values come from Faker (names, country codes); emails use the reserved
  example.com domain, so they can never belong to a real person.

The generator does not judge its own output. qaseed.data_checks verifies the
database independently with SQL. Nothing here talks to production systems.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from faker import Faker
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from qaseed.config import ROOT, SCHEMA_PATH
from qaseed.plan_models import ScenarioPlan
from qaseed.schema import load_schema

DEFAULT_SEED = 42
INVOICE_TERMS_DAYS = 30
PLAN_PRICES_CENTS = {"Starter": 1900, "Team": 4900, "Business": 9900}
# Status given to rows the scenario did not ask for: the "healthy" default.
NORMAL_VALUES = {
    ("customers", "status"): "active",
    ("subscriptions", "status"): "active",
    ("invoices", "status"): "paid",
    ("payments", "status"): "succeeded",
}
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class GenerationError(ValueError):
    """The plan cannot be turned into rows (it is not valid, or a condition cannot be met exactly)."""


@dataclass
class GeneratedDataset:
    engine: Engine
    seed: int
    as_of: date
    row_counts: dict[str, int] = field(default_factory=dict)
    path: Path | None = None

    def close(self) -> None:
        self.engine.dispose()


def create_database(path: Path | None = None) -> Engine:
    """Fresh SQLite database with the sample schema and foreign keys enforced.

    path=None gives a private in-memory database. A file path must not exist yet.
    """
    if path is None:
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        path = Path(path)
        if path.exists():
            raise GenerationError(f"{path.name} already exists; choose a new file name.")
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{path.as_posix()}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    with engine.connect() as conn:
        conn.connection.driver_connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return engine


def generate(
    plan: ScenarioPlan,
    seed: int = DEFAULT_SEED,
    as_of: date | None = None,
    path: Path | None = None,
    schema: dict | None = None,
) -> GeneratedDataset:
    """Insert the rows a valid plan describes and return the open dataset."""
    if plan.status != "valid":
        raise GenerationError(f"Only a valid plan can be generated (this one is {plan.status}).")
    as_of = as_of or date.today()
    schema = schema or load_schema()
    tables = {table["name"]: table for table in schema["tables"]}
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)
    counts = plan.counts()

    rows: dict[str, list[dict]] = {}
    for name in plan.generation_order:
        values = _column_values(plan, tables[name], counts[name], rng)
        builder = _BUILDERS[name]
        rows[name] = [builder(i, values, rows, rng, fake, as_of) for i in range(counts[name])]

    engine = create_database(path)
    try:
        with engine.begin() as conn:
            for name in plan.generation_order:
                if rows[name]:
                    columns = list(rows[name][0])
                    for column in (name, *columns):
                        _check_ident(column)
                    sql = f"INSERT INTO {name} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"
                    conn.execute(text(sql), rows[name])
    except Exception:
        engine.dispose()
        raise
    return GeneratedDataset(engine=engine, seed=seed, as_of=as_of, row_counts={n: len(r) for n, r in rows.items()}, path=path)


def sample_rows(dataset: GeneratedDataset, table: str, limit: int = 3) -> list[dict]:
    _check_ident(table)
    with dataset.engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {table} ORDER BY 1 LIMIT :limit"), {"limit": limit})
        return [dict(row) for row in result.mappings()]


# ---- values for constrained columns ------------------------------------------


def _column_values(plan: ScenarioPlan, table: dict, total: int, rng: random.Random) -> dict[str, list[str]]:
    """Exact, shuffled values for every column that has an allowed-value list."""
    name = table["name"]
    result: dict[str, list[str]] = {}
    for column, allowed in table["allowed_values"].items():
        requested = [c for c in plan.conditions if c.table == name and c.column == column]
        values = [c.value for c in requested for _ in range(c.count)]
        if len(values) > total:  # the validator rejects this; guard anyway
            raise GenerationError(f"{name}.{column}: conditions need {len(values)} rows but only {total} exist.")
        remaining = total - len(values)
        if remaining:
            used = {c.value for c in requested}
            normal = NORMAL_VALUES.get((name, column))
            filler = normal if normal in allowed and normal not in used else next((v for v in allowed if v not in used), None)
            if filler is None:
                raise GenerationError(
                    f"{name}.{column}: {remaining} rows have no value left that keeps the requested counts exact "
                    f"(every allowed value is already requested)."
                )
            values += [filler] * remaining
        rng.shuffle(values)
        result[column] = values
    return result


# ---- one builder per table ---------------------------------------------------


def _customer(i, values, rows, rng, fake, as_of):
    name = fake.name()
    slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".") or "customer"
    return {
        "customer_id": i + 1,
        "email": f"{slug}.{i + 1}@example.com",
        "display_name": name,
        "country_code": fake.country_code(),
        "status": values["status"][i],
        "created_on": _iso(as_of - timedelta(days=rng.randint(200, 730))),
    }


def _subscription(i, values, rows, rng, fake, as_of):
    customer = _parent(rows["customers"], i)
    status = values["status"][i]
    plan_name = rng.choice(list(PLAN_PRICES_CENTS))
    started = date.fromisoformat(customer["created_on"]) + timedelta(days=rng.randint(0, 90))
    return {
        "subscription_id": i + 1,
        "customer_id": customer["customer_id"],
        "plan_name": plan_name,
        "status": status,
        "monthly_price_cents": PLAN_PRICES_CENTS[plan_name],
        "started_on": _iso(started),
        "canceled_on": _iso(as_of - timedelta(days=rng.randint(1, 30))) if status == "canceled" else None,
    }


def _invoice(i, values, rows, rng, fake, as_of):
    subscription = _parent(rows["subscriptions"], i)
    status = values["status"][i]
    if status == "overdue":  # due date already passed
        issued = as_of - timedelta(days=rng.randint(INVOICE_TERMS_DAYS + 1, 90))
    elif status == "open":  # not yet due
        issued = as_of - timedelta(days=rng.randint(0, INVOICE_TERMS_DAYS - 1))
    else:  # paid / void: an older invoice
        issued = as_of - timedelta(days=rng.randint(35, 100))
    return {
        "invoice_id": i + 1,
        "subscription_id": subscription["subscription_id"],
        "invoice_number": f"INV-{issued:%Y%m}-{i + 1:06d}",
        "amount_cents": subscription["monthly_price_cents"],
        "status": status,
        "issued_on": _iso(issued),
        "due_on": _iso(issued + timedelta(days=INVOICE_TERMS_DAYS)),
    }


def _payment(i, values, rows, rng, fake, as_of):
    invoice = _parent(rows["invoices"], i)
    attempted = min(date.fromisoformat(invoice["issued_on"]) + timedelta(days=rng.randint(0, 10)), as_of)
    return {
        "payment_id": i + 1,
        "invoice_id": invoice["invoice_id"],
        "amount_cents": invoice["amount_cents"],
        "status": values["status"][i],
        "attempted_on": _iso(attempted),
    }


_BUILDERS = {"customers": _customer, "subscriptions": _subscription, "invoices": _invoice, "payments": _payment}


def _parent(parents: list[dict], i: int) -> dict:
    return parents[i % len(parents)]


def _iso(day: date) -> str:
    return day.isoformat()


def _check_ident(name: str) -> None:
    if not _IDENT_RE.match(name):
        raise GenerationError(f"Unexpected identifier: {name!r}")


# ---- command line ------------------------------------------------------------


def mock_plan(scenario_id: str) -> ScenarioPlan:
    """Plan a fixture scenario from its hand-written mock reply (offline, labelled source=mock)."""
    from qaseed.evaluate import load_scenarios
    from qaseed.llm_client import KEY_NAME, MODEL_NAME
    from qaseed.planner import canned_reply_post, plan_scenario

    case = next((c for c in load_scenarios() if c["id"] == scenario_id), None)
    if case is None:
        raise GenerationError(f"Unknown scenario id {scenario_id}.")
    return plan_scenario(
        case["scenario"],
        env={KEY_NAME: "mock-key-not-real", MODEL_NAME: "mock"},
        post=canned_reply_post(case["mock_reply"]),
        source="mock",
    )


def build_and_check(plan: ScenarioPlan, seed: int, as_of: date, path: Path | None = None):
    """Generate a valid plan and run the independent checks. Returns (dataset, report)."""
    from qaseed.data_checks import run_checks

    dataset = generate(plan, seed=seed, as_of=as_of, path=path)
    return dataset, run_checks(dataset.engine, plan, as_of)


def coverage_rows(plan: ScenarioPlan, report) -> list[dict]:
    """Requested vs generated vs verified, one row per table count and per condition."""
    actual = {(r.kind, r.table, r.column, r.value): r for r in report.results}
    out = []
    for table in plan.tables:
        r = actual[("row_count", table.table, None, None)]
        label = f"{table.table} rows" + (" (derived)" if table.count_source == "derived" else "")
        out.append({"what": label, "requested": table.count, "generated": r.actual, "verified": r.passed})
    for cond in plan.conditions:
        r = actual[("condition_count", cond.table, cond.column, cond.value)]
        out.append({"what": f"{cond.table}.{cond.column} = {cond.value}", "requested": cond.count, "generated": r.actual, "verified": r.passed})
    return out


def _print_run(label: str, plan: ScenarioPlan, dataset: GeneratedDataset, report, samples: int) -> None:
    print(f"scenario: {plan.scenario}")
    print(f"plan: status={plan.status} source={plan.source}  (source=mock means a hand-written AI reply, not a live model)")
    print(f"generated: seed={dataset.seed} as_of={dataset.as_of} rows={dataset.row_counts}")
    print("| requested | generated | verified |")
    print("|---|---|---|")
    for row in coverage_rows(plan, report):
        print(f"| {row['what']}: {row['requested']} | {row['generated']} | {'PASS' if row['verified'] else 'FAIL'} |")
    print("checks:")
    for r in report.results:
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.description} (expected {r.expected}, actual {r.actual})")
    print(f"{label}: {report.passed_count}/{len(report.results)} checks passed -> {'PASS' if report.passed else 'FAIL'}")
    for table in plan.generation_order[: 4 if samples else 0]:
        for row in sample_rows(dataset, table, samples):
            print(f"  sample {table}: {row}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario-id", default="S01", help="offline fixture scenario (default S01)")
    parser.add_argument("--all", action="store_true", help="run every valid offline fixture scenario")
    parser.add_argument("--live", default="", help="plan this scenario with live Gemini (uses free-tier quota)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--as-of", default="", help="reference date YYYY-MM-DD (default today)")
    parser.add_argument("--db", default="", help="also save the SQLite database to this new file")
    parser.add_argument("--replace", action="store_true", help="with --db: delete an existing .db/.sqlite3 file first")
    parser.add_argument("--sample", type=int, default=2, help="sample rows to print per table (0 = none)")
    parser.add_argument("--out", default="", help="write the coverage results to this JSON file")
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    db_path = (Path(args.db) if Path(args.db).is_absolute() else ROOT / args.db) if args.db else None
    if db_path and db_path.exists():
        if not args.replace:
            print(f"error: {db_path.name} already exists. Use a new --db file name, or add --replace to rebuild it.")
            return 2
        if db_path.suffix.lower() not in (".db", ".sqlite3") or not db_path.is_file():
            print(f"error: --replace only deletes .db or .sqlite3 files; {db_path.name} was left untouched.")
            return 2
        db_path.unlink()
        print(f"replaced: {db_path.name} (previous synthetic database deleted)")

    if args.all:
        from qaseed.evaluate import load_scenarios

        ids = [c["id"] for c in load_scenarios() if c["expected"]["status"] == "valid"]
        plans = [(i, mock_plan(i)) for i in ids]
    elif args.live:
        from qaseed.planner import plan_scenario

        plans = [("live", plan_scenario(args.live))]
    else:
        plans = [(args.scenario_id, mock_plan(args.scenario_id))]

    results, exit_code = [], 0
    for label, plan in plans:
        if plan.status != "valid":
            print(f"{label}: plan is {plan.status} ({', '.join(plan.issue_codes())}); nothing generated.")
            for issue in plan.issues:
                print(f"  {issue.code}: {issue.message}")
            exit_code = max(exit_code, 2)
            continue
        dataset, report = build_and_check(plan, args.seed, as_of, db_path if len(plans) == 1 else None)
        try:
            print(f"== {label}")
            _print_run(label, plan, dataset, report, 0 if args.all else args.sample)
            results.append({
                "id": label, "scenario": plan.scenario, "source": plan.source, "seed": dataset.seed,
                "as_of": dataset.as_of.isoformat(), "rows": dataset.row_counts,
                "checks_passed": report.passed_count, "checks_total": len(report.results),
                "coverage": coverage_rows(plan, report),
                "failed": [r.description for r in report.results if not r.passed],
            })
            if not report.passed:
                exit_code = max(exit_code, 1)
        finally:
            dataset.close()
        if db_path and len(plans) == 1:
            print(f"database saved: {db_path.name} (synthetic data; *.db is gitignored)")

    if args.all:
        passed = sum(r["checks_passed"] == r["checks_total"] for r in results)
        checks = sum(r["checks_total"] for r in results)
        ok = sum(r["checks_passed"] for r in results)
        print(f"summary: {passed}/{len(results)} scenarios fully verified; {ok}/{checks} checks passed")
    if args.out:
        out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"seed": args.seed, "as_of": as_of.isoformat(), "runs": results}, indent=2) + "\n", encoding="utf-8")
        try:
            shown = out.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            shown = out.name
        print(f"results={shown}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
