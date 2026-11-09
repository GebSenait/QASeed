"""Day 3 scenario planner: plain-English scenario -> validated JSON plan.

Flow: scenario -> one LLM call (prompt P01) -> strict Pydantic parse ->
deterministic schema validation (plan_validator). The LLM only interprets
the request. It does not decide what the schema allows, and its output is
never executed as SQL. No records are generated here.

Usage:
    python -m qaseed.planner "Generate 50 customers, including 10 canceled subscriptions"

This makes one Gemini API request (two if the first reply is malformed) and
needs GEMINI_API_KEY and GEMINI_MODEL in the local .env (free tier of
Google AI Studio). Without them it prints status=SKIPPED and makes no
network call.
"""

from __future__ import annotations

import json
import os
import sys
import time

from pydantic import ValidationError

from qaseed import llm_client
from qaseed.config import load_local_env
from qaseed.llm_client import KEY_NAME, MODEL_NAME
from qaseed.plan_models import Issue, LLMInterpretation, PlanMetadata, ScenarioPlan
from qaseed.plan_validator import MAX_ROWS_PER_TABLE, validate_interpretation
from qaseed.schema import load_schema

# P01 -> P02 (2026-11-09): live run showed P01 made the model ask about
# unstated totals, which QASeed derives itself. P02 says to leave them null.
# P02 -> P03 (2026-11-09): live S11 put unknown tables in "unsupported"
# instead of "tables", so the validator never emitted unknown_table.
PROMPT_ID = "P03"
MAX_SCENARIO_CHARS = 2000
MAX_ATTEMPTS = 2  # one retry, only when the reply is not a valid plan
MAX_OUTPUT_TOKENS = 4096  # Gemini counts any thinking tokens against this limit

SYSTEM_PROMPT = """You are the QASeed scenario planner (prompt {prompt_id}).
Turn a QA tester's plain-English test-data scenario into a JSON plan for this
SQLite schema. Use ONLY these tables, columns and values:

{schema}

Return ONLY a JSON object with exactly these keys:
{{
  "status": "ok" | "needs_clarification" | "unsupported",
  "tables": [{{"table": <table name>, "count": <integer or null>}}],
  "conditions": [{{"table": <table>, "column": <column>, "value": <allowed value>, "count": <integer>}}],
  "assumptions": [<string>],
  "ambiguities": [<string>],
  "unsupported": [<string>]
}}

Rules:
- "tables": every table the scenario mentions. "count" is the total number of
  rows of that table the scenario states, or null if it does not state one.
  Copy numbers exactly as written, even if they look invalid (e.g. 0 or -5).
- Unstated totals are NOT a problem: leave "count" null and do not ask about
  them. QASeed derives missing totals from related tables and records that as
  an assumption. Example: "50 customers, including 10 canceled subscriptions"
  -> customers count 50, subscriptions count null, plus the condition.
- If numbers look impossible or contradict each other, still copy them as
  written. QASeed detects invalid counts and conflicts itself; do not ask.
- "conditions": rows that must have a specific value, e.g. "10 canceled
  subscriptions" -> subscriptions.status = "canceled", count 10. Map
  synonyms to the allowed values (cancelled -> canceled, unpaid past due
  date -> overdue).
- Unknown names: if the scenario names a table, column or value that is not
  in the schema above (e.g. refunds, loyalty_tier, suspended), still put that
  exact name in "tables" or "conditions" with status "ok". Do not rename it,
  drop it, or move it to "unsupported". QASeed rejects those with
  unknown_table / unknown_column / invalid_value.
- "assumptions": interpretation choices you made.
- "ambiguities": use ONLY when you cannot tell which records to create at
  all, e.g. no record type and no number is given ("make some test data").
  Then use status "needs_clarification" and ask at most 2 short questions.
  Never ask about unstated totals, plan names, dates, amounts or other
  details QASeed fills in. If you can fill "tables" or "conditions", status
  is "ok" and "ambiguities" is [].
- "unsupported": ONLY for work outside synthetic test-data planning (copying
  real or production data, other systems, deleting data). Use status
  "unsupported". Never use "unsupported" for an unknown table, column or
  value name; those belong in "tables"/"conditions" as above.
- Never invent tables, columns or values. Never write SQL.
"""


def schema_summary(schema: dict) -> str:
    """Compact, model-readable description of the inspected schema."""
    lines = []
    for table in schema["tables"]:
        columns = ", ".join(column["name"] for column in table["columns"])
        lines.append(f"- {table['name']}({columns})")
        for fk in table["foreign_keys"]:
            lines.append(f"    {fk['from_column']} references {fk['to_table']}.{fk['to_column']}")
        for column, values in table["allowed_values"].items():
            lines.append(f"    {column} values: {', '.join(values)}")
    lines.append(f"Row counts must be between 1 and {MAX_ROWS_PER_TABLE} per table.")
    return "\n".join(lines)


