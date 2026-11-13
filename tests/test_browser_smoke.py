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
The plain Streamlit Community Cloud URL is fine: Cloud shows the app in a frame
served from /~/+/, and the tests switch to that inner address themselves.
"""

from __future__ import annotations

import io
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

import pytest

from qaseed import pipeline
from qaseed.config import ROOT

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

PAYMENT_ID = "D5-01"
STARTUP_SECONDS = 60
CLOUD_APP_PATH = "/~/+/"  # where Streamlit Community Cloud serves the app itself (needs the trailing slash)


def deployed_app_url(url: str) -> str:
    """The address the tests open for a deployed app.

    Streamlit Community Cloud's public URL (https://<name>.streamlit.app) is a wrapper page with the app in a
    frame, so QASeed's elements are not on it. For that URL the tests open the frame's own address instead.
    Any other URL is used as given, without a trailing slash.
    """
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.endswith(".streamlit.app") and parts.path.strip("/") == "" and not parts.query:
        return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc, CLOUD_APP_PATH, "", ""))
    return url.rstrip("/")


BASE_URL = deployed_app_url(os.environ.get("QASEED_BASE_URL", ""))  # set to test a deployed app instead of a local server


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


HEADLINE = "From test scenarios to verified data."


@pytest.fixture
def landing(browser, app_url):
    """A fresh visitor on the landing page."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = context.new_page()
    page.goto(app_url)
    heading = page.get_by_text(HEADLINE)
    try:
        heading.wait_for(timeout=120_000 if BASE_URL else 30_000)  # a hosted app may wake from sleep
    except sync_api.Error:  # the Streamlit websocket occasionally stalls on a busy machine: reload once
        page.reload()
        heading.wait_for(timeout=60_000)
    yield page
    context.close()


@pytest.fixture
def page(landing):
    """Get started -> empty Describe step, in Ready-made examples mode."""
    landing.locator(".st-key-start_btn button").click()
    landing.get_by_role("textbox", name="Test scenario").wait_for(timeout=30_000)
    use_ready_made_mode(landing)
    return landing


def use_ready_made_mode(page) -> None:
    """Ready-made examples mode (recorded replies): never spends Live AI quota, locally or on a deployed app."""
    picker = page.locator(".st-key-sample_choice")
    if picker.count() == 0:
        page.get_by_test_id("stSidebar").get_by_text("Ready-made examples", exact=True).click()
        picker.wait_for(timeout=30_000)


def pick_example(page, example_id: str) -> None:
    """Choose an example, copy it into the scenario box and press Generate, waiting for each Streamlit rerun."""
    expect = sync_api.expect
    text = next(s["scenario"] for s in pipeline.sample_scenarios() if s["id"] == example_id)
    picker = page.locator(".st-key-sample_choice input")  # Streamlit renders the dropdown lazily
    picker.wait_for(timeout=30_000)
    picker.click()
    page.get_by_role("option", name=re.compile(rf"^{re.escape(example_id)} ")).click()  # selecting loads it
    expect(page.get_by_role("textbox", name="Test scenario")).to_have_value(text, timeout=15_000)
    page.locator(".st-key-plan_btn button").click()


def review_and_verify(page) -> None:
    """Review step: confirm the plan, then Generate & verify."""
    expect = sync_api.expect
    page.get_by_text("Review the plan").wait_for(timeout=30_000)
    expect(page.locator(".st-key-generate_btn button")).to_be_disabled()  # confirmation comes first
    page.locator(".st-key-reviewed label").click()
    expect(page.locator(".st-key-generate_btn button")).to_be_enabled(timeout=15_000)
    page.locator(".st-key-generate_btn button").click()
    page.get_by_text("Verification results").wait_for(timeout=30_000)


def open_outputs(page) -> None:
    page.locator(".st-key-outputs_btn button").click()
    page.get_by_text("Download results").wait_for(timeout=30_000)


def tracker(page) -> list[str]:
    """now / done / blocked / todo of the four workspace steps, read from the stepper buttons' keys."""
    steps = page.locator('[class*="st-key-nav_"]')
    sync_api.expect(steps).to_have_count(4)
    classes = steps.evaluate_all("els => els.map(e => [...e.classList].find(c => c.startsWith('st-key-nav_')))")
    return [c.split("_", 2)[2] for c in classes]


def test_landing_page_has_one_cta_and_the_four_step_overview(landing):
    expect = sync_api.expect
    expect(landing.locator(".qs-name")).to_have_text("QASeed")
    expect(landing.locator(".qs-brand svg")).to_be_visible()
    expect(landing.get_by_text("Less setup. More testing.")).to_be_visible()
    expect(landing.locator(".qs-overview .item")).to_have_count(4)
    expect(landing.locator(".st-key-start_btn button")).to_have_text(re.compile("Get started"))
    assert landing.locator(".st-key-home_btn").count() == 0


def test_browser_tab_shows_the_same_name_and_sprout_mark_as_the_header(landing):
    """Tab title is just "QASeed" (Streamlit Cloud adds " · Streamlit"); the tab icon is the header's sprout mark."""
    assert re.fullmatch(r"QASeed( · Streamlit)?", landing.title()), landing.title()
    icon = landing.evaluate("[...document.querySelectorAll('link[rel*=icon]')].map(l => l.href).pop()")
    assert landing.request.get(icon).body() == (ROOT / "assets" / "brand" / "qaseed_mark.png").read_bytes()


def test_brand_and_ready_made_mode_without_api_key(page):
    expect = sync_api.expect
    expect(page.locator(".qs-name")).to_have_text("QASeed")
    expect(page.locator(".st-key-home_btn button")).to_be_visible()
    if not BASE_URL:  # the local test server has no key; a deployed app has one in its Secrets
        expect(page.get_by_text("Live AI is not set up")).to_be_visible()
    expect(page.locator(".st-key-sample_choice")).to_be_visible()


