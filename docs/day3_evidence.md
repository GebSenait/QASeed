# Day 3 — Building the Brain of Your App

> Labelling rule used in this document: **MOCK** = canned model replies written by hand to test QASeed's own code. **LIVE** = a real API response. LIVE results are in sections 1.5a (samples) and 1.5b (full 18-scenario run).

---

## 1. Technical evidence

### 1.1 Implemented code

| File | Role |
|---|---|
| `qaseed/planner.py` | Planner Agent: prompt `P01`, one Google Gemini `generateContent` call (JSON response mode), one bounded retry on malformed output, CLI. |
| `qaseed/llm_client.py` | Gemini REST client (stdlib only): key in `x-goog-api-key` header, model list, labelled errors, key redaction. |
| `qaseed/plan_models.py` | Pydantic contracts: `LLMInterpretation` (strict, no extra keys, real integers) and `ScenarioPlan` (validated output). |
| `qaseed/plan_validator.py` | Deterministic validation: schema references, allowed values, count limits (1–10,000), conflicts, FK dependencies, derived counts as explicit assumptions, verification checks for Day 4. |
| `qaseed/schema.py` | Schema parser extended with allowed `CHECK ... IN` values and UNIQUE columns. |
| `qaseed/evaluate.py` | Runs the 18-scenario set in mock mode (default) or live mode (`--live`). |
| `qaseed/llm_ping.py` | Gemini connectivity check with latency; `--list-models` lists the models the key can call without generating anything. |
| `tests/fixtures/day3_scenarios.json` | 18 scenarios, mock replies, expected outcomes. |
| `tests/test_planner.py`, `tests/test_plan_validator.py`, `tests/test_llm_ping.py` | Automated tests. |

Workflow: `scenario → planner.plan_scenario → LLM (JSON) → plan_models.LLMInterpretation (Pydantic) → plan_validator.validate_interpretation → ScenarioPlan`. The LLM interprets; Python decides what the schema allows. The plan is data only and is never executed as SQL.

### 1.2 Planned vs completed

| Planned for Day 3 | Status |
|---|---|
| `_project_references/` ignored | Done (`git check-ignore` verified) |
| Resolve runtime API access | **Done**: provider switched to Google Gemini free tier; live requests succeed (section 1.5). |
| Pydantic plan contract | Done |
| Schema-aware deterministic validation | Done |
| Planner LLM call with error handling + bounded retry | Done, tested with mock replies only |
| 10–20 scenarios with expected outcomes | Done (18) |
| Mock baseline | Done (18/18) |
| Live baseline + latency | Done: full 18-scenario run on P03, 15/18 matched, 3 `llm_error` (free-tier limit suspected), 0 wrong plans (section 1.5b) |
| README run instructions | Done |
| Streamlit planner screen | Not in scope (Day 5) |

### 1.3 Test commands and actual output (run 2026-11-09 on this machine)

Before changes (Day 2 regression baseline): `python -m pytest -q` → `26 passed in 0.24s`.

After changes (Gemini provider), **before the Gemini key was added**. These SKIPPED outputs show the safe no-key behaviour; live results are in sections 1.5a/1.5b.

```
$ python -m pytest -q
100 passed

$ python -m qaseed.llm_ping
status=SKIPPED
provider=Google Gemini
model=gemini-3.5-flash-lite
error=GEMINI_API_KEY is not set
exit=2

$ python -m qaseed.planner "Generate 50 customers, including 10 canceled subscriptions, 8 overdue invoices, and 5 failed payments."
status=SKIPPED
reason=GEMINI_API_KEY and GEMINI_MODEL must be set in .env to plan with the LLM.
No API call was made. This is not a pass.
exit=2

$ python -m qaseed.evaluate --live
status=SKIPPED
exit=2
```

No tests were removed to make the suite pass; the connectivity tests were rewritten for the Gemini request and response format.

Day 3 pytest-html report (final): `reports/day3/day3_test_report_20261109-181137.html`, **136 passed, 0 failed, 0 errors, 0 skipped** (run history in `reports/day3/index.md`; earlier run 135 passed before the P03 prompt test). The extra tests since the 100-test run cover `config`, `plan_models`, `evaluate`, `llm_client`, the Streamlit app (headless AppTest) and the report script; a rule test now requires one test file per module.

