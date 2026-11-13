"""Real-browser smoke test: the Streamlit app in headless Chromium (Playwright).

AppTest (tests/test_app.py) checks the app logic without a browser. This file
starts the real Streamlit server and clicks through it like a user, so it also
catches problems that only show up in a browser (layout, widgets, downloads).

No API call is made: the local server runs with GEMINI_API_KEY blank, and every
test plans only ready-made examples (recorded AI replies).

Setup once:  python -m playwright install chromium
Skip it:     python -m pytest -m "not browser"
Deployed app (smoke test the public URL, still no API call):
    QASEED_BASE_URL=https://<your-app>.streamlit.app python -m pytest -m browser tests/test_browser_smoke.py
"""

from __future__ import annotations

import io
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

import pytest

from qaseed import pipeline
from qaseed.config import ROOT

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

PAYMENT_ID = "D5-01"
STARTUP_SECONDS = 60
BASE_URL = os.environ.get("QASEED_BASE_URL", "").rstrip("/")  # set to test a deployed app instead of a local server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def app_url():
    if BASE_URL:  # deployed app: nothing to start
        yield BASE_URL
        return
    port = _free_port()
    env = {**os.environ, "GEMINI_API_KEY": "", "GEMINI_MODEL": ""}  # blank wins over .env: no live API
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"), "--server.headless", "true",
         "--server.address", "127.0.0.1", "--server.port", str(port), "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + STARTUP_SECONDS
        while True:
            try:
                if urllib.request.urlopen(f"{url}/_stcore/health", timeout=2).read() == b"ok":
                    break
            except OSError:
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                pytest.fail("Streamlit server did not start")
            time.sleep(0.5)
        yield url
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except sync_api.Error as exc:  # browser binaries not downloaded yet
            pytest.skip(f"Chromium for Playwright is not installed (python -m playwright install chromium): {exc}")
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser, app_url):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = context.new_page()
    page.goto(app_url)
    heading = page.get_by_text("1 · Describe the scenario")
    try:
        heading.wait_for(timeout=120_000 if BASE_URL else 30_000)  # a hosted app may wake from sleep
    except sync_api.Error:  # the Streamlit websocket occasionally stalls on a busy machine: reload once
        page.reload()
        heading.wait_for(timeout=60_000)
    use_ready_made_mode(page)
    yield page
    context.close()


def use_ready_made_mode(page) -> None:
    """Ready-made examples mode (recorded replies): never spends Live AI quota, locally or on a deployed app."""
    picker = page.locator(".st-key-sample_choice")
    if picker.count() == 0:
        page.get_by_test_id("stSidebar").get_by_text("Ready-made examples", exact=True).click()
        picker.wait_for(timeout=30_000)


def pick_example(page, example_id: str) -> None:
    """Choose an example, copy it into the scenario box and plan it, waiting for each Streamlit rerun."""
    expect = sync_api.expect
    text = next(s["scenario"] for s in pipeline.sample_scenarios() if s["id"] == example_id)
    picker = page.locator(".st-key-sample_choice input")  # Streamlit renders the dropdown lazily
    picker.wait_for(timeout=30_000)
    picker.click()
    page.get_by_role("option", name=re.compile(rf"^{re.escape(example_id)} ")).click()  # selecting loads it
    expect(page.get_by_role("textbox", name="Your test scenario (plain English)")).to_have_value(text, timeout=15_000)
    page.locator(".st-key-plan_btn button").click()


def test_brand_and_ready_made_mode_without_api_key(page):
    expect = sync_api.expect
    expect(page.locator(".qs-name")).to_have_text("QASeed")
    expect(page.locator(".qs-brand img")).to_be_visible()  # tracked logo: branded locally and when hosted
    if not BASE_URL:  # the local test server has no key; a deployed app has one in its Secrets
        expect(page.get_by_text("Live AI is not set up")).to_be_visible()
    expect(page.locator(".st-key-sample_choice")).to_be_visible()


