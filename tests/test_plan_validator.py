"""Deterministic plan validation, independent of any LLM."""

from qaseed.plan_models import LLMInterpretation
from qaseed.plan_validator import MAX_ROWS_PER_TABLE, validate_interpretation
from qaseed.schema import load_schema

SCHEMA = load_schema()


def plan_for(**reply):
    data = {"status": "ok", "tables": [], "conditions": [], "assumptions": [], "ambiguities": [], "unsupported": []}
    data.update(reply)
    return validate_interpretation(LLMInterpretation.model_validate(data), SCHEMA, "test", "mock")


def cond(table, value, count, column="status"):
    return {"table": table, "column": column, "value": value, "count": count}


def test_schema_parser_reports_allowed_values_and_unique_columns():
    tables = {t["name"]: t for t in SCHEMA["tables"]}
    assert tables["customers"]["allowed_values"] == {"status": ["active", "closed"]}
    assert tables["subscriptions"]["allowed_values"]["status"] == ["trialing", "active", "canceled", "past_due"]
    assert tables["invoices"]["allowed_values"]["status"] == ["open", "paid", "overdue", "void"]
    assert tables["payments"]["allowed_values"]["status"] == ["pending", "succeeded", "failed"]
    assert tables["customers"]["unique_columns"] == ["email"]
    assert tables["invoices"]["unique_columns"] == ["invoice_number"]
    assert tables["payments"]["unique_columns"] == []


def test_condition_on_non_enumerated_column_is_rejected():
    plan = plan_for(tables=[{"table": "customers", "count": 5}], conditions=[cond("customers", "AE", 5, column="country_code")])
    assert plan.status == "rejected"
    assert plan.issue_codes() == ["unsupported_condition_column"]


def test_same_condition_with_two_counts_conflicts():
    plan = plan_for(tables=[{"table": "invoices", "count": 20}], conditions=[cond("invoices", "overdue", 3), cond("invoices", "overdue", 4)])
    assert plan.issue_codes() == ["count_conflict"]


def test_same_condition_repeated_identically_is_merged():
    plan = plan_for(tables=[{"table": "invoices", "count": 20}], conditions=[cond("invoices", "overdue", 3), cond("invoices", "overdue", 3)])
    assert plan.status == "valid"
    assert len(plan.conditions) == 1


def test_table_with_two_counts_is_rejected():
    plan = plan_for(tables=[{"table": "customers", "count": 5}, {"table": "customers", "count": 6}])
    assert plan.issue_codes() == ["duplicate_table"]


def test_condition_count_must_be_positive():
    plan = plan_for(tables=[{"table": "payments", "count": 5}], conditions=[cond("payments", "failed", 0)])
    assert plan.issue_codes() == ["invalid_count"]


def test_limits_are_inclusive():
    assert plan_for(tables=[{"table": "customers", "count": MAX_ROWS_PER_TABLE}]).status == "valid"
    assert plan_for(tables=[{"table": "customers", "count": 1}]).status == "valid"
    assert plan_for(tables=[{"table": "customers", "count": MAX_ROWS_PER_TABLE + 1}]).status == "rejected"


def test_derived_count_is_raised_to_fit_conditions():
    plan = plan_for(tables=[{"table": "customers", "count": 5}], conditions=[cond("subscriptions", "canceled", 8)])
    assert plan.status == "valid"
    assert plan.counts() == {"customers": 5, "subscriptions": 8}
    assert any("raised from 5 to 8" in a for a in plan.assumptions)


def test_conditions_only_derive_parents():
    plan = plan_for(conditions=[cond("payments", "failed", 5)])
    assert plan.status == "valid"
    assert plan.counts() == {"customers": 5, "subscriptions": 5, "invoices": 5, "payments": 5}
    assert len(plan.assumptions) == 4


def test_table_without_any_count_needs_clarification():
    plan = plan_for(tables=[{"table": "customers", "count": None}])
    assert plan.status == "needs_clarification"
    assert plan.issue_codes() == ["ambiguous_request"]


def test_no_tables_needs_clarification():
    plan = plan_for()
    assert plan.status == "needs_clarification"
    assert plan.issue_codes() == ["no_tables"]


def test_model_ambiguity_blocks_an_otherwise_valid_plan():
    plan = plan_for(tables=[{"table": "customers", "count": 5}], ambiguities=["Which plan names?"])
    assert plan.status == "needs_clarification"
    assert plan.tables == []


def test_errors_take_precedence_over_clarifications():
    plan = plan_for(tables=[{"table": "refunds", "count": 5}], ambiguities=["How many?"])
    assert plan.status == "rejected"


def test_validation_is_deterministic():
    reply = {"tables": [{"table": "customers", "count": 50}], "conditions": [cond("subscriptions", "canceled", 10), cond("payments", "failed", 5)]}
    assert plan_for(**reply).model_dump_json() == plan_for(**reply).model_dump_json()


def test_more_children_than_parents_is_allowed():
    plan = plan_for(tables=[{"table": "customers", "count": 3}, {"table": "subscriptions", "count": 9}])
    assert plan.status == "valid"
    assert plan.counts() == {"customers": 3, "subscriptions": 9}
    assert [t.count_source for t in plan.tables] == ["stated", "stated"]
