"""Service layer behind the UI (qaseed/pipeline.py). Offline: all API calls are mocked."""

import io
import json
import logging
import zipfile
from datetime import date

import pytest

import qaseed.pipeline as pipeline
from qaseed.data_checks import CheckReport, CheckResult
from qaseed.evaluate import load_scenarios, sequence_post
from qaseed.generator import GenerationError
from qaseed.llm_client import KEY_NAME, MODEL_NAME
from qaseed.pipeline import PipelineError, find_sample, llm_status, plan_request, run_generation, sample_scenarios

AS_OF = date(2026, 11, 11)
FAKE_KEY = "AIzaTEST_pipeline_key_0000"
ENV = {KEY_NAME: FAKE_KEY, MODEL_NAME: "gemini-test"}
S01 = next(c for c in load_scenarios() if c["id"] == "S01")
PAYMENT_SCENARIO = load_scenarios(pipeline.DAY5_SCENARIOS_PATH)[0]["scenario"]


def never_called(url, payload, key):
    raise AssertionError("no API request expected")


# ---- configuration ------------------------------------------------------------


def test_llm_status_reports_model_but_never_the_key():
    status = llm_status(ENV)
    assert status == {"configured": True, "model": "gemini-test", "provider": "Google Gemini"}
    assert FAKE_KEY not in str(status)
    assert llm_status({})["configured"] is False
    assert llm_status({KEY_NAME: FAKE_KEY})["configured"] is False  # model missing


def test_samples_cover_the_18_planning_scenarios_and_the_day5_payment_scenario():
    samples = sample_scenarios()
    assert len(samples) == 19
    assert sum(s["expected"] == "valid" for s in samples) == 8
    assert samples[-1]["id"] == "D5-01" and samples[-1]["category"] == "payment_story"
    assert find_sample("  " + S01["scenario"].replace(" ", "  ") + " ")["id"] == "S01"
    assert find_sample("something else") is None


# ---- planning -----------------------------------------------------------------


def test_offline_sample_is_planned_from_its_recorded_reply():
    outcome = plan_request(S01["scenario"], "offline")
    assert outcome.sample_id == "S01"
    assert outcome.plan.status == "valid"
    assert outcome.plan.source == "mock"


def test_offline_custom_text_is_refused_honestly_without_a_request():
    outcome = plan_request("Generate 10 customers with active subscriptions please", "offline")
    assert outcome.plan.status == "rejected"
    assert outcome.plan.issue_codes() == ["llm_not_configured"]
    assert "only plans the 19 ready-made examples" in outcome.plan.issues[0].message
    assert outcome.plan.source == "none"


def test_offline_mode_still_applies_guardrails():
    outcome = plan_request("10 customers, contact jane.doe@gmail.com", "offline")
    assert outcome.plan.issue_codes() == ["pii_detected"]
    assert "jane.doe@gmail.com" not in outcome.plan.scenario


def test_live_mode_uses_the_planner_with_the_given_transport():
    post = sequence_post([{"reply": S01["mock_reply"]}])
    outcome = plan_request(S01["scenario"], "live", env=ENV, post=post)
    assert outcome.plan.status == "valid"
    assert outcome.plan.source == "llm"
    assert len(post.calls) == 1


def test_live_mode_without_key_makes_no_request():
    outcome = plan_request(S01["scenario"], "live", env={}, post=never_called)
    assert outcome.plan.issue_codes() == ["llm_not_configured"]


def test_live_api_failure_is_reported_without_the_key():
    post = sequence_post([{"error": "auth"}])
    outcome = plan_request(S01["scenario"], "live", env=ENV, post=post)
    assert outcome.plan.status == "rejected"
    assert outcome.plan.issue_codes() == ["llm_error"]
    assert outcome.plan.metadata.error_kind == "auth"
    assert FAKE_KEY not in outcome.plan.model_dump_json()
    assert len(post.calls) == 1  # auth errors are not retried


def test_live_rate_limit_is_retried_with_bounded_backoff(monkeypatch):
    waits = []
    monkeypatch.setattr(pipeline, "_sleep", waits.append)
    post = sequence_post([{"error": "rate_limit"}])
    outcome = plan_request(S01["scenario"], "live", env=ENV, post=post)
    assert outcome.plan.issue_codes() == ["llm_error"]
    assert outcome.plan.metadata.transport_retries == 2
    assert waits == [2.0, 4.0]
    assert len(post.calls) == 3


def test_unknown_mode_is_a_programming_error():
    with pytest.raises(ValueError):
        plan_request("x", "cloud")


# ---- generation and export ----------------------------------------------------


