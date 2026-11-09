"""Run the Day 3 scenario set and print a baseline table.

    python -m qaseed.evaluate                 # mock mode: canned replies, no network
    python -m qaseed.evaluate --live          # live mode: real Gemini requests (free-tier quota)
    python -m qaseed.evaluate --live --ids S01,S08,S14

Mock mode measures QASeed's deterministic validation only. Live mode
measures how well the configured model interprets each scenario. Report
them separately; a mock result is never evidence of model accuracy.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from qaseed.config import ROOT, load_local_env
from qaseed.llm_client import KEY_NAME, MODEL_NAME, api_key, selected_model
from qaseed.plan_models import ScenarioPlan
from qaseed.planner import MAX_ATTEMPTS, canned_reply_post, plan_scenario
from qaseed.schema import load_schema

SCENARIOS_PATH = ROOT / "tests" / "fixtures" / "day3_scenarios.json"


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


def expected_for(case: dict, mode: str) -> dict:
    if mode == "live" and "live_expected" in case:
        return case["live_expected"]
    return case["expected"]


def compare(plan: ScenarioPlan, expected: dict) -> list[str]:
    """Return a list of mismatches; empty means the outcome matched."""
    problems = []
    if plan.status != expected["status"]:
        problems.append(f"status {plan.status} != {expected['status']}")
    for code in expected.get("issue_codes", []):
        if code not in plan.issue_codes():
            problems.append(f"missing issue {code}")
    if "counts" in expected and plan.counts() != expected["counts"]:
        problems.append(f"counts {plan.counts()} != {expected['counts']}")
    if "condition_count" in expected and len(plan.conditions) != expected["condition_count"]:
        problems.append(f"{len(plan.conditions)} conditions != {expected['condition_count']}")
    return problems


def run(mode: str, ids: set[str] | None = None, env: dict | None = None) -> list[dict]:
    schema = load_schema()
    rows = []
    for case in load_scenarios():
        if ids and case["id"] not in ids:
            continue
        if mode == "mock":
            plan = plan_scenario(
                case["scenario"],
                env={KEY_NAME: "mock-key-not-real", MODEL_NAME: "mock"},
                post=canned_reply_post(case["mock_reply"]),
                schema=schema,
                source="mock",
            )
        else:
            plan = plan_scenario(case["scenario"], env=env, schema=schema, source="llm")
        expected = expected_for(case, mode)
        problems = compare(plan, expected)
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "expected": expected["status"],
            "actual": plan.status,
            "issues": ",".join(plan.issue_codes()),
            "match": not problems,
            "problems": problems,
            "latency_ms": plan.metadata.latency_ms,
            "source": plan.source,
        })
    return rows


def print_report(rows: list[dict], mode: str) -> None:
    print(f"mode={mode}  (mock = canned replies, NOT model accuracy)" if mode == "mock" else f"mode={mode}")
    print("| id | category | expected | actual | issues | match |" + (" latency_ms |" if mode == "live" else ""))
    print("|---|---|---|---|---|---|" + ("---|" if mode == "live" else ""))
    for row in rows:
        line = f"| {row['id']} | {row['category']} | {row['expected']} | {row['actual']} | {row['issues'] or '-'} | {'yes' if row['match'] else 'NO'} |"
        if mode == "live":
            line += f" {row['latency_ms']} |"
        print(line)
    for row in rows:
        for problem in row["problems"]:
            print(f"mismatch {row['id']}: {problem}")
    total = len(rows)
    matched = sum(row["match"] for row in rows)
    by_status = {s: sum(r["actual"] == s for r in rows) for s in ("valid", "rejected", "needs_clarification")}
    print(f"summary: {matched}/{total} matched expected outcome; "
          f"valid={by_status['valid']} rejected={by_status['rejected']} needs_clarification={by_status['needs_clarification']}")
    latencies = [r["latency_ms"] for r in rows if mode == "live" and r["latency_ms"] is not None]
    if latencies:
        print(f"latency_ms: median={statistics.median(latencies):.0f} min={min(latencies)} max={max(latencies)} (n={len(latencies)})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="call the real Gemini API (uses free-tier quota)")
    parser.add_argument("--ids", default="", help="comma-separated scenario IDs, e.g. S01,S08")
    args = parser.parse_args(argv)
    ids = {part.strip() for part in args.ids.split(",") if part.strip()} or None
    mode = "live" if args.live else "mock"
    env = None
    if mode == "live":
        load_local_env()
        env = dict(os.environ)
        if not api_key(env) or not selected_model(env):
            print("status=SKIPPED")
            print(f"reason={KEY_NAME} and {MODEL_NAME} must be set in .env for --live. No API call was made.")
            return 2
        count = len(ids) if ids else len(load_scenarios())
        print(f"Live run: up to {count * MAX_ATTEMPTS} Gemini requests ({count} scenarios, at most {MAX_ATTEMPTS} attempts each). Free-tier rate limits may return 429.")
    rows = run(mode, ids, env)
    print_report(rows, mode)
    return 0 if all(row["match"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
