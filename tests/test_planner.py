"""Day 3 planner tests. All replies are canned (mock); no live API is called."""

import json

import pytest

import qaseed.llm_client as llm_client
import qaseed.planner as planner
from qaseed.evaluate import compare, load_scenarios
from qaseed.llm_client import KEY_NAME, MODEL_NAME
from qaseed.planner import (
    MAX_ATTEMPTS,
    MAX_SCENARIO_CHARS,
    PROMPT_ID,
    canned_reply_post,
    parse_interpretation,
    plan_scenario,
)
from qaseed.schema import load_schema

SECRET = "AIzaSyTEST-not-a-real-key-0123456789abc"
ENV = {KEY_NAME: SECRET, MODEL_NAME: "gemini-3.5-flash-lite"}
GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
SCENARIOS = load_scenarios()
SCHEMA = load_schema()
VALID_REPLY = {
    "status": "ok",
    "tables": [{"table": "customers", "count": 5}, {"table": "subscriptions", "count": None}],
    "conditions": [{"table": "subscriptions", "column": "status", "value": "active", "count": 5}],
    "assumptions": [],
    "ambiguities": [],
    "unsupported": [],
}


def recording_post(*replies):
    """post() stand-in that returns the given replies in order and records payloads."""
    calls = []

    def post(url, payload, api_key):
        calls.append({"url": url, "payload": payload, "api_key": api_key})
        reply = replies[min(len(calls), len(replies)) - 1]
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {
            "candidates": [{"content": {"role": "model", "parts": [{"text": content}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150},
        }

    post.calls = calls
    return post


# ---- scenario set -----------------------------------------------------------


def test_scenario_set_size_and_coverage():
    assert 10 <= len(SCENARIOS) <= 20
    ids = [case["id"] for case in SCENARIOS]
    assert len(ids) == len(set(ids))
    statuses = {case["expected"]["status"] for case in SCENARIOS}
    assert statuses == {"valid", "rejected", "needs_clarification"}
    codes = {code for case in SCENARIOS for code in case["expected"].get("issue_codes", [])}
    for required in ("invalid_count", "unknown_table", "unknown_column", "invalid_value",
                     "ambiguous_request", "condition_exceeds_count", "count_conflict",
                     "malformed_llm_output", "unsupported_request"):
        assert required in codes


@pytest.mark.parametrize("case", SCENARIOS, ids=[case["id"] for case in SCENARIOS])
def test_mock_scenario_matches_expected(case):
    plan = plan_scenario(case["scenario"], env=ENV, post=canned_reply_post(case["mock_reply"]), schema=SCHEMA, source="mock")
    assert compare(plan, case["expected"]) == []
    assert plan.source == "mock"
    if plan.status == "valid":
        assert plan.issues == []
        assert plan.verification_checks
    else:
        assert plan.tables == []
        assert plan.verification_checks == []
        assert plan.issues


@pytest.mark.parametrize("case", [c for c in SCENARIOS if c["expected"]["status"] == "valid"], ids=lambda c: c["id"])
def test_valid_plans_only_reference_schema(case):
    plan = plan_scenario(case["scenario"], env=ENV, post=canned_reply_post(case["mock_reply"]), schema=SCHEMA, source="mock")
    tables = {table["name"]: table for table in SCHEMA["tables"]}
    for planned in plan.tables:
        assert planned.table in tables
        assert 1 <= planned.count <= 10_000
    for cond in plan.conditions:
        assert cond.value in tables[cond.table]["allowed_values"][cond.column]
    for check in plan.verification_checks:
        assert check.table in tables
        if check.column:
            assert check.column in [c["name"] for c in tables[check.table]["columns"]]
    # parents always come before children
    for rel in plan.relationships:
        assert plan.generation_order.index(rel.parent_table) < plan.generation_order.index(rel.child_table)


def test_flagship_plan_contents():
    case = SCENARIOS[0]
    plan = plan_scenario(case["scenario"], env=ENV, post=canned_reply_post(case["mock_reply"]), schema=SCHEMA, source="mock")
    assert plan.generation_order == ["customers", "subscriptions", "invoices", "payments"]
    assert [t.count_source for t in plan.tables] == ["stated", "derived", "derived", "derived"]
    assert any("one subscription per customer" in a for a in plan.assumptions)
    checks = {(c.kind, c.table, c.column, c.value): c.expected for c in plan.verification_checks}
    assert checks[("row_count", "customers", None, None)] == 50
    assert checks[("condition_count", "subscriptions", "status", "canceled")] == 10
    assert checks[("condition_count", "invoices", "status", "overdue")] == 8
    assert checks[("condition_count", "payments", "status", "failed")] == 5
    assert checks[("foreign_key", "payments", "invoice_id", None)] == 0
    assert checks[("unique", "customers", "email", None)] == 0
    assert checks[("unique", "invoices", "invoice_number", None)] == 0
    assert plan.metadata.prompt_id == PROMPT_ID


# ---- LLM call and error handling ----------------------------------------------


def test_missing_key_or_model_makes_no_call():
    for env in ({}, {KEY_NAME: SECRET}, {MODEL_NAME: "gemini-3.5-flash-lite"}, {KEY_NAME: "  ", MODEL_NAME: "m"}):
        post = recording_post(VALID_REPLY)
        plan = plan_scenario("5 customers", env=env, post=post, schema=SCHEMA)
        assert plan.status == "rejected"
        assert plan.issue_codes() == ["llm_not_configured"]
        assert plan.source == "none"
        assert post.calls == []


@pytest.mark.parametrize("scenario,code", [("   ", "empty_scenario"), ("x" * (MAX_SCENARIO_CHARS + 1), "scenario_too_long")])
def test_bad_scenario_input_makes_no_call(scenario, code):
    post = recording_post(VALID_REPLY)
    plan = plan_scenario(scenario, env=ENV, post=post, schema=SCHEMA)
    assert plan.issue_codes() == [code]
    assert post.calls == []


def test_request_payload():
    post = recording_post(VALID_REPLY)
    plan = plan_scenario("5 customers with active subscriptions", env=ENV, post=post, schema=SCHEMA)
    assert plan.status == "valid"
    assert plan.source == "llm"
    assert len(post.calls) == 1
    call = post.calls[0]
    assert call["url"] == GENERATE_URL
    assert call["api_key"] == SECRET
    payload = call["payload"]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["maxOutputTokens"] == planner.MAX_OUTPUT_TOKENS
    assert SECRET not in json.dumps(payload)
    system = payload["systemInstruction"]["parts"][0]["text"]
    assert "subscriptions(" in system
    assert "status values: trialing, active, canceled, past_due" in system
    assert "invoice_id references invoices.invoice_id" in system
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "Scenario: 5 customers with active subscriptions"}]}]
    assert plan.metadata.model == "gemini-3.5-flash-lite"
    assert plan.metadata.usage == "promptTokenCount=100 candidatesTokenCount=50 totalTokenCount=150"
    assert plan.metadata.attempts == 1
    assert plan.metadata.latency_ms is not None