def test_payment_example_end_to_end_with_verified_downloads(page):
    expect = sync_api.expect
    pick_example(page, PAYMENT_ID)
    review_and_verify(page)
    values = page.locator('[data-testid="stMetricValue"]')
    for i, value in enumerate(["48", "48", "24/24", "0"]):  # planned, created, checks passed, checks failed
        expect(values.nth(i)).to_have_text(value)
    expect(page.get_by_text("All checks passed. Your 48 records match the plan.")).to_be_visible()
    open_outputs(page)
    expect(page.locator('[data-testid="stDownloadButton"]')).to_have_count(1)  # one ZIP holds every file
    with page.expect_download() as info:
        page.locator(".st-key-download_csv_zip button").click()
    data = info.value.path().read_bytes()
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert names == ["customers.csv", "subscriptions.csv", "invoices.csv", "payments.csv", "validation_report.json"]
    assert re.fullmatch(r"qaseed_test_data_\d{4}-\d{2}-\d{2}_\d{4}\.zip", info.value.suggested_filename)
    import json

    report = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("validation_report.json"))
    assert report["export_format"] == "csv" and report["summary"]["passed"] is True
    payments = zipfile.ZipFile(io.BytesIO(data)).read("payments.csv").decode("utf-8").splitlines()
    assert len(payments) == 13 and sum(",failed," in line for line in payments) == 4


def test_preview_shows_the_files_inside_the_zip_without_a_download_button(page):
    expect = sync_api.expect
    pick_example(page, PAYMENT_ID)
    review_and_verify(page)
    open_outputs(page)
    with page.expect_download() as direct:
        page.locator(".st-key-download_csv_zip button").click()
    archive = zipfile.ZipFile(io.BytesIO(direct.value.path().read_bytes()))
    page.locator(".st-key-preview_csv_zip button").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("ZIP contents")).to_be_visible(timeout=15_000)
    expect(dialog.get_by_text(direct.value.suggested_filename).first).to_be_visible()
    assert dialog.get_by_role("button", name=re.compile("^Download")).count() == 0  # no download inside Preview
    dialog.get_by_text("payments.csv", exact=True).last.click()  # open one file from the ZIP
    page.wait_for_timeout(1000)
    expect(dialog.locator('[data-testid="stDataFrame"]').last).to_be_visible()
    dialog.get_by_text("validation_report.json", exact=True).last.click()
    expect(dialog.locator("code").first).to_contain_text('"passed": true')
    shown = dialog.locator("code").first.inner_text()
    assert '"checks_total": 24' in shown and archive.read("validation_report.json").decode("utf-8").startswith("{")


def test_negative_example_shows_plain_reason_and_stays_on_describe(page):
    pick_example(page, "S16")
    page.get_by_text("Bad request: status counts exceed the total.").wait_for(timeout=30_000)
    assert page.get_by_text("on purpose").count() == 0 and page.get_by_text("How to fix").count() == 0
    assert page.locator(".st-key-generate_btn").count() == 0
    sync_api.expect(page.locator('[class*="st-key-nav_1_"] button')).to_be_disabled()


def test_full_journey_home_return_and_new_in_a_real_browser(page):
    """Each step shows its real state; Home keeps the run; Return resumes it; New starts an empty Describe."""
    expect = sync_api.expect
    assert tracker(page) == ["now", "todo", "todo", "todo"]
    pick_example(page, PAYMENT_ID)
    review_and_verify(page)
    assert tracker(page) == ["done", "done", "now", "todo"]
    open_outputs(page)
    with page.expect_download():
        page.locator(".st-key-download_csv_zip button").click()
    page.locator(".st-key-home_btn button").click()
    expect(page.locator(".st-key-start_btn button")).to_have_text(re.compile("Return to workspace"))
    page.locator(".st-key-start_btn button").click()
    page.get_by_text("Download results").wait_for(timeout=30_000)  # resumed where we left off
    assert tracker(page) == ["done", "done", "done", "now"]
    page.locator(".st-key-new_btn button").click()
    expect(page.get_by_role("textbox", name="Test scenario")).to_have_value("")
    assert tracker(page) == ["now", "todo", "todo", "todo"]
    assert page.locator(".st-key-download_csv_zip").count() == 0


def test_header_is_not_hidden_under_streamlits_top_bar(page):
    """The QASeed header starts below Streamlit's top bar with the sidebar open or collapsed.

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


@pytest.mark.parametrize("given,opened", [
    # Streamlit Community Cloud public URL -> the app's own address inside Cloud's frame (Day 7 finding)
    ("https://proven-data-seed.streamlit.app", "https://proven-data-seed.streamlit.app/~/+/"),
    ("https://proven-data-seed.streamlit.app/", "https://proven-data-seed.streamlit.app/~/+/"),
    ("  https://Proven-Data-Seed.streamlit.app  ", "https://Proven-Data-Seed.streamlit.app/~/+/"),
    # an inner address given explicitly (the Day 7 workaround) is kept exactly
    ("https://proven-data-seed.streamlit.app/~/+/?", "https://proven-data-seed.streamlit.app/~/+/?"),
    # anything else: as given, without a trailing slash; blank means "start a local server"
    ("http://127.0.0.1:8501/", "http://127.0.0.1:8501"),
    ("https://qaseed.example.com/", "https://qaseed.example.com"),
    ("", ""),
])
def test_deployed_app_url_opens_the_app_itself(given, opened):
    assert deployed_app_url(given) == opened
