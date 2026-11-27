# QASeed — Pilot Security Readiness (post-MVP)

**Scope:** safe use by 3–5 invited pilot testers on a private Streamlit Community Cloud app.
**Date:** 2026-11-27 · **Branch:** `test/day8-e2e-hosted` (base commit `db17ec4`; same file tree as `main` at `a0a2e00`) · **Changes:** uncommitted, for review.
**Method:** code review of the complete workflow, offline automated tests with mocked Gemini, no live or billable API calls (the local `.env` has no Gemini key), synthetic data only.

## Decision: CONDITIONAL GO

For a small, supervised, private pilot, provided the four conditions in [Section 8](#8-go-conditions) are met. The main condition: the code's Live AI limits are best-effort and kept only in process memory. The real cost ceiling has to come from Google (a free-tier key, or a project with billing off or capped).

> The hosted app runs whatever branch it deploys from (normally `main`). The fix below **is not live** until it is reviewed, merged and redeployed.

---

## 1. Workflow audited

| Step | Code | Security-relevant behaviour |
|---|---|---|
| Scenario | [app.py](../app.py) Describe step; [qaseed/planner.py](../qaseed/planner.py) `precheck` | Server-side 2,000-character cap (`MAX_SCENARIO_CHARS`, planner.py:51). The browser-side `max_chars` is not trusted. Injection, SQL, PII and secret patterns are refused **before** any API call, and a refused input does not spend Live AI budget. |
| Gemini plan | planner.py `plan_scenario`, [qaseed/llm_client.py](../qaseed/llm_client.py) | At most 4 requests per plan (planner.py:54). 60 s timeout per request (llm_client.py:31). Backoff 2 s, then 4 s, capped at 30 s. Auth, permission, model and safety errors are not retried. The key is sent in a header and redacted from every message. |
| Validation | [qaseed/plan_validator.py](../qaseed/plan_validator.py) | The model only interprets the scenario. Tables, columns and values are checked against the schema, and counts must be 1–10,000 per table (plan_validator.py:24). Model output is never executed as SQL. |
| Python generation | [qaseed/generator.py](../qaseed/generator.py) | Every run gets a **new private in-memory SQLite** database (`sqlite://` + StaticPool, generator.py:87), disposed in `finally`. Values come from Faker or whitelists. Emails use `example.com`. |
| SQLite validation | [qaseed/data_checks.py](../qaseed/data_checks.py) | Independent SQL checks. Identifiers are whitelisted, and values are bound parameters. |
| ZIP/CSV download | [qaseed/export.py](../qaseed/export.py), [qaseed/artifacts.py](../qaseed/artifacts.py) | Export happens only when every check passed. The ZIP is reloaded into a **second fresh in-memory** database and re-checked. The download bytes live in that session's state only. |

Shared, process-wide objects (`st.cache_resource`): the module warm-up thread, the app-wide `DailyCounter`, and (new) `DailyViewerUsage`. None of them holds plans, scenarios or generated data. `DailyViewerUsage` holds only a hashed viewer key and a count.

---

## 2. Findings by severity

No P0 (critical) issue was found.

### P1-1: Live AI "per-session" limit could be lifted by refresh or a new tab (CONFIRMED, FIXED in code; hosted effect NOT VERIFIED)

- **Where:** app.py `live_plan_allowed` (now app.py:389). The counter `live_plans_used` lived only in `st.session_state`.
- **Root cause:** Streamlit session state belongs to one browser connection. A refresh, a new tab or a second browser starts at 0, so one tester could use the whole app-wide daily budget (default 40) alone. That counter was the only server-side limit.
- **Fix (minimal, same architecture):**
  - `pipeline.DailyViewerUsage` (pipeline.py:198): a thread-safe, per-day ledger in process memory, shared by all sessions through `st.cache_resource` (app.py:236).
  - `pipeline.viewer_key()` (pipeline.py:148): identifies the viewer from `st.user.email`, kept only as a SHA-256 hash (24 hex). Returns `""` when the host provides no identity.
  - New per-viewer daily limit `QASEED_LIVE_PLANS_PER_VIEWER_PER_DAY` (default 10, pipeline.py:139), settable from Streamlit secrets.
  - Order of checks: session limit, then viewer limit, then app-wide limit. If the app-wide limit refuses, the viewer's plan is given back, because nothing was sent.
  - Limit refusals are logged with their scope (`scope=session|viewer|app`), never with the email or the hash.
  - When the viewer is known, the sidebar shows **"Live AI plans left today: N"**. Without identity it still shows "…left in this session: N". This caption is how you check identity detection on the hosted app.
- **Residual risk:** this depends on Streamlit Community Cloud passing the signed-in viewer's email to `st.user` for private apps. I could not verify that offline (Streamlit 1.67 fills `st.user` from platform headers). If it doesn't, the app falls back to the old behaviour: session limit plus app-wide limit.

### P1-2: App-wide Live AI budget is not durable, and there is no app-side cost ceiling beyond it (CONFIRMED, NOT FIXABLE reliably in the current setup)

- **Where:** pipeline.py:179 `DailyCounter`, app.py:230.
- **Root cause:** the counter (like the new per-viewer ledger) lives in process memory. It resets when the app reboots, redeploys or wakes from sleep, and at server-local midnight (UTC on Community Cloud). Community Cloud offers no persistent store that two sessions can update atomically, so a durable quota would need an external database. That is out of scope (no hosting or SaaS changes).
- **Actual bound:** about `QASEED_LIVE_PLANS_PER_DAY` × 4 requests per process lifetime per day. Several reboots in one day multiply it.
- **Mitigation (operational, required):** keep the cost ceiling at the provider. Use a Gemini key whose Google Cloud project has **billing disabled (free tier)**, or a hard quota or budget alert. Keep the pilot supervised.

### P2 findings (not fixed: low impact for 3–5 trusted testers)

| ID | Finding | Where | Why P2 |
|---|---|---|---|
| P2-1 | The generation limit (`QASEED_GENERATIONS_PER_SESSION`) is also session-only, so a refresh lifts it. | app.py:918 | CPU only, no API cost. Measured worst case: 40,000 rows in 9.6 s, 0.47 MB ZIP, about 33 MB peak Python memory. |
| P2-2 | Gemini free-tier prompts may be used by Google to improve its products. Scenario text leaves the app. | provider terms | Guardrails block obvious PII and secrets but are pattern-based. Testers must be told not to enter confidential data. |
| P2-3 | A worst-case live plan holds the session for up to about 5 minutes (4 × 60 s timeout + backoff). | planner.py, llm_client.py | Bounded. Affects only that tester's session. |
| P2-4 | A non-`ExportBlocked` exception during export propagates past `run_generation`'s handler. | pipeline.py:323 onward | Unlikely. The UI shows only the exception type (`showErrorDetails = "type"`). No key or data is exposed. |
| P2-5 | `validation_report.json` in the ZIP contains the tester's scenario text verbatim (already through the guardrails). | pipeline.py:379 | Delivered only to the same session. It is shared only if the tester shares the ZIP. |
| P2-6 | The CSV export has no formula escaping. | export.py `table_csv` | Not exploitable today: no user-controlled or model-controlled value reaches a CSV cell (statuses are whitelisted, other values are generated). A regression test now enforces this. Escaping would break the byte-exact reload check, so it is better added only if free-text columns ever appear. |
| P2-7 | Viewer-key hash is unsalted. | pipeline.py:148 | It exists in memory only and is never logged or exported. |

---

## 3. Fixes and changed files

| File | Change |
|---|---|
| `qaseed/pipeline.py` | `DailyViewerUsage`, `viewer_key()`, `viewer_live_limit()`, new secret `QASEED_LIVE_PLANS_PER_VIEWER_PER_DAY` added to the `load_secrets` allow-list |
| `app.py` | `viewer_live_usage()` (cache_resource), `current_viewer()`, per-viewer check in `live_plan_allowed()`, scope-tagged limit logs, "left today" caption |
| `tests/test_pilot_security.py` | **New**: 25 focused regression tests (below) |
| `tests/test_app.py` | One autouse fixture. AppTest gives every session the default `st.user` email `test@example.com`, which would make all existing journeys share one per-viewer budget. The fixture keeps them modelling "no identity", which is the behaviour they were written for. No assertion changed. |
| `docs/pilot_security_readiness.md` | This report |

---

## 4. Test evidence

All runs are offline. Gemini is mocked through `llm_client.post_json`, and the key is a fake `AIzaTEST_…` value. Python 3.12.0, Streamlit 1.67.0, pytest 9.2.0, project `.venv`. Command: `python -m pytest -q -rs`.

| Run | Result | Time |
|---|---|---|
| **Baseline** (before changes) | **471 passed, 10 skipped, 0 failed** | 138 s |
| First full run after the fix | 485 passed, **11 failed**, 10 skipped. All failures were in `test_app.py`, caused by AppTest's shared default `st.user` email (see Section 3), not by the app. | 142 s |
| **Final** | **496 passed, 10 skipped, 0 failed** (471 + 25 new) | 152 s |

The 10 skips are the same before and after: 2 need the Day-3 commit in git history, 5 need Live AI on the local browser server (no key), and 3 are opt-in live Gemini tests (`QASEED_E2E_LIVE=1`).

**The new test catches the bug:** run against the original `app.py`, `test_viewer_live_limit_survives_refresh_and_new_sessions` **fails**. It passes with the fix.

New tests in `tests/test_pilot_security.py`:

| Area | Tests |
|---|---|
| Viewer identity and budget | Email is hashed and never kept. Missing or broken identity gives `""`. Limit parsing and secrets loading. Per-viewer daily budget resets the next day and `release` never goes negative. 16 threads cannot overspend (`DailyViewerUsage`, `DailyCounter`). |
| Refresh and new session | A viewer's limit **survives a new session**: the refused plan makes no API call, and another viewer keeps their own budget. An app-wide refusal does not use up the viewer's plan. **Documented limitation:** with no identity, a refresh does reset the session budget. |
| Session isolation | Two sessions never share plan, result, ZIP bytes or scenario text. Session A is unchanged after B runs. A refreshed session starts empty. New clears the run and the download. A failed generation leaves no result, no download and no internal message. |
| Per-run storage | Each run creates exactly two databases (generation and CSV re-check), both in memory. No file appears in `data/`. Nine **concurrent** generations (3 scenarios × 3 seeds, threads) are byte-identical to sequential runs and all distinct. |
| Export | For all 8 valid ready-made examples × 3 seeds: no CSV cell starts with `= + - @ TAB CR`, ZIP CRCs are intact, and no member name allows path traversal. |

Existing tests that cover the other controls (already in the baseline): server-side input length, guardrail refusals before the API, bounded retries and backoff, auth errors not retried, key never in UI, logs or plans, scenario text never logged, model text shown literally (no HTML or markdown injection), export blocked when checks fail or the reloaded CSV fails.

Repository secret scan: every Google-key-shaped string in tracked files is a fake fixture (`AIzaTEST…`, `AIzaSyOTHER…`). `.env` and `.streamlit/secrets.toml` are gitignored.

---

## 5. Security controls

| # | Control | Status | Evidence |
|---|---|---|---|
| 1 | Unique per-run SQLite storage (in memory, disposed) | **PASS** | code + `test_every_run_uses_its_own_in_memory_database…` |
| 2 | No database or temp files on disk | **PASS** | same test; no `tempfile` use in the app |
| 3 | Concurrent generations isolated | **PASS** | `test_concurrent_generations_…` |
| 4 | Plans, results and downloads cannot cross sessions | **PASS** (automated). Hosted two-browser check reported passed by you, not re-run by me. | `test_two_sessions_never_share…` |
| 5 | Reset (New) / refresh / failed generation leave no stale data | **PASS** | 3 tests |
| 6 | Cached or shared objects hold no user data | **PASS** | code review (3 `cache_resource` objects) |
| 7 | Input length cap (server side) | **PASS** | existing tests |
| 8 | Record caps (1–10,000 per table) | **PASS** | existing validator tests; worst case measured |
| 9 | Gemini timeout and bounded retries (≤ 4 requests) | **PASS** | existing tests |
| 10 | Locally refused input costs no API call or budget | **PASS** | existing tests |
| 11 | Per-session Live AI limit | **PASS**, by design reset by refresh | existing + new tests |
| 12 | Per-viewer Live AI limit survives refresh and new sessions | **PASS locally** (mocked identity) · **NOT VERIFIED hosted** | new tests; manual check M3 |
| 13 | App-wide daily Live AI limit | **PASS within one process** · **FAIL for durability** (resets on reboot) | P1-2 |
| 14 | Provider-side quota / billing ceiling | **NOT VERIFIED** | manual check M1 |
| 15 | API key never in UI, logs, exports or repo | **PASS** | existing tests + repo scan |
| 16 | Scenario text and emails never logged | **PASS** | existing log test; new logs carry scope only |
| 17 | Error details hidden from users | **PASS** | `showErrorDetails = "type"`, safe messages, failed-generation test |
| 18 | Model text cannot inject HTML or markdown | **PASS** | existing test |
| 19 | CSV formula-injection safety | **PASS** (no controllable cells) | new test |
| 20 | ZIP integrity and re-verification | **PASS** | existing + new tests |
| 21 | Private access, invites, revocation | **NOT VERIFIED by me** (reported manual pass) | manual check M2 |
| 22 | XSRF protection on (not disabled in config) | **PASS (config)** · NOT VERIFIED hosted | `.streamlit/config.toml` |

---

## 6. Remaining risks

**Cost**
- The in-app limits reset when the app reboots. The real ceiling is the Google project's billing and quota settings (P1-2).
- If the host provides no viewer identity, one tester can use the whole app-wide daily budget by refreshing.

**Privacy**
- Scenario text goes to Google Gemini. On the free tier it may be used to improve Google's products (P2-2).
- Streamlit Cloud logs (visible to the app's owner and developers) contain event names, counts and timings only.
- The ZIP report contains the tester's own scenario (P2-5).

**Operational**
- Process-memory limits are lost on reboot.
- A worst-case Gemini call blocks one session for up to about 5 minutes.
- The generation limit can be lifted by refresh (CPU only).
- No automated hosted re-test of this fix yet.

---

## 7. Deployment configuration you need to check (manual)

| # | Check | Expected |
|---|---|---|
| M1 | Google AI Studio / Cloud project of the hosted `GEMINI_API_KEY` | Billing **disabled** (free tier), or a hard quota or budget alert. Key restricted to the Generative Language API. Plan to rotate it after the pilot. |
| M2 | Streamlit Cloud → app → Sharing | **Private**. Viewer list contains only the 3–5 invitees. Not public, no "anyone with the link". |
| M3 | Identity detection (after deploying this change) | Sign in as a tester and switch to Live AI. The sidebar should say **"Live AI plans left today: N"**. Use one plan, refresh: N should not reset. If it says "…left in this session", `st.user` has no email, control #12 does not apply, and the pilot depends on the session limit, the app-wide limit and M1. |
| M4 | Streamlit secrets (top-level keys) | `GEMINI_API_KEY`, `GEMINI_MODEL`; suggested pilot values `QASEED_LIVE_PLANS_PER_SESSION = 3`, `QASEED_LIVE_PLANS_PER_VIEWER_PER_DAY = 8`, `QASEED_LIVE_PLANS_PER_DAY = 30`, `QASEED_GENERATIONS_PER_SESSION = 20`; optional `QASEED_DEFAULT_MODE = "offline"`. No `[auth]` section (`st.login` is not used). |
| M5 | Deployed branch and commit | The app redeployed from the reviewed commit that contains this fix. |
| M6 | Who can read the app logs and secrets | Only you (app owner / workspace developers). |

## 8. GO conditions

1. M1 confirmed: a provider-side cost ceiling (free tier or capped billing).
2. M2 confirmed: private app, invitees only.
3. M3 done after deployment. If identity is **not** detected, still GO, but supervised, with `QASEED_LIVE_PLANS_PER_DAY` lowered (for example 20).
4. Testers are told: synthetic scenarios only, no real customer data or confidential details (the text is sent to Google Gemini).

If M1 or M2 cannot be confirmed: **NO-GO** until they are.