def test_run_generation_flagship_is_verified_and_exportable():
    result = run_generation(plan_request(S01["scenario"], "offline").plan, 42, AS_OF)
    assert result.passed and result.exportable
    assert (result.passed_count, result.total_checks) == (25, 25)
    assert result.row_counts == {"customers": 50, "subscriptions": 50, "invoices": 50, "payments": 50}
    coverage = {row["what"]: row for row in result.coverage}
    assert coverage["subscriptions.status = canceled"] == {"what": "subscriptions.status = canceled", "requested": 10, "generated": 10, "verified": True}
    assert coverage["payments.status = failed"]["generated"] == 5
    assert all(len(rows) == pipeline.SAMPLE_ROWS for rows in result.samples.values())
    names = zipfile.ZipFile(io.BytesIO(result.csv_zip)).namelist()
    assert names == ["customers.csv", "subscriptions.csv", "invoices.csv", "payments.csv", "validation_report.json"]
    assert result.export_checks == "25/25"  # the CSV files themselves were reloaded and re-checked
    assert result.zip_contents[0] == {"file": "customers.csv", "rows": 50}
    report = json.loads(zipfile.ZipFile(io.BytesIO(result.csv_zip)).read("validation_report.json"))
    assert report["export_format"] == "csv"
    assert report["summary"] == {"passed": True, "checks_passed": 25, "checks_total": 25}
    assert report["seed"] == 42 and report["as_of"] == "2026-11-11"
    assert report["scenario"] == S01["scenario"]
    assert {r["what"]: r["generated"] for r in report["requested_vs_generated"]}["invoices.status = overdue"] == 8
    assert "post-MVP" in report["note"]


def test_run_generation_is_reproducible_for_the_same_seed():
    plan = plan_request(S01["scenario"], "offline").plan
    assert run_generation(plan, 7, AS_OF).csv_zip == run_generation(plan, 7, AS_OF).csv_zip
    assert run_generation(plan, 7, AS_OF).csv_zip != run_generation(plan, 8, AS_OF).csv_zip


def test_run_generation_refuses_an_invalid_plan():
    plan = plan_request("Generate -5 customers.", "offline").plan
    with pytest.raises(PipelineError) as error:
        run_generation(plan, 42, AS_OF)
    assert error.value.category == "plan_not_valid"


def test_failed_checks_block_export(monkeypatch):
    real = pipeline.build_and_check

    def broken(plan, seed, as_of):
        dataset, report = real(plan, seed, as_of)
        report.results.append(CheckResult("plan", "row_count", "customers", "simulated failure", 1, 0))
        return dataset, report

    monkeypatch.setattr(pipeline, "build_and_check", broken)
    result = run_generation(plan_request(S01["scenario"], "offline").plan, 42, AS_OF)
    assert not result.passed and not result.exportable
    assert result.csv_zip is None
    assert "Export blocked" in result.export_note


def test_export_is_blocked_when_the_csv_files_fail_reverification(monkeypatch):
    failing = CheckReport(results=[CheckResult("integrity", "csv_reload", "*", "simulated", 1, 0)])
    monkeypatch.setattr(pipeline, "verify_csv_zip", lambda data, plan, as_of: failing)
    result = run_generation(plan_request(S01["scenario"], "offline").plan, 42, AS_OF)
    assert result.passed  # the database checks passed ...
    assert not result.exportable and result.csv_zip is None  # ... but the file did not, so nothing is offered
    assert result.export_checks == "0/1"
    assert "fresh database" in result.export_note


@pytest.mark.parametrize("exc, category", [
    (GenerationError("payments.status: no value left"), "generation_invalid"),
    (RuntimeError(f"internal detail {FAKE_KEY}"), "unexpected"),
])
def test_generation_errors_become_safe_pipeline_errors(monkeypatch, exc, category):
    def boom(*args):
        raise exc

    monkeypatch.setattr(pipeline, "build_and_check", boom)
    with pytest.raises(PipelineError) as error:
        run_generation(plan_request(S01["scenario"], "offline").plan, 42, AS_OF)
    assert error.value.category == category
    assert FAKE_KEY not in str(error.value)
    assert "internal detail" not in str(error.value)


# ---- safe logging -------------------------------------------------------------


def test_logs_have_status_timings_and_categories_but_no_scenario_text(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="qaseed")
    secret_scenario = "50 customers for the Zephyrine-Project launch with 10 canceled subscriptions"
    plan_request(secret_scenario, "offline")
    plan_request(S01["scenario"], "live", env=ENV, post=sequence_post([{"error": "auth"}]))
    run_generation(plan_request(S01["scenario"], "offline").plan, 42, AS_OF)
    text = caplog.text
    assert "event=plan mode=offline" in text
    assert "error_kind=auth" in text
    assert "event=generate status=verified" in text and "checks=25/25" in text and "total_ms=" in text
    assert "Zephyrine" not in text
    assert S01["scenario"] not in text
    assert FAKE_KEY not in text
    assert "@example.com" not in text  # no generated values


