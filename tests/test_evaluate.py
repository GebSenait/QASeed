"""Scenario evaluation runner (qaseed/evaluate.py). No live API calls."""

import json
from pathlib import Path

import pytest

import qaseed.evaluate as evaluate
import qaseed.llm_client as llm_client
from qaseed.evaluate import compare, expected_for, load_scenarios, load_set, metrics, run, sequence_post
from qaseed.llm_client import KEY_NAME, MODEL_NAME, LLMError
from qaseed.plan_models import Issue, PlannedCondition, PlannedTable, ScenarioPlan


def plan(status="valid", counts=None, conditions=0, codes=()):
    return ScenarioPlan(
        scenario="s",
        source="mock",
        status=status,
        tables=[PlannedTable(table=t, count=c, count_source="stated") for t, c in (counts or {}).items()],
        conditions=[PlannedCondition(table="invoices", column="status", value="overdue", count=1)] * conditions,
        issues=[Issue(severity="error", code=code, message="m") for code in codes],
    )


def test_compare_reports_each_mismatch():
    assert compare(plan(counts={"customers": 5}, conditions=1), {"status": "valid", "counts": {"customers": 5}, "condition_count": 1}) == []
    problems = compare(plan(status="rejected", codes=["unknown_table"]), {"status": "valid", "issue_codes": ["invalid_count"], "counts": {"customers": 5}, "condition_count": 2})
    assert len(problems) == 4


def test_compare_checks_exact_conditions_and_request_budget():
    expected = {"status": "valid", "conditions": [["invoices", "status", "overdue", 1]], "min_requests": 2, "max_requests": 3}
    assert compare(plan(conditions=1), expected, requests=2) == []
    problems = compare(plan(conditions=0), expected, requests=4)
    assert any("missing conditions" in p for p in problems)
    assert any("> max 3" in p for p in problems)
    assert any("< min 2" in p for p in compare(plan(conditions=1), expected, requests=1))


def test_expected_for_uses_live_override_only_in_live_mode():
    case = {"expected": {"status": "rejected"}, "live_expected": {"status": "valid"}}
    assert expected_for(case, "mock")["status"] == "rejected"
    assert expected_for(case, "live")["status"] == "valid"
    assert expected_for({"expected": {"status": "valid"}}, "live")["status"] == "valid"


def test_sets_and_unique_ids():
    core, robustness, both = load_set("core"), load_set("robustness"), load_set("all")
    assert len(core) == 18 and len(robustness) == 12 and len(both) == 30
    assert len({c["id"] for c in both}) == 30
    assert {c["set"] for c in both} == {"core", "robustness"}
    for case in robustness:
        assert "mock_reply" in case or "mock_sequence" in case
        assert set(case["modes"]) <= {"mock", "live"}


def test_every_valid_core_case_lists_its_expected_conditions():
    for case in load_scenarios():
        if case["expected"]["status"] == "valid":
            assert len(case["expected"]["conditions"]) == case["expected"]["condition_count"], case["id"]


def test_mock_run_matches_every_scenario_and_is_labelled_mock():
    rows = run("mock", set_name="all")
    assert [r["id"] for r in rows if not r["match"]] == []
    assert {row["source"] for row in rows} <= {"mock", "none"}


def test_ids_filter():
    rows = run("mock", ids={"S01", "S08"})
    assert [row["id"] for row in rows] == ["S01", "S08"]


def test_live_mode_only_runs_live_capable_cases(monkeypatch):
    """Guardrail cases are live-capable because they never reach the API."""
    def explode(*args):
        raise AssertionError("network was called")

    monkeypatch.setattr(llm_client, "post_json", explode)
    rows = run("live", env={KEY_NAME: "AIzaTEST-not-a-real-key-0000", MODEL_NAME: "m"}, set_name="robustness")
    assert [r["id"] for r in rows] == ["S25", "S26", "S27", "S28"]
    assert all(r["match"] and r["requests"] == 0 for r in rows)


def test_sequence_post_plays_steps_and_repeats_the_last():
    post = sequence_post([{"error": "rate_limit", "retry_after": 3}, {"blocked": "SAFETY"}])
    with pytest.raises(LLMError) as caught:
        post("u", {}, "k")
    assert caught.value.kind == "rate_limit" and caught.value.retry_after == 3
    assert post("u", {}, "k") == {"promptFeedback": {"blockReason": "SAFETY"}}
    assert post("u", {}, "k") == {"promptFeedback": {"blockReason": "SAFETY"}}
    assert len(post.calls) == 3


