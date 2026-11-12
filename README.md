# QASeed — AI-Powered test data tool

## Table of Contents

- [Overview](#overview)
- [The Problem](#the-problem)
- [How QASeed Works](#how-qaseed-works)
- [Key Capabilities](#key-capabilities)
- [Example Scenario & Results](#example-scenario--results)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Testing & Reliability](#testing--reliability)
- [Current Limitations & Roadmap](#current-limitations--roadmap)
- [Author & License](#author--license)

## Overview

QASeed turns a test scenario such as *"50 customers, including 10 canceled subscriptions, 8 overdue invoices and 5 failed payments"* into linked, synthetic records for a sample SaaS billing schema (`customers → subscriptions → invoices → payments`). The download is offered only after every requested count, relationship and business rule has been checked in the data.

A random data generator gives you rows. QASeed gives you the exact specific business conditions a test needs, with checks that show they are there.

## The Problem

- **Linked records are slow to build.** Creating one fake customer is easy. Creating a customer with a canceled subscription, an overdue invoice and a failed payment, all correctly linked, means writing SQL inserts and fixing inconsistent rows before testing can even start.
- **Specific states matter.** Regression and edge-case tests need exact combinations of statuses and dates, not random values.
- **Production data is not a safe shortcut.** Copying live customer data into test environments creates privacy and compliance risk.

## How QASeed Works

**Describe → Review AI Plan → Generate & Validate → Download CSV**

1. **Describe:** type a scenario, or pick one of 19 ready-made examples.
2. **Review AI Plan:** Gemini returns a structured JSON plan.
3. **Generate & Validate:** a seeded generator creates the rows in dependency order. Independent SQL checks then confirm counts, requested conditions, foreign keys, uniqueness and date/status rules, showing *requested vs actual*.
4. **Download CSV:** a ZIP with one CSV per table plus `validation_report.json`. The ZIP is reloaded into a fresh database and re-checked before the download button appears.

## Key Capabilities

- **AI planning with guardrails**
- **Schema-checked plans**
- **Linked synthetic data**
- **Independent validation**
- **Verified export**
- **Repeatable**
- **Bounded API use**

## Example Scenario & Results

> *Generate 50 customers, including 10 canceled subscriptions, 8 overdue invoices, and 5 failed payments.*

| Requested | Generated | Verified |
|---|---|---|
| customers: 50 | 50 | PASS |
| subscriptions / invoices / payments (derived, one per parent): 50 each | 50 each | PASS |
| subscriptions with status `canceled`: 10 | 10 | PASS |
| invoices with status `overdue`: 8 | 8 | PASS |
| payments with status `failed`: 5 | 5 | PASS |

**Result:** 200 linked rows, **25/25 data checks passed** (counts, conditions, foreign keys, unique emails and invoice numbers, SQLite `foreign_key_check`, date and payment/invoice rules). Full output: [reports/day5/generator_S01_mock.txt](reports/day5/generator_S01_mock.txt).

**Live Gemini evidence:** the payment scenario *"12 payments: 8 succeeded, 4 failed"* was planned live through the app (1 request), giving 48 rows, 24/24 checks and a CSV ZIP that passed 10/10 independent checks ([reports/day5/ui_e2e_payment_live.txt](reports/day5/ui_e2e_payment_live.txt)).

## Tech Stack

Python 3.11+ · Streamlit · Google Gemini API (REST) · Pydantic · Faker · SQLAlchemy · SQLite · pytest · pytest-html · Playwright

## Project Structure

```text
├── app.py            # Streamlit UI (Describe → Review → Generate & Validate → Download)
├── qaseed/           # planner, guardrails, LLM client, validator, generator, data checks, export, pipeline
├── data/schema.sql   # sample schema: customers, subscriptions, invoices, payments
├── tests/            # one test file per module, browser tests, scenario fixtures
├── scripts/          # daily HTML test report, end-to-end UI check, Day 4 baseline replay
├── reports/          # test reports and evaluation results, one folder per day
├── docs/             # daily evidence, plans, testing guide, Day 6 release readiness
├── assets/brand/     # QASeed logo
└── requirements.txt  # single dependency file (app + tests)
```

## Quick Start

Requires Python 3.11+ (3.12 recommended).

```bash
git clone https://github.com/GebSenait/QASeed.git
cd QASeed
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium      # only for the real-browser tests
```

Optional, for Live AI: copy `.env.example` to `.env` and set `GEMINI_API_KEY` (free key: https://aistudio.google.com/apikey) and `GEMINI_MODEL`, e.g. `gemini-3.5-flash-lite`. Without a key, all ready-made examples still work.

```bash
streamlit run app.py --server.address localhost   # http://localhost:8501
python -m pytest -q                               # all tests, offline (no API calls)
```

## Testing & Reliability

- **404 automated tests** (offline, fake keys) cover planning, guardrails, API errors and retries, schema validation, generation, business rules, data relationships, independent checks, CSV export, the Streamlit journeys (AppTest) and 5 real-browser tests (Playwright + Chromium).
- **Evaluation:** 30-scenario set, 21/30 → 30/30 after the Day 4 prompt and guardrail changes (mock replies); live Gemini 22/22. 0 unsafe requests accepted.

| Command | What it does |
|---|---|
| `python -m pytest -q -m "not browser"` | All tests except the browser ones (faster) |
| `python scripts/test_report.py --day 6` | Save a self-contained HTML test report to `reports/day6/` |
| `python scripts/day5_ui_e2e.py` | Click through the app headless and check the downloaded ZIP (offline) |
| `python -m qaseed.evaluate --set all` | 30-scenario evaluation with metrics (offline) |
| `python -m qaseed.generator --all` | Requested vs generated vs verified for every valid example |

Live commands (`--live`, `python -m qaseed.llm_ping`) call Gemini and print `status=SKIPPED` without a key. A skip is not a pass. Evidence: [reports/](reports/README.md) · [testing guide](docs/testing_and_reporting.md) · daily evidence: [Day 3](docs/day3_evidence.md) · [Day 4](docs/day4_evidence.md) · [Day 5](docs/day5_evidence.md) · [Day 6](docs/day6_release_readiness.md)

## Author

**Senait Gebreab** — QA & Test Automation Engineer | Creator of QASeed
