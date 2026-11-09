# QASeed

**Developer-first AI test data agent.** Describe a test scenario in plain English and QASeed plans connected, schema-valid synthetic records for it.

> "Generate 50 customers, including 10 canceled subscriptions, 8 overdue invoices, and 5 failed payments."

A random data generator gives you rows. QASeed is built to give you the exact business conditions a test needs, and to prove they exist.

## How it works (Day 3)

```text
Scenario → Gemini LLM (prompt P03) → JSON plan → Pydantic + schema checks → validated plan
```

- **LLM interprets** the scenario: tables, counts, status conditions, assumptions.
- **Python decides** what is allowed. Unknown tables, columns and values are rejected, as are invalid counts and conflicting numbers. Vague requests are flagged for clarification.
- **Plan output:** generation order, relationships, derived counts (recorded as assumptions), and the checks a generated dataset must pass.

Sample schema: `customers → subscriptions → invoices → payments` (SQLite, synthetic data only).

## Status

| Day | Delivered |
|---|---|
| 2 | Project setup, sample schema, Streamlit schema viewer, tests |
| 3 | Scenario planner on **Google Gemini free tier** (`gemini-3.5-flash-lite`), 18-scenario evaluation set, daily test reports |
| 4 (next) | Generate linked records from a validated plan and verify its checks |

**Day 3 results:** 136 automated tests passing · mock evaluation 18/18 · live evaluation 15/18 with 0 wrong plans (3 free-tier API errors). Details: [docs/day3_evidence.md](docs/day3_evidence.md).

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
Copy-Item .env.example .env         # macOS/Linux: cp .env.example .env
```

Then add your free Gemini key (from https://aistudio.google.com/apikey) to `.env`:

```text
GEMINI_API_KEY=<your key>
GEMINI_MODEL=gemini-3.5-flash-lite
```

## Commands

| Command | What it does |
|---|---|
| `python -m qaseed.llm_ping --list-models` | List models your key can use (no generation) |
| `python -m qaseed.llm_ping` | One live connectivity check |
| `python -m qaseed.planner "<scenario>"` | Plan one scenario with the live LLM |
| `python -m qaseed.evaluate` | Run the 18 scenarios with mock replies (offline) |
| `python -m qaseed.evaluate --live [--ids S01,S08]` | Run scenarios against live Gemini |
| `python -m pytest -q` | Run all tests |
| `python scripts/test_report.py` | Save an HTML test report to `reports/day<N>/` |
| `streamlit run app.py` | Open the schema viewer |

Without a key, live commands print `status=SKIPPED` and make no API call; a skip is not a pass. HTTP 429 means the free-tier rate limit was reached: wait a minute and retry.

## Tech stack

Python · Google Gemini API · Pydantic · SQLite · Streamlit · pytest + pytest-html (Faker and SQLAlchemy planned for Day 4)

## Docs

- [Day 3 implementation plan](docs/day3_implementation_plan.md)
- [Day 3 evidence and results](docs/day3_evidence.md)
- [Testing and reporting rules](docs/testing_and_reporting.md)

## Safety

Synthetic data only. No production database connections. Keys live only in your local `.env`, which Git ignores; never commit credentials or customer data.
