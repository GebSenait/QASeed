"""Scenario evaluation runner (qaseed/evaluate.py). No live API calls."""

import qaseed.evaluate as evaluate
import qaseed.llm_client as llm_client
from qaseed.evaluate import compare, expected_for, load_scenarios, run
from qaseed.llm_client import KEY_NAME, MODEL_NAME
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


def test_expected_for_uses_live_override_only_in_live_mode():
    case = {"expected": {"status": "rejected"}, "live_expected": {"status": "valid"}}
    assert expected_for(case, "mock")["status"] == "rejected"
    assert expected_for(case, "live")["status"] == "valid"
    assert expected_for({"expected": {"status": "valid"}}, "live")["status"] == "valid"


def test_mock_run_matches_every_scenario_and_is_labelled_mock():
    rows = run("mock")
    assert len(rows) == len(load_scenarios())
    assert all(row["match"] for row in rows)
    assert {row["source"] for row in rows} == {"mock"}


def test_ids_filter():
    rows = run("mock", ids={"S01", "S08"})
    assert [row["id"] for row in rows] == ["S01", "S08"]


def test_main_mock_prints_summary(capsys):
    assert evaluate.main([]) == 0
    out = capsys.readouterr().out
    assert "mode=mock" in out and "NOT model accuracy" in out
    assert "summary: 18/18 matched" in out


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
    rows = [{"id": "S01", "category": "c", "expected": "valid", "actual": "valid", "issues": "", "match": True, "problems": [], "latency_ms": 1200, "source": "llm"}]
    evaluate.print_report(rows, "live")
    out = capsys.readouterr().out
    assert "latency_ms |" in out and "| 1200 |" in out
    assert "median=1200" in out
