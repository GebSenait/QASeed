"""Seeded linked-data generator (qaseed/generator.py). Offline: plans come from mock replies."""

import json
from datetime import date

import pytest
from sqlalchemy import text

import qaseed.generator as generator
from qaseed.data_checks import run_checks
from qaseed.evaluate import load_scenarios
from qaseed.generator import GenerationError, coverage_rows, generate, mock_plan

AS_OF = date(2026, 11, 11)
VALID_IDS = [c["id"] for c in load_scenarios() if c["expected"]["status"] == "valid"]


def rows(dataset, sql, **params):
    with dataset.engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def scalar(dataset, sql, **params):
    with dataset.engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


@pytest.fixture
def flagship():
    dataset = generate(mock_plan("S01"), seed=42, as_of=AS_OF)
    yield dataset
    dataset.close()


def test_flagship_counts_and_exact_conditions(flagship):
    assert flagship.row_counts == {"customers": 50, "subscriptions": 50, "invoices": 50, "payments": 50}
    assert scalar(flagship, "SELECT COUNT(*) FROM subscriptions WHERE status = 'canceled'") == 10
    assert scalar(flagship, "SELECT COUNT(*) FROM invoices WHERE status = 'overdue'") == 8
    assert scalar(flagship, "SELECT COUNT(*) FROM payments WHERE status = 'failed'") == 5


def test_unrequested_rows_get_the_normal_status(flagship):
    statuses = {r["status"] for r in rows(flagship, "SELECT DISTINCT status FROM subscriptions")}
    assert statuses == {"canceled", "active"}
    assert {r["status"] for r in rows(flagship, "SELECT DISTINCT status FROM invoices")} == {"overdue", "paid"}
    assert {r["status"] for r in rows(flagship, "SELECT DISTINCT status FROM customers")} == {"active"}


def test_rows_are_linked_and_every_parent_is_used(flagship):
    assert scalar(flagship, "SELECT COUNT(DISTINCT customer_id) FROM subscriptions") == 50
    assert scalar(flagship, "SELECT COUNT(*) FROM subscriptions s LEFT JOIN customers c USING (customer_id) WHERE c.customer_id IS NULL") == 0
    assert scalar(flagship, "PRAGMA foreign_keys") == 1


def test_values_are_synthetic(flagship):
    for row in rows(flagship, "SELECT email, country_code FROM customers"):
        assert row["email"].endswith("@example.com")
        assert len(row["country_code"]) == 2


def test_status_drives_dates(flagship):
    assert scalar(flagship, "SELECT COUNT(*) FROM subscriptions WHERE status = 'canceled' AND canceled_on IS NULL") == 0
    assert scalar(flagship, "SELECT COUNT(*) FROM subscriptions WHERE status <> 'canceled' AND canceled_on IS NOT NULL") == 0
    assert scalar(flagship, "SELECT COUNT(*) FROM invoices WHERE status = 'overdue' AND due_on >= :d", d=AS_OF.isoformat()) == 0


def test_same_seed_same_data_different_seed_different_data():
    plan = mock_plan("S05")
    a, b, c = (generate(plan, seed=s, as_of=AS_OF) for s in (7, 7, 8))
    try:
        dump = lambda d: rows(d, "SELECT * FROM invoices ORDER BY invoice_id") + rows(d, "SELECT * FROM customers ORDER BY customer_id")
        assert dump(a) == dump(b)
        assert dump(a) != dump(c)
    finally:
        for d in (a, b, c):
            d.close()


@pytest.mark.parametrize("scenario_id", VALID_IDS)
def test_every_valid_fixture_scenario_generates_and_passes_all_checks(scenario_id):
    plan = mock_plan(scenario_id)
    dataset = generate(plan, seed=42, as_of=AS_OF)
    try:
        report = run_checks(dataset.engine, plan, AS_OF)
        assert [r.description for r in report.failed()] == []
        assert dataset.row_counts == plan.counts()
    finally:
        dataset.close()


