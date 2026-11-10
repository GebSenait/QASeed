"""Baseline replay script (scripts/day4_baseline.py). Offline; needs the Day 3 commit in Git."""

import importlib.util
import shutil
import subprocess

import pytest

from qaseed.config import ROOT

spec = importlib.util.spec_from_file_location("day4_baseline", ROOT / "scripts" / "day4_baseline.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def _have_day3_commit() -> bool:
    if not shutil.which("git"):
        return False
    result = subprocess.run(["git", "cat-file", "-e", f"{baseline.DAY3_COMMIT}^{{commit}}"], cwd=ROOT, capture_output=True)
    return result.returncode == 0


needs_git = pytest.mark.skipif(not _have_day3_commit(), reason="Day 3 commit not available in this checkout")


@needs_git
def test_loads_the_day3_planner_from_git():
    day3 = baseline.load_day3_planner()
    assert day3.PROMPT_ID == "P03"
    assert not hasattr(day3, "MAX_TRANSPORT_RETRIES")


@needs_git
def test_baseline_reproduces_known_day3_failures(capsys, monkeypatch):
    import qaseed.evaluate as evaluate

    monkeypatch.setattr(evaluate, "plan_scenario", evaluate.plan_scenario)  # restored after the test
    monkeypatch.setattr(evaluate, "PROMPT_ID", evaluate.PROMPT_ID)
    assert baseline.main(["--set", "all", "--ids", "S01,S19,S25"]) == 1
    out = capsys.readouterr().out
    assert "baseline: planner from da6206a (prompt P03)" in out
    assert "| S01 | normal / flagship multi-table | valid | valid | - | 1 | yes |" in out
    assert "| S19 | API: rate limit (429) then success | valid | rejected | llm_error | 1 | NO |" in out
    assert "| S25 | guardrail: prompt injection in the scenario | rejected | valid | - | 1 | NO |" in out


def test_live_mode_is_refused(monkeypatch):
    monkeypatch.setattr(baseline, "load_day3_planner", lambda: type("M", (), {"PROMPT_ID": "P03", "plan_scenario": None}))
    import qaseed.evaluate as evaluate

    monkeypatch.setattr(evaluate, "plan_scenario", evaluate.plan_scenario)
    monkeypatch.setattr(evaluate, "PROMPT_ID", evaluate.PROMPT_ID)
    assert baseline.main(["--live"]) == 2
