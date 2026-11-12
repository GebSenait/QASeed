"""CSV export (qaseed/export.py): the ZIP is re-opened and its contents verified, not just produced.

Day 5 MVP scope is CSV only; SQL export is a planned post-MVP enhancement.
"""

import csv
import io
import json
import zipfile
from datetime import date

import pytest

import qaseed.export as export
from qaseed.data_checks import CheckReport, CheckResult
from qaseed.export import REPORT_NAME, ExportBlocked, csv_zip, read_csv_zip, table_csv, verify_csv_zip
from qaseed.generator import build_and_check, mock_plan

AS_OF = date(2026, 11, 11)
TABLES = ["customers", "subscriptions", "invoices", "payments"]
COLUMNS = {
    "customers": ["customer_id", "email", "display_name", "country_code", "status", "created_on"],
    "subscriptions": ["subscription_id", "customer_id", "plan_name", "status", "monthly_price_cents", "started_on", "canceled_on"],
    "invoices": ["invoice_id", "subscription_id", "invoice_number", "amount_cents", "status", "issued_on", "due_on"],
    "payments": ["payment_id", "invoice_id", "amount_cents", "status", "attempted_on"],
}
REPORT = {"product": "QASeed", "export_format": "csv", "summary": {"passed": True}}


@pytest.fixture(scope="module")
def flagship():
    plan = mock_plan("S01")
    dataset, report = build_and_check(plan, 42, AS_OF)
    data = csv_zip(dataset.engine, plan.generation_order, report, REPORT)
    yield plan, dataset, report, data
    dataset.close()


def _rezip(data: bytes, edit) -> bytes:
    """Copy the ZIP, letting edit(name, text) change any file's text."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, edit(name, src.read(name).decode("utf-8")))
    return out.getvalue()


def test_zip_holds_four_table_csvs_and_the_validation_report(flagship):
    *_, data = flagship
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert names == [f"{t}.csv" for t in TABLES] + [REPORT_NAME]
    assert not any(name.endswith(".sql") for name in names)  # SQL export is post-MVP
    assert json.loads(zipfile.ZipFile(io.BytesIO(data)).read(REPORT_NAME)) == REPORT


def test_csv_record_counts_and_column_names(flagship):
    *_, data = flagship
    tables = read_csv_zip(data)
    assert {t: len(rows) for t, rows in tables.items()} == {t: 50 for t in TABLES}
    for table in TABLES:
        assert list(tables[table][0]) == COLUMNS[table]


def test_csv_ids_are_unique_ordered_and_complete(flagship):
    *_, data = flagship
    for table, rows in read_csv_zip(data).items():
        ids = [int(r[COLUMNS[table][0]]) for r in rows]
        assert ids == list(range(1, len(rows) + 1))
    emails = [r["email"] for r in read_csv_zip(data)["customers"]]
    assert len(set(emails)) == 50 and all(e.endswith("@example.com") for e in emails)


def test_csv_foreign_keys_resolve_across_files(flagship):
    *_, data = flagship
    t = read_csv_zip(data)
    customer_ids = {r["customer_id"] for r in t["customers"]}
    subscription_ids = {r["subscription_id"] for r in t["subscriptions"]}
    invoice_ids = {r["invoice_id"] for r in t["invoices"]}
    assert {r["customer_id"] for r in t["subscriptions"]} <= customer_ids
    assert {r["subscription_id"] for r in t["invoices"]} <= subscription_ids
    assert {r["invoice_id"] for r in t["payments"]} <= invoice_ids


def test_csv_contains_exactly_the_requested_conditions(flagship):
    *_, data = flagship
    t = read_csv_zip(data)
    assert sum(r["status"] == "canceled" for r in t["subscriptions"]) == 10
    assert sum(r["status"] == "overdue" for r in t["invoices"]) == 8
    assert sum(r["status"] == "failed" for r in t["payments"]) == 5
    assert all((r["canceled_on"] != "") == (r["status"] == "canceled") for r in t["subscriptions"])  # NULL -> empty cell


def test_reloading_the_csv_files_passes_the_same_checks(flagship):
    plan, _, report, data = flagship
    reloaded = verify_csv_zip(data, plan, AS_OF)
    assert reloaded.passed
    assert reloaded.passed_count == report.passed_count == 25


@pytest.mark.parametrize("name, edit, failed_kind", [
    ("subscriptions.csv", lambda text: text.replace(",canceled,", ",active,", 1), "condition_count"),
    ("payments.csv", lambda text: "\n".join(text.splitlines()[:-1]) + "\n", "row_count"),
    ("payments.csv", lambda text: text.replace("\n1,1,", "\n1,999,", 1), "csv_reload"),  # broken foreign key
    ("customers.csv", lambda text: text.replace("\n2,", "\n1,", 1), "csv_reload"),  # duplicate ID
])
def test_tampered_csv_files_fail_verification(flagship, name, edit, failed_kind):
    plan, *_, data = flagship
    tampered = _rezip(data, lambda n, text: edit(text) if n == name else text)
    report = verify_csv_zip(tampered, plan, AS_OF)
    assert not report.passed
    assert failed_kind in {r.kind for r in report.failed()}


def test_unreadable_zip_fails_verification(flagship):
    plan, *_ = flagship
    report = verify_csv_zip(b"not a zip", plan, AS_OF)
    assert [r.kind for r in report.failed()] == ["csv_reload"]


def test_csv_zip_is_byte_identical_for_the_same_dataset(flagship):
    plan, dataset, report, data = flagship
    assert csv_zip(dataset.engine, plan.generation_order, report, REPORT) == data


def test_csv_text_is_ordered_by_primary_key(flagship):
    _, dataset, *_ = flagship
    lines = table_csv(dataset.engine, "customers").splitlines()
    assert lines[0] == ",".join(COLUMNS["customers"])
    assert [int(line.split(",")[0]) for line in lines[1:]] == list(range(1, 51))
    assert list(csv.reader(io.StringIO(lines[1])))[0][1].endswith("@example.com")


@pytest.mark.parametrize("report", [
    CheckReport(),  # no checks run
    CheckReport(results=[CheckResult("plan", "row_count", "customers", "50 customers", 50, 49)]),
])
def test_export_is_blocked_unless_every_check_passed(flagship, report):
    plan, dataset, *_ = flagship
    with pytest.raises(ExportBlocked):
        csv_zip(dataset.engine, plan.generation_order, report)


def test_unknown_identifier_is_refused(flagship):
    _, dataset, *_ = flagship
    with pytest.raises(ValueError):
        table_csv(dataset.engine, "customers; DROP TABLE customers")


def test_sql_export_is_not_part_of_the_mvp():
    assert not hasattr(export, "sql_script")


# ---- Day 6: CSV formula (spreadsheet) injection -----------------------------------------


def test_no_csv_cell_starts_like_a_spreadsheet_formula():
    """Every value is generated by QASeed (never copied from the scenario); none may start like a formula."""
    from qaseed.pipeline import plan_request, run_generation, sample_scenarios

    checked = 0
    for sample in sample_scenarios():
        plan = plan_request(sample["scenario"], "offline").plan
        if plan.status != "valid":
            continue
        for seed in (0, 42, 999_999):
            result = run_generation(plan, seed, AS_OF)
            for table, rows in read_csv_zip(result.csv_zip).items():
                for row in rows:
                    for value in row.values():
                        checked += 1
                        assert value[:1] not in ("=", "+", "-", "@", "\t", "\r"), (sample["id"], table, value)
    assert checked > 1000
