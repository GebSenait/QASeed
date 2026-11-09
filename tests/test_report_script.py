"""Daily pytest-html report helper (scripts/test_report.py). Does not run pytest itself."""

import importlib.util
from datetime import date, datetime

from qaseed.config import ROOT

_spec = importlib.util.spec_from_file_location("qaseed_test_report", ROOT / "scripts" / "test_report.py")
test_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(test_report)


def test_challenge_day_mapping():
    assert test_report.challenge_day(date(2026, 11, 9)) == 3
    assert test_report.challenge_day(date(2026, 11, 14)) == 8
    assert test_report.challenge_day(date(2026, 11, 7)) == 1
    assert test_report.challenge_day(date(2026, 11, 15)) is None
    assert test_report.challenge_day(date(2026, 11, 6)) is None


def test_index_is_created_once_and_rows_appended(tmp_path):
    stamp = datetime(2026, 11, 9, 10, 30, 0)
    report = tmp_path / "day3_test_report_20261109-103000.html"
    index = test_report._append_index(tmp_path, 3, stamp, report, 0, {"passed": 5})
    test_report._append_index(tmp_path, 3, stamp, report, 1, {"passed": 4, "failed": 1})
    text = index.read_text(encoding="utf-8")
    assert text.count("# QASeed — Day 3 test reports") == 1
    assert "| 2026-11-09 10:30:00 | PASS | 5 | 0 | 0 | 0 |" in text
    assert "| FAIL (exit 1) | 4 | 1 | 0 | 0 |" in text
    assert f"[{report.name}]({report.name})" in text


def test_out_of_range_date_requires_explicit_day(monkeypatch, capsys):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 12, 1)

    monkeypatch.setattr(test_report, "date", FakeDate)
    assert test_report.main([]) == 2
    assert "Pass --day N" in capsys.readouterr().out