def test_redact_filter_masks_keys_emails_and_long_numbers(caplog):
    caplog.set_level(logging.INFO, logger="qaseed")
    pipeline._log(logging.INFO, "probe", note=f"{FAKE_KEY} jane@corp.com +971501234567")
    assert FAKE_KEY not in caplog.text
    assert "jane@corp.com" not in caplog.text
    assert "501234567" not in caplog.text
    assert "event=probe" in caplog.text


def test_configure_logging_is_idempotent():
    before = len(pipeline.configure_logging().handlers)
    assert len(pipeline.configure_logging().handlers) == before


# ---- Day 5 payment scenario (recorded live reply, replayed offline) ------------


def test_payment_scenario_offline_end_to_end():
    """8 succeeded + 4 failed payments; failed payments' invoices stay unpaid, succeeded ones are paid."""
    payment = find_sample(PAYMENT_SCENARIO)
    outcome = plan_request(PAYMENT_SCENARIO, "offline")
    assert payment["id"] == outcome.sample_id == "D5-01"
    assert outcome.plan.status == "valid" and outcome.plan.source == "mock"
    result = run_generation(outcome.plan, 42, AS_OF)
    assert result.exportable and result.export_checks == f"{result.total_checks}/{result.total_checks}"
    assert result.row_counts == {"customers": 12, "subscriptions": 12, "invoices": 12, "payments": 12}
    coverage = {row["what"]: row["generated"] for row in result.coverage}
    assert coverage["payments.status = succeeded"] == 8 and coverage["payments.status = failed"] == 4
    story = {c["check"]: c["actual"] for c in result.checks if c["group"] == "business_rule"}
    assert story["0 invoices with a succeeded payment that are not paid"] == 0
    assert story["0 invoices with only failed or pending payments that are not unpaid (open or overdue)"] == 0


# ---- hosted demo helpers ------------------------------------------------------------


def test_load_secrets_copies_only_known_names_and_keeps_existing_values():
    env = {MODEL_NAME: "already-set"}
    secrets = {KEY_NAME: FAKE_KEY, MODEL_NAME: "gemini-cloud", "QASEED_DEFAULT_MODE": "offline", "OTHER_TOKEN": "x"}
    loaded = pipeline.load_secrets(secrets, env)
    assert loaded == [KEY_NAME, "QASEED_DEFAULT_MODE"]  # names only, never values
    assert env == {MODEL_NAME: "already-set", KEY_NAME: FAKE_KEY, "QASEED_DEFAULT_MODE": "offline"}


def test_load_secrets_tolerates_missing_secrets():
    class NoSecrets:
        def __getitem__(self, name):
            raise FileNotFoundError("no secrets.toml")

    env = {}
    assert pipeline.load_secrets(NoSecrets(), env) == [] and env == {}


@pytest.mark.parametrize("env, expected", [
    ({}, {"per_session": 3, "per_day": 40}),
    ({"QASEED_LIVE_PLANS_PER_SESSION": "5", "QASEED_LIVE_PLANS_PER_DAY": "100"}, {"per_session": 5, "per_day": 100}),
    ({"QASEED_LIVE_PLANS_PER_SESSION": "abc", "QASEED_LIVE_PLANS_PER_DAY": "-2"}, {"per_session": 3, "per_day": 0}),
])
def test_live_limits(env, expected):
    assert pipeline.live_limits(env) == expected


def test_default_mode():
    assert pipeline.default_mode(["live", "offline"], {}) == "live"
    assert pipeline.default_mode(["live", "offline"], {"QASEED_DEFAULT_MODE": "Offline"}) == "offline"
    assert pipeline.default_mode(["offline"], {"QASEED_DEFAULT_MODE": "live"}) == "offline"  # live not available


def test_daily_counter_limits_per_day_and_resets_the_next_day():
    counter = pipeline.DailyCounter()
    day, next_day = date(2026, 11, 11), date(2026, 11, 12)
    assert [counter.try_use(2, day) for _ in range(3)] == [True, True, False]
    assert counter.try_use(2, next_day) is True


def test_json_report_download_matches_the_report_in_the_zip():
    result = run_generation(plan_request(S01["scenario"], "offline").plan, 42, AS_OF)
    inside = zipfile.ZipFile(io.BytesIO(result.csv_zip)).read("validation_report.json")
    assert result.report_json == inside
    assert json.loads(result.report_json)["summary"]["passed"] is True
