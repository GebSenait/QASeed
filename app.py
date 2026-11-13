"""QASeed Streamlit app: landing page, then Describe -> Review -> Verify -> Download.

UI only. Planning, generation, checks, export and logging live in
qaseed.pipeline and the modules it reuses. Results are kept in session state,
so Streamlit reruns and moving between steps never repeat an API call, a
generation or a verification by themselves.
"""

import base64
import html
import importlib
import logging
import re
import threading
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from qaseed.config import load_local_env

# The workspace's heavy modules (pipeline -> SQLAlchemy and Faker; pandas/pyarrow for tables) are imported below the
# landing page, so a first visit shows the landing page without waiting for them. A background warm-up loads them
# while the landing page is read, so Get started does not wait either.
load_local_env()
log = logging.getLogger("qaseed")  # the logger pipeline.configure_logging() sets up once the workspace opens

# Browser-tab icon: the same sprout mark as the header (tracked in the repo). The app still works without it.
LOGO = Path(__file__).resolve().parent / "assets" / "brand" / "qaseed_mark.png"
HAS_LOGO = LOGO.is_file()

st.set_page_config(page_title="QASeed", page_icon=str(LOGO) if HAS_LOGO else "🌱", layout="centered")

WARM_UP_MODULES = ("qaseed.pipeline", "qaseed.artifacts", "qaseed.schema", "qaseed.planner", "pandas", "pyarrow")


@st.cache_resource(show_spinner=False)
def warm_up_workspace() -> threading.Thread:
    """Once per server process: import the workspace's modules in the background while the landing page is shown."""
    def work() -> None:
        for name in WARM_UP_MODULES:
            try:
                importlib.import_module(name)
            except Exception:  # a failed warm-up only means the normal import happens when the workspace opens
                pass

    thread = threading.Thread(target=work, name="qaseed-warm-up", daemon=True)
    thread.start()
    return thread

LIVE, OFFLINE = "live", "offline"
STEPS = ["Describe", "Review", "Verify", "Download"]
PREVIEW_ROWS = 100  # rows shown in a CSV preview; the downloaded file always has every row
EXAMPLE_PURPOSE = {  # short label for each ready-made example
    "valid": "✅ Valid",
    "rejected": "⛔ Invalid",
    "needs_clarification": "❓ Incomplete",
}
# One short, general line per plan issue. Full technical detail stays in the plan's issues (and the app log).
ISSUE_LINE = {
    "invalid_count": "Bad request: row counts must be between 1 and 10,000.",
    "unknown_table": "Bad request: unknown record type.",
    "unknown_column": "Bad request: unknown field.",
    "unsupported_condition_column": "Bad request: this field can't be used as a condition.",
    "invalid_value": "Bad request: status value not allowed.",
    "condition_exceeds_count": "Bad request: more matching rows than rows in total.",
    "count_conflict": "Bad request: status counts exceed the total.",
    "duplicate_table": "Bad request: a record type is listed twice.",
    "no_tables": "Bad request: no records requested.",
    "unsupported_request": "Bad request: only synthetic test data can be generated.",
    "unsafe_input": "Blocked: instructions or SQL are not allowed.",
    "pii_detected": "Blocked: personal data is not allowed.",
    "secret_detected": "Blocked: keys or passwords are not allowed.",
    "empty_scenario": "Enter a scenario.",
    "scenario_too_long": "Scenario too long: keep it under 2,000 characters.",
    "malformed_llm_output": "AI reply unusable: try again.",
    "ambiguous_request": "More detail needed: which records, and how many?",
}
LLM_ERROR_LINE = {  # API failures by kind (llm_client error kinds)
    "rate_limit": "AI service busy: try again in a minute.",
    "auth": "AI service rejected the API key.",
    "permission": "AI service rejected the API key.",
    "invalid_model": "AI model not available.",
    "bad_request": "Bad request.",
    "blocked": "Request blocked by the AI provider.",
}
GENERATION_ERROR_LINE = {  # pipeline.PipelineError categories
    "generation_invalid": "Bad request: these counts can't all be generated consistently.",
    "plan_not_valid": "Plan not valid.",
}
# Describe-step examples: (label, built-in example id). Each one has a recorded AI reply, so it also plans in
# Ready-made examples mode (the hosted default) without an API call, and its text stays editable.
QUICK_EXAMPLES = [("Payments", "D5-01"), ("Subscriptions", "S03"), ("Invoices", "S05")]

SPROUT = ('<svg viewBox="0 0 48 48" aria-hidden="true">'
          '<path d="M23 39V21" stroke="#1769AA" stroke-width="3" stroke-linecap="round" fill="none"/>'
          '<path d="M23 28C12 28 9 20 9 12c10 0 14 6 14 16Z" fill="#24834B"/>'
          '<path d="M23 23c0-10 6-15 16-15 0 10-6 15-16 15Z" fill="#1769AA"/>'
          '<path d="M15 39h16" stroke="#1769AA" stroke-width="3" stroke-linecap="round"/></svg>')
_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{}</svg>'
OVERVIEW = [  # landing "How it works": (step, icon paths, description)
    ("Describe", '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
                 '<path d="M9 13h6M9 17h6"/>', "Set the scenario"),
    ("Review", '<path d="M10 6h10M10 12h10M10 18h10"/><path d="M3.5 6l1.5 1.5L7.5 5M3.5 12l1.5 1.5L7.5 11'
               'M3.5 18l1.5 1.5L7.5 17"/>', "Confirm the plan"),
    ("Verify", '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
     "Inspect SQL checks"),
    ("Download", '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>', "Get the outputs"),
]

