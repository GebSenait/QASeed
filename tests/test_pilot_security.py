"""Pilot security regression tests: session isolation, per-run storage, Live AI budgets, export safety.

Offline only. Gemini is never called: the key is a fake value and llm_client.post_json is replaced by a
recorded-reply stub in every test that enables Live AI. Scenarios and generated data are synthetic.
"""

import csv
import io
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

import qaseed.generator as generator
import qaseed.llm_client as llm_client
import qaseed.pipeline as pipeline
from qaseed.config import ROOT
from qaseed.evaluate import load_scenarios, sequence_post
from qaseed.llm_client import KEY_NAME, MODEL_NAME

AS_OF = date(2026, 11, 27)
FAKE_KEY = "AIzaTEST_fake_pilot_key_0000"
CASES = {c["id"]: c for c in load_scenarios() + load_scenarios(pipeline.DAY5_SCENARIOS_PATH)}
SAMPLE_INDEX = {s["id"]: i for i, s in enumerate(pipeline.sample_scenarios())}
VALID_IDS = [s["id"] for s in pipeline.sample_scenarios() if s["expected"] == "valid"]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")  # cells a spreadsheet may run as a formula (CSV injection)


# ---- helpers ------------------------------------------------------------------------------------------


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.setenv(KEY_NAME, "")
    monkeypatch.setenv(MODEL_NAME, "")
    monkeypatch.setattr(llm_client, "post_json", _never_called)


@pytest.fixture
def live(monkeypatch):
    """Live AI configured with a fake key; every request goes to a recorded-reply stub (no network)."""
    monkeypatch.setenv(KEY_NAME, FAKE_KEY)
    monkeypatch.setenv(MODEL_NAME, "gemini-test")
    monkeypatch.delenv(pipeline.DEFAULT_MODE_ENV, raising=False)
    monkeypatch.setenv(pipeline.LIVE_PER_DAY_ENV, "1000")  # the app-wide counter is shared by the whole test run
    monkeypatch.setattr(pipeline, "_sleep", lambda seconds: None)
    post = sequence_post([{"reply": CASES["S01"]["mock_reply"]}])
    monkeypatch.setattr(llm_client, "post_json", post)
    return post


@pytest.fixture
def viewer(monkeypatch):
    """Pretend the host signed a viewer in (as a private Streamlit Community Cloud app does). Returns a setter."""
    current = {"key": ""}
    monkeypatch.setattr(pipeline, "viewer_key", lambda user: current["key"])

    def sign_in(key: str) -> None:
        current["key"] = key

    return sign_in


def _never_called(url, payload, key):
    raise AssertionError("no API request expected")


def open_app() -> AppTest:
    """A brand-new browser session (first visit, refresh or new tab), past the landing page."""
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception
    return click(app, "start_btn")


def click(app: AppTest, key: str) -> AppTest:
    app.button(key=key).click().run(timeout=30)
    assert not app.exception
    return app


def run_sample_to_download(app: AppTest, case_id: str) -> AppTest:
    app.selectbox(key="sample_choice").select_index(SAMPLE_INDEX[case_id]).run()
    click(app, "plan_btn")
    app.checkbox(key="reviewed").check().run(timeout=30)
    click(app, "generate_btn")
    return click(app, "outputs_btn")


def plan_live(app: AppTest, case_id: str) -> AppTest:
    app.text_area(key="scenario").input(CASES[case_id]["scenario"]).run()
    return click(app, "plan_btn")


def limit_warnings(app: AppTest) -> list[str]:
    return [w.value for w in app.main.warning if "limit" in w.value]


def offline_plan(case_id: str):
    return pipeline.plan_request(CASES[case_id]["scenario"], "offline").plan


# ---- viewer identity and per-viewer budget (server side) -----------------------------------------------


def test_viewer_key_is_a_hash_and_empty_without_identity():
    key = pipeline.viewer_key({"email": "Pilot.Tester@Example.com"})
    assert key.startswith("viewer:") and len(key) == len("viewer:") + 24
    assert "pilot" not in key.lower() and "example" not in key.lower()  # the email itself is never kept
    assert pipeline.viewer_key({"email": "  pilot.tester@example.com "}) == key  # case and spaces do not matter
    assert pipeline.viewer_key({"email": "other@example.com"}) != key
    for no_identity in ({}, {"email": None}, {"email": ""}, {"email": "not-an-email"}, None, object()):
        assert pipeline.viewer_key(no_identity) == ""

    class Broken:
        def get(self, name):
            raise RuntimeError("no script run context")

    assert pipeline.viewer_key(Broken()) == ""


