# QASeed

**Developer-first AI test data agent.** Describe a test scenario in plain English; QASeed plans it, generates linked synthetic records, and proves the requested conditions exist.

> "Generate 50 customers, including 10 canceled subscriptions, 8 overdue invoices, and 5 failed payments."

A random data generator gives you rows. QASeed gives you the exact business conditions a test needs, with checks that show they are there.

## How it works

1. **Safety check:** personal data, secrets, SQL statements and prompt-injection phrases are refused before any API call.
2. **AI planner:** Google Gemini turns the request into a structured JSON plan (bounded retries on API errors).
3. **Plan validation:** Python checks every table, column, allowed value and count against the real schema.
4. **Generation:** a seeded generator (Faker + SQLAlchemy/SQLite) creates linked rows in dependency order.
5. **Data checks:** independent SQL checks verify row counts, requested conditions, foreign keys, uniqueness and date/status rules.

Sample schema: `customers → subscriptions → invoices → payments`. Synthetic data only.

## Progress

| Day | Focus | Status |
|---|---|---|
| 1 | Idea and scope | ✅ Completed |
| 2 | Environment, sample schema, schema viewer | ✅ Completed |
| 3 | AI planner + schema validation, 18-scenario baseline | ✅ Completed |
| 4 | Evaluation, error handling and retries, guardrails, prompt iteration | ✅ Completed |
| 5 | Data generation + data checks ✅ · export and Streamlit UI | 🟡 In progress |
| 6 | Bounded repair, security review, bug fixes | ⏭️ Next phase |
| 7 | Deployment and hosted smoke tests | ⏭️ Next phase |
| 8 | Final submission and demo | ⏭️ Next phase |

## Results and test reports

| Day | Result | Evidence |
|---|---|---|
| 3 | 136 tests passing; live Gemini 15/18 (3 free-tier API errors, 0 wrong plans) | [evidence](docs/day3_evidence.md) · [test report](reports/day3/index.md) |
| 4 | 227 tests passing; 30-scenario evaluation 21/30 → 30/30 (mock); live Gemini 22/22; 0 unsafe requests accepted | [evidence](docs/day4_evidence.md) · [reports](reports/day4/index.md) |
| 5 | 262 tests passing; 7/7 offline scenarios generated and fully verified (116/116 data checks) | [reports](reports/day5/index.md) |

"Mock" runs use scripted AI replies to test QASeed's own logic; "live" runs call the real model.

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate             # macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
copy .env.example .env             # macOS/Linux: cp .env.example .env
```

Add a free Gemini key (https://aistudio.google.com/apikey) to `.env`: `GEMINI_API_KEY=<your key>` and `GEMINI_MODEL=gemini-3.5-flash-lite`. Offline commands need no key.

## Commands

| Command | What it does |
|---|---|
| `python -m pytest -q` | Run all tests (offline) |
| `python -m qaseed.generator` | Plan an offline scenario, generate linked rows, run the data checks |
| `python -m qaseed.generator --all` | Requested vs generated vs verified for every valid offline scenario |
| `python -m qaseed.evaluate --set all` | 30-scenario evaluation with metrics (offline) |
| `python -m qaseed.planner "<scenario>"` | Plan one scenario with live Gemini |
| `python -m qaseed.generator --live "<scenario>" --db data/demo.db` | Live plan → generate → check, keep the SQLite file |
| `python -m qaseed.llm_ping` | One live connectivity check |
| `python scripts/test_report.py` | Save the day's HTML test report to `reports/` |
| `streamlit run app.py` | Open the schema viewer |

Without a key, live commands print `status=SKIPPED` and make no API call. A skip is not a pass.

## Project layout

```text
qaseed/      planner, validator, guardrails, LLM client, generator, data checks, evaluation
tests/       one test file per module + scenario fixtures
reports/     daily test reports and evaluation results
docs/        daily evidence
data/        sample schema (generated .db files are gitignored)
```

## Tech stack

Python · Google Gemini API · Pydantic · Faker · SQLAlchemy · SQLite · Streamlit · pytest + pytest-html

## Safety

Synthetic data only; no production connections. API keys live only in the local `.env` (gitignored) and are redacted from all output. Generated emails use the reserved `example.com` domain.
