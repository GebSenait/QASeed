"""Typed contracts for the Day 3 scenario planner.

LLMInterpretation is what the model must return. It is parsed strictly
(no extra keys, real integers) but is NOT trusted: table names, columns,
values and counts are checked against the schema in plan_validator.

ScenarioPlan is the validated, reviewable plan QASeed produces. It is data
only. Nothing in it is executed as SQL.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

PLAN_VERSION = "day3-plan-v1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- What the LLM returns -------------------------------------------------


class TableRequest(_Strict):
    table: str = Field(min_length=1)
    count: StrictInt | None = None


class ConditionRequest(_Strict):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    value: str = Field(min_length=1)
    count: StrictInt


class LLMInterpretation(_Strict):
    status: Literal["ok", "needs_clarification", "unsupported"]
    tables: list[TableRequest] = Field(default_factory=list)
    conditions: list[ConditionRequest] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)


# ---- What QASeed produces ---------------------------------------------------

IssueCode = Literal[
    "malformed_llm_output",
    "llm_error",
    "llm_not_configured",
    "empty_scenario",
    "scenario_too_long",
    "unknown_table",
    "unknown_column",
    "unsupported_condition_column",
    "invalid_value",
    "invalid_count",
    "condition_exceeds_count",
    "count_conflict",
    "duplicate_table",
    "ambiguous_request",
    "unsupported_request",
    "no_tables",
    "unsafe_input",
    "pii_detected",
    "secret_detected",
]


class Issue(_Strict):
    severity: Literal["error", "clarification"]
    code: IssueCode
    message: str


class PlannedTable(_Strict):
    table: str
    count: int
    count_source: Literal["stated", "derived"]


class PlannedCondition(_Strict):
    table: str
    column: str
    value: str
    count: int


class Relationship(_Strict):
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str


class VerificationCheck(_Strict):
    kind: Literal["row_count", "condition_count", "foreign_key", "unique"]
    table: str
    column: str | None = None
    value: str | None = None
    expected: int
    description: str


class PlanMetadata(_Strict):
    model: str = ""
    prompt_id: str = ""
    attempts: int = 0  # model replies received
    requests: int = 0  # API requests sent (includes transport retries)
    transport_retries: int = 0
    retried_errors: list[str] = Field(default_factory=list)  # kind of each error that triggered a retry, kept even if the retry succeeded
    error_kind: str = ""  # kind of the error that ended the run, e.g. rate_limit, auth, blocked; "" if a reply arrived
    latency_ms: int | None = None
    usage: str = ""


class ScenarioPlan(_Strict):
    plan_version: str = PLAN_VERSION
    scenario: str
    source: Literal["llm", "mock", "none"]
    status: Literal["valid", "needs_clarification", "rejected"]
    tables: list[PlannedTable] = Field(default_factory=list)
    generation_order: list[str] = Field(default_factory=list)
    conditions: list[PlannedCondition] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    verification_checks: list[VerificationCheck] = Field(default_factory=list)
    metadata: PlanMetadata = Field(default_factory=PlanMetadata)

    def issue_codes(self) -> list[str]:
        return [issue.code for issue in self.issues]

    def counts(self) -> dict[str, int]:
        return {table.table: table.count for table in self.tables}