@pytest.mark.parametrize("env, expected", [({}, 10), ({pipeline.LIVE_PER_VIEWER_DAY_ENV: " 4 "}, 4),
                                           ({pipeline.LIVE_PER_VIEWER_DAY_ENV: "x"}, 10),
                                           ({pipeline.LIVE_PER_VIEWER_DAY_ENV: "-1"}, 0)])
def test_viewer_live_limit(env, expected):
    assert pipeline.viewer_live_limit(env) == expected


def test_viewer_limit_can_be_set_from_hosted_secrets():
    env = {}
    assert pipeline.load_secrets({pipeline.LIVE_PER_VIEWER_DAY_ENV: "5"}, env) == [pipeline.LIVE_PER_VIEWER_DAY_ENV]
    assert pipeline.viewer_live_limit(env) == 5


def test_daily_viewer_usage_is_per_viewer_and_resets_the_next_day():
    usage = pipeline.DailyViewerUsage()
    day, next_day = date(2026, 11, 27), date(2026, 11, 28)
    assert [usage.try_use("a", 2, day) for _ in range(3)] == [True, True, False]
    assert usage.try_use("b", 2, day) is True  # another viewer has their own budget
    usage.release("a", day)
    assert usage.used("a", day) == 1 and usage.try_use("a", 2, day) is True
    usage.release("c", day)  # releasing an unused key never goes negative
    assert usage.used("c", day) == 0
    assert usage.try_use("a", 2, next_day) is True and usage.used("a", next_day) == 1