Streamlit baseline: `streamlit.testing` AppTest ran `app.py` with 0 exceptions (title "QASeed", 4 schema tables rendered); a headless `streamlit run app.py` on port 8599 answered `/_stcore/health` with `ok`. `app.py` is unchanged.

### 1.4 Scenario evaluation — MOCK baseline (`python -m qaseed.evaluate`)

| id | category | expected | actual | issues | match |
|---|---|---|---|---|---|
| S01 | normal / flagship multi-table | valid | valid | - | yes |
| S02 | normal customer/subscription | valid | valid | - | yes |
| S03 | canceled subscriptions | valid | valid | - | yes |
| S04 | failed payments | valid | valid | - | yes |
| S05 | overdue invoices | valid | valid | - | yes |
| S06 | multiple related records | valid | valid | - | yes |
| S07 | mixed subscription statuses | valid | valid | - | yes |
| S08 | invalid count (negative) | rejected | rejected | invalid_count | yes |
| S09 | invalid count (zero) | rejected | rejected | invalid_count | yes |
| S10 | invalid count (too large) | rejected | rejected | invalid_count ×2 | yes |
| S11 | unsupported table | rejected | rejected | unknown_table | yes |
| S12 | unsupported column | rejected | rejected | unknown_column | yes |
| S13 | value not allowed by schema | rejected | rejected | invalid_value | yes |
| S14 | ambiguous / incomplete | needs_clarification | needs_clarification | ambiguous_request | yes |
| S15 | conflict: subset larger than total | rejected | rejected | condition_exceeds_count | yes |
| S16 | conflict: statuses exceed total | rejected | rejected | count_conflict | yes |
| S17 | malformed model output | rejected | rejected | malformed_llm_output | yes |
| S18 | out-of-scope (production data) | rejected | rejected | unsupported_request | yes |

Summary: **18/18 matched; valid = 7, rejected = 10, needs_clarification = 1.** This proves the deterministic validation behaves as specified. It does **not** measure model interpretation accuracy, because the replies were hand-written.

Flagship plan (MOCK), abridged: customers 50 (stated); subscriptions, invoices, payments 50 each (derived, "one per parent", recorded as assumptions); conditions `subscriptions.status=canceled ×10`, `invoices.status=overdue ×8`, `payments.status=failed ×5`; generation order customers → subscriptions → invoices → payments; 3 FK relationships; 12 verification checks (4 row counts, 3 condition counts, 3 foreign-key, 2 uniqueness).

Rejected example (MOCK):

```json
{"scenario": "20 invoices: 15 paid and 10 overdue.", "status": "rejected",
 "issues": [{"severity": "error", "code": "count_conflict",
   "message": "invoices.status: 15 paid + 10 overdue = 25, more than the 20 invoices requested. A row has one status."}]}
```

### 1.5 API connection status

