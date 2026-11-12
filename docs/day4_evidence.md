# Day 4 — Optimizing Integration & Application Evaluation

## Checklist

- [x] **Evaluation approach defined (metrics + test set)** — 30 scenarios, 10 metrics, expected vs actual saved per scenario.
- [x] **Error handling + retries added (API)** — typed API errors, bounded retries with backoff, fail fast on non-recoverable errors.
- [x] **Prompt/model iteration based on failures** — baseline measured first; fixes and prompt P04 re-measured on the same set and live.
- [x] **Latency/cost notes captured** — local and live latency, token use; cost labelled *not measured*.
- [x] **Safety/guardrails considered (content, PII, injection)** — unsafe input refused before any API call; model output capped and redacted.

## Artifacts / notes

| Field | Entry |
|---|---|
| **Evaluation dataset link / location** | [`tests/fixtures/day3_scenarios.json`](../tests/fixtures/day3_scenarios.json) — 18 core planning scenarios (reused from Day 3) · [`tests/fixtures/day4_eval_scenarios.json`](../tests/fixtures/day4_eval_scenarios.json) — 12 new robustness/safety scenarios · results in [`reports/day4/`](../reports/day4/index.md) |
| **Metrics used** | Outcome accuracy · structured-plan validity · schema correctness · count accuracy · condition coverage · rejection accuracy · false acceptances · false rejections · unexpected failure rate · API request budget · latency (median/p95/max) · tokens |
| **Top failure modes found** | 1. Rate limits, timeouts and network errors failed immediately (no retry). 2. Prompt injection, personal data, an API key and a SQL statement were accepted and sent to the model (4 false acceptances). 3. A correct JSON plan wrapped in extra text was refused. 4. Provider safety blocks were retried and misreported. |
| **Fixes applied** | Bounded retries (2 s, 4 s, server hint honoured, max 4 requests per scenario); no retry for auth, permission, model or safety-block errors · safety check before the API call (injection, SQL, PII, secrets) · JSON extracted from surrounding text, then strictly validated · model notes capped and redacted · prompt P04 marks the scenario as untrusted data. **Result: 21/30 → 30/30 on the same set; live 22/22.** |

## Decisions made today (why)

- **Measure before fixing.** The baseline ran on the unchanged Day 3 planner, so the before/after comparison uses the same 30 scenarios.
- **Guardrails run before the AI call.** Personal data and secrets never reach the free-tier provider, and unsafe input costs no quota.
- **Retry only what can succeed.** Busy-service and network errors are retried a bounded number of times; a bad key or a blocked prompt fails at once with a clear message.
- **Keep the strict output contract.** Extra fields, text instead of numbers and SQL inside values are still refused; nothing is silently repaired.

## Blockers / help needed

- No blocking issues. Live calls used the free tier only.
- Cost is not measured (free tier; project billing status not checked).
- The 3 Day 3 live API errors were not reproduced on Day 4; their cause stays unconfirmed.

---

## Supporting results

**Before vs after — same 30 scenarios (mock replies, tests QASeed's own logic)**

| Metric | Before (Day 3 code) | After (Day 4) |
|---|---|---|
| Outcome accuracy | 21/30 | **30/30** |
| False acceptances (unsafe/invalid accepted) | 4/20 | **0/20** |
| False rejections (valid refused) | 3/10 | 0/10 |
| Condition coverage | 12/15 | 15/15 |
| Rejection accuracy | 15/20 | 20/20 |
| Unexpected failures | 3/30 | 0/30 |
| Request-budget violations | 9 | 0 |

**Live run — Gemini, prompt P04 (22 scenarios that can run live)**

| Measure | Result |
|---|---|
| Outcome accuracy | **22/22** (18/18 planning + 4/4 safety); Day 3 live was 15/18 |
| Wrong plans / API failures | 0 / 0 (one transient error recovered by a retry) |
| API requests | 19 (safety cases made 0) |
| Model latency | median 3,506 ms · min 1,109 · max 8,460 |
| Tokens | 21,275 total · about 1,200 per scenario |
| Cost | Not measured |

**Tests:** 136 → **227 passing**, 0 failed, 0 skipped ([final report](../reports/day4/day4_test_report_20261110-173826.html)). QASeed's own processing: median 0.18 ms per scenario.

**Found and fixed while verifying:** a phone number followed by a comma was not detected; a recovered retry did not record its error type; a local file path appeared in captured test output.

**Known limits:** pattern-based guardrails miss plain personal names and non-English or reworded prompt injections; the schema validator still blocks any unknown table, value or SQL in the plan.

## Reproduce

```bash
python -m qaseed.evaluate --set all                 # after: 30/30 (offline)
python scripts/day4_baseline.py --set all           # before: 21/30, Day 3 planner loaded from Git (offline)
python -m qaseed.evaluate --live --set all --delay 5    # live Gemini (about 20 free-tier requests)
python -m pytest -q
```
