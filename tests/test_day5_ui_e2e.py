"""scripts/day5_ui_e2e.py: drives app.py headless and checks the downloaded ZIP. Offline only here."""

import importlib.util
import io
import zipfile

import pytest

import qaseed.llm_client as llm_client
from qaseed.config import ROOT
from qaseed.llm_client import KEY_NAME

_spec = importlib.util.spec_from_file_location("qaseed_day5_ui_e2e", ROOT / "scripts" / "day5_ui_e2e.py")
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)


@pytest.fixture
def no_requests(monkeypatch):
    monkeypatch.setenv(KEY_NAME, "")  # restored after the test (the script blanks it for offline runs)

    def never(url, payload, key):
        raise AssertionError("no API request expected")

    monkeypatch.setattr(llm_client, "post_json", never)


def test_offline_payment_scenario_passes_end_to_end(no_requests, capsys):
    assert e2e.main(["--as-of", "2026-11-11"]) == 0
    out = capsys.readouterr().out
    assert "mode=offline plan_status=valid source=mock" in out
    assert "PASS  every failed payment's invoice is unpaid (open/overdue)" in out
    assert "PASS  every payment -> invoice -> subscription -> customer link resolves  [12/12]" in out
    assert "result=PASS" in out


def test_live_mode_needs_explicit_confirmation(no_requests, capsys):
    assert e2e.main(["--live"]) == 2
    assert "No request was sent" in capsys.readouterr().out


def test_offline_mode_refuses_text_without_a_recorded_reply(no_requests, capsys):
    assert e2e.main(["--scenario", "Generate 3 customers."]) == 2
    assert "only runs built-in samples" in capsys.readouterr().out


def test_zip_check_catches_a_failed_payment_on_a_paid_invoice(no_requests):
    from datetime import date

    from qaseed import pipeline

    outcome = pipeline.plan_request(e2e.PAYMENT_SCENARIO, "offline")
    data = pipeline.run_generation(outcome.plan, 42, date(2026, 11, 11)).csv_zip
    assert all(ok for _, ok, _ in e2e.verify_zip(data, {"succeeded": 8, "failed": 4}))
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            text = src.read(name).decode("utf-8")
            if name == "invoices.csv":
                text = text.replace(",open,", ",paid,")
            dst.writestr(name, text)
    failed = [name for name, ok, _ in e2e.verify_zip(out.getvalue(), {"succeeded": 8, "failed": 4}) if not ok]
    assert failed == ["every failed payment's invoice is unpaid (open/overdue)"]