| Field | Value |
|---|---|
| Provider | Google Gemini, free tier of Google AI Studio (REST, stdlib HTTP, no SDK) |
| Model | `gemini-3.5-flash-lite` (set in `.env`; Stable and free-tier per Google's model page, checked 2026-11-09) |
| Key | Owner's AI Studio key, in local `.env` only; value never printed |
| Status | **PASS (live)**: genuine Gemini replies received on 2026-11-09 through `qaseed.planner` and `qaseed.evaluate --live` (owner-run, terminal screenshots). |
| Why Gemini | OpenAI's API needs prepaid credits; the project goal is a free runtime LLM for Days 3–8. |

### 1.5a Scenario evaluation — LIVE baseline, prompt P01 (owner-run)

Command: `python -m qaseed.evaluate --live --ids S01,S08,S14` · model `gemini-3.5-flash-lite` · 2026-11-09

| id | category | expected | actual (LIVE) | issues | match | latency_ms |
|---|---|---|---|---|---|---|
| S01 | normal / flagship multi-table | valid | needs_clarification | ambiguous_request | **NO** | 1500 |
| S08 | invalid count (negative) | rejected | rejected | invalid_count | yes | 1129 |
| S14 | ambiguous / incomplete | needs_clarification | needs_clarification | ambiguous_request ×6 | yes | 1244 |

Summary: **2/3 matched**; median latency 1244 ms (min 1129, max 1500, n = 3).

Flagship planner run (`python -m qaseed.planner "<flagship scenario>"`): status `needs_clarification`, 1 attempt, 1498 ms, `promptTokenCount=646 candidatesTokenCount=323 totalTokenCount=969`.

**Failure found (S01):** the model correctly wrote assumptions for the unstated subscription/invoice/payment totals, but it *also* listed "How many total subscriptions, invoices, and payments should be generated?" as an ambiguity. QASeed treats any ambiguity as blocking, so a scenario it can plan was not accepted. Cause: prompt P01 tells the model to use `ambiguities` "when the scenario does not say ... how much to create", which conflicts with QASeed's design of deriving unstated counts in Python. This is a prompt/contract mismatch, not a connectivity or validation bug. The planner did fail safe: no plan was accepted.

**Fix applied: prompt P02 (2026-11-09).** `qaseed/planner.py` now tells the model to leave unstated totals as `null` without asking; to copy impossible or conflicting numbers as written (QASeed detects them); and to use `ambiguities` only when it cannot tell which records to create at all (at most 2 questions). No validation rule was loosened. A regression test checks the prompt wording (`test_prompt_p02_does_not_ask_about_unstated_totals`). After the change: `pytest` 101 passed; mock baseline still 18/18. **P02 live result (owner-run, 2026-11-09):**

| id | category | expected | actual (LIVE, P02) | issues | match | latency_ms |
|---|---|---|---|---|---|---|
| S01 | normal / flagship multi-table | valid | valid | - | yes | 2249 |
| S08 | invalid count (negative) | rejected | rejected | invalid_count | yes | 1642 |
| S14 | ambiguous / incomplete | needs_clarification | needs_clarification | ambiguous_request ×2 | yes | 935 |

Summary: **3/3 matched** (P01: 2/3); median latency 1642 ms (min 935, max 2249, n = 3). S14 now asks 2 questions instead of 6, as P02 instructs. Still a 3-scenario sample; the full 18-scenario live run is the next step.

Flagship planner run on P02 (`python -m qaseed.planner "<flagship scenario>"`, owner-run): status `valid`, source `llm`, 1 attempt, 1355 ms, `promptTokenCount=829 candidatesTokenCount=274 totalTokenCount=1103` (P01 run: `needs_clarification`, 1498 ms, 969 tokens). The screenshot shows the end of the plan (verification checks and metadata); the resolved counts and conditions were not visible in it.

**Prompt P03 (2026-11-09).** A full live run on P02 showed that S11 ("50 customers and 5 refunds") came back as `unsupported_request`, because the model moved the unknown table into `unsupported`. P03 tells the model to keep unknown table/column/value names in `tables`/`conditions`, so QASeed's validator reports `unknown_table` / `unknown_column` / `invalid_value`. It also limits `unsupported` to work outside synthetic test-data planning. `PROMPT_ID` was bumped to P03 and the suite passes (136 tests). The P02 full-run table itself was not captured.

### 1.5b Full LIVE baseline, prompt P03 (owner-run, 2026-11-09)

Command: `python -m qaseed.evaluate --live` (all 18 scenarios) · model `gemini-3.5-flash-lite`

| Result | Count | Scenarios |
|---|---|---|
| Matched expected outcome | **15 / 18** | S01, S03–S16 (S11 now reports `unknown_table`) |
| `llm_error` (no reply from Gemini) | 3 / 18 | S02, S17, S18 |
| Wrong plan or wrong rejection | **0 / 18** | — |

**Known limitation, accepted for Day 3:** the 3 failures are API-call failures, not planning failures. Every scenario that got a model reply matched its expected outcome. The likely cause is the free-tier rate/quota limit after many requests in a row, but the exact error text was not captured, so this is **unconfirmed**. Follow-up for Day 4: rerun `python -m qaseed.evaluate --live --ids S02,S17,S18` after a pause and record the error text; consider a short delay between live scenarios.

### 1.6 Known limitations

- Live run: 3 of 18 scenarios failed with `llm_error` (free-tier limit suspected, unconfirmed); they need a rerun.
- Conditions are limited to enumerated `status` columns. Requests like "customers in AE" are rejected as `unsupported_condition_column`.
- Unstated counts are derived by a simple rule (one child per parent, or one parent per child), written down as assumptions. This may not match what the tester meant.
- No cross-table business rules yet (for example, "a failed payment belongs to an overdue invoice").
- Gemini's JSON response mode guarantees JSON, not QASeed's schema. Pydantic enforces the schema after the reply.
- Free-tier rate limits apply per project, and Google may use free-tier prompts to improve its products (QASeed sends only synthetic scenarios and the sample schema).
- English only; one sample schema; no records are generated.

---

1. Browser: the Day 3 pytest-html report (`reports/day3/…html`) summary showing 136 passed (`day3_test_report_20261109-181137.html`).
2. Terminal: `python -m qaseed.evaluate` mock table and summary (label it "mock").
3. Terminal: `python -m qaseed.llm_ping --list-models` and `python -m qaseed.llm_ping` (optional; the planner and live evaluation screenshots already prove live connectivity).
4. After a key is configured: `python -m qaseed.planner "<flagship scenario>"` showing the JSON plan, plus one rejected example (e.g. S16).
5. `git check-ignore -v _project_references/ .env` output.
6. GitHub PR page after you commit and push.

---

## 3. Innovation and value proposition

QASeed is designed to turn plain-English test scenarios into structured requirements for related synthetic records and later verify that the requested test conditions are satisfied.

What Day 3 actually delivers is the first half of that. Every request becomes an explicit, checkable plan with counts per table, status conditions, parent-child dependencies, written assumptions and a list of checks such as "exactly 8 invoices have status = 'overdue'". The planner also refuses impossible or out-of-schema requests with a reason instead of guessing. Data generation and verification of the generated data are **not** implemented yet; they are planned for Day 4.

---

## 4. Live evaluation procedure (for the owner, after configuring credentials)

These steps use your Gemini free-tier quota. Run them yourself.

1. Put `GEMINI_API_KEY=<your key>` in the local `.env` only. `GEMINI_MODEL=gemini-3.5-flash-lite` is already set.
2. Check: `git check-ignore .env` prints `.env`, and `git status` does not list `.env`.
3. `python -m qaseed.llm_ping --list-models` (no generation). Confirm `gemini-3.5-flash-lite` is listed.
4. `python -m qaseed.llm_ping` (1 request). Expect `status=PASS`.
5. `python -m qaseed.planner "Generate 50 customers, including 10 canceled subscriptions, 8 overdue invoices, and 5 failed payments."` (1–2 requests). Record status, latency and usage.
6. `python -m qaseed.evaluate --live --ids S01,S08,S14` (3–6 requests), giving at least 3 timed requests.
7. Optional full set: `python -m qaseed.evaluate --live` (18–36 requests; may hit free-tier rate limits). For S17, live mode checks a normal valid plan, because the malformed-output case only applies to mocks.
8. Record the LIVE results in this document as a separate table. Do not merge them with the MOCK table.

---

## 5. Early market-validation preparation

Interview questions for QA engineers, SDETs and developers (5–7 minutes). No interviews have been conducted yet.

1. Think of the last time preparing test data slowed you down. What was the scenario, and what did you have to create?
2. Which data situations are hardest to set up? Examples: a canceled subscription with an overdue invoice and a failed payment, all linked together.
3. Roughly how long does it take you to prepare a set of connected records for one scenario, including fixing broken links or statuses?
4. What do you use today (SQL scripts, fixtures, factories, Faker, copies of production, a vendor tool), and what is the main workaround you rely on?
5. If a tool turned a plain-English scenario into linked test data and showed proof that the requested conditions exist, would you try it on your own project? What would make you trust or reject it?

Record answers anonymously. Ask for consent before quoting anyone. Do not report adoption or market fit from these conversations.