def test_metrics_on_the_full_mock_set():
    m = metrics(run("mock", set_name="all"))
    assert m["scenarios"] == 30
    assert m["outcome_accuracy"]["value"] == 1.0
    assert m["false_acceptances"]["hits"] == 0
    assert m["false_rejections"]["hits"] == 0
    assert m["schema_correctness"]["value"] == 1.0
    assert m["condition_coverage"]["n"] == 15 and m["condition_coverage"]["hits"] == 15
    assert m["unexpected_failure_rate"]["hits"] == 0
    assert m["request_budget_violations"] == 0
    assert m["latency_ms"]["n"] == 30


def test_rows_never_contain_a_key_shaped_value():
    rows = run("mock", ids={"S27"}, set_name="all")
    assert "AIza" not in json.dumps(rows)


def test_main_mock_prints_summary(capsys):
    assert evaluate.main([]) == 0
    out = capsys.readouterr().out
    assert "mode=mock" in out and "NOT model accuracy" in out
    assert "summary: 18/18 matched" in out
    assert "metric condition_coverage=1.000 (12/12)" in out
    assert "cost=NOT MEASURED" in out


def test_main_writes_results_json(tmp_path, capsys):
    out = tmp_path / "eval.json"
    assert evaluate.main(["--set", "all", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["mode"] == "mock" and data["set"] == "all" and len(data["rows"]) == 30
    assert data["metrics"]["outcome_accuracy"]["hits"] == 30
    assert {"expected", "actual", "problems", "requests", "wall_ms"} <= set(data["rows"][0])
    printed = capsys.readouterr().out
    assert "results=eval.json" in printed
    assert str(tmp_path) not in printed and str(Path.home()) not in printed  # no local paths in reports


def test_results_path_inside_the_project_is_shown_relative(tmp_path):
    assert evaluate._display_path(evaluate.ROOT / "reports" / "day4" / "x.json") == "reports/day4/x.json"
    assert evaluate._display_path(tmp_path / "x.json") == "x.json"


def test_main_live_without_key_is_skipped_and_calls_nothing(monkeypatch, capsys):
    monkeypatch.setattr(evaluate, "load_local_env", lambda: None)
    monkeypatch.delenv(KEY_NAME, raising=False)
    monkeypatch.delenv(MODEL_NAME, raising=False)

    def explode(*args):
        raise AssertionError("network was called")

    monkeypatch.setattr(llm_client, "post_json", explode)
    assert evaluate.main(["--live"]) == 2
    assert "status=SKIPPED" in capsys.readouterr().out


def test_live_report_shows_latency_column(capsys):
    rows = [{
        "id": "S01", "set": "core", "category": "c", "expected": {"status": "valid", "counts": {}}, "expected_status": "valid",
        "actual": "valid", "issues": [], "counts": {}, "conditions_expected": 0, "conditions_found": 0, "schema_violations": [],
        "match": True, "problems": [], "requests": 1, "wall_ms": 1210.0, "llm_latency_ms": 1200, "total_tokens": 1103, "source": "llm",
    }]
    evaluate.print_report(rows, "live")
    out = capsys.readouterr().out
    assert "latency_ms |" in out and "| 1200 |" in out
    assert "llm_latency_ms: median=1200" in out
    assert "total_tokens=1103" in out and "cost=NOT MEASURED" in out


def test_report_shows_recovered_and_final_retry_errors(capsys):
    rows = {r["id"]: r for r in run("mock", ids={"S19", "S22"}, set_name="all")}
    assert rows["S19"]["retried_errors"] == ["rate_limit"] and rows["S19"]["error_kind"] == ""
    assert rows["S22"]["retried_errors"] == ["rate_limit", "rate_limit"] and rows["S22"]["error_kind"] == "rate_limit"
    evaluate.print_report(list(rows.values()), "mock")
    out = capsys.readouterr().out
    assert "retry S19: rate_limit -> recovered" in out
    assert "retry S22: rate_limit,rate_limit -> ended with rate_limit" in out
