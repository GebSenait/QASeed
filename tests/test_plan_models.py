"""Pydantic plan contracts (qaseed/plan_models.py)."""

import pytest
from pydantic import ValidationError

from qaseed.plan_models import (
    PLAN_VERSION,
    Issue,
    LLMInterpretation,
    PlannedTable,
    ScenarioPlan,
)


def test_interpretation_defaults_to_empty_lists():
    interp = LLMInterpretation.model_validate({"status": "ok"})
    assert interp.tables == [] and interp.conditions == []
    assert interp.assumptions == [] and interp.ambiguities == [] and interp.unsupported == []


@pytest.mark.parametrize(
    "data",
    [
        {"status": "ok", "extra": 1},
        {"status": "maybe"},
        {"status": "ok", "tables": [{"table": "customers", "count": 1.5}]},
        {"status": "ok", "tables": [{"table": "", "count": 1}]},
        {"status": "ok", "conditions": [{"table": "invoices", "column": "status", "value": "overdue"}]},
    ],
    ids=["extra_key", "bad_status", "float_count", "empty_table", "condition_without_count"],
)
def test_interpretation_rejects_contract_violations(data):
    with pytest.raises(ValidationError):
        LLMInterpretation.model_validate(data)


def test_scenario_plan_helpers_and_round_trip():
    plan = ScenarioPlan(
        scenario="s",
        source="mock",
        status="valid",
        tables=[PlannedTable(table="customers", count=5, count_source="stated")],
        issues=[Issue(severity="error", code="invalid_count", message="m")],
    )
    assert plan.plan_version == PLAN_VERSION
    assert plan.counts() == {"customers": 5}
    assert plan.issue_codes() == ["invalid_count"]
    assert ScenarioPlan.model_validate_json(plan.model_dump_json()) == plan


def test_scenario_plan_rejects_unknown_status_and_fields():
    with pytest.raises(ValidationError):
        ScenarioPlan(scenario="s", source="mock", status="done")
    with pytest.raises(ValidationError):
        ScenarioPlan(scenario="s", source="mock", status="valid", sql="DROP TABLE x")