def test_payment_example_end_to_end_with_verified_csv_download(page):
    pick_example(page, PAYMENT_ID)
    page.get_by_text("Plan is valid").wait_for(timeout=30_000)
    page.locator(".st-key-generate_btn button").click()
    page.get_by_text("Download verified").wait_for(timeout=30_000)
    values = page.locator('[data-testid="stMetricValue"]')
    sync_api.expect(values.nth(0)).to_have_text("48")  # rows generated
    sync_api.expect(values.nth(1)).to_have_text("24/24")  # data checks passed
    with page.expect_download() as info:
        page.locator(".st-key-download_csv_zip button").click()
    data = info.value.path().read_bytes()
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert names == ["customers.csv", "subscriptions.csv", "invoices.csv", "payments.csv", "validation_report.json"]
    assert info.value.suggested_filename == "qaseed_d5-01_seed42_csv.zip"
    with page.expect_download() as report_info:
        page.locator(".st-key-download_report_json button").click()
    import json

    report = json.loads(report_info.value.path().read_text(encoding="utf-8"))
    assert report_info.value.suggested_filename == "qaseed_d5-01_seed42_validation_report.json"
    assert report["export_format"] == "csv" and report["summary"]["passed"] is True
    assert report == json.loads(zipfile.ZipFile(io.BytesIO(data)).read("validation_report.json"))  # same report


def test_negative_example_shows_plain_reason_and_no_generate_button(page):
    pick_example(page, "S16")
    page.get_by_text("Bad request: status counts exceed the total.").wait_for(timeout=30_000)
    assert page.get_by_text("on purpose").count() == 0 and page.get_by_text("How to fix").count() == 0
    assert page.locator(".st-key-generate_btn").count() == 0


def tracker(page) -> list[str]:
    sync_api.expect(page.locator(".qs-step")).to_have_count(4)  # drawn at the end of each Streamlit run
    return page.locator(".qs-step").evaluate_all("els => els.map(e => e.className.replace('qs-step', '').trim())")


def test_full_journey_tracker_download_and_reset_in_a_real_browser(page):
    """Each step shows its real state; downloading completes the flow; Reset returns to a clean start."""
    expect = sync_api.expect
    assert tracker(page) == ["now", "todo", "todo", "todo"]
    pick_example(page, PAYMENT_ID)
    page.get_by_text("Plan is valid").wait_for(timeout=30_000)
    expect(page.locator(".qs-step").nth(1)).to_have_class(re.compile(r"\bnow\b"))
    assert tracker(page) == ["done", "now", "todo", "todo"]  # review step is active, not skipped
    page.locator(".st-key-generate_btn button").click()
    page.get_by_text("Download verified").wait_for(timeout=30_000)
    assert tracker(page) == ["done", "done", "done", "now"]
    with page.expect_download():
        page.locator(".st-key-download_csv_zip button").click()
    expect(page.locator(".qs-step").nth(3)).to_have_class(re.compile(r"\bdone\b"))
    assert tracker(page) == ["done", "done", "done", "done"]
    page.locator(".st-key-reset_btn button").click()
    expect(page.get_by_role("textbox", name="Your test scenario (plain English)")).to_have_value("")
    expect(page.locator(".qs-step").nth(0)).to_have_class(re.compile(r"\bnow\b"))
    assert tracker(page) == ["now", "todo", "todo", "todo"]
    assert page.get_by_text("2 · Review the AI plan").count() == 0
    assert page.locator(".st-key-download_csv_zip").count() == 0


def test_header_is_not_hidden_under_streamlits_top_bar(page):
    """The QASeed brand row starts below Streamlit's top bar with the sidebar open or collapsed.

    Day 7 hotfix: on Streamlit Community Cloud the top bar is opaque, so a brand row under it was cut off.
    """
    expect = sync_api.expect

    def tops():
        return page.evaluate("""() => [document.querySelector('[data-testid="stHeader"]').getBoundingClientRect().bottom,
                                       document.querySelector('.qs-brand').getBoundingClientRect().top]""")

    page.locator(".qs-brand").wait_for(timeout=30_000)
    page.locator('[data-testid="stHeader"]').wait_for(state="attached", timeout=30_000)
    header_bottom, brand_top = tops()
    assert brand_top >= header_bottom, f"sidebar open: header {header_bottom}px overlaps the brand row at {brand_top}px"
    page.hover('[data-testid="stSidebar"]')
    page.get_by_role("button", name="Collapse sidebar").click()
    expect(page.locator('[data-testid="stSidebar"]')).to_have_attribute("data-collapsed", "true")
    page.wait_for_timeout(600)  # padding transition
    header_bottom, brand_top = tops()
    assert brand_top >= header_bottom, f"header {header_bottom}px overlaps the brand row at {brand_top}px"
    expect(page.locator(".qs-name")).to_be_visible()