def test_only_valid_plans_are_generated():
    with pytest.raises(GenerationError, match="Only a valid plan"):
        generate(mock_plan("S08"))


def test_condition_that_cannot_be_kept_exact_is_refused():
    plan = mock_plan("S07")  # 25 subscriptions = 5 trialing + 5 past_due + 15 active
    plan.tables = [t.model_copy(update={"count": 30}) if t.table == "subscriptions" else t for t in plan.tables]
    # 30 rows, 25 requested, 5 left: 'canceled' is still unused, so those 5 become canceled ...
    generate(plan, as_of=AS_OF).close()
    # ... but with every allowed value requested there is nothing exact left for the remaining rows.
    plan.conditions.append(plan.conditions[0].model_copy(update={"value": "canceled", "count": 1}))
    with pytest.raises(GenerationError, match="no value left"):
        generate(plan, as_of=AS_OF)


def test_database_file_is_written_and_never_overwritten(tmp_path):
    target = tmp_path / "demo.db"
    generate(mock_plan("S02"), as_of=AS_OF, path=target).close()
    assert target.exists() and target.stat().st_size > 0
    with pytest.raises(GenerationError, match="already exists"):
        generate(mock_plan("S02"), as_of=AS_OF, path=target)


def test_coverage_rows_mark_derived_counts(flagship):
    plan = mock_plan("S01")
    coverage = coverage_rows(plan, run_checks(flagship.engine, plan, AS_OF))
    assert coverage[0] == {"what": "customers rows", "requested": 50, "generated": 50, "verified": True}
    assert {"what": "subscriptions rows (derived)", "requested": 50, "generated": 50, "verified": True} in coverage
    assert {"what": "invoices.status = overdue", "requested": 8, "generated": 8, "verified": True} in coverage


def test_cli_single_scenario(capsys):
    assert generator.main(["--scenario-id", "S01", "--as-of", "2026-11-11", "--sample", "1"]) == 0
    out = capsys.readouterr().out
    assert "source=mock" in out
    assert "| subscriptions.status = canceled: 10 | 10 | PASS |" in out
    assert "checks passed -> PASS" in out
    assert "sample customers:" in out


def test_cli_all_writes_results_without_local_paths(tmp_path, capsys):
    out_file = tmp_path / "datagen.json"
    assert generator.main(["--all", "--as-of", "2026-11-11", "--out", str(out_file)]) == 0
    printed = capsys.readouterr().out
    assert f"summary: {len(VALID_IDS)}/{len(VALID_IDS)} scenarios fully verified" in printed
    assert "results=datagen.json" in printed and str(tmp_path) not in printed
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(data["runs"]) == len(VALID_IDS) and all(not r["failed"] for r in data["runs"])


def test_cli_invalid_plan_generates_nothing(capsys):
    assert generator.main(["--scenario-id", "S16"]) == 2
    assert "nothing generated" in capsys.readouterr().out


def test_cli_existing_db_file_gives_a_clear_message_not_a_traceback(tmp_path, capsys):
    target = tmp_path / "demo.db"
    target.write_bytes(b"old")
    assert generator.main(["--as-of", "2026-11-11", "--sample", "0", "--db", str(target)]) == 2
    assert "demo.db already exists" in capsys.readouterr().out
    assert target.read_bytes() == b"old"


def test_cli_replace_rebuilds_the_db_file(tmp_path, capsys):
    target = tmp_path / "demo.db"
    target.write_bytes(b"old")
    assert generator.main(["--as-of", "2026-11-11", "--sample", "0", "--db", str(target), "--replace"]) == 0
    out = capsys.readouterr().out
    assert "replaced: demo.db" in out and "database saved: demo.db" in out
    assert target.read_bytes()[:16] == b"SQLite format 3\x00"


def test_cli_replace_never_deletes_other_file_types(tmp_path, capsys):
    target = tmp_path / "notes.txt"
    target.write_text("keep me", encoding="utf-8")
    assert generator.main(["--sample", "0", "--db", str(target), "--replace"]) == 2
    assert "left untouched" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "keep me"
