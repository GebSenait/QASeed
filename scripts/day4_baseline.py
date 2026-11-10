"""Reproduce the Day 4 BASELINE: today's evaluation set run on the Day 3 planner.

    python scripts/day4_baseline.py --set all --out reports/day4/eval_mock_baseline_P03.json

Loads qaseed/planner.py exactly as it was at the Day 3 commit (prompt P03, one
retry for malformed output, no transport retries, no guardrails) straight from
Git, and runs it through the current evaluation harness in mock mode. The
validator and schema code were not changed on Day 4, so only the planner is
swapped. Offline: no API calls are made.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAY3_COMMIT = "da6206a"  # main after the Day 3 merge


def load_day3_planner(commit: str = DAY3_COMMIT) -> types.ModuleType:
    source = subprocess.run(
        ["git", "show", f"{commit}:qaseed/planner.py"],
        cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout
    module = types.ModuleType("qaseed_day3_planner")
    module.__file__ = f"<{commit}:qaseed/planner.py>"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def main(argv: list[str] | None = None) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import qaseed.evaluate as evaluate

    day3 = load_day3_planner()
    assert day3.PROMPT_ID == "P03", day3.PROMPT_ID

    def day3_plan(*args, sleep=None, **kwargs):  # the Day 3 planner had no sleep parameter
        return day3.plan_scenario(*args, **kwargs)

    evaluate.plan_scenario = day3_plan
    evaluate.PROMPT_ID = day3.PROMPT_ID
    args = list(sys.argv[1:] if argv is None else argv)
    if "--live" in args:
        print("Baseline replay is mock-only.")
        return 2
    print(f"baseline: planner from {DAY3_COMMIT} (prompt {day3.PROMPT_ID})")
    return evaluate.main(args)


if __name__ == "__main__":
    sys.exit(main())