STYLE = """
<style>
:root {
  --qs-navy: #102A43; --qs-blue: #1769AA; --qs-soft: #EAF2F8; --qs-green: #24834B;
  --qs-canvas: #F7F9FC; --qs-card: #FFFFFF; --qs-border: #DCE4ED; --qs-muted: #526579;
  --qs-success: #18794E; --qs-danger: #B42318; --qs-warning: #9A6700;
}
/* Content starts below Streamlit's top bar (3.75rem): on Streamlit Community Cloud the bar is opaque. */
.block-container {max-width: 880px; padding-top: 4rem; padding-bottom: 2rem;}
[data-testid="stHeading"] h3 {font-size: 1.4rem; font-weight: 700; color: var(--qs-navy); padding: 0 0 0.2rem;}
button:focus-visible, textarea:focus-visible {outline: 3px solid #8FC3EB !important; outline-offset: 2px;}
/* Reruns: keep the current screen crisp instead of fading it out while the next one loads */
[data-stale="true"] {opacity: 1 !important; transition: none !important;}

/* header: navy bar, sprout mark, wordmark, Home */
.st-key-qs_header {background: var(--qs-navy); border-radius: 14px; padding: 0.7rem 1.1rem; margin-bottom: 0.4rem;}
.st-key-qs_header [data-testid="stMarkdownContainer"] {margin-bottom: 0;}  /* keep the bar's full height */
.qs-brand {display: flex; align-items: center; gap: 0.6rem; margin: 0;}
.qs-brand svg {width: 34px; height: 34px; background: #E7F2F1; border-radius: 9px; padding: 4px; display: block;}
.qs-name {font-size: 1.3rem; font-weight: 700; color: #FFFFFF; letter-spacing: -0.01em; line-height: 1;}
.st-key-home_btn button {background: transparent; color: #FFFFFF; border: 1px solid rgba(255,255,255,0.45);}
.st-key-home_btn button:hover {background: rgba(255,255,255,0.12); color: #FFFFFF; border-color: #FFFFFF;}

/* cards */
.st-key-qs_card, .st-key-qs_stepper {background: var(--qs-card); border-radius: 16px;}
.qs-small {color: var(--qs-muted); font-size: 0.8rem; margin: 0;}
.qs-label {font-weight: 600; color: var(--qs-navy); margin: 0;}

/* landing */
.qs-h1 {font-size: 2rem; line-height: 1.2; font-weight: 700; color: var(--qs-navy); margin: 0.2rem 0 0.8rem; padding: 0;}
.qs-lead {color: var(--qs-muted); font-size: 1rem; line-height: 1.6; margin: 0 0 0.4rem;}
.qs-overview {display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 1.1rem;}  /* stay inside the card */
.qs-overview .item {padding: 14px 6px; background: var(--qs-canvas); border: 1px solid var(--qs-border);
                    border-radius: 10px; text-align: center; color: var(--qs-navy);}
.qs-overview .icon {display: grid; place-items: center; width: 40px; height: 40px; margin: 0 auto 8px;
                    border-radius: 50%; background: var(--qs-soft); color: var(--qs-blue);}
.qs-overview .icon svg {width: 22px; height: 22px;}
.qs-overview strong {display: block; font-size: 0.95rem;}
.qs-overview span {color: var(--qs-muted); font-size: 0.8rem;}
.qs-footer {text-align: center; font-weight: 600; color: var(--qs-navy); margin: 1.4rem 0 0.4rem;}

/* workspace stepper: number or mark + name; current step highlighted */
[class*="st-key-nav_"] button {background: var(--qs-canvas); border: 1px solid var(--qs-border); color: var(--qs-navy);}
[class*="st-key-nav_"][class*="_now"] button {background: var(--qs-soft); border-color: var(--qs-blue); font-weight: 700;}
[class*="st-key-nav_"][class*="_done"] button {color: var(--qs-success);}
[class*="st-key-nav_"][class*="_blocked"] button {color: var(--qs-danger);}

/* Describe: examples (selected = soft blue, 2px blue border, medium-weight navy label) and Clear */
[class*="st-key-ex_wrap_"] button {background: #FFFFFF; border: 1px solid var(--qs-border); color: var(--qs-navy);}
[class*="st-key-ex_wrap_"][class*="_on"] button {background: var(--qs-soft); border: 2px solid var(--qs-blue); font-weight: 600;}
[class*="st-key-ex_wrap_"][class*="_on"] button [data-testid="stIconMaterial"] {color: var(--qs-blue);}
.st-key-scenario [data-baseweb="textarea"], .st-key-scenario textarea {background: #FFFFFF;}
.st-key-clear_btn button {min-height: 0; padding: 0.15rem 0.6rem; font-size: 0.82rem; color: var(--qs-muted);}

/* Review and Verify */
.qs-info {background: var(--qs-canvas); padding: 0.7rem 0.8rem; border-radius: 9px; color: var(--qs-navy); margin: 0 0 0.6rem;}
.qs-rule {padding: 0.45rem 0; border-bottom: 1px solid var(--qs-border); color: var(--qs-navy); margin: 0;}
.qs-rule::before {content: "✓"; color: var(--qs-green); margin-right: 0.55rem;}
.qs-h {font-size: 0.95rem; font-weight: 700; color: var(--qs-navy); margin: 0.6rem 0 0.35rem;}
.qs-info p, .qs-info ul {margin: 0;}
.qs-info ul {padding-left: 1.1rem;}
.qs-info li {margin: 0.15rem 0;}
.qs-chain {font-weight: 600; margin-bottom: 0.35rem !important;}
.qs-muted {color: var(--qs-muted); font-size: 0.85rem;}
.qs-ev {padding: 0.5rem 0; border-bottom: 1px solid var(--qs-border); color: var(--qs-navy); margin: 0;}
.qs-ev:last-child {border-bottom: 0;}
.qs-ev::before {content: "✓"; color: var(--qs-success); font-weight: 700; margin-right: 0.55rem;}
.qs-ev.bad::before {content: "✕"; color: var(--qs-danger);}
.qs-ev .qs-muted {display: block; margin-left: 1.25rem;}
.qs-badge {font-size: 0.8rem; font-weight: 700; padding: 0.2rem 0.6rem; border-radius: 999px;}
.qs-badge.pass {color: var(--qs-success); background: #E8F4ED;}
.qs-badge.fail {color: var(--qs-danger); background: #FDEDEB;}
[data-testid="stMetric"] {background: var(--qs-canvas); border: 1px solid var(--qs-border); border-radius: 10px; padding: 0.6rem 0.8rem;}

/* Download: the verified ZIP. Preview is view-only: no table toolbar (it has its own CSV download) */
[role="dialog"] [data-testid="stElementToolbar"] {display: none;}
.qs-file {margin: 0; font-weight: 600; color: var(--qs-navy); overflow-wrap: anywhere;}

@media (max-width: 640px) {
  .block-container {padding-top: 4.2rem; padding-left: 1rem; padding-right: 1rem;}  /* clear Streamlit's mobile top bar */
  .qs-h1 {font-size: 1.6rem;}
  .qs-overview {gap: 5px;}
  .qs-overview .item {padding: 9px 3px;}
  .qs-overview span {font-size: 0.72rem;}
  [class*="st-key-nav_"], [class*="st-key-ex_wrap_"], [class*="st-key-ex_wrap_"] .stElementContainer {min-width: 0;}
  [class*="st-key-nav_"] button, [class*="st-key-ex_wrap_"] button {padding: 0.4rem 0.15rem; font-size: 0.74rem; min-width: 0;}
}
</style>
"""
STATE_DEFAULTS = {
    "scenario": "",
    "example_choice": None,  # index into QUICK_EXAMPLES while its text is in the box unedited
    "plan_outcome": None,  # pipeline.PlanOutcome
    "plan_key": None,  # (cleaned scenario, mode) the plan was made for
    "reviewed": False,  # "I've reviewed the plan." for the current plan
    "gen_result": None,  # pipeline.GenerationResult
    "gen_key": None,  # (plan_key, seed, as_of) the data was generated for
    "gen_error": "",
    "gen_at": None,  # when the current run finished: gives its ZIP a clear, stable name
    "downloaded": False,  # the user downloaded a file of the current result
    "notice": "",
    "step": 0,  # workspace step shown: 0 Describe, 1 Review, 2 Verify, 3 Download
}
for name, value in STATE_DEFAULTS.items():
    st.session_state.setdefault(name, value)
