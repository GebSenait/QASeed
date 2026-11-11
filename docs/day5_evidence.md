# Day 5 — Integration of Model/API with Interface

Date: 11 Nov 2026 · Branch: `feature/day5-ui-integration` (uncommitted, awaiting owner review) · Interface: Streamlit 1.67 · Plan: [day5_implementation_plan.md](day5_implementation_plan.md)

> **MVP export scope: CSV only.** QASeed exports customers, subscriptions, invoices and payments as CSV files in one ZIP with `validation_report.json`. **SQL export is a planned post-MVP enhancement** and is not part of Day 5. Priority: a working end-to-end Streamlit demo and verified data quality over more export formats.

## Checklist

| # | Official item | Status | Evidence |
|---|---|---|---|
| 1 | User flow designed (screens + inputs/outputs) | **DONE** | 4-step tracked flow in `app.py`: Describe → Review AI plan → Generate & validate → Download. A step tracker shows each step as done, active or blocked; Reset returns to a clean start. |
| 2 | Interface connected to backend/logic | **DONE** | `qaseed/pipeline.py` connects the app to planner, validator, generator, data checks and `qaseed/export.py`. **Verified live:** the payment scenario was planned by Gemini through the app and generated, checked and exported (1 API request). |
| 3 | UX basics: loading, error states, reset/clear | **DONE** | Spinners; one-line, general error messages (e.g. “Bad request: status counts exceed the total.”, “AI service busy: try again in a minute.”); one-step examples; Reset; duplicate-click and stale-plan guards. |
| 4 | Logging of inputs/outputs enabled (safe logging) | **DONE** | One `qaseed` log line per step: mode, sample id, scenario *length*, status, issue codes, requests, retries, error category, timings, row/check counts, export re-check result, ZIP size. Scenario text, generated values and the key are never logged; a redaction filter masks key/email/number shapes. |
| 5 | Demo link created (even if rough) | **DONE (link pending upload)** | Live-take demo video recorded (82 s, 1920×1080 MP4, with the author's name on screen), kept outside the repo for upload. Hosted app: Day 7. |

## Workbook fields (ready to paste)

| Field | Entry |
|---|---|
| **Interface tech** | Streamlit 1.67 (Python); tested headless with Streamlit AppTest and in a real browser with Playwright + Chromium |
| **Demo link** | PASTE LINK to the uploaded demo video (LinkedIn / Loom / YouTube unlisted). Hosted app: Day 7. |
| **Screenshots link** | PASTE LINK to the folder with the 6 live-take screenshots (step 1 Describe, step 2 Review AI plan, step 3 Generate & validate, step 4 Download, flow complete, invalid request blocked). |
| **Known UX issues to fix** | 1. Without Live AI, only the 19 ready-made examples can be planned. 2. A failed check blocks export, but there is no automatic repair yet (Day 6). 3. On phones the requested-vs-actual table scrolls sideways inside the table. 4. Export is CSV + JSON; SQL export is post-MVP. |

### Decisions made today (why)

- **CSV-only export for the MVP; SQL export moved to post-MVP.** CSV covers the demo need; time went into a working end-to-end flow and proven data quality.
- **Payments and invoices must tell one story.** The owner's payment scenario showed that failed payments could sit on paid invoices. The generator now links payments to matching invoices, two new checks prove it, and impossible combinations are refused instead of silently produced.
- **Verify the file, not just the database.** Before the download is offered, the ZIP is re-read, loaded into a fresh SQLite database and the same checks re-run; tampered files fail.
- **Service layer between UI and logic (`qaseed/pipeline.py`).** The UI cannot fabricate results; every plan, check and file comes from tested modules.
- **One live request, then replay.** The live Gemini reply for the payment scenario was recorded once and is replayed offline in the app, tests and demo, so the free tier is not used up.
- **Results kept in session state; same input reuses the stored plan.** Streamlit reruns and double clicks do not spend API quota or regenerate data.
- **Log metadata, not content.** Scenario length and outcome are enough to debug; the text itself may hold customer details.
- **A workflow that shows real progress.** The plan stays “in review” until data is generated; downloading completes the flow; Reset returns to a clean start.
- **One-line, general error messages.** Invalid requests show one clear line; technical detail stays in the plan JSON for developers.
- **Protect the free tier.** Live AI is capped per session (3) and per day (40), configurable; ready-made examples are never capped.
- **Real-browser tests (Playwright).** The full journey is clicked through in Chromium, which catches problems a headless logic test cannot.

### Blockers / help needed

- No blockers. Deployment is scheduled for Day 7.
- Live use stayed on the Gemini free tier (6 requests across 4 live plans on Day 5). Cost is not measured.

---

## Live end-to-end run through the app (Gemini, 11 Nov 2026)

Command: `python scripts/day5_ui_e2e.py --live --confirm --as-of 2026-11-11` (drives `app.py` with Streamlit AppTest: type scenario → *Plan scenario* → *Generate & validate data* → the ZIP from the download button). Full output: [reports/day5/ui_e2e_payment_live.txt](../reports/day5/ui_e2e_payment_live.txt).

Scenario: *"Generate 12 subscription payments: 8 successful and 4 failed. Each payment must have a valid invoice, subscription, and customer. Every failed invoice must remain unpaid, and every successful invoice must be paid."*

| Step | Result |
|---|---|
| Plan | `gemini-3.5-flash-lite`, prompt P04: **valid**, 1 request, model latency 6,251 ms, 1,319 tokens. Payments 12 (stated), invoices 12 (stated), subscriptions 12 and customers 12 (derived); conditions payments succeeded 8, failed 4. |
| Generate & validate | 48 rows; **24/24 data checks**; requested vs generated vs verified 6/6 PASS |
| Download | One button: *Download CSV files + validation report (ZIP)* |
| Independent ZIP checks | 10/10 PASS: 4 CSVs + report; 12 rows each with unique IDs; 12/12 payment → invoice → subscription → customer links; 8 succeeded / 4 failed; succeeded → `paid` ×8; failed → `open` ×4; report 24/24 |

The model did not request invoice statuses. Before today's generator change, all 12 invoices would have defaulted to `paid`, so the 4 failed payments would have been linked to paid invoices. The new rule and checks prevent and detect this.

The recorded reply is stored in `tests/fixtures/day5_ui_scenarios.json` (D5-01) and replayed offline: [reports/day5/ui_e2e_payment_offline.txt](../reports/day5/ui_e2e_payment_offline.txt) → PASS with no API call.

## Final live demo take (11 Nov 2026, 09:56)

Recorded with Playwright (headless Chromium) through the real app in Live AI mode, one Plan click, after an offline rehearsal with no API call.

| Step | Result |
|---|---|
| Plan | `gemini-3.5-flash-lite`, prompt P04: valid; 2 requests (one temporary API error retried automatically by QASeed), 5,403 ms |
| Generate & validate | 48 rows; **24/24 data checks**; requested = actual for all 6 counts |
| Download | CSV ZIP re-checked in a fresh database: 24/24; independent file checks 10/10 (every failed payment on an open invoice) |
| Workflow tracker | step 1 → step 2 active (review) → step 4 active after generate → all ✓ after download |
| Invalid request (S16) | one line “Bad request: status counts exceed the total.”; step 2 blocked; no data created; no API call |

Video and 6 phase screenshots are kept outside the repository for upload (no key, local path or account name visible; checked frame by frame).

## Bonus challenges

| Bonus | Status | Evidence |
|---|---|---|
| Add download/export features | **DONE** | CSV ZIP (4 tables + `validation_report.json`) and a separate **“Download validation report (JSON)”** button; the JSON is byte-identical to the report in the ZIP (browser test) |
| UI deployment (free cloud) | **Deferred to Day 7** | Not part of Day 5 |

## Verified user journey (manual, browser, 11 Nov 2026)

These early manual runs record the wording the app used at the time; the current one-line messages are described above.

| Run | Setup | Steps | Result |
|---|---|---|---|
| M1 API unavailable | App started with `GEMINI_API_KEY` blank | Sidebar | "Live planner not configured … Offline samples only."; only *Offline samples* offered |
| M2 custom text, no API | same | Typed "Generate 12 customers with 3 canceled subscriptions." → Plan | "This scenario needs the live AI planner, which is not available. Nothing was sent or generated." + `llm_not_configured`; no Generate button |
| M3 flagship | same | Sample S01 → Plan | "Plan is valid"; source: built-in sample reply (mock, no API call); 4 tables; 3 conditions; 4 assumptions |
| M4 duplicate click | same | Plan clicked again | "This scenario is already planned (no new API request)"; log `event=plan status=reused` |
| M5 generate | same | Generate & validate | 200 rows; all checks passed; requested vs generated vs verified 7/7 PASS; sample rows per table |
| M6 invalid | same | Sample S11 "Create 50 customers and 5 refunds." → Plan | "Plan rejected" + `unknown_table — Table 'refunds' is not in the schema…`; no Generate button |
| M7 reset | same | Reset / clear all | Scenario, plan and results cleared |
| M8 live configured | Normal `.env`, `--server.address localhost` | Sidebar | "Live planner: Google Gemini (`gemini-3.5-flash-lite`)", live mode default; key not shown |
| M9 CSV download | Blank key, offline S01, seed 42 | Generate → download ZIP | One download button; ZIP contents listed; "Download verified". The served file's SHA-256 was identical to the pipeline's ZIP |

The browser runs M1–M9 were made before the payment/invoice rule was added. The S01 checks then numbered 23; they are now 25 (offline S01: 25/25, ZIP 4,767 bytes). The browser flow itself did not change; the live AppTest run above exercises the current build.

Defects found and fixed during the manual run: seed/as-of metric was truncated (split into Seed metric + caption); Reset did not return the sample picker to the first sample.

### Safe log lines (live run)

```text
INFO qaseed event=plan mode=live sample=- scenario_chars=213 status=valid source=llm issues=- requests=1 retries=0 error_kind=- model_ms=6251 total_ms=6257
INFO qaseed event=generate status=verified seed=42 as_of=2026-11-11 rows=48 checks=24/24 failed=- export=ready export_checks=24/24 zip_bytes=2832 total_ms=303
```

No scenario text, generated values or key.

### CSV export contents (S01, seed 42, as-of 2026-11-11)

| Check | Result |
|---|---|
| Files | `customers.csv`, `subscriptions.csv`, `invoices.csv`, `payments.csv`, `validation_report.json`; no `.sql` |
| Record counts / columns | 50 rows each, schema column order |
| IDs | Unique, 1..50, ordered by primary key; 50 unique emails, all `@example.com` |
| Foreign keys | Every child row's parent exists in its parent file |
| Requested conditions | 10 canceled subscriptions, 8 overdue invoices, 5 failed payments (each on an unpaid invoice) |
| Reload | CSVs loaded into a fresh SQLite DB pass the same **25/25** checks |
| Tamper detection | Changing a status, dropping a row, breaking a foreign key, duplicating an ID or corrupting the ZIP each fails verification (automated) |

## Automated tests

`python scripts/test_report.py --day 5` → **375 passed, 0 failed, 0 skipped**, including 5 real-browser tests ([report](../reports/day5/day5_test_report_20261111-110720.html)). Day 4 end: 227; Day 5 start: 262. No live API calls in pytest. Other checks: 30-scenario evaluation 30/30 (mock); `python scripts/day5_ui_e2e.py` PASS.

| New / changed test file | Tests | Covers |
|---|---|---|
| `tests/test_app.py` | 53 (was 1) | All 8 valid examples end to end (plan → review → generate → verified → download → reset); every invalid example shows exactly one general line; tracker states; quick examples; connection status; About; live errors (mocked); Live AI caps; default mode |
| `tests/test_browser_smoke.py` | 5 (new) | Real Chromium: brand + logo, payment example to verified ZIP and JSON downloads, one-line rejection, full tracker journey with download and reset, header visible with the sidebar collapsed |
| `tests/test_pipeline.py` | 29 | Planning in every mode, generation, validation report, export gating and re-check, JSON report, secrets loading, Live AI limits, daily counter, safe logging |
| `tests/test_export.py` | 17 | ZIP contents, counts, columns, IDs, cross-file foreign keys, conditions, reload, tamper detection, no SQL export |
| `tests/test_generator.py` | 27 (+4) | Payment/invoice story: free and fixed invoice statuses, impossible combinations refused |
| `tests/test_data_checks.py` | 14 (+2) | Both new payment/invoice rules catch a tampered database |
| `tests/test_day5_ui_e2e.py` | 4 (new) | Script: offline payment run passes, live needs `--confirm`, ZIP check catches a failed payment on a paid invoice |
| `tests/test_layout.py` | updated | New modules required; obsolete UI assertion replaced |

Offline generator results: 7/7 scenarios, **120/120 checks** ([datagen_mock.json](../reports/day5/datagen_mock.json)).

## Dependencies

Runtime (`requirements.txt`): no new packages. Development (`requirements-dev.txt`): pytest, pytest-html and **Playwright 1.65** (+ `python -m playwright install chromium`) for the real-browser tests. `pip check` → no broken requirements (Python 3.12.0, Streamlit 1.67.0).

## Post-MVP enhancements (recorded, not built)

- **SQL export:** `INSERT` script for the generated dataset, re-verified by loading it into a fresh database like the CSV export.
- Automatic repair of failed checks (Day 6 scope); hosted deployment and smoke tests (Day 7 scope).

## Screenshots

Captured automatically during the final live take (Playwright, 3200×1800): one per phase (step 1 Describe, step 2 Review AI plan, step 3 Generate & validate, step 4 Download), the completed flow and the blocked invalid request. They are kept outside the repository for upload. To re-capture by hand, follow [day5_manual_test_guide.md](day5_manual_test_guide.md); before publishing, check that no key, local path or account name is visible.