def test_daily_viewer_usage_never_overspends_under_concurrency():
    usage, granted = pipeline.DailyViewerUsage(), []
    barrier = threading.Barrier(16)

    def spend():
        barrier.wait()
        granted.extend(usage.try_use("same-viewer", 25) for _ in range(10))

    threads = [threading.Thread(target=spend) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert granted.count(True) == 25 and usage.used("same-viewer") == 25


def test_daily_counter_never_overspends_under_concurrency():
    counter, granted = pipeline.DailyCounter(), []
    with ThreadPoolExecutor(max_workers=16) as pool:
        granted = list(pool.map(lambda _: counter.try_use(40), range(200)))
    assert granted.count(True) == 40


def test_viewer_live_limit_survives_refresh_and_new_sessions(live, viewer, monkeypatch):
    monkeypatch.setenv(pipeline.LIVE_PER_SESSION_ENV, "5")
    monkeypatch.setenv(pipeline.LIVE_PER_VIEWER_DAY_ENV, "1")
    viewer(f"viewer:{uuid.uuid4().hex[:24]}")
    first = open_app()
    assert any(c.value == "Live AI plans left today: 1" for c in first.sidebar.caption)
    plan_live(first, "S01")
    assert first.session_state["step"] == 1 and len(live.calls) == 1
    refreshed = open_app()  # refresh / new tab: a new session with empty session state
    assert refreshed.session_state["live_plans_used"] == 0
    assert any(c.value == "Live AI plans left today: 0" for c in refreshed.sidebar.caption)
    plan_live(refreshed, "S02")
    assert limit_warnings(refreshed) == ["Live AI limit reached: try a ready-made example."]
    assert len(live.calls) == 1  # the refused plan never reached the API
    viewer(f"viewer:{uuid.uuid4().hex[:24]}")  # a different invited tester keeps their own budget
    other = plan_live(open_app(), "S01")
    assert other.session_state["step"] == 1 and len(live.calls) == 2


def test_app_wide_refusal_does_not_use_up_the_viewers_plan(live, viewer, monkeypatch):
    monkeypatch.setenv(pipeline.LIVE_PER_VIEWER_DAY_ENV, "1")
    viewer(f"viewer:{uuid.uuid4().hex[:24]}")
    monkeypatch.setenv(pipeline.LIVE_PER_DAY_ENV, "0")
    app = plan_live(open_app(), "S01")
    assert limit_warnings(app) and live.calls == []
    monkeypatch.setenv(pipeline.LIVE_PER_DAY_ENV, "1000")  # app budget available again (e.g. the next day)
    app = plan_live(open_app(), "S01")
    assert app.session_state["step"] == 1 and len(live.calls) == 1  # the viewer's single plan was still there


def test_without_host_identity_only_the_session_and_app_limits_apply(live, viewer, monkeypatch):
    """Documents the limitation: with no signed-in identity, a refresh starts a new session budget."""
    monkeypatch.setenv(pipeline.LIVE_PER_SESSION_ENV, "1")
    viewer("")
    app = open_app()
    assert any(c.value == "Live AI plans left in this session: 1" for c in app.sidebar.caption)
    plan_live(app, "S01")
    plan_live(open_app(), "S01")
    assert len(live.calls) == 2  # bounded only by QASEED_LIVE_PLANS_PER_DAY (app-wide, process memory)


# ---- session isolation, refresh, reset, failed generation ------------------------------------------------


def test_two_sessions_never_share_plans_results_or_downloads(no_key):
    a = run_sample_to_download(open_app(), "S01")
    a_zip = a.session_state["gen_result"].csv_zip
    assert a_zip and len(a.get("download_button")) == 1
    b = open_app()  # second browser, or a refresh of the first
    for name in ("plan_outcome", "plan_key", "gen_result", "gen_key"):
        assert b.session_state[name] is None
    assert b.session_state["step"] == 0 and not b.get("download_button")
    run_sample_to_download(b, "S05")
    b_zip = b.session_state["gen_result"].csv_zip
    assert b_zip != a_zip
    a.run(timeout=30)  # session A reruns after B worked: still its own plan and file
    assert a.session_state["gen_result"].csv_zip == a_zip
    assert a.session_state["plan_outcome"].sample_id == "S01" and b.session_state["plan_outcome"].sample_id == "S05"
    report_a = zipfile.ZipFile(io.BytesIO(a_zip)).read("validation_report.json").decode()
    assert CASES["S05"]["scenario"] not in report_a  # B's scenario never appears in A's export


def test_new_clears_the_run_and_its_download(no_key):
    app = run_sample_to_download(open_app(), "S01")
    click(app, "new_btn")
    assert app.session_state["gen_result"] is None and app.session_state["plan_outcome"] is None
    assert app.session_state["step"] == 0 and not app.get("download_button")


def test_failed_generation_leaves_no_result_and_no_download(no_key, monkeypatch):
    def fail(plan, seed, as_of):
        raise pipeline.PipelineError("generation_invalid", "Generation stopped: simulated")

    app = open_app()
    app.selectbox(key="sample_choice").select_index(SAMPLE_INDEX["S01"]).run()
    click(app, "plan_btn")
    monkeypatch.setattr(pipeline, "run_generation", fail)
    app.checkbox(key="reviewed").check().run(timeout=30)
    click(app, "generate_btn")
    assert app.session_state["gen_result"] is None and app.session_state["step"] == 1
    assert [e.value for e in app.error] == ["Bad request: these counts can't all be generated consistently."]
    assert "simulated" not in "\n".join(e.value for e in app.error)  # internals stay out of the UI
    assert not app.get("download_button")


# ---- per-run storage ----------------------------------------------------------------------------------


def test_every_run_uses_its_own_in_memory_database_and_writes_no_file(monkeypatch):
    created = []
    real = generator.create_database

    def recording(path=None):
        engine = real(path)
        created.append((path, engine.url.database))
        return engine

    monkeypatch.setattr(generator, "create_database", recording)
    data_before = sorted(p.name for p in (ROOT / "data").iterdir())
    result = pipeline.run_generation(offline_plan("S01"), 42, AS_OF)
    assert result.exportable
    assert created == [(None, None), (None, None)]  # generation DB + CSV re-check DB, both in memory
    assert sorted(p.name for p in (ROOT / "data").iterdir()) == data_before


def test_concurrent_generations_are_isolated_and_match_sequential_runs():
    jobs = [(case_id, seed) for case_id in ("S01", "S05", "D5-01") for seed in (1, 2, 3)]
    plans = {case_id: offline_plan(case_id) for case_id, _ in jobs}
    sequential = {job: pipeline.run_generation(plans[job[0]], job[1], AS_OF).csv_zip for job in jobs}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        parallel = dict(zip(jobs, pool.map(lambda job: pipeline.run_generation(plans[job[0]], job[1], AS_OF).csv_zip, jobs)))
    assert parallel == sequential
    assert len(set(parallel.values())) == len(jobs)  # every run produced its own file


# ---- export integrity and CSV formula injection ----------------------------------------------------------


@pytest.mark.parametrize("case_id", VALID_IDS)
def test_exported_csv_cells_cannot_start_a_spreadsheet_formula(case_id):
    for seed in (1, 42, 999_999):
        result = pipeline.run_generation(offline_plan(case_id), seed, AS_OF)
        assert result.exportable
        with zipfile.ZipFile(io.BytesIO(result.csv_zip)) as archive:
            assert archive.testzip() is None  # every member's CRC is intact
            for name in archive.namelist():
                assert "/" not in name and "\\" not in name and ".." not in name  # no path traversal on unzip
                if name.endswith(".csv"):
                    for row in csv.reader(io.StringIO(archive.read(name).decode("utf-8"))):
                        risky = [cell for cell in row if cell.startswith(FORMULA_START)]
                        assert not risky, f"{case_id} seed {seed} {name}: {risky[:3]}"