st.session_state.setdefault("page", "landing")  # landing | workspace
st.session_state.setdefault("workspace_started", False)  # the visitor opened the workspace in this session
st.session_state.setdefault("as_of", date.today())
# Result of the last real Gemini call in this session (kept across New): None = not verified yet.
st.session_state.setdefault("live_check", None)
# Live AI plans this visitor has started (kept across New, so New cannot lift the limit).
st.session_state.setdefault("live_plans_used", 0)


@st.cache_resource
def daily_live_counter() -> "pipeline.DailyCounter":
    """Live AI plans used today by all visitors of this app (shared across sessions)."""
    return pipeline.DailyCounter()


# ---- state: invalidation, validity, navigation -------------------------------------------


def clear_generation() -> None:
    st.session_state.gen_result = None
    st.session_state.gen_key = None
    st.session_state.gen_error = ""
    st.session_state.gen_at = None
    st.session_state.downloaded = False


def clear_plan() -> None:
    """The scenario changed: its plan, review, data, checks and files no longer apply."""
    st.session_state.plan_outcome = None
    st.session_state.plan_key = None
    st.session_state.reviewed = False
    st.session_state.notice = ""
    clear_generation()


def reset() -> None:
    """New: a deliberate full reset of the workflow (Live AI budget and connection status are kept)."""
    for name, value in STATE_DEFAULTS.items():
        st.session_state[name] = value
    st.session_state.sample_choice = None  # examples dropdown back to "Choose an example…"
    st.session_state.result_filter = "all"
    log.info("event=reset")


def load_scenario(text: str, example: int | None = None) -> None:
    """Put an example into the scenario box and start a fresh run (the previous plan and results no longer apply)."""
    st.session_state.scenario = text
    st.session_state.example_choice = example
    clear_plan()


def sample_text(example_id: str) -> str:
    return next(s["scenario"] for s in pipeline.sample_scenarios() if s["id"] == example_id)


def use_quick_example(number: int) -> None:
    load_scenario(sample_text(QUICK_EXAMPLES[number][1]), number)


def use_sample() -> None:
    """Selecting a ready-made example in the sidebar loads it at once: no second click needed."""
    sample = st.session_state.get("sample_choice")
    if sample:
        quick = next((n for n, (_, sid) in enumerate(QUICK_EXAMPLES) if sid == sample["id"]), None)
        load_scenario(sample["scenario"], quick)


def scenario_edited() -> None:
    """Manual edit: the example is no longer selected and everything planned from the old text is stale."""
    st.session_state.example_choice = None
    clear_plan()


def clear_scenario() -> None:
    st.session_state.scenario = ""
    st.session_state.sample_choice = None
    load_scenario("")


def plan_key() -> tuple[str, str]:
    return clean_text(st.session_state.scenario), st.session_state.mode


def gen_key() -> tuple:
    return st.session_state.plan_key, int(st.session_state.seed), st.session_state.as_of


def plan_ready() -> bool:
    """A valid plan exists for the scenario and mode currently entered."""
    outcome = st.session_state.plan_outcome
    return outcome is not None and st.session_state.plan_key == plan_key() and outcome.plan.status == "valid"


def run_ready() -> bool:
    """Data and SQL checks exist for the current plan, seed and as-of date."""
    return plan_ready() and st.session_state.gen_result is not None and st.session_state.gen_key == gen_key()


def overall_pass(result) -> bool:
    """PASS only when checks ran, every one passed and the exported files passed them again."""
    return bool(result.total_checks) and result.passed and result.passed_count == result.total_checks and result.exportable


def sql_row_total(result) -> int | None:
    """Rows actually in the generated database, as counted by the SQL row-count checks (None if none ran).

    The generator's own row_counts are what it intended to write; this is what the independent checks found.
    """
    counts = [c["actual"] for c in result.checks if c.get("kind") == "row_count"]
    return sum(counts) if counts else None


def max_step() -> int:
    """Furthest step the current data allows: Review needs a plan, Verify and Download need a run."""
    return 3 if run_ready() else 1 if plan_ready() else 0


def go_to(step: int) -> None:
    if 0 <= step <= max_step():
        st.session_state.step = step


def step_states() -> list[str]:
    """now / done / blocked / todo for Describe, Review, Verify, Download (derived from data, not history)."""
    result = st.session_state.gen_result
    ran = run_ready()
    done = [plan_ready(), ran, ran and overall_pass(result), ran and st.session_state.downloaded and result.exportable]
    blocked = [False, bool(st.session_state.gen_error), ran and not overall_pass(result), False]
    states = []
    for i in range(4):
        if i == st.session_state.step:
            states.append("now")
        else:
            states.append("blocked" if blocked[i] else "done" if done[i] else "todo")
    return states


def open_workspace() -> None:
    """Get started (first visit) or Return to workspace (resume the last valid step)."""
    if not st.session_state.workspace_started:
        reset()
        st.session_state.workspace_started = True
    st.session_state.page = "workspace"


