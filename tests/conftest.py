"""Shared pytest configuration, including pytest-html report customisation.

The pytest-html hooks are optional: plain `python -m pytest -q` works with or
without pytest-html installed. Report metadata never includes secrets; only
the Gemini model ID is read from .env, never the key.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from qaseed.config import ROOT

REPORT_DAY_ENV = "QASEED_REPORT_DAY"
E2E_LIVE_ENV = "QASEED_E2E_LIVE"  # "1": tests/test_e2e_suite.py also runs its 3 live Gemini tests
BASE_URL_ENV = "QASEED_BASE_URL"  # set: browser tests open this deployed app instead of a local server


def _live_run() -> bool:
    return os.environ.get(E2E_LIVE_ENV) == "1" and bool(os.environ.get(BASE_URL_ENV, "").strip())


def _git(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _configured_model() -> str:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return "not configured"
    return (dotenv_values(ROOT / ".env").get("GEMINI_MODEL") or "not configured").strip()


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    config.addinivalue_line("markers", "browser: real-browser test (Streamlit + headless Chromium via Playwright)")
    config.addinivalue_line("markers", "e2e: end-to-end suite (tests/test_e2e_suite.py)")
    config.addinivalue_line("markers", "live: calls the real Gemini API (opt-in: QASEED_E2E_LIVE=1 and QASEED_BASE_URL)")
    try:
        from pytest_metadata.plugin import metadata_key
    except ImportError:
        return
    if metadata_key not in config.stash:
        return
    metadata = config.stash[metadata_key]
    day = os.environ.get(REPORT_DAY_ENV, "")
    metadata["Project"] = "QASeed"
    if day:
        metadata["Challenge day"] = f"Day {day}"
    metadata["Git branch"] = _git("rev-parse", "--abbrev-ref", "HEAD")
    metadata["Git commit"] = _git("rev-parse", "--short", "HEAD")
    metadata["Uncommitted changes"] = "yes" if _git("status", "--porcelain") not in ("", "unknown") else "no"
    base_url = os.environ.get(BASE_URL_ENV, "").strip()
    metadata["Browser tests target"] = base_url or "local Streamlit server (no API key)"
    if base_url:
        metadata["Runtime LLM"] = "Google Gemini (the deployed app's own settings)"
    else:
        metadata["Runtime LLM"] = f"Google Gemini ({_configured_model()})"
    if _live_run():
        metadata["LLM calls in these tests"] = ("3 live Gemini plans through the deployed app (tests marked live); "
                                                "every other test uses recorded replies or is refused before the API")
    else:
        metadata["LLM calls in these tests"] = "None: mock replies only (live results come from python -m qaseed.evaluate --live)"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Keep each phase's result on the test item (rep_call), so fixtures can react to a failure."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.hookimpl(optionalhook=True)
def pytest_html_report_title(report):
    day = os.environ.get(REPORT_DAY_ENV, "")
    report.title = f"QASeed — Day {day} Test Report" if day else "QASeed — Test Report"


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_summary(prefix):
    if _live_run():
        prefix.append(
            "<p><b>Scope:</b> end-to-end browser tests (headless Chromium) against the deployed app "
            f"<code>{os.environ.get(BASE_URL_ENV, '').strip()}</code>: ready-made examples (recorded replies), "
            "refused inputs, and 3 live Gemini plans. Live results depend on the model's reply on the day.</p>"
        )
        return
    prefix.append(
        "<p><b>Scope:</b> automated offline tests (unit, contract, mock-LLM planner scenarios, "
        "Streamlit smoke test). These results show QASeed's own code is correct; they are "
        "<b>not</b> a measure of live model accuracy.</p>"
    )
