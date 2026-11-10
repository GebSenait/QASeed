"""Run the QASeed evaluation sets and report metrics.

    python -m qaseed.evaluate                          # mock, core set (18 Day 3 scenarios)
    python -m qaseed.evaluate --set all                # mock, core + Day 4 robustness/safety (30)
    python -m qaseed.evaluate --set all --out reports/day4/eval_mock.json
    python -m qaseed.evaluate --live --delay 5         # live Gemini, core set (free-tier quota)
    python -m qaseed.evaluate --live --ids S01,S08,S14

Sets: "core" = tests/fixtures/day3_scenarios.json (planning quality),
"robustness" = tests/fixtures/day4_eval_scenarios.json (API errors,
retries, malformed output, guardrails), "all" = both.

Mock mode measures QASeed's deterministic code only (hand-written replies
and simulated API errors). Live mode measures the configured model. Report
them separately; a mock result is never evidence of model accuracy.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

from qaseed import llm_client
from qaseed.config import ROOT, load_local_env
from qaseed.llm_client import KEY_NAME, MODEL_NAME, LLMError, api_key, public_text, selected_model
from qaseed.plan_models import ScenarioPlan
from qaseed.planner import MAX_REQUESTS, PROMPT_ID, plan_scenario
from qaseed.schema import load_schema

SCENARIOS_PATH = ROOT / "tests" / "fixtures" / "day3_scenarios.json"
ROBUSTNESS_PATH = ROOT / "tests" / "fixtures" / "day4_eval_scenarios.json"
SETS = {"core": (SCENARIOS_PATH,), "robustness": (ROBUSTNESS_PATH,), "all": (SCENARIOS_PATH, ROBUSTNESS_PATH)}
PIPELINE_FAILURES = ("llm_error", "malformed_llm_output")


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


def load_set(name: str) -> list[dict]:
    cases = []
    for path in SETS[name]:
        label = "core" if path == SCENARIOS_PATH else "robustness"
        cases += [{**case, "set": label} for case in load_scenarios(path)]
    return cases


def expected_for(case: dict, mode: str) -> dict:
    if mode == "live" and "live_expected" in case:
        return case["live_expected"]
    return case["expected"]


def compare(plan: ScenarioPlan, expected: dict, requests: int | None = None) -> list[str]:
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
    missing = [c for c in _expected_conditions(expected) if c not in _plan_conditions(plan)]
    if missing:
        problems.append(f"missing conditions {missing}")
    if requests is not None:
        if "max_requests" in expected and requests > expected["max_requests"]:
            problems.append(f"{requests} API requests > max {expected['max_requests']}")
        if "min_requests" in expected and requests < expected["min_requests"]:
            problems.append(f"{requests} API requests < min {expected['min_requests']}")
    return problems


def schema_violations(plan: ScenarioPlan, schema: dict) -> list[str]:
    """Independent re-check that a plan only uses names and values from the schema."""
    tables = {table["name"]: table for table in schema["tables"]}
    problems = [f"unknown table {t.table}" for t in plan.tables if t.table not in tables]
    for cond in plan.conditions:
        table = tables.get(cond.table)
        if table is None:
            problems.append(f"unknown table {cond.table}")
        elif cond.value not in table["allowed_values"].get(cond.column, []):
            problems.append(f"{cond.table}.{cond.column}={cond.value} not allowed")
    return problems


def sequence_post(steps: list[dict]):
    """Mock post() that plays scripted steps in order; the last step repeats.

    {"reply": dict | str}  a model reply
    {"error": kind}        raises LLMError(kind), e.g. rate_limit, timeout, auth
    {"blocked": reason}    a provider safety block (no candidates)
    """
    calls: list[dict] = []

    def post(url, payload, key):
        step = steps[min(len(calls), len(steps) - 1)]
        calls.append(step)
        if "error" in step:
            raise LLMError(step["error"], f"{step['error']} (simulated)", step.get("retry_after"))
        if "blocked" in step:
            return {"promptFeedback": {"blockReason": step["blocked"]}}
        reply = step["reply"]
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": content}]}, "finishReason": "STOP"}]}

    post.calls = calls
    return post


def counting_post(post):
    calls: list[int] = []

    def wrapped(url, payload, key):
        calls.append(1)
        return post(url, payload, key)

    wrapped.calls = calls
    return wrapped


def run(
    mode: str,
    ids: set[str] | None = None,
    env: dict | None = None,
    set_name: str = "core",
    delay: float = 0.0,
) -> list[dict]:
    schema = load_schema()
    rows = []
    cases = [c for c in load_set(set_name) if mode in c.get("modes", ("mock", "live"))]
    for index, case in enumerate(c for c in cases if not ids or c["id"] in ids):
        backoff: list[float] = []
        if mode == "mock":
            steps = case.get("mock_sequence") or [{"reply": case["mock_reply"]}]
            post = sequence_post(steps)
            run_env = {KEY_NAME: "mock-key-not-real", MODEL_NAME: "mock"}
            source, sleep = "mock", backoff.append  # simulated API: record backoff, do not wait
        else:
            if index and delay:
                time.sleep(delay)
            post = counting_post(llm_client.post_json)
            run_env, source = env, "llm"

            def sleep(seconds, _log=backoff):
                _log.append(seconds)
                time.sleep(seconds)

        started = time.perf_counter()
        plan = plan_scenario(case["scenario"], env=run_env, post=post, schema=schema, source=source, sleep=sleep)
        wall_ms = round((time.perf_counter() - started) * 1000, 2)
        requests = len(post.calls)
        expected = expected_for(case, mode)
        problems = compare(plan, expected, requests)
        expected_conditions = _expected_conditions(expected)
        rows.append({
            "id": case["id"],
            "set": case["set"],
            "category": case["category"],
            "scenario": public_text(case["scenario"], ""),
            "expected": expected,
            "expected_status": expected["status"],
            "actual": plan.status,
            "issues": plan.issue_codes(),
            "issue_messages": [public_text(issue.message, "") for issue in plan.issues],
            "counts": plan.counts(),
            "conditions": [list(c) for c in _plan_conditions(plan)],
            "conditions_expected": len(expected_conditions),
            "conditions_found": sum(c in _plan_conditions(plan) for c in expected_conditions),
            "schema_violations": schema_violations(plan, schema) if plan.status == "valid" else [],
            "match": not problems,
            "problems": problems,
            "requests": requests,
            "backoff_s": round(sum(backoff), 2),
            "wall_ms": wall_ms,
            "llm_latency_ms": plan.metadata.latency_ms,
            "error_kind": plan.metadata.error_kind,
            "retried_errors": plan.metadata.retried_errors,
            "usage": plan.metadata.usage,
            "total_tokens": _total_tokens(plan.metadata.usage),
            "prompt_id": plan.metadata.prompt_id or PROMPT_ID,
            "source": plan.source,
        })
    return rows


def metrics(rows: list[dict]) -> dict:
    """Day 4 evaluation metrics. Each value is {"value": x, "n": denominator}."""

    def ratio(hits: int, n: int) -> dict:
        return {"value": round(hits / n, 3) if n else None, "hits": hits, "n": n}

    replied = [r for r in rows if r["requests"] and "llm_error" not in r["issues"]]
    valid = [r for r in rows if r["actual"] == "valid"]
    expect_valid = [r for r in rows if r["expected_status"] == "valid"]
    expect_block = [r for r in rows if r["expected_status"] != "valid"]
    with_counts = [r for r in expect_valid if "counts" in r["expected"]]
    unexpected_failures = [
        r for r in rows
        if any(code in r["issues"] for code in PIPELINE_FAILURES)
        and not any(code in r["expected"].get("issue_codes", []) for code in PIPELINE_FAILURES)
    ]
    walls = sorted(r["wall_ms"] for r in rows)
    tokens = [r["total_tokens"] for r in rows if r["total_tokens"] is not None]
    return {
        "scenarios": len(rows),
        "outcome_accuracy": ratio(sum(r["match"] for r in rows), len(rows)),
        "structured_plan_validity": ratio(sum("malformed_llm_output" not in r["issues"] for r in replied), len(replied)),
        "schema_correctness": ratio(sum(not r["schema_violations"] for r in valid), len(valid)),
        "count_accuracy": ratio(sum(r["counts"] == r["expected"]["counts"] for r in with_counts), len(with_counts)),
        "condition_coverage": ratio(sum(r["conditions_found"] for r in expect_valid), sum(r["conditions_expected"] for r in expect_valid)),
        "rejection_accuracy": ratio(
            sum(r["actual"] == r["expected_status"] and all(c in r["issues"] for c in r["expected"].get("issue_codes", [])) for r in expect_block),
            len(expect_block),
        ),
        "false_acceptances": ratio(sum(r["actual"] == "valid" for r in expect_block), len(expect_block)),
        "false_rejections": ratio(sum(r["actual"] != "valid" for r in expect_valid), len(expect_valid)),
        "unexpected_failure_rate": ratio(len(unexpected_failures), len(rows)),
        "api_requests": sum(r["requests"] for r in rows),
        "request_budget_violations": sum(any("API requests" in p for p in r["problems"]) for r in rows),
        "latency_ms": {
            "median": round(statistics.median(walls), 2) if walls else None,
            "p95": walls[max(0, math.ceil(0.95 * len(walls)) - 1)] if walls else None,
            "max": walls[-1] if walls else None,
            "n": len(walls),
        },
        "total_tokens": sum(tokens) if tokens else None,
    }


def print_report(rows: list[dict], mode: str) -> None:
    label = "  (mock = canned replies and simulated API errors, NOT model accuracy)" if mode == "mock" else ""
    print(f"mode={mode}{label}")
    print("| id | category | expected | actual | issues | requests | match |" + (" latency_ms |" if mode == "live" else ""))
    print("|---|---|---|---|---|---|---|" + ("---|" if mode == "live" else ""))
    for row in rows:
        line = (f"| {row['id']} | {row['category']} | {row['expected_status']} | {row['actual']} | "
                f"{','.join(row['issues']) or '-'} | {row['requests']} | {'yes' if row['match'] else 'NO'} |")
        if mode == "live":
            line += f" {row['llm_latency_ms']} |"
        print(line)
    for row in rows:
        for problem in row["problems"]:
            print(f"mismatch {row['id']}: {problem}")
    for row in rows:
        if row.get("retried_errors"):
            outcome = f"ended with {row['error_kind']}" if row.get("error_kind") else "recovered"
            print(f"retry {row['id']}: {','.join(row['retried_errors'])} -> {outcome}")
    total = len(rows)
    matched = sum(row["match"] for row in rows)
    by_status = {s: sum(r["actual"] == s for r in rows) for s in ("valid", "rejected", "needs_clarification")}
    print(f"summary: {matched}/{total} matched expected outcome; "
          f"valid={by_status['valid']} rejected={by_status['rejected']} needs_clarification={by_status['needs_clarification']}")
    m = metrics(rows)
    for name in ("structured_plan_validity", "schema_correctness", "count_accuracy", "condition_coverage",
                 "rejection_accuracy", "false_acceptances", "false_rejections", "unexpected_failure_rate"):
        item = m[name]
        value = "n/a" if item["value"] is None else f"{item['value']:.3f}"
        print(f"metric {name}={value} ({item['hits']}/{item['n']})")
    print(f"metric api_requests={m['api_requests']} request_budget_violations={m['request_budget_violations']}")
    lat = m["latency_ms"]
    where = "local wall-clock incl. any real backoff" if mode == "live" else "local wall-clock, no network"
    print(f"latency_ms ({where}): median={lat['median']} p95={lat['p95']} max={lat['max']} (n={lat['n']})")
    live_lat = [r["llm_latency_ms"] for r in rows if mode == "live" and r["llm_latency_ms"] is not None]
    if live_lat:
        print(f"llm_latency_ms: median={statistics.median(live_lat):.0f} min={min(live_lat)} max={max(live_lat)} (n={len(live_lat)})")
    print(f"total_tokens={m['total_tokens'] if m['total_tokens'] is not None else 'NOT MEASURED'}  cost=NOT MEASURED")


def write_results(path: Path, rows: list[dict], mode: str, set_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "mode": mode,
        "set": set_name,
        "prompt_id": PROMPT_ID,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "mock = hand-written replies and simulated API errors; NOT model accuracy" if mode == "mock" else "live Gemini results",
        "metrics": metrics(rows),
        "rows": rows,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="call the real Gemini API (uses free-tier quota)")
    parser.add_argument("--ids", default="", help="comma-separated scenario IDs, e.g. S01,S08")
    parser.add_argument("--set", dest="set_name", choices=sorted(SETS), default="core", help="scenario set (default: core)")
    parser.add_argument("--out", default="", help="write per-scenario expected vs actual results and metrics to this JSON file")
    parser.add_argument("--delay", type=float, default=0.0, help="live mode: seconds to wait between scenarios")
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
        count = len([c for c in load_set(args.set_name) if "live" in c.get("modes", ("mock", "live")) and (not ids or c["id"] in ids)])
        print(f"Live run: up to {count * MAX_REQUESTS} Gemini requests ({count} scenarios, at most {MAX_REQUESTS} each). Free-tier rate limits may return 429.")
    rows = run(mode, ids, env, args.set_name, args.delay)
    print_report(rows, mode)
    if args.out:
        out = Path(args.out)
        out = out if out.is_absolute() else ROOT / out
        write_results(out, rows, mode, args.set_name)
        print(f"results={_display_path(out)}")
    return 0 if all(row["match"] for row in rows) else 1


def _display_path(path: Path) -> str:
    """Project-relative path, or just the file name: never a local absolute path (it can hold the user's name)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _expected_conditions(expected: dict) -> list[tuple]:
    return [tuple(c) for c in expected.get("conditions", [])]


def _plan_conditions(plan: ScenarioPlan) -> list[tuple]:
    return [(c.table, c.column, c.value, c.count) for c in plan.conditions]


def _total_tokens(usage: str) -> int | None:
    for part in (usage or "").split():
        if part.startswith("totalTokenCount="):
            return int(part.split("=", 1)[1])
    return None


if __name__ == "__main__":
    sys.exit(main())