def go_home() -> None:
    st.session_state.page = "landing"  # nothing is cleared: Return to workspace resumes


def new_workflow() -> None:
    reset()
    st.session_state.step = 0


def live_plan_allowed() -> bool:
    """Spend one Live AI plan if this session and today's app-wide budget allow it."""
    limits = pipeline.live_limits()
    if st.session_state.live_plans_used >= limits["per_session"]:
        return False
    if not daily_live_counter().try_use(limits["per_day"]):
        return False
    st.session_state.live_plans_used += 1
    return True


def mark_downloaded() -> None:
    st.session_state.downloaded = True


def record_live_check(outcome) -> None:
    """Connection status from a real live plan: a reply arrived (verified) or the API call failed."""
    meta = outcome.plan.metadata
    if outcome.mode != LIVE or outcome.plan.source != "llm":
        return
    if meta.attempts > 0:
        st.session_state.live_check = {"ok": True, "ms": meta.latency_ms, "at": datetime.now().strftime("%H:%M")}
    elif "llm_error" in outcome.plan.issue_codes():
        st.session_state.live_check = {"ok": False, "kind": meta.error_kind or "unknown"}


# ---- text helpers --------------------------------------------------------------------------


def plan_message(plan, mode: str, live_ready: bool) -> tuple[str, str]:
    """("error" | "warning", one general line) for a plan that is not valid."""
    lines = []
    for issue in plan.issues:
        if issue.code == "llm_error":
            line = LLM_ERROR_LINE.get(plan.metadata.error_kind, "AI service unavailable: try again shortly.")
        elif issue.code == "llm_not_configured":
            line = ("Your own text needs Live AI: switch the planning mode." if mode == OFFLINE and live_ready
                    else "Live AI is not set up: choose a ready-made example.")
        else:
            line = ISSUE_LINE.get(issue.code, "Bad request.")
        if line not in lines:
            lines.append(line)
    if not lines:
        lines = ["Bad request."]
    text = lines[0] + (f" (+{len(lines) - 1} more)" if len(lines) > 1 else "")
    return ("warning" if plan.status == "needs_clarification" else "error"), text


def plain_markdown(text: str) -> str:
    """Show model-written text literally: no links, images, HTML or formatting from the model."""
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~:$])", r"\\\1", text)


def esc(value) -> str:
    return html.escape(str(value))


def plan_source(plan) -> str:
    """Who planned it, in plain words: the live Gemini model (requests, time) or a recorded reply."""
    meta = plan.metadata
    if plan.source == "llm":
        seconds = f" · {meta.latency_ms / 1000:.1f} s" if meta.latency_ms is not None else ""
        return f"Planned by Gemini ({meta.model or 'model'}) · {meta.requests} request(s){seconds}"
    if plan.source == "mock":
        return "Planned from a recorded Gemini reply (ready-made example, no API call)"
    return "Checked by QASeed's own rules (not sent to the AI)"


def singular(name: str) -> str:
    return name[:-1] if name.endswith("s") else name


def a_an(word: str) -> str:
    """'a customer', 'an invoice'."""
    return f"{'an' if word[:1].lower() in 'aeiou' else 'a'} {word}"


def count_rule(notes: list[str]) -> str:
    """The validator's count rule in a few words, e.g. 'one per subscription'."""
    parts = []
    for note in notes:
        one = re.search(r"assuming one \w+ per (\w+)", note)
        raised = re.search(r"raised from \d+ to (\d+)", note)
        if one:
            parts.append(f"one per {one.group(1)}")
        elif raised:
            parts.append(f"raised to {raised.group(1)} to fit your conditions")
        elif "minimum needed" in note:
            parts.append("the minimum your conditions need")
        else:
            parts.append(note)
    return "; ".join(parts)


def count_lines(plan) -> tuple[list[str], set[str]]:
    """(one short HTML line per table saying where its number comes from, the validator notes those lines use).

    Works for any valid plan, live or built-in: it reads only the schema-checked plan. A table either has its
    number in the scenario, or the validator worked it out (plan_validator.resolve_counts writes one note each):
    tables the scenario does not mention are added because a requested table's NOT NULL foreign key needs them.
    """
    lines, used = [], set()
    for table in plan.tables:
        name, n = table.table, table.count
        if table.count_source == "stated":  # the tile already says "from your scenario"
            continue
        notes = [a for a in plan.assumptions if a.startswith((f"{name} count not stated", f"{name} raised from"))]
        used.update(notes)
        children = [r.child_table for r in plan.relationships if r.parent_table == name]
        if any(c.table == name for c in plan.conditions) or not children:
            why = "total not in your scenario"
        else:
            why = f"each {esc(singular(children[0]))} must belong to {esc(a_an(singular(name)))}"
        rule = count_rule(notes)
        lines.append(f"<b>{n} {esc(name)}</b> · {why}" + (f" ({esc(rule)})" if rule else ""))
    return lines, used


def relationship_block(plan) -> str:
    """Schema links in plain words: the chain, then 'Each X belongs to a Y (via column)'."""
    chain = " → ".join(esc(t.capitalize()) for t in plan.generation_order)
    items = "".join(f"<li>Each {esc(singular(r.child_table))} belongs to {esc(a_an(singular(r.parent_table)))} "
                    f'<span class="qs-muted">({esc(r.child_table)}.{esc(r.child_column)})</span></li>'
                    for r in plan.relationships)
    body = f"<ul>{items}</ul>" if items else '<p class="qs-muted">No links needed: one record type only.</p>'
    return f'<div class="qs-info"><p class="qs-chain">{chain}</p>{body}</div>'


RULE_TEXT = {  # data_checks.BUSINESS_RULES in plain English (each rule must find 0 rows that break it)
    "0 customers created after the as-of date": "Customers are created on or before the as-of date",
    "0 subscriptions where canceled status and cancel date disagree": "Only canceled subscriptions have a cancel date",
    "0 subscriptions canceled before they started or after the as-of date":
        "Cancel dates fall between the start date and the as-of date",
    "0 subscriptions starting before their customer was created": "Subscriptions start after their customer is created",
    "0 invoices due before they were issued": "Invoices are due after they are issued",
    "0 overdue invoices that are not yet past due": "Overdue invoices are past their due date",
    "0 open invoices that are already past due": "Open invoices are not yet past their due date",
    "0 invoices issued before their subscription started": "Invoices are issued after their subscription starts",
    "0 payments attempted before their invoice was issued or after the as-of date":
        "Payments happen after the invoice date and by the as-of date",
    "0 invoices with a succeeded payment that are not paid": "Invoices with a successful payment are paid",
    "0 invoices with only failed or pending payments that are not unpaid (open or overdue)":
        "Invoices without a successful payment stay unpaid",
}


def plain_check(check: dict, plan) -> tuple[str, str, str]:
    """(area, check in plain English, evidence) for one SQL check row from the pipeline."""
    kind, expected, actual = check["kind"], check["expected"], check["actual"]
    planned = next((v for v in plan.verification_checks if v.description == check["check"]), None)

    def counted() -> str:
        return f"{actual} found · {expected} expected"

    def none_found(what: str) -> str:
        return "None found" if actual == 0 else f"{actual} {what} found"

    if kind == "foreign_keys_enabled":
        return "Links", "The database enforces links between records", "On" if actual == expected else "Off"
    if kind == "foreign_key_check":
        return "Links", "The database finds no broken links", none_found("broken links")
    if planned is not None and kind == "row_count":
        return "Counts", f"{planned.table.capitalize()}: number of records", counted()
    if planned is not None and kind == "condition_count":
        return "Statuses", f"{planned.table.capitalize()} with {planned.column} “{planned.value}”", counted()
    if planned is not None and kind == "foreign_key":
        parent = next((r.parent_table for r in plan.relationships
                       if r.child_table == planned.table and r.child_column == planned.column), "parent")
        return ("Links", f"Every {singular(planned.table)} belongs to an existing {singular(parent)}",
                none_found("missing links"))
    if planned is not None and kind == "unique":
        column = planned.column.removeprefix(f"{singular(planned.table)}_").replace("_", " ")
        return "Unique values", f"{singular(planned.table).capitalize()} {column}s are unique", none_found("duplicates")
    if kind == "business_rule":
        return "Data rules", RULE_TEXT.get(check["check"], check["check"]), none_found("rows break this rule")
    return "Other", check["check"], f"{actual} found · {expected} expected"


EVIDENCE_AREAS = [  # area, what it proves. Counts and statuses are in "Requested vs generated" above.
    ("Links", "every record points to an existing parent record"),
    ("Unique values", "no duplicates where values must be unique"),
    ("Data rules", "dates and statuses are consistent"),
]


def evidence_lines(result, plan) -> list[str]:
    """A few plain bullet points that summarise the checks by area, naming any check that failed."""
    by_area: dict[str, list[tuple[str, str, bool]]] = {}
    for check in result.checks:
        area, text, evidence = plain_check(check, plan)
        by_area.setdefault(area, []).append((text, evidence, bool(check["passed"])))
    lines = []
    for area, claim in EVIDENCE_AREAS:
        items = by_area.get(area)
        if not items:
            continue
        failed = [(t, e) for t, e, ok in items if not ok]
        detail = f"{len(items) - len(failed)} of {len(items)} checks passed"
        if failed:
            detail = "Failed: " + "; ".join(f"{t} ({e})" for t, e in failed)
        lines.append(f'<p class="qs-ev{" bad" if failed else ""}"><b>{area}</b>: {esc(claim if not failed else "a check failed")}'
                     f'<span class="qs-muted">{esc(detail)}</span></p>')
    if result.export_checks:
        ok = result.exportable
        lines.append(f'<p class="qs-ev{"" if ok else " bad"}"><b>Download files</b>: '
                     f'{"re-checked after export" if ok else "failed the re-check after export"}'
                     f'<span class="qs-muted">Reloaded into a fresh database: {esc(result.export_checks)} '
                     f'checks passed again</span></p>')
    return lines


def requested_vs_generated(result, plan) -> list[dict]:
    """Per table and per requested status: requested, generated (found by the SQL checks), result."""
    labels = ([f"{t.table.capitalize()} records" + (" (added by QASeed)" if t.count_source == "derived" else "")
               for t in plan.tables]
              + [f"{c.table.capitalize()} with {c.column} “{c.value}”" for c in plan.conditions])
    if len(labels) != len(result.coverage):  # pipeline.coverage_rows lists tables, then conditions
        labels = [row["what"] for row in result.coverage]
    return [{"What": label, "Requested": row["requested"], "Generated": row["generated"],
             "Result": "PASS" if row["verified"] else "FAIL"} for label, row in zip(labels, result.coverage)]


def expected_vs_found(result, plan) -> list[dict]:
    """Every SQL check in plain English with its expected and found value."""
    rows = []
    for check in result.checks:
        _, text, _ = plain_check(check, plan)
        expected, found = check["expected"], check["actual"]
        if check["kind"] == "foreign_keys_enabled":
            expected, found = "On", "On" if found == check["expected"] else "Off"
        rows.append({"Check": text, "Expected": str(expected), "Found": str(found),
                     "Result": "PASS" if check["passed"] else "FAIL"})
    return rows


def result_banner(result, passed: bool, rows: int | None) -> tuple[str, str]:
    """("success" | "error", one plain sentence) for the Verify step."""
    failed = result.total_checks - result.passed_count
    if passed:
        return "success", f"All checks passed. Your {rows} records match the plan."
    if not result.total_checks:
        return "error", "No checks ran, so the data can't be trusted. Downloads are blocked."
    if not result.passed:
        return "error", f"{failed} of {result.total_checks} checks failed. The data doesn't match the plan, so downloads are blocked."
    return "error", "The data passed, but the exported files failed the re-check. Downloads are blocked."


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def two_buttons(back_key: str, next_label: str, next_key: str, *, next_disabled: bool = False,
                next_icon: str | None = None, next_on_click=None) -> tuple[bool, bool]:
    """Equal-width Back (outline) and primary action, as on every workspace step."""
    with st.container(horizontal=True, gap="small"):
        back = st.button("Back", key=back_key, width="stretch")
        forward = st.button(next_label, key=next_key, type="primary", width="stretch", disabled=next_disabled,
                            icon=next_icon, on_click=next_on_click)
    return back, forward


# ---- header ----------------------------------------------------------------------------------

