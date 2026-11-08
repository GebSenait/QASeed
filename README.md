
# QASeed

QASeed is a developer-first AI test data agent that helps developers and QA engineers create realistic, connected test data from plain-English testing scenarios.

## Current Progress — Day 2

- Python development environment configured.
- Sample database schema created with four related tables: customers, subscriptions, invoices, and payments.
- Basic Streamlit interface for viewing the database schema.
- Automated tests and schema checks added.
- LLM API connectivity check prepared; configuration pending.

**Next:** Connect an LLM API and begin building the test scenario planner.

## Tech Stack

Python · Streamlit · SQLite · SQLAlchemy · Pytest

## Getting Started

Requires Python 3.11 or newer.

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
python -m pytest -q
python -m qaseed.schema
streamlit run app.py
```

To test LLM connectivity after configuring your API credentials:

```bash
python -m qaseed.llm_ping
```

Keep API keys in your local `.env` file. Never commit credentials or customer data to GitHub.

## Project Status

Day 2 foundation complete, subject to local verification. AI planning, synthetic data generation, and validation will be implemented in the following stages.