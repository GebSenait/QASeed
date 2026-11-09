"""Deterministic validation of an LLM interpretation against the real schema.

No LLM is involved here. The same interpretation and schema always produce
the same plan. This is where unknown tables/columns/values, bad counts and
conflicting counts are rejected, and where unstated counts are derived
(each derivation is written down as an assumption).
"""

from __future__ import annotations

from collections import defaultdict

from qaseed.plan_models import (
    Issue,
    LLMInterpretation,
    PlanMetadata,
    PlannedCondition,
    PlannedTable,
    Relationship,
    ScenarioPlan,
    VerificationCheck,
)

MAX_ROWS_PER_TABLE = 10_000


def validate_interpretation(
    interpretation: LLMInterpretation,
    schema: dict,
    scenario: str,
    source: str,
    metadata: PlanMetadata | None = None,
) -> ScenarioPlan:
    """Check an interpretation against the schema and return a ScenarioPlan."""
    v = _Validation(schema)
    v.read_model_flags(interpretation)
    stated = v.read_tables(interpretation)
    conditions = v.read_conditions(interpretation, stated)

    if not v.errors() and not v.clarifications() and not stated:
        v.clarify("no_tables", "The scenario does not name any records to create.")

    counts: dict[str, int] = {}
    order: list[str] = []
    if not v.errors():
        order = v.generation_order(stated)
        counts = v.resolve_counts(order, stated, conditions)
        v.check_condition_totals(counts, conditions)

    if v.errors():
        status = "rejected"
    elif v.clarifications():
        status = "needs_clarification"
    else:
        status = "valid"

    plan = ScenarioPlan(
        scenario=scenario,
        source=source,
        status=status,
        assumptions=v.assumptions,
        issues=v.issues,
        metadata=metadata or PlanMetadata(),
    )
    if status != "valid":
        return plan

    plan.generation_order = order
    plan.tables = [
        PlannedTable(
            table=name,
            count=counts[name],
            count_source="stated" if stated.get(name) is not None else "derived",
        )
        for name in order
    ]
    plan.conditions = conditions
    plan.relationships = v.relationships(order)
    plan.verification_checks = v.verification_checks(order, counts, conditions, plan.relationships)
    return plan


def valid_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_ROWS_PER_TABLE


class _Validation:
    def __init__(self, schema: dict) -> None:
        self.tables = {table["name"]: table for table in schema["tables"]}
        self.schema_order = [table["name"] for table in schema["tables"]]
        self.issues: list[Issue] = []
        self.assumptions: list[str] = []

    # ---- issue helpers

    def error(self, code: str, message: str) -> None:
        self.issues.append(Issue(severity="error", code=code, message=message))

    def clarify(self, code: str, message: str) -> None:
        self.issues.append(Issue(severity="clarification", code=code, message=message))

    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def clarifications(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "clarification"]

    # ---- reading the interpretation

    def read_model_flags(self, interp: LLMInterpretation) -> None:
        self.assumptions.extend(text.strip() for text in interp.assumptions if text.strip())
        for text in interp.unsupported:
            if text.strip():
                self.error("unsupported_request", f"Not supported: {text.strip()}")
        for text in interp.ambiguities:
            if text.strip():
                self.clarify("ambiguous_request", text.strip())
        if interp.status == "unsupported" and not self.errors():
            self.error("unsupported_request", "The request is outside what QASeed can plan.")
        if interp.status == "needs_clarification" and not self.clarifications():
            self.clarify("ambiguous_request", "The request needs clarification before planning.")

    def read_tables(self, interp: LLMInterpretation) -> dict[str, int | None]:
        stated: dict[str, int | None] = {}
        for request in interp.tables:
            if request.table not in self.tables:
                self.error("unknown_table", f"Table '{request.table}' is not in the schema. Known tables: {', '.join(self.schema_order)}.")
                continue
            if request.count is not None and not valid_count(request.count):
                self.error("invalid_count", f"{request.table}: count {request.count} must be between 1 and {MAX_ROWS_PER_TABLE}.")
                continue
            if request.table in stated:
                previous = stated[request.table]
                if previous is not None and request.count is not None and previous != request.count:
                    self.error("duplicate_table", f"{request.table} is requested with two different counts ({previous} and {request.count}).")
                elif previous is None:
                    stated[request.table] = request.count
                continue
            stated[request.table] = request.count
        return stated

    def read_conditions(self, interp: LLMInterpretation, stated: dict[str, int | None]) -> list[PlannedCondition]:
        conditions: list[PlannedCondition] = []
        seen: dict[tuple[str, str, str], int] = {}
        for cond in interp.conditions:
            table = self.tables.get(cond.table)
            if table is None:
                self.error("unknown_table", f"Condition refers to unknown table '{cond.table}'.")
                continue
            columns = [column["name"] for column in table["columns"]]
            if cond.column not in columns:
                self.error("unknown_column", f"Column '{cond.table}.{cond.column}' is not in the schema. Columns: {', '.join(columns)}.")
                continue
            allowed = table["allowed_values"].get(cond.column)
            if allowed is None:
                supported = ", ".join(f"{cond.table}.{name}" for name in table["allowed_values"]) or "none"
                self.error("unsupported_condition_column", f"Conditions on '{cond.table}.{cond.column}' are not supported yet. Supported: {supported}.")
                continue
            if cond.value not in allowed:
                self.error("invalid_value", f"'{cond.value}' is not a valid {cond.table}.{cond.column}. Allowed: {', '.join(allowed)}.")
                continue
            if not valid_count(cond.count):
                self.error("invalid_count", f"{cond.table}.{cond.column}={cond.value}: count {cond.count} must be between 1 and {MAX_ROWS_PER_TABLE}.")
                continue
            key = (cond.table, cond.column, cond.value)
            if key in seen:
                if seen[key] != cond.count:
                    self.error("count_conflict", f"{cond.table}.{cond.column}={cond.value} is requested as both {seen[key]} and {cond.count}.")
                continue
            seen[key] = cond.count
            conditions.append(PlannedCondition(table=cond.table, column=cond.column, value=cond.value, count=cond.count))
            stated.setdefault(cond.table, None)
        return conditions

    # ---- dependencies and counts

    def parents(self, name: str) -> list[str]:
        return [fk["to_table"] for fk in self.tables[name]["foreign_keys"] if fk["to_table"] != name]

    def generation_order(self, stated: dict[str, int | None]) -> list[str]:
        """Requested tables plus every ancestor, parents before children."""
        required: set[str] = set()
        pending = list(stated)
        while pending:
            name = pending.pop()
            if name not in required:
                required.add(name)
                pending.extend(self.parents(name))
        order: list[str] = []
        remaining = [name for name in self.schema_order if name in required]
        while remaining:
            ready = [name for name in remaining if all(p in order for p in self.parents(name))]
            if not ready:  # cycle; not possible in the sample schema
                raise ValueError("Foreign-key cycle in schema")
            for name in ready:
                order.append(name)
                remaining.remove(name)
        return order

    def resolve_counts(
        self,
        order: list[str],
        stated: dict[str, int | None],
        conditions: list[PlannedCondition],
    ) -> dict[str, int]:
        minimum = _condition_minimum(conditions)
        counts: dict[str, int | None] = {name: stated.get(name) for name in order}
        children = {name: [c for c in order if name in self.parents(c)] for name in order}

        # 1. one child per parent
        for name in order:
            if counts[name] is None:
                known = [p for p in self.parents(name) if counts.get(p) is not None]
                if known:
                    counts[name] = counts[known[0]]
                    self.assumptions.append(f"{name} count not stated; assuming one {_singular(name)} per {_singular(known[0])} ({counts[name]}).")
        # 2. at least the rows the conditions need
        for name in order:
            if counts[name] is None and minimum.get(name):
                counts[name] = minimum[name]
                self.assumptions.append(f"{name} count not stated; using the minimum needed for its conditions ({counts[name]}).")
        # 3. one parent per child
        for name in reversed(order):
            if counts[name] is None:
                known = [c for c in children[name] if counts.get(c) is not None]
                if known:
                    counts[name] = max(counts[c] for c in known)
                    self.assumptions.append(f"{name} count not stated; assuming one {_singular(name)} per {_singular(known[0])} ({counts[name]}).")
        # 4. one child per parent again, for tables reachable only from above
        for name in order:
            if counts[name] is None:
                known = [p for p in self.parents(name) if counts.get(p) is not None]
                if known:
                    counts[name] = counts[known[0]]
                    self.assumptions.append(f"{name} count not stated; assuming one {_singular(name)} per {_singular(known[0])} ({counts[name]}).")

        resolved: dict[str, int] = {}
        for name in order:
            value = counts[name]
            if value is None:
                self.clarify("ambiguous_request", f"How many {name} should be created? The scenario does not say.")
                continue
            if stated.get(name) is None and minimum.get(name, 0) > value:
                self.assumptions.append(f"{name} raised from {value} to {minimum[name]} so the requested conditions fit.")
                value = minimum[name]
            if not valid_count(value):
                self.error("invalid_count", f"{name}: derived count {value} must be between 1 and {MAX_ROWS_PER_TABLE}.")
                continue
            resolved[name] = value
        return resolved

    def check_condition_totals(
        self,
        counts: dict[str, int],
        conditions: list[PlannedCondition],
    ) -> None:
        by_column: dict[tuple[str, str], list[PlannedCondition]] = defaultdict(list)
        for cond in conditions:
            by_column[(cond.table, cond.column)].append(cond)
        for (table, column), group in by_column.items():
            total_rows = counts.get(table)
            if total_rows is None:
                continue
            for cond in group:
                if cond.count > total_rows:
                    self.error("condition_exceeds_count", f"{cond.count} {table} with {column}={cond.value} requested, but only {total_rows} {table} in total.")
            total = sum(cond.count for cond in group)
            if total > total_rows and all(cond.count <= total_rows for cond in group):
                parts = " + ".join(f"{c.count} {c.value}" for c in group)
                self.error("count_conflict", f"{table}.{column}: {parts} = {total}, more than the {total_rows} {table} requested. A row has one {column}.")

    # ---- output helpers

    def relationships(self, order: list[str]) -> list[Relationship]:
        return [
            Relationship(child_table=name, child_column=fk["from_column"], parent_table=fk["to_table"], parent_column=fk["to_column"])
            for name in order
            for fk in self.tables[name]["foreign_keys"]
            if fk["to_table"] in order
        ]

    def verification_checks(
        self,
        order: list[str],
        counts: dict[str, int],
        conditions: list[PlannedCondition],
        relationships: list[Relationship],
    ) -> list[VerificationCheck]:
        checks = [
            VerificationCheck(kind="row_count", table=name, expected=counts[name], description=f"{name} has exactly {counts[name]} rows")
            for name in order
        ]
        checks += [
            VerificationCheck(kind="condition_count", table=c.table, column=c.column, value=c.value, expected=c.count, description=f"exactly {c.count} {c.table} rows have {c.column} = '{c.value}'")
            for c in conditions
        ]
        checks += [
            VerificationCheck(kind="foreign_key", table=r.child_table, column=r.child_column, expected=0, description=f"0 {r.child_table} rows whose {r.child_column} has no matching {r.parent_table}.{r.parent_column}")
            for r in relationships
        ]
        checks += [
            VerificationCheck(kind="unique", table=name, column=column, expected=0, description=f"0 duplicate {name}.{column} values")
            for name in order
            for column in self.tables[name]["unique_columns"]
        ]
        return checks


def _condition_minimum(conditions: list[PlannedCondition]) -> dict[str, int]:
    """Smallest table size that fits every condition on that table."""
    per_column: dict[tuple[str, str], int] = defaultdict(int)
    for cond in conditions:
        per_column[(cond.table, cond.column)] += cond.count
    minimum: dict[str, int] = {}
    for (table, _column), total in per_column.items():
        minimum[table] = max(minimum.get(table, 0), total)
    return minimum


def _singular(name: str) -> str:
    return name[:-1] if name.endswith("s") else name
