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
    metadata["Runtime LLM"] = f"Google Gemini ({_configured_model()})"
    metadata["LLM calls in these tests"] = "None: mock replies only (live results come from python -m qaseed.evaluate --live)"


@pytest.hookimpl(optionalhook=True)
def pytest_html_report_title(report):
    day = os.environ.get(REPORT_DAY_ENV, "")
    report.title = f"QASeed — Day {day} Test Report" if day else "QASeed — Test Report"


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_summary(prefix):
    prefix.append(
        "<p><b>Scope:</b> automated offline tests (unit, contract, mock-LLM planner scenarios, "
        "Streamlit smoke test). These results show QASeed's own code is correct; they are "
        "<b>not</b> a measure of live model accuracy.</p>"
    )
