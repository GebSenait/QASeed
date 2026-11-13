# Testing and Daily Test Reports

Tools: **pytest** (test runner) + **pytest-html** (standalone HTML report with pass/fail details, durations, timestamps and environment metadata). Both are in `requirements.txt`, with Playwright for the browser tests.

## Folder layout

```text
QASeed/
├── qaseed/<module>.py          # application code
├── app.py                      # Streamlit UI
├── tests/
│   ├── conftest.py             # report title + safe metadata (no secrets)
│   ├── fixtures/               # scenario sets, canned LLM replies
│   └── test_<module>.py        # one test file per module (enforced)
├── scripts/test_report.py      # runs pytest and writes the day's report
└── reports/
    ├── day3/
    │   ├── index.md                               # one row per run
    │   └── day3_test_report_YYYYMMDD-HHMMSS.html  # self-contained report
    ├── day4/ ... day8/
```

## Daily command (terminal, venv active)

```bash
python scripts/test_report.py
```

The day is worked out from the date (Day 3 = 9 Nov 2026 … Day 8 = 14 Nov 2026). To choose it yourself:

```bash
python scripts/test_report.py --day 4
```

Extra pytest options go after `--`, e.g. only planner tests:

```bash
python scripts/test_report.py --day 4 -- -k planner
```

Open the HTML file from `reports/dayN/` in a browser. `reports/dayN/index.md` lists every run that day with pass/fail counts and the commit it ran on.

Quick runs without a report are still fine: `python -m pytest -q`.

## Smoke test of the deployed app (Playwright, headless)

The 5 browser tests can run against the hosted app instead of a local server. They stay in Ready-made examples mode, so they make no Gemini calls. PowerShell:

```bash
$env:QASEED_BASE_URL = "https://proven-data-seed.streamlit.app"
python scripts/test_report.py --day 7 -- -m browser
Remove-Item Env:QASEED_BASE_URL
```

Use the public app URL as it is. Streamlit Community Cloud shows the app in a frame served from `/~/+/`, and the tests open that inner address themselves; the outer page does not contain QASeed's elements. Open the URL in a browser first if the app may be asleep.

## Rules

1. **One test file per module.** Every `qaseed/<name>.py` and `app.py` must have `tests/test_<name>.py`. `tests/test_layout.py::test_every_module_has_its_own_test_file` fails if one is missing, so a new Day 4–8 module (e.g. a generator) needs its test file in the same change.
2. **New behaviour ships with tests.** Add or update tests in the same change as the code. Never delete or weaken a test just to get a green report.
3. **No live API calls in pytest.** Tests use mock `post`/`get` functions and fake keys (`AIzaTEST…`). Live Gemini checks are run separately (`python -m qaseed.llm_ping`, `python -m qaseed.evaluate --live`) and recorded as LIVE evidence, never mixed into the mock results.
4. **Generate a report at least once per day,** at the end of the day's work and before any commit you plan to show as evidence.
5. **Keep reports honest.** Do not edit report files by hand. A failing run stays in `index.md`; fix the problem and run again, so the history shows both.
6. **No secrets or personal details in reports.** Never print absolute local paths (they contain the account name); `test_layout.py` checks this. Report metadata shows only the Gemini *model ID*, branch and commit, never the key. Before committing reports, check: `git grep -n "AIza" -- reports` should print nothing except fake `AIzaTEST` values (normally nothing at all).
7. **Reports are evidence.** `reports/` is tracked by Git, so the DDS workbook can link to a report. Commit the final report of each day; delete scratch reports from the same day first if you don't want them public, and update `index.md` to match.

## What the reports cover

| Test file | Covers |
|---|---|
| `test_layout.py` | Project files, `.gitignore` rules, module-test rule, single `requirements.txt` covering every third-party import, no local account names/paths in README, docs or reports |
| `test_config.py` | Paths and `.env` loading |
| `test_schema.py` | SQLite schema, foreign keys, allowed values |
| `test_llm_client.py` | Gemini request/response helpers, typed errors (kind, retryable, Retry-After), key redaction |
| `test_llm_ping.py` | Connectivity check and `--list-models` (mocked) |
| `test_plan_models.py` | Pydantic plan contract |
| `test_plan_validator.py` | Deterministic plan validation |
| `test_planner.py` | Planner flow with 18 mock scenarios, malformed-output and transport retries, backoff, request cap, prompts P02–P04 |
| `test_guardrails.py` | Injection / SQL / PII / secret detection, false-positive check on core scenarios, free-text caps |
| `test_evaluate.py` | Evaluation runner: sets, scripted API errors, metrics, JSON results |
| `test_generator.py` | Seeded generator: exact conditions, links, status-driven dates, payment/invoice status story, reproducibility, CLI |
| `test_data_checks.py` | Independent data checks, each proven to catch a deliberately broken database |
| `test_pipeline.py` | UI service layer: offline/live/no-key/API-error planning, generation, export gating, validation report, safe logging |
| `test_export.py` | CSV ZIP export: files, record counts, columns, IDs, cross-file foreign keys, requested conditions, reload into a fresh DB, tamper detection (SQL export is post-MVP) |
| `test_app.py` | Streamlit journeys (headless AppTest): success with CSV download, invalid scenario, no key, API errors, duplicate clicks, stale plan, reset |
| `test_browser_smoke.py` | Real browser (Playwright + headless Chromium) against the running Streamlit server, no API key: brand and mode, payment example to a verified CSV ZIP download, rejected example, header not hidden under Streamlit's top bar; also runs against the deployed app with `QASEED_BASE_URL`. Skip with `-m "not browser"`; setup `python -m playwright install chromium` |
| `test_report_script.py` | Daily report helper |
| `test_day4_baseline.py` | Day 4 baseline replay (Day 3 planner loaded from Git) |
| `test_day5_ui_e2e.py` | `scripts/day5_ui_e2e.py`: app clicked through headless with the payment scenario (offline), live mode needs `--confirm`, ZIP check catches a failed payment on a paid invoice |

## Saving live model results next to the day's report (optional)

Live results are not part of pytest. To keep a text copy in the same day folder, use `cmd` redirection (PowerShell 5.1 redirection writes UTF-16):

```bash
cmd /c "python -m qaseed.evaluate --live --ids S01,S08,S14 > reports\day3\live_eval_P02.txt"
```