def build_prompt(scenario: str, schema: dict) -> tuple[str, list[dict]]:
    """Return (system instruction, conversation turns) for prompt P01."""
    system = SYSTEM_PROMPT.format(prompt_id=PROMPT_ID, schema=schema_summary(schema))
    return system, [{"role": "user", "text": f"Scenario: {scenario}"}]


def parse_interpretation(text: str) -> LLMInterpretation:
    """Parse the model reply. Raises ValueError with a short reason."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply is not JSON ({exc.msg})") from None
    try:
        return LLMInterpretation.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "root"
        raise ValueError(f"reply does not match the plan contract at {where}: {first['msg']}") from None


def plan_scenario(
    scenario: str,
    env: dict[str, str] | None = None,
    post=None,
    schema: dict | None = None,
    source: str = "llm",
) -> ScenarioPlan:
    """Plan a scenario. post(url, payload, api_key) performs the HTTP call.

    Pass source="mock" when post returns canned replies, so the plan is
    labelled as a mock result and never mistaken for a live model result.
    """
    if env is None:
        load_local_env()
        env = dict(os.environ)
    post = post or llm_client.post_json
    schema = schema or load_schema()
    scenario = " ".join((scenario or "").split())

    if not scenario:
        return _failed(scenario, "empty_scenario", "Enter a test scenario.")
    if len(scenario) > MAX_SCENARIO_CHARS:
        return _failed(scenario, "scenario_too_long", f"Scenario is longer than {MAX_SCENARIO_CHARS} characters.")

    model = llm_client.selected_model(env)
    api_key = llm_client.api_key(env)
    if not api_key or not model:
        return _failed(scenario, "llm_not_configured", f"{KEY_NAME} and {MODEL_NAME} must be set in .env to plan with the LLM.")

    system, turns = build_prompt(scenario, schema)
    url = llm_client.generate_url(model)
    metadata = PlanMetadata(model=model, prompt_id=PROMPT_ID)
    started = time.perf_counter()
    last_problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        metadata.attempts = attempt
        payload = llm_client.build_payload(system, turns, MAX_OUTPUT_TOKENS, json_output=True, env=env)
        try:
            body = post(url, payload, api_key)
        except Exception as exc:  # network, auth, quota, model errors from llm_client
            metadata.latency_ms = _elapsed_ms(started)
            return _failed(scenario, "llm_error", llm_client.public_text(str(exc), api_key), metadata, source)
        metadata.usage = llm_client.usage_text(body)
        reply = llm_client.reply_text(body)
        try:
            if not reply:
                raise ValueError(f"empty reply ({llm_client.empty_reply_reason(body)})")
            interpretation = parse_interpretation(reply)
        except ValueError as exc:
            last_problem = str(exc)
            if llm_client.finish_reason(body) == "MAX_TOKENS":
                last_problem += " (reply was cut off at the output token limit)"
            turns = turns + [
                {"role": "model", "text": reply[:2000] or "(empty)"},
                {"role": "user", "text": f"That reply was invalid: {last_problem}. Return only the JSON object."},
            ]
            continue
        metadata.latency_ms = _elapsed_ms(started)
        return validate_interpretation(interpretation, schema, scenario, source, metadata)

    metadata.latency_ms = _elapsed_ms(started)
    return _failed(scenario, "malformed_llm_output", f"No valid plan after {MAX_ATTEMPTS} attempts: {llm_client.public_text(last_problem, api_key)}", metadata, source)


def canned_reply_post(reply: str | dict):
    """A post() stand-in that returns one fixed model reply. For mock runs and tests only."""
    content = reply if isinstance(reply, str) else json.dumps(reply)

    def post(url, payload, api_key):
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": content}]}, "finishReason": "STOP"}]}

    return post


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('usage: python -m qaseed.planner "<test scenario>"')
        return 2
    plan = plan_scenario(" ".join(args))
    if plan.issue_codes() == ["llm_not_configured"]:
        print("status=SKIPPED")
        print(f"reason={plan.issues[0].message}")
        print("No API call was made. This is not a pass.")
        return 2
    print(plan.model_dump_json(indent=2))
    print(f"status={plan.status} source={plan.source} model={plan.metadata.model} "
          f"latency_ms={plan.metadata.latency_ms} attempts={plan.metadata.attempts} usage={plan.metadata.usage}")
    return 0 if plan.status == "valid" else 1


def _failed(
    scenario: str,
    code: str,
    message: str,
    metadata: PlanMetadata | None = None,
    source: str = "none",
) -> ScenarioPlan:
    return ScenarioPlan(
        scenario=scenario,
        source=source if code in ("llm_error", "malformed_llm_output") else "none",
        status="rejected",
        issues=[Issue(severity="error", code=code, message=message)],
        metadata=metadata or PlanMetadata(),
    )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


if __name__ == "__main__":
    sys.exit(main())
