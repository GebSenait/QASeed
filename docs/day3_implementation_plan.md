# Day 3 Implementation Plan — Scenario Planner

## 1. Actual repository state (verified before coding)

| Item          | Observed                                                                                                                                                                                                                                                                                           |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Branch / sync | `main` at `8548c99`, equal to `origin/main` (local refs; no network fetch performed). Working tree clean except untracked `_project_references/`.                                                                                                                                                  |
| Day 3 branch  | `day3/agent-planning` created from `main`. `main` untouched.                                                                                                                                                                                                                                       |
| `.gitignore`  | Covered `.env`, `.env.*` (with `!.env.example`), `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.db`, `*.sqlite3`, `.streamlit/secrets.toml`. `_project_references/` **was missing** — added as the first change and verified with `git check-ignore`. No reference file was ever tracked. |
| Code          | `qaseed/config.py` (paths, optional `.env` load), `qaseed/schema.py` (in-memory SQLite + PRAGMA-based inspection), `qaseed/llm_ping.py` (LLM connectivity ping via `urllib`; Day 2 targeted OpenAI), `app.py` (Streamlit schema viewer), `data/schema.sql` (4 tables).                             |
| Tests         | 26 passing (`test_layout.py`, `test_schema.py`, `test_llm_ping.py`).                                                                                                                                                                                                                               |
| Python / deps | Python 3.12.0 venv; streamlit 1.67.0, pytest 9.2.0, python-dotenv 1.2.4. **Pydantic was not installed.**                                                                                                                                                                                           |
| LLM config    | No runtime API key configured → live connectivity cannot be tested. (Day 3 switched the provider to Google Gemini's free tier; see section 5.)                                                                                                                                                     |
| Not present   | No planner, no generator, no validator, no SQLAlchemy/Faker usage (README lists SQLAlchemy in the stack, but no code uses it yet).                                                                                                                                                                 |

## 2. Confirmed Day 3 requirements

Official DDS Progress Workbook (read locally from the gitignored `_project_references/` folder, read-only) — Day 3 "Building the Brain of Your App" checklist:

1. Core logic implemented (model/prompt/workflow).
2. Data ingestion or API integration expanded beyond hello world.
3. 10–20 test questions/examples drafted ("exam set").
4. First measurable baseline created.
5. README updated with run instructions.

Workbook fields: the "brain" in 1–2 sentences, link to key code, baseline results, what to improve next, decisions, blockers. Section 4 (LLM/model selection and prompt versions) also applies.

Day 1 scope: schema-aware planning that turns "10 canceled subscriptions" into generation rules; visible assumptions; synthetic data only.

## 3. Architecture components relevant to Day 3

Target architecture (diagram inspected visually): User Input → Streamlit Interface → AI Agent Layer (Planner Agent (LLM) → Generation Orchestrator → Validation & Repair Agent, with a retry/repair loop) → Tooling Layer (Schema Parser, Data Generator (Faker + rules), Constraint Validator (SQLAlchemy/Python), Export Service) → Data Layer (sample SaaS schema in SQLite, generated dataset) → Outputs (validated test data, validation report, SQL/CSV). *(Day 5 scope update: the MVP exports CSV only; SQL export is a planned post-MVP enhancement.)* Core flow: Scenario → Plan → Generate → Validate → Repair → Export.

Day 3 builds only:

- **Planner Agent (LLM)** — one runtime LLM call that interprets the scenario into JSON.
- **Schema Parser (tightened)** — adds allowed status values (from `CHECK ... IN (...)`) and UNIQUE columns.
- **Deterministic plan validation** — Python, not an agent. This is *plan* validation, not the Day 4 Constraint Validator of generated data.

Not built on Day 3: Generation Orchestrator, Faker generator, data Constraint Validator, Repair loop, Export Service, Streamlit integration.

## 4. Proposed source files

| File | Change |
|---|---|
| `qaseed/schema.py` | Add `allowed_values` and `unique_columns` per table; add `load_schema()` helper. |
| `qaseed/plan_models.py` (new) | Pydantic models: `LLMInterpretation` (what the LLM must return) and `ScenarioPlan` (validated output). |
| `qaseed/plan_validator.py` (new) | Deterministic validation: schema references, values, counts, conflicts, dependencies, derived counts, verification checks. |
| `qaseed/llm_client.py` (new) | Gemini REST client: request body, `x-goog-api-key` header, model list, error labels, key redaction. |
| `qaseed/planner.py` (new) | Prompt `P01`, one Gemini call through `llm_client`, bounded retry on malformed JSON, CLI. |
| `qaseed/evaluate.py` (new) | Runs the scenario set in `mock` mode (canned LLM replies) or `live` mode (explicit flag). |
| `qaseed/llm_ping.py` | Rewritten for Gemini; adds `--list-models` and `latency_ms`. |
| `tests/fixtures/day3_scenarios.json` (new) | 18 scenarios with mock LLM replies and expected outcomes. |
| `tests/test_planner.py`, `tests/test_plan_validator.py` (new) | Automated tests. |
| `requirements.txt`, `.env.example`, `README.md`, `.gitignore` | Pydantic; documented optional env var; Day 3 run instructions; `_project_references/`. |

`app.py` is intentionally unchanged (UI integration is Day 5).

## 5. LLM configuration and integration approach

- Provider: **Google Gemini, free tier of Google AI Studio** (one provider only). Decision: OpenAI's API needs prepaid credits, and the project goal is a free runtime LLM for Days 3–8.
- SDK: none. Uses stdlib `urllib` against the Gemini REST API (`POST /v1beta/models/{model}:generateContent`), so no new dependency is needed.
- JSON output: `generationConfig.responseMimeType = "application/json"`. Pydantic does the real schema validation.
- Model: `GEMINI_MODEL` from `.env`, default recommendation `gemini-3.5-flash-lite`. Google's model page marks it Stable and free-tier, and its default thinking level is minimal (checked 2026-11-09). `python -m qaseed.llm_ping --list-models` shows the IDs the owner's key can actually call.
- Thinking: not sent by default; optional `GEMINI_THINKING_LEVEL` (minimal/low/medium/high). `maxOutputTokens` is 4096 because thinking tokens count against it.
- Secrets: `GEMINI_API_KEY` only from the local `.env`; sent in the `x-goog-api-key` header, never in the URL; redacted from all output (including `AIza…` key shapes); never logged.
- Errors: missing key/model → SKIPPED (no network call); invalid key, 403 permission, 404 model, 429 free-tier quota, 5xx, network, timeout, blocked prompt, `MAX_TOKENS` cut-off → labelled FAIL/`llm_error`; malformed plan JSON → one bounded retry, then `malformed_llm_output`.

## 6. Planner input/output contract

Input: scenario text (1–2,000 chars) + schema summary generated from `data/schema.sql`.

LLM must return (`LLMInterpretation`, unknown keys forbidden):

```json
{
  "status": "ok | needs_clarification | unsupported",
  "tables": [{"table": "customers", "count": 50}],
  "conditions": [{"table": "subscriptions", "column": "status", "value": "canceled", "count": 10}],
  "assumptions": ["..."],
  "ambiguities": ["..."],
  "unsupported": ["..."]
}
```

`count` may be `null` when the scenario does not state it. Validated output (`ScenarioPlan`): `status` (`valid | needs_clarification | rejected`), `source` (`llm | mock`), `prompt_id`, resolved `tables` (count + whether stated or derived) in generation order, `conditions`, `relationships` (from schema FKs, not from the LLM), `assumptions`, `issues` (severity, code, message), `verification_checks`, and metadata (model, latency, usage, attempts).

## 7. Deterministic validation approach

1. Pydantic parse (strict ints, no extra keys) → else `malformed_llm_output`.
2. Every table and column must exist in the inspected schema → `unknown_table` / `unknown_column`.
3. Day 3 conditions are limited to enumerated columns (the `status` columns) → `unsupported_condition_column`; values must be in the `CHECK` list → `invalid_value`.
4. Counts must be integers in 1..10,000 → `invalid_count`.
5. Condition count > table count → `condition_exceeds_count`; sum of different values on one column > table count → `count_conflict`; same column/value twice with different counts → `count_conflict`.
6. Parent tables of every requested table are added via FKs. Unstated counts are derived and recorded as assumptions: from the parent (one child per parent), else from the child (one parent per child), and never below the condition total.
7. LLM `unsupported` items → rejected; LLM `needs_clarification` or no tables → needs_clarification.
8. Verification checks emitted for Day 4: row counts, condition counts, zero orphan FKs, uniqueness of UNIQUE columns.

## 8. Test scenarios and evaluation criteria

18 scenarios (`tests/fixtures/day3_scenarios.json`): 7 valid (flagship, active, canceled, failed payments, overdue/void, multi-level related, mixed statuses), 3 invalid counts (negative, zero, too large), unsupported table, unsupported column, invalid status value, ambiguous, two conflicts, malformed LLM output, out-of-scope request. Each has an expected status, issue code and (where valid) resolved counts.

Mock mode: canned LLM replies → measures the deterministic pipeline only. Live mode: the same scenarios to the real model → measures model interpretation (expected-status match, count match, latency). Mock results are never reported as model accuracy.

## 9. Dependencies and risks

- New dependency: `pydantic>=2.7,<3` (installed 2.14.3). Gemini needs no extra package.
- Risk: no API key → live planner unverified (blocker carried from Day 2).
- Risk: free-tier rate limits (per-project RPM/RPD, shown in AI Studio) can interrupt live evaluation or the demo → errors are labelled; run small `--ids` batches.
- Risk: Google may use free-tier prompts to improve its products. QASeed sends only the sample schema and synthetic scenarios, never real customer data.
- Risk: derived-count rules (one child per parent) are a simplification; may not match tester intent.
- Risk: conditions only on status columns; cross-table semantic rules (e.g. a failed payment must belong to an overdue invoice) are not expressed yet.

## 10. Known limitations

No data generation, no data validation, no repair loop, no UI integration, no latency baseline for a live model, single schema only, English only.

## 11. Acceptance criteria

- [x] `_project_references/` ignored; no secrets tracked or printed.
- [x] Planner returns a Pydantic-validated plan for the flagship scenario (mock).
- [x] Unknown tables/columns/values and invalid counts are rejected; conflicts and ambiguity flagged.
- [x] 10–20 scenarios with expected outcomes; all Day 2 + Day 3 tests pass (final: 136 passed).
- [x] Missing key → SKIPPED with no network call; live status reported honestly.
- [x] Evidence document lists real commands and outputs (`docs/day3_evidence.md`).
- [x] Live API verified and live planner baseline recorded (Gemini `gemini-3.5-flash-lite`, prompt P03: 15/18 matched, 3 free-tier API errors, 0 wrong plans).

## 12. Explicitly excluded from Day 3

Faker/SQLAlchemy generation, data constraint validator, repair loop, export, Streamlit planner screen, deployment, RAG/vector DB, multiple agents or providers, commits/pushes/PRs.
