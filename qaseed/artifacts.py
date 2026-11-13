"""The download of one verified run: a single ZIP read from the run's own export bytes (UI helper, read-only).

The Download step offers one file, the ZIP the pipeline already built and re-verified for the current run
(GenerationResult.csv_zip: one CSV per table + validation_report.json). Preview reads the files inside that
same ZIP, so what is previewed is exactly what is downloaded, and nothing is regenerated or re-checked here.
A run whose export was blocked has no download.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import datetime

from qaseed.export import REPORT_NAME

ZIP_MIME = "application/zip"


@dataclass(frozen=True)
class Artifact:
    name: str  # file name the browser saves
    description: str
    data: bytes
    mime: str


@dataclass(frozen=True)
class ZipEntry:
    name: str  # name inside the ZIP, e.g. customers.csv
    kind: str  # "csv" | "json"
    description: str
    rows: int | None
    data: bytes


def zip_name(created: datetime) -> str:
    """Clear, unique-per-run download name, e.g. qaseed_test_data_2026-11-13_2337.zip."""
    return f"qaseed_test_data_{created:%Y-%m-%d_%H%M}.zip"


def bundle(result, name: str) -> Artifact | None:
    """The run's verified ZIP, or None when the export was blocked."""
    if not getattr(result, "exportable", False) or not result.csv_zip:
        return None
    entries = zip_files(result.csv_zip)
    tables = [e.name[:-4] for e in entries if e.kind == "csv"]
    description = f"{len(tables)} CSV files + validation report"
    return Artifact(name=name, description=description, data=result.csv_zip, mime=ZIP_MIME)


def zip_files(data: bytes) -> list[ZipEntry]:
    """Every file inside the ZIP, in stored order, with a short description and its row count (CSV only)."""
    entries = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            content = archive.read(name)
            if name.endswith(".csv"):
                rows = len(list(csv.DictReader(io.StringIO(content.decode("utf-8")))))
                entries.append(ZipEntry(name, "csv", f"{name[:-4].capitalize()} records", rows, content))
            elif name == REPORT_NAME:
                entries.append(ZipEntry(name, "json", "Verification report (every SQL check)", None, content))
    return entries


def csv_rows(data: bytes, limit: int) -> tuple[list[dict], int]:
    """(first `limit` rows as dicts, total row count) of a UTF-8 CSV file."""
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    return rows[:limit], len(rows)
