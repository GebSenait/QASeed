# Day 5 Implementation Plan — Interface Integration

> **Scope update (11 Nov 2026): CSV-only export for the Day 5 MVP.** QASeed exports the four related tables (customers, subscriptions, invoices, payments) as CSV files in one ZIP together with a validation report. **SQL export is not part of Day 5; it is a planned post-MVP enhancement.** Priority: a working end-to-end Streamlit demo and verified data quality over additional export formats. The Day 1 proposal and the architecture guide list "SQL/CSV" downloads; those reference documents are left unchanged and this note records the decision.

## 1. Audit findings (verified before coding)

| Item | Observed |
|---|---|
| Git | Branch `feature/day5-ui-integration` at `7b3ddec` (merge of PR #2 into this branch's base). Working tree clean. `main` not touched. |
| `.gitignore` | Covers `_project_references/`, `.env`, `.env.*` (keeps `.env.example`), `.streamlit/secrets.toml`, `*.db`, `*.sqlite3`, `.venv/`, caches. `tests/test_layout.py` enforces these rules. |
| Python / deps | Python 3.12.0 venv; Streamlit 1.67 (`>=1.67,<2`), pytest, pytest-html, pydantic, Faker, SQLAlchemy, python-dotenv. No new dependency needed for Day 5. |
| Regression suite | `python -m pytest -q` → **262 passed, 0 failed, 0 skipped** (9.2 s). No failures to triage. |
| References | All five `_project_references/*.docx` read (text extracted locally, documents unchanged). Architecture diagram inspected. Workbook Day 5 checklist and fields confirmed (section 2). |
| API | `.env` contains `GEMINI_API_KEY` and `GEMINI_MODEL` (values not printed). No live request was made during the audit. |

### What already works (reuse, do not rewrite)

| Component | Module | State |
|---|---|---|
| Input guardrails (injection, SQL, PII, secrets) | `qaseed/guardrails.py` | Working, tested |
| AI planner (Gemini, prompt P04, bounded retries, strict JSON contract) | `qaseed/planner.py`, `qaseed/llm_client.py` | Working; live 22/22 on Day 4 |
| Schema-based plan validation, derived counts, generation order, verification checks | `qaseed/plan_validator.py`, `qaseed/plan_models.py` | Working, tested |
| Seeded generator (Faker + SQLAlchemy/SQLite, exact condition counts, status-driven dates) | `qaseed/generator.py` (`generate`, `build_and_check`, `coverage_rows`, `sample_rows`, `mock_plan`) | Working; 7/7 offline scenarios, 116/116 checks |
| Independent data checks (plan checks, FK integrity, business rules) | `qaseed/data_checks.py` | Working, each check proven against a broken DB |
| Offline mock replies for 18 scenarios | `tests/fixtures/day3_scenarios.json` | Working (labelled `source=mock`) |

### Partial / missing / broken

| Item | State |
|---|---|
| Streamlit app (`app.py`) | **Partial** — Day 2 schema viewer only; shows "Scenario generation is not yet implemented." No planner/generator/check connection. |
| CSV export | **Missing** — no export code anywhere. (SQL export: out of Day 5 scope, post-MVP.) |
| Streamlit-facing service layer | **Missing** — orchestration exists only inside the generator CLI (`main`). |
| Runtime logging | **Missing** — modules use `print` for CLI output only; no `logging` usage. |
| Loading / error / reset states, rerun protection | **Missing** (no interactive UI). |
| Repair loop | **Not started** — Day 6 by the architecture guide; out of scope today. |
| Hosted demo | **Not started** — Day 7 by the architecture guide. |
| Broken | Nothing broken. Two tests (`test_app.py`, `test_layout.py::test_app_source_compiles`) assert the old "not yet implemented" text and must be updated with the new UI. |

## 2. Official Day 5 requirements (DDS workbook)

Checklist: (1) user flow designed (screens + inputs/outputs); (2) interface connected to backend/logic; (3) UX basics: loading, error states, reset/clear; (4) logging of inputs/outputs enabled (safe logging); (5) demo link created (even if rough).

Workbook fields: interface tech · demo link · screenshots link · known UX issues to fix · decisions made today (why) · blockers / help needed.

Architecture guide, Day 5: connect the existing planner, generator and validator to Streamlit without duplicating business logic; enter scenario → review plan → generate and validate; loading and clear error states (missing key, invalid request, generation failed, validation blocked); reset; SQL and CSV downloads with stable ordering, **blocked on failed checks**; seeded sample scenario for a reliable demo; validate exports by loading them into a fresh SQLite database; local demo is acceptable if labelled honestly. **Day 5 MVP applies this to CSV only** (scope update above).

## 3. Proposed minimal changes

| File | Change |
|---|---|
| `qaseed/export.py` (new) | CSV per table (schema column order, rows by primary key, empty cell = NULL) and one ZIP with the four CSVs + `validation_report.json`. Raises if the check report did not pass. `verify_csv_zip` reloads the CSV files into a fresh SQLite database and re-runs the same checks. No SQL export (post-MVP). |
| `qaseed/pipeline.py` (new) | UI-independent service layer: `llm_status()`, `plan_request()` (live Gemini or offline sample replies), `run_generation()` → result object with coverage rows, checks, sample rows and export bytes. Safe structured logging (metadata only, timings, error categories). Reuses planner, generator, data checks, export. |
| `app.py` | Rebuilt as a 4-step flow on top of `pipeline`; session state, callbacks, spinners, reset, stale-plan and duplicate-run guards, honest status messages. |
| `tests/test_export.py`, `tests/test_pipeline.py` (new) | Export contents re-imported into a fresh SQLite DB and re-checked; logging contains no scenario text or key; offline/live/no-key/API-error paths with mocked HTTP. |
| `tests/test_app.py` | Headless AppTest journeys: sample success with downloads, invalid scenario, no API key, API error, reset, repeated clicks. |
| `tests/test_layout.py` | Replace the obsolete "not yet implemented" assertion. |
| `README.md`, `docs/day5_evidence.md` | Day 5 run instructions and workbook evidence. |

No new framework, dependency, database or deployment.

## 4. End-to-end user flow

1. **Describe** — type a scenario or load a built-in sample. Choose the planner: *Live Gemini* (only offered when a key is configured) or *Offline sample* (canned replies for the 18 built-in scenarios, labelled `mock`). Optional seed and as-of date.
2. **Review AI plan** — status (valid / needs clarification / rejected), source, model, requests and latency; tables with stated vs derived counts; requested conditions; assumptions; issues with clear reasons. Invalid plans stop here.
3. **Generate & validate** — only for a valid plan whose scenario text has not changed. Shows requested vs generated vs verified per table and condition, every data check (PASS/FAIL), and sample rows.
4. **Download** — one ZIP: `customers.csv`, `subscriptions.csv`, `invoices.csv`, `payments.csv`, `validation_report.json`. Offered only when every data check passed **and** the CSV files pass the same checks after being reloaded into a fresh database; otherwise export is blocked with the reason.

Reset clears the scenario, plan and results.

## 5. Acceptance criteria and test approach

| # | Criterion | How it is tested |
|---|---|---|
| A1 | S01 (50 customers, 10 canceled, 8 overdue, 5 failed) completes plan → generate → 23/23 checks → CSV ZIP download | AppTest + manual browser run |
| A2 | ZIP holds 4 CSVs + validation report; record counts, column names, unique IDs, cross-file foreign keys and requested conditions are correct; CSVs reloaded into a fresh DB pass the same checks; tampered files fail | `test_export.py`, `test_pipeline.py` |
| A3 | Invalid scenario shows the rejection reasons and offers no Generate button | AppTest (S08, S11) + manual |
| A4 | No API key: live mode is not offered; a typed scenario gets an honest "not configured" message; no request is made | AppTest with `GEMINI_API_KEY=""` + manual |
| A5 | API failure (timeout / rate limit) shows a safe error, no key, no fake plan | AppTest with mocked `post_json` |
| A6 | Reset clears state; repeated clicks / reruns do not re-plan or re-generate | AppTest |
| A7 | Logs contain status, timings and error categories; never the scenario text or key | `test_pipeline.py` with `caplog` |
| A8 | Day 2–5 regression suite passes | `python scripts/test_report.py --day 5` |

## 6. Risks and blockers

- **Live API use:** no live Gemini request will be made without the owner's approval; live UI planning is covered by mocked tests and marked for owner verification.
- **Streamlit reruns** can repeat an API call or a generation — handled with session state and callbacks.
- **Screenshots** must be taken by the owner (the agent cannot save browser screenshots to the repo); instructions are in the evidence file.
- **Public demo URL** is a Day 7 deliverable; Day 5 is local only (`http://localhost:8501`).
- **No repair loop yet:** a failed check blocks export; repair is Day 6.
- **SQL export deferred:** users who need `INSERT` statements must load the CSV files themselves until the post-MVP SQL export lands.

## 7. Addendum (11 Nov 2026, later): payment scenario and branch recovery

**Branch.** A later audit found the repository on `main` with all Day 5 work uncommitted and `feature/day5-ui-integration` deleted (reflog: checkout to `main` at 04:10). The branch was recreated from the same commit (`7b3ddec`) with `git switch -c`, which carries the uncommitted work over without touching `main`.

**New end-to-end test scenario (owner):** "Generate 12 subscription payments: 8 successful and 4 failed. Each payment must have a valid invoice, subscription, and customer. Every failed invoice must remain unpaid, and every successful invoice must be paid."

**Gap found.** The generator linked payments to invoices by position and gave unrequested invoices the status `paid`, so failed payments could sit on paid invoices, and no data check looked at this.

| Change | File |
|---|---|
| Payments are linked so that a succeeded payment's invoice is `paid` and a failed or pending payment's invoice is `open`/`overdue`. When the plan fixes invoice statuses, payments are matched to them. When it does not, an invoice whose payments all failed becomes `open`. Impossible combinations are refused with a clear message. | `qaseed/generator.py` |
| Two new business-rule checks: invoices with a succeeded payment are paid; invoices with only failed or pending payments are unpaid | `qaseed/data_checks.py` |
| Scenario D5-01 with the reply recorded from the live Gemini run, replayed offline as a built-in sample | `tests/fixtures/day5_ui_scenarios.json`, `qaseed/pipeline.py` |
| Headless click-through of `app.py` (offline by default; `--live --confirm` for one Gemini plan) plus independent checks of the downloaded ZIP | `scripts/day5_ui_e2e.py` |
| Tests | `test_generator.py`, `test_data_checks.py`, `test_pipeline.py`, `test_app.py`, new `test_day5_ui_e2e.py` |

API budget: exactly one live plan (1 Gemini request); every other run uses the recorded reply.
