"""CSV export of a generated, verified dataset (Day 5 MVP scope: CSV only).

One ZIP download holds:
- customers.csv, subscriptions.csv, invoices.csv, payments.csv: columns in
  schema order, rows ordered by primary key, UTF-8, header row, empty cell
  for NULL;
- validation_report.json: scenario, plan source, seed, as-of date, row
  counts, requested vs generated vs verified, and every data check.

Exports are only produced when every data check passed (qaseed.data_checks);
otherwise ExportBlocked is raised. verify_csv_zip() re-reads the finished ZIP,
loads the CSV files into a fresh SQLite database with the sample schema and
re-runs the same checks, so the file the user downloads is itself verified,
not just the database it came from.

SQL export (INSERT script) is a planned post-MVP enhancement, not part of
the Day 5 MVP.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from qaseed.data_checks import CheckReport, CheckResult, run_checks
from qaseed.plan_models import ScenarioPlan

REPORT_NAME = "validation_report.json"
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)  # fixed timestamp: the same dataset always gives byte-identical ZIP files


class ExportBlocked(RuntimeError):
    """The dataset did not pass every data check, so it must not be exported."""


def ensure_exportable(report: CheckReport) -> None:
    if not report.passed:
        failed = len(report.failed()) if report.results else "no"
        raise ExportBlocked(f"Export blocked: {failed} data checks failed or none were run.")


def table_rows(engine: Engine, table: str) -> tuple[list[str], list[tuple]]:
    """(column names, rows ordered by the first column = primary key)."""
    _check_ident(table)
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {table} ORDER BY 1"))
        return list(result.keys()), [tuple(row) for row in result]


def table_csv(engine: Engine, table: str) -> str:
    columns, rows = table_rows(engine, table)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(["" if value is None else value for value in row] for row in rows)
    return buffer.getvalue()


def csv_zip(engine: Engine, tables: list[str], report: CheckReport, validation_report: dict | None = None) -> bytes:
    """ZIP with one CSV per table (dependency order) and, if given, validation_report.json."""
    ensure_exportable(report)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for table in tables:
            _write(archive, f"{table}.csv", table_csv(engine, table))
        if validation_report is not None:
            _write(archive, REPORT_NAME, json.dumps(validation_report, indent=2, default=str) + "\n")
    return buffer.getvalue()


def read_csv_zip(data: bytes) -> dict[str, list[dict]]:
    """{table: rows as dicts} for every .csv file in the ZIP (values are strings, '' = NULL)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {
            name[:-4]: list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8"))))
            for name in archive.namelist() if name.endswith(".csv")
        }


def verify_csv_zip(data: bytes, plan: ScenarioPlan, as_of: date) -> CheckReport:
    """Load the ZIP's CSV files into a fresh database with the sample schema and re-run every check."""
    from qaseed.generator import create_database

    try:
        tables = read_csv_zip(data)
    except (zipfile.BadZipFile, UnicodeDecodeError, csv.Error):
        return _reload_failed("the ZIP or a CSV file could not be read")
    engine = create_database()  # in-memory, schema applied, foreign keys enforced
    try:
        try:
            _load(engine, tables, plan)
        except Exception as exc:  # bad IDs, broken links, wrong columns: the file does not prove the data
            return _reload_failed(f"the CSV files do not load into the schema ({type(exc).__name__})")
        return run_checks(engine, plan, as_of)
    finally:
        engine.dispose()


def _reload_failed(reason: str) -> CheckReport:
    return CheckReport(results=[CheckResult("integrity", "csv_reload", "*", f"CSV files reload cleanly: {reason}", 1, 0)])


def _load(engine: Engine, tables: dict[str, list[dict]], plan: ScenarioPlan) -> None:
    if set(tables) != set(plan.generation_order):
        raise ValueError("unexpected set of CSV files")
    with engine.begin() as conn:
        for table in plan.generation_order:  # parents first, so foreign keys are checked on insert
            rows = tables[table]
            if not rows:
                continue
            columns = list(rows[0])
            for name in (table, *columns):
                _check_ident(name)
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"
            conn.execute(text(sql), [{k: (None if v == "" else v) for k, v in row.items()} for row in rows])


def _write(archive: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content.encode("utf-8"))


def _check_ident(name: str) -> None:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"Unexpected identifier: {name!r}")