html_block(STYLE)
with st.container(key="qs_header", horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    html_block(f'<div class="qs-brand">{SPROUT}<span class="qs-name">QASeed</span></div>')
    if st.session_state.page == "workspace":
        st.button("Home", key="home_btn", on_click=go_home, icon=":material/home:")


# ---- landing -----------------------------------------------------------------------------------

if st.session_state.page == "landing":
    with st.container(border=True, key="qs_card"):
        html_block('<h1 class="qs-h1">From test scenarios to verified data.</h1>'
                   '<p class="qs-lead">QASeed turns natural-language test scenarios into specific, connected test '
                   "data. AI helps plan and generate the records, while independent SQL checks validate the "
                   "results.</p>")
        st.button("Return to workspace" if st.session_state.workspace_started else "Get started", key="start_btn",
                  type="primary", on_click=open_workspace, icon=":material/arrow_forward:", icon_position="right")
        st.divider()
        items = "".join(f'<div class="item"><div class="icon">{_ICON.format(paths)}</div><strong>{name}</strong>'
                        f"<span>{text}</span></div>" for name, paths, text in OVERVIEW)
        html_block(f'<p class="qs-small" style="margin-bottom:0.6rem">HOW IT WORKS</p><div class="qs-overview">{items}</div>')
    html_block('<p class="qs-footer">Less setup. More testing.</p>')
    warm_up_workspace()  # after the landing page is sent, so the first paint never waits for it
    st.stop()


# ---- workspace modules: imported here, after the landing page (and usually already warmed up) -------

from qaseed import __version__, pipeline  # noqa: E402
from qaseed import artifacts  # noqa: E402  Download step: files read from the verified run's own bytes
from qaseed.generator import DEFAULT_SEED  # noqa: E402
from qaseed.guardrails import clean_text  # noqa: E402
from qaseed.planner import MAX_SCENARIO_CHARS  # noqa: E402
from qaseed.schema import create_connection, inspect_schema  # noqa: E402

try:  # Streamlit Community Cloud: the API key and settings come from the app's Secrets box
    pipeline.load_secrets(st.secrets)
except Exception:  # no secrets configured (e.g. local run with .env)
    pass
log = pipeline.configure_logging()
st.session_state.setdefault("seed", DEFAULT_SEED)


# ---- sidebar: planning mode, ready-made examples, settings, about (workspace only) ---------------

status = pipeline.llm_status()
with st.sidebar:
    if status["configured"]:
        mode_options = [LIVE, OFFLINE]
    else:
        st.warning("Live AI is not set up: ready-made examples only.")
        mode_options = [OFFLINE]
    if st.session_state.get("mode") not in mode_options:
        st.session_state.mode = pipeline.default_mode(mode_options)
    mode_text = {LIVE: ("Live AI", "Any scenario"), OFFLINE: ("Ready-made examples", "Built-in scenarios")}
    st.radio(
        "Planning mode",
        mode_options,
        key="mode",
        format_func=lambda m: mode_text[m][0],
        captions=[mode_text[m][1] for m in mode_options],
        help=f"Live AI uses {status['provider']} ({status['model'] or 'not set up'}).",
        persist_state="page",
    )
    connection_status = st.empty()  # filled at the end of the run, after any live plan in this run
    live_budget = st.empty()  # "Live AI plans left in this session", also filled at the end of the run
    if st.session_state.mode == OFFLINE:  # Live AI plans any text, so the full examples list is not needed
        st.selectbox(
            "Ready-made examples",
            pipeline.sample_scenarios(),
            index=None,
            key="sample_choice",
            on_change=use_sample,
            placeholder="Select",
            format_func=lambda s: f"{s['id']} · {EXAMPLE_PURPOSE.get(s['expected'], s['expected'])} — {s['scenario'][:55]}",
            help="Recorded examples: no API call.",
            persist_state="page",
        )
    with st.expander("Advanced settings"):
        st.number_input(
            "Seed", min_value=0, max_value=1_000_000, step=1, key="seed", persist_state="page",
            help="Same seed, same data.",
        )
        st.date_input(
            "As-of date", key="as_of", persist_state="page",
            help="The data's \"today\".",
        )
        st.caption("Change only to vary or reproduce data.")
    with st.expander("About"):
        st.markdown(
            f"**QASeed v{__version__}** · synthetic test data only · no production connections.\n\n"
            "**Project status**\n"
            "- ✅ AI planning, schema checks, linked data generation, independent data checks, verified CSV export\n"
            "- ✅ Day 6: security review, input and budget checks, performance fixes\n"
            "- ✅ Day 7: hosted demo\n"
            "- ⏭️ Post-MVP: SQL export, automatic repair of failed checks"
        )
        connection = create_connection()
        try:
            schema = inspect_schema(connection)
        finally:
            connection.close()
        st.markdown("**Sample schema** (customers → subscriptions → invoices → payments)")
        st.caption(f"In-memory SQLite. Foreign-key enforcement is {'on' if schema['foreign_keys_enabled'] else 'off'}.")
        for table in schema["tables"]:
            st.markdown(f"**{table['name']}** " + " ".join(
                f"· `{fk['from_column']} → {fk['to_table']}.{fk['to_column']}`" for fk in table["foreign_keys"]))
            st.table([
                {
                    "column": column["name"],
                    "type": column["type"],
                    "not null": "yes" if column["not_null"] else "",
                    "primary key": "yes" if column["primary_key"] else "",
                    "allowed values": ", ".join(table["allowed_values"].get(column["name"], [])),
                }
                for column in table["columns"]
            ])

seed, as_of = int(st.session_state.seed), st.session_state.as_of
st.session_state.step = min(st.session_state.step, max_step())  # a stale step falls back to the last valid one


# ---- stepper -----------------------------------------------------------------------------------

with st.container(border=True, key="qs_stepper"):
    html_block(f'<p class="qs-small">Step {st.session_state.step + 1} of 4</p>')
    furthest = max_step()
    marks = {"done": "✓", "blocked": "✕"}
    row = st.container(horizontal=True, gap="small", wrap=False)
    for i, state in enumerate(step_states()):
        row.button(f"{marks.get(state, i + 1)}  {STEPS[i]}", key=f"nav_{i}_{state}", on_click=go_to, args=(i,),
                      disabled=i > furthest, width="stretch")

if st.session_state.notice:  # e.g. "Already planned": a short pop-up, not a permanent box
    st.toast(st.session_state.notice, icon=":material/info:")
    st.session_state.notice = ""

step = st.session_state.step


# ---- step 1: Describe --------------------------------------------------------------------------

if step == 0:
    with st.container(border=True, key="qs_card"):
        st.subheader("Describe")
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            html_block('<p class="qs-label">Test scenario</p>')
            st.button("Clear", key="clear_btn", icon=":material/close:", on_click=clear_scenario,
                      disabled=not st.session_state.scenario)
        st.text_area(
            "Test scenario",
            key="scenario",
            height=140,
            max_chars=MAX_SCENARIO_CHARS,
            placeholder="Describe the records, counts, and conditions you need.",
            help="Do not paste real customer data, keys or SQL; such input is refused before any API call.",
            label_visibility="collapsed",
            on_change=scenario_edited,
            persist_state="page",
        )
        html_block('<p class="qs-small" style="margin-bottom:0.3rem">Try an example</p>')
        examples_row = st.container(horizontal=True, gap="small", wrap=False)
        for number, (label, _) in enumerate(QUICK_EXAMPLES):
            selected = st.session_state.example_choice == number
            with examples_row.container(key=f"ex_wrap_{number}_{'on' if selected else 'off'}"):
                st.button(label, key=f"quick_example_{number}", on_click=use_quick_example, args=(number,),
                          width="stretch", icon=":material/check:" if selected else None)
        # Always enabled: Streamlit saves typed text only when the box loses focus, so a button disabled while the
        # box looked empty would swallow the first click after typing. An empty scenario gets "Enter a scenario."
        # from QASeed's own input check instead (no API call, no Live AI budget spent).
        plan_clicked = st.button("Generate", key="plan_btn", type="primary", width="stretch")

        message = st.container()  # the outcome of Generate, right under the button
        if plan_clicked:
            key = plan_key()
            outcome = st.session_state.plan_outcome
            if outcome is not None and st.session_state.plan_key == key:
                log.info("event=plan status=reused")
                if outcome.plan.status == "valid":
                    st.session_state.notice = "Already planned (no new request)."
                    st.session_state.step = 1
                    st.rerun()
                st.toast("Already planned (no new request).", icon=":material/info:")
            # Input refused by QASeed's own checks never reaches the API, so it does not spend a Live AI plan.
            elif key[1] == LIVE and not pipeline.blocked_locally(st.session_state.scenario) and not live_plan_allowed():
                message.warning("Live AI limit reached: try a ready-made example.")
                log.warning("event=plan mode=live status=limited")
            else:
                clear_plan()
                label = "Asking Gemini to plan the scenario (up to 4 requests)…" if key[1] == LIVE else "Planning from the built-in sample reply…"
                with message, st.spinner(label):
                    try:
                        st.session_state.plan_outcome = pipeline.plan_request(st.session_state.scenario, key[1])
                        st.session_state.plan_key = key
                        record_live_check(st.session_state.plan_outcome)
                    except Exception as exc:  # unexpected local failure; planner problems come back as plan issues
                        log.error("event=plan status=failed category=unexpected error_type=%s", type(exc).__name__)
                        message.error("Planning failed: try again.")
                if plan_ready():
                    st.session_state.step = 1
                    st.rerun()

        outcome = st.session_state.plan_outcome
        if outcome is not None and st.session_state.plan_key == plan_key() and outcome.plan.status != "valid":
            level, line = plan_message(outcome.plan, st.session_state.mode, status["configured"])
            (message.warning if level == "warning" else message.error)(line)


# ---- step 2: Review ------------------------------------------------------------------------------

elif step == 1:
    plan = st.session_state.plan_outcome.plan
    with st.container(border=True, key="qs_card"):
        st.subheader("Review the plan")
        html_block('<p class="qs-lead">Confirm that the plan matches your scenario.</p>')
        st.caption(plan_source(plan))
        st.success("Plan is valid: it matches the database schema.")
        for column, table in zip(st.columns(max(1, len(plan.tables)), gap="small"), plan.tables):
            stated = table.count_source == "stated"
            column.metric(table.table.capitalize(), table.count,
                          delta="from your scenario" if stated else "added by QASeed",
                          delta_color="off", delta_arrow="off")
        lines, explained = count_lines(plan)
        if any(t.count_source == "derived" for t in plan.tables):  # only when some number was worked out
            html_block('<p class="qs-h">Counts</p>'
                       f'<div class="qs-info">{"<br>".join(lines)}</div>')
        html_block('<p class="qs-h">Relationships</p>' + relationship_block(plan))
        html_block('<p class="qs-h">Conditions</p>')
        if plan.conditions:
            html_block("".join(f'<p class="qs-rule">{c.count} {esc(c.table)} with {esc(c.column)} “{esc(c.value)}”</p>'
                               for c in plan.conditions))
        else:
            st.caption("None: every row gets the normal status.")
        other_assumptions = [a for a in plan.assumptions if a not in explained]  # count notes are shown above
        if other_assumptions:
            with st.expander(f"Assumptions ({len(other_assumptions)})"):
                st.markdown("\n".join(f"- {plain_markdown(a)}" for a in other_assumptions))

        st.checkbox("I've reviewed the plan.", key="reviewed", persist_state="page")
        if st.session_state.gen_error:
            st.error(st.session_state.gen_error)
        elif st.session_state.gen_result is not None and not run_ready():
            st.caption("Seed or date changed since the last run: press Generate & verify to use the new settings.")
        back, generate = two_buttons("review_back", "Generate & verify", "generate_btn",
                                     next_disabled=not st.session_state.reviewed)
        if back:
            st.session_state.step = 0
            st.rerun()
        if generate and st.session_state.reviewed:
            if run_ready():
                st.session_state.notice = "Already generated with these settings."
                log.info("event=generate status=reused")
            else:
                clear_generation()
                with st.spinner("Generating linked rows and running independent SQL checks…"):
                    try:
                        st.session_state.gen_result = pipeline.run_generation(plan, seed, as_of)
                        st.session_state.gen_key = gen_key()
                        st.session_state.gen_at = datetime.now()
                    except pipeline.PipelineError as exc:
                        st.session_state.gen_error = GENERATION_ERROR_LINE.get(
                            exc.category, "Generation failed: nothing was exported.")
            if run_ready():
                st.session_state.step = 2
            st.rerun()


# ---- step 3: Verify ------------------------------------------------------------------------------

elif step == 2:
    result = st.session_state.gen_result
    plan = st.session_state.plan_outcome.plan
    passed = overall_pass(result)
    failed_count = result.total_checks - result.passed_count
    with st.container(border=True, key="qs_card"):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.subheader("Verification results")
            html_block(f'<span class="qs-badge {"pass" if passed else "fail"}">{"PASS" if passed else "FAIL"}</span>')
        rows_total = sql_row_total(result)  # counted by the independent SQL checks, not the generator's own tally
        level, line = result_banner(result, passed, rows_total)
        (st.success if level == "success" else st.error)(line)
        m1, m2, m3, m4 = st.columns(4, gap="small")
        m1.metric("Planned records", sum(t.count for t in plan.tables),
                  help="All records in the reviewed plan: the numbers from your scenario plus the ones QASeed added.")
        m2.metric("Records created", "—" if rows_total is None else rows_total,
                  help="Records found in the generated data by the SQL checks.")
        m3.metric("Checks passed", f"{result.passed_count}/{result.total_checks}", help="SQL checks that passed.")
        m4.metric("Checks failed", failed_count, help="SQL checks that failed.")
        st.caption(f"Seed {result.seed} · As-of date {result.as_of.isoformat()} · Generated and checked in "
                   f"{result.elapsed_ms} ms · Same plan + seed + date = identical data")
        html_block('<p class="qs-h">Requested vs generated</p>')
        st.dataframe(requested_vs_generated(result, plan), hide_index=True, width="stretch",
                     column_config={"Result": st.column_config.Column(width="small")})

        shown_filter = st.segmented_control(
            "Filter results", ["all", "passed", "failed"], default="all", required=True, key="result_filter",
            format_func={"all": "All results", "passed": "Passed", "failed": "Failed"}.get,
            label_visibility="collapsed",
        ) or "all"
        rows = [c for c in result.checks
                if shown_filter == "all" or (shown_filter == "passed") == bool(c["passed"])]
        if rows:
            table_rows = []
            for c in rows:
                _, text, _ = plain_check(c, plan)  # the evidence is summarised under "Detailed evidence"
                table_rows.append({"Check": text, "Status": "PASS" if c["passed"] else "FAIL"})
            st.dataframe(
                table_rows, hide_index=True, width="stretch",
                column_config={"Check": st.column_config.Column(width="large"),
                               "Status": st.column_config.Column(width="small")},
            )
        else:
            st.caption("No checks match this filter.")

        with st.expander("Detailed evidence"):
            html_block("".join(evidence_lines(result, plan)))
            html_block('<p class="qs-h">Every check: expected and found</p>')
            st.dataframe(expected_vs_found(result, plan), hide_index=True, width="stretch",
                         column_config={"Check": st.column_config.Column(width="large"),
                                        "Result": st.column_config.Column(width="small")})
        with st.expander(f"Sample rows (first {pipeline.SAMPLE_ROWS} per table)"):  # also when checks failed
            tabs = st.tabs([f"{t.capitalize()} ({result.row_counts.get(t, len(rows))})"
                            for t, rows in result.samples.items()])
            for tab, rows in zip(tabs, result.samples.values()):
                tab.dataframe(rows, hide_index=True, width="stretch")
        back, outputs = two_buttons("verify_back", "Outputs", "outputs_btn")
        if back:
            st.session_state.step = 1
            st.rerun()
        if outputs:
            st.session_state.step = 3
            st.rerun()


# ---- step 4: Download ----------------------------------------------------------------------------

else:
    result = st.session_state.gen_result
    if st.session_state.gen_at is None:  # e.g. a run kept from before this field existed
        st.session_state.gen_at = datetime.now()
    item = artifacts.bundle(result, artifacts.zip_name(st.session_state.gen_at))

    @st.dialog("ZIP contents", width="large")
    def preview(bundle: artifacts.Artifact) -> None:
        entries = artifacts.zip_files(bundle.data)
        html_block(f'<p class="qs-file">{esc(bundle.name)}</p>')
        st.dataframe([{"File": e.name, "Contents": e.description, "Rows": e.rows if e.rows is not None else "—"}
                      for e in entries], hide_index=True, width="stretch")
        chosen = st.segmented_control("Open a file", [e.name for e in entries], default=entries[0].name,
                                      required=True, key="preview_file") or entries[0].name
        entry = next(e for e in entries if e.name == chosen)
        if entry.kind == "csv":
            rows, total = artifacts.csv_rows(entry.data, PREVIEW_ROWS)
            st.dataframe(rows, hide_index=True, width="stretch")
            if total > PREVIEW_ROWS:
                st.caption(f"First {PREVIEW_ROWS} of {total} rows. The download has every row.")
        else:
            st.code(entry.data.decode("utf-8"), language="json")

    with st.container(border=True, key="qs_card"):
        st.subheader("Download results")
        if item is not None:
            st.success("Your verified test data is ready.")
            with st.container(border=True, key="file_row_csv_zip"):
                info, look, save = st.columns([5, 2, 2], vertical_alignment="center", gap="small")
                with info:
                    html_block(f'<p class="qs-file">{esc(item.name)}</p><p class="qs-small">{esc(item.description)}</p>')
                if look.button("Preview", key="preview_csv_zip", width="stretch", icon=":material/visibility:"):
                    preview(item)
                save.download_button("Download", item.data, item.name, mime=item.mime, key="download_csv_zip",
                                     on_click=mark_downloaded, type="primary", width="stretch",
                                     icon=":material/download:")
        else:
            st.error(result.export_note or "Export blocked: verification failed.")
        back, _ = two_buttons("download_back", "New", "new_btn", next_icon=":material/add:", next_on_click=new_workflow)
        if back:
            st.session_state.step = 2
            st.rerun()


# ---- sidebar status, filled last so it reflects any live plan made in this run ----------------------

if status["configured"]:
    check = st.session_state.live_check
    if check is None:
        connection_status.caption("Gemini ready · confirmed by your first live plan")
    elif check["ok"]:
        connection_status.caption(f"✓ Gemini connected · verified {check['at']} ({check['ms']:,} ms)")
    else:
        connection_status.caption(f"⚠ Last live request failed ({check['kind']}). Try again.")
    if st.session_state.mode == LIVE:
        left = max(0, pipeline.live_limits()["per_session"] - st.session_state.live_plans_used)
        live_budget.caption(f"Live AI plans left in this session: {left}")
