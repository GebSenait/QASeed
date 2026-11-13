"""Download-step file (qaseed.artifacts): the run's verified ZIP, read from its own bytes, never regenerated."""

import dataclasses
import io
import json
import zipfile
from datetime import date, datetime

import pytest

from qaseed import artifacts, pipeline
from qaseed.generator import mock_plan

AS_OF = date(2026, 11, 13)


@pytest.fixture(scope="module")
def result():
    return pipeline.run_generation(mock_plan("S05"), 42, AS_OF)


def test_zip_name_is_clear_and_unique_per_run():
    assert artifacts.zip_name(datetime(2026, 11, 13, 23, 7)) == "qaseed_test_data_2026-11-13_2307.zip"
    assert artifacts.zip_name(datetime(2026, 11, 13, 23, 7)) != artifacts.zip_name(datetime(2026, 11, 13, 23, 8))


def test_bundle_is_the_verified_zip_itself(result):
    item = artifacts.bundle(result, "qaseed_test_data_2026-11-13_2307.zip")
    assert item.data == result.csv_zip and item.mime == "application/zip"  # Preview and Download use these bytes
    assert item.name == "qaseed_test_data_2026-11-13_2307.zip"
    assert item.description == "3 CSV files + validation report"


def test_a_blocked_export_has_no_download(result):
    assert artifacts.bundle(dataclasses.replace(result, passed=False), "x.zip") is None
    assert artifacts.bundle(dataclasses.replace(result, csv_zip=None), "x.zip") is None
    assert artifacts.bundle(None, "x.zip") is None


def test_zip_files_lists_every_entry_with_real_row_counts(result):
    entries = artifacts.zip_files(result.csv_zip)
    with zipfile.ZipFile(io.BytesIO(result.csv_zip)) as archive:
        assert [e.name for e in entries] == archive.namelist()
        for e in entries:
            assert e.data == archive.read(e.name)
    rows = {e["file"]: e["rows"] for e in result.zip_contents}
    for e in entries:
        if e.kind == "csv":
            assert e.rows == rows[e.name] and e.description == f"{e.name[:-4].capitalize()} records"
    report = entries[-1]
    assert report.kind == "json" and report.rows is None and json.loads(report.data)["summary"]["passed"] is True


def test_csv_preview_limits_rows_without_changing_the_file(result):
    entry = next(e for e in artifacts.zip_files(result.csv_zip) if e.kind == "csv")
    every_row = entry.data.decode("utf-8").splitlines()
    rows, total = artifacts.csv_rows(entry.data, 3)
    assert total == len(every_row) - 1 and len(rows) == min(3, total)
    assert list(rows[0]) == every_row[0].split(",")