def test_thinking_level_is_sent_only_when_configured():
    post = recording_post(VALID_REPLY)
    plan_scenario("5 customers", env=ENV, post=post, schema=SCHEMA)
    plan_scenario("5 customers", env={**ENV, "GEMINI_THINKING_LEVEL": "minimal"}, post=post, schema=SCHEMA)
    assert "thinkingConfig" not in post.calls[0]["payload"]["generationConfig"]
    assert post.calls[1]["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}


def test_malformed_reply_is_retried_once_then_succeeds():
    post = recording_post("not json at all", VALID_REPLY)
    plan = plan_scenario("5 customers with active subscriptions", env=ENV, post=post, schema=SCHEMA)
    assert plan.status == "valid"
    assert plan.metadata.attempts == 2
    assert len(post.calls) == 2
    retry_contents = post.calls[1]["payload"]["contents"]
    assert [turn["role"] for turn in retry_contents] == ["user", "model", "user"]
    assert "invalid" in retry_contents[-1]["parts"][0]["text"]


def test_malformed_reply_stops_after_bounded_attempts():
    post = recording_post("still not json")
    plan = plan_scenario("5 customers", env=ENV, post=post, schema=SCHEMA)
    assert plan.status == "rejected"
    assert plan.issue_codes() == ["malformed_llm_output"]
    assert len(post.calls) == MAX_ATTEMPTS


def test_api_error_is_reported_without_the_key():
    def post(url, payload, api_key):
        raise RuntimeError(f"authentication failed: bad key {api_key}")

    plan = plan_scenario("5 customers", env=ENV, post=post, schema=SCHEMA)
    assert plan.status == "rejected"
    assert plan.issue_codes() == ["llm_error"]
    assert SECRET not in plan.model_dump_json()
    assert "[redacted]" in plan.issues[0].message


def test_unexpected_response_shape_is_malformed_not_a_crash():
    plan = plan_scenario("5 customers", env=ENV, post=lambda *a: {"candidates": []}, schema=SCHEMA)
    assert plan.issue_codes() == ["malformed_llm_output"]


def test_truncated_reply_is_explained():
    def post(url, payload, api_key):
        return {"candidates": [{"content": {"parts": [{"text": '{"status": "ok", "tab'}]}, "finishReason": "MAX_TOKENS"}]}

    plan = plan_scenario("5 customers", env=ENV, post=post, schema=SCHEMA)
    assert plan.issue_codes() == ["malformed_llm_output"]
    assert "output token limit" in plan.issues[0].message


@pytest.mark.parametrize(
    "reply",
    [
        {**VALID_REPLY, "sql": "DROP TABLE customers"},  # extra key
        {**VALID_REPLY, "tables": [{"table": "customers", "count": "5"}]},  # string count
        {**VALID_REPLY, "tables": [{"table": "customers", "count": True}]},  # bool count
        {**VALID_REPLY, "status": "done"},  # unknown status
        ["not", "an", "object"],
    ],
    ids=["extra_key", "string_count", "bool_count", "bad_status", "array"],
)
def test_contract_violations_are_rejected(reply):
    with pytest.raises(ValueError):
        parse_interpretation(json.dumps(reply))


def test_code_fenced_json_is_accepted():
    text = "```json\n" + json.dumps(VALID_REPLY) + "\n```"
    assert parse_interpretation(text).tables[0].table == "customers"


def test_cli_without_key_is_skipped_and_calls_nothing(monkeypatch, capsys):
    monkeypatch.setattr(planner, "load_local_env", lambda: None)
    monkeypatch.delenv(KEY_NAME, raising=False)
    monkeypatch.delenv(MODEL_NAME, raising=False)

    def explode(*args):
        raise AssertionError("network was called")

    monkeypatch.setattr(llm_client, "post_json", explode)
    code = planner.main(["10 canceled subscriptions"])
    out = capsys.readouterr().out
    assert code == 2
    assert "status=SKIPPED" in out
    assert "not a pass" in out


def test_cli_without_scenario_shows_usage(capsys):
    assert planner.main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_prompt_p02_does_not_ask_about_unstated_totals():
    """Live run 2026-11-09 (P01): the model asked about totals QASeed derives itself (S01)."""
    system, turns = planner.build_prompt("50 customers, including 10 canceled subscriptions", SCHEMA)
    assert f"(prompt {PROMPT_ID})" in system
    assert "leave \"count\" null and do not ask" in system
    assert "Never ask about unstated totals" in system
    assert "how\n  much to create" not in system
    assert turns == [{"role": "user", "text": "Scenario: 50 customers, including 10 canceled subscriptions"}]


def test_prompt_p03_keeps_unknown_names_out_of_unsupported():
    """Live run 2026-11-09 (P02): S11 put 'refunds' in unsupported instead of tables."""
    assert PROMPT_ID == "P03"
    system, _ = planner.build_prompt("Create 50 customers and 5 refunds.", SCHEMA)
    assert "(prompt P03)" in system
    assert 'put that\n  exact name in "tables" or "conditions" with status "ok"' in system
    assert 'Never use "unsupported" for an unknown table, column or\n  value name' in system
    assert 'ONLY for work outside synthetic test-data planning' in system
