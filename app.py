"""QASeed Streamlit app: scenario -> AI plan -> generate -> validate -> download CSV (ZIP).

UI only. Planning, generation, checks, export and logging live in
qaseed.pipeline and the modules it reuses. Results are kept in session state,
so Streamlit reruns never repeat an API call or a generation by themselves.
"""

import base64
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from qaseed import __version__, pipeline
from qaseed.config import load_local_env
from qaseed.generator import DEFAULT_SEED
from qaseed.guardrails import clean_text
from qaseed.planner import MAX_SCENARIO_CHARS
from qaseed.schema import create_connection, inspect_schema

load_local_env()
try:  # Streamlit Community Cloud: the API key and settings come from the app's Secrets box
    pipeline.load_secrets(st.secrets)
except Exception:  # no secrets configured (e.g. local run with .env)
    pass
log = pipeline.configure_logging()

# Brand asset (tracked in the repo so the hosted app is branded). The app still works without it.
LOGO = Path(__file__).resolve().parent / "assets" / "brand" / "qaseed_logo.png"
HAS_LOGO = LOGO.is_file()

st.set_page_config(page_title="QASeed · AI test data agent", page_icon=str(LOGO) if HAS_LOGO else "✅", layout="wide")

LIVE, OFFLINE = "live", "offline"
EXAMPLE_PURPOSE = {  # short label for each ready-made example
    "valid": "✅ Valid",
    "rejected": "⛔ Invalid",
    "needs_clarification": "❓ Incomplete",
}
# One short, general line per plan issue. Full technical detail stays in the "Plan JSON" expander.
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
QUICK_EXAMPLES = [  # editable starting points, all supported built-in scenarios (label, example id)
    ("Payments · 8 succeeded, 4 failed", "D5-01"),
    ("Subscriptions · 50 customers, 10 canceled", "S01"),
    ("Invoices · 6 overdue, 4 void", "S05"),
]
STYLE = """
<style>
.block-container {max-width: 1180px; padding-top: 1.6rem; padding-bottom: 2rem;}
/* Sidebar collapsed: Streamlit's top bar then shows its logo and "»" button, so start below that bar */
.stApp:has([data-testid="stSidebar"][data-collapsed="true"]) .block-container {padding-top: 4rem;}
[data-testid="stHeading"] h3 {font-size: 1.15rem; font-weight: 700; padding: 0.1rem 0 0.4rem;}
.qs-brand {display: flex; align-items: center; flex-wrap: wrap; column-gap: 0.75rem; row-gap: 0.3rem; margin: 0 0 0.7rem;}
.qs-brand img {width: 44px; height: 44px; display: block;}
.qs-name {font-family: "Segoe UI", "Inter", system-ui, -apple-system, sans-serif; font-size: 2.2rem; font-weight: 800;
          letter-spacing: -0.04em; line-height: 1; color: #16191B;}
.qs-name span {color: #1B813E;}
.qs-value {color: #4F5250; font-size: 1.02rem; line-height: 1.4; padding-left: 0.85rem; border-left: 2px solid #E3E3DD;}
.qs-value b {color: #1B813E;}
.qs-steps {display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0 0 0.9rem;}
.qs-step {padding: 0.3rem 0.8rem; border-radius: 999px; border: 1px solid #E3E3DD; background: #FFFFFF;
          color: #8A8D89; font-size: 0.84rem; font-weight: 600;}
.qs-step.done {color: #1B813E; border-color: #9FD0B0; background: #EAF5EE;}
.qs-step.now {color: #FFFFFF; background: #16191B; border-color: #16191B;}
.qs-step.blocked {color: #B42F2F; border-color: #E8B4B4; background: #FBEEEE;}
.qs-hint {color: #4F5250 !important; font-size: 0.86rem !important; line-height: 1.45 !important; margin: -0.4rem 0 0.1rem;}
.st-key-reset_btn button {background: #EAF5EE; border: 1px solid #9FD0B0;
          color: #1B813E; font-weight: 600;}
.st-key-reset_btn button:hover {background: #D7EEDF; border-color: #1B813E;
          color: #146B32;}
@media (max-width: 640px) {
  .block-container {padding-top: 4.2rem; padding-left: 1rem; padding-right: 1rem;}  /* clear Streamlit's mobile top bar */
  .qs-name {font-size: 1.8rem;}
  .qs-brand img {width: 36px; height: 36px;}
  .qs-value {flex-basis: 100%; padding-left: 0; border-left: 0; font-size: 0.95rem;}
  .qs-step {font-size: 0.78rem; padding: 0.25rem 0.6rem;}
}
</style>
"""
STATE_DEFAULTS = {
    "scenario": "",
    "plan_outcome": None,  # pipeline.PlanOutcome
    "plan_key": None,  # (cleaned scenario, mode) the plan was made for
    "gen_result": None,  # pipeline.GenerationResult
    "gen_key": None,  # (plan_key, seed, as_of) the data was generated for
    "gen_error": "",
    "downloaded": False,  # the user clicked Download for the current result
    "notice": "",
}
for name, value in STATE_DEFAULTS.items():
    st.session_state.setdefault(name, value)
# Result of the last real Gemini call in this session (kept across Reset): None = not verified yet.
st.session_state.setdefault("live_check", None)
# Live AI plans this visitor has started (kept across Reset, so Reset cannot lift the limit).
st.session_state.setdefault("live_plans_used", 0)


@st.cache_resource
def daily_live_counter() -> pipeline.DailyCounter:
    """Live AI plans used today by all visitors of this app (shared across sessions)."""
    return pipeline.DailyCounter()


def reset() -> None:
    for name, value in STATE_DEFAULTS.items():
        st.session_state[name] = value
    st.session_state.sample_choice = None  # examples dropdown back to "Choose an example…"
    log.info("event=reset")


def load_scenario(text: str) -> None:
    """Put an example into the scenario box and start a fresh run (the previous plan and results no longer apply)."""
    st.session_state.scenario = text
    st.session_state.plan_outcome = None
    st.session_state.plan_key = None
    st.session_state.notice = ""
    clear_generation()


def use_sample() -> None:
    """Selecting an example in the dropdown loads it at once: no second click needed."""
    sample = st.session_state.get("sample_choice")
    if sample:
        load_scenario(sample["scenario"])


def use_quick_example(example_id: str) -> None:
    load_scenario(next(s["scenario"] for s in pipeline.sample_scenarios() if s["id"] == example_id))


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


def plan_key() -> tuple[str, str]:
    return clean_text(st.session_state.scenario), st.session_state.mode


def clear_generation() -> None:
    st.session_state.gen_result = None
    st.session_state.gen_key = None
    st.session_state.gen_error = ""
    st.session_state.downloaded = False


def record_live_check(outcome) -> None:
    """Connection status from a real live plan: a reply arrived (verified) or the API call failed."""
    meta = outcome.plan.metadata
    if outcome.mode != LIVE or outcome.plan.source != "llm":
        return
    if meta.attempts > 0:
        st.session_state.live_check = {"ok": True, "ms": meta.latency_ms, "at": datetime.now().strftime("%H:%M")}
    elif "llm_error" in outcome.plan.issue_codes():
        st.session_state.live_check = {"ok": False, "kind": meta.error_kind or "unknown"}


def step_states() -> list[str]:
    """done / now / blocked / todo for Describe, Review AI plan, Generate & validate, Download CSV."""
    outcome = st.session_state.plan_outcome
    if outcome is None or st.session_state.plan_key != plan_key():
        return ["now", "todo", "todo", "todo"]  # nothing planned yet, or the scenario changed since
    if outcome.plan.status != "valid":
        return ["done", "blocked", "todo", "todo"]
    result = st.session_state.gen_result
    if st.session_state.gen_error or (result is not None and not result.exportable):
        return ["done", "done", "blocked", "todo"]
    if result is None:
        return ["done", "now", "todo", "todo"]  # plan shown: review it, then press Generate
    if not st.session_state.downloaded:
        return ["done", "done", "done", "now"]
    return ["done", "done", "done", "done"]  # downloaded: the whole flow is complete


def step_tracker(states: list[str]) -> str:
    names = ["Describe", "Review AI plan", "Generate & validate", "Download CSV"]
    marks = {"done": "✓ ", "blocked": "✕ "}
    chips = [f'<span class="qs-step {state}">{marks.get(state, f"{n} · ")}{name}</span>'
             for n, (name, state) in enumerate(zip(names, states), start=1)]
    return '<div class="qs-steps">' + "".join(chips) + "</div>"


@st.cache_data(show_spinner=False)
def logo_data_uri() -> str:
    return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii") if HAS_LOGO else ""


def brand_header() -> str:
    """One compact row: logo, wordmark ("QA" ink, "Seed" green) and the value proposition."""
    logo = f'<img src="{logo_data_uri()}" alt="QASeed logo">' if HAS_LOGO else ""
    return (f'<div class="qs-brand">{logo}<span class="qs-name">QA<span>Seed</span></span>'
            '<span class="qs-value">Describe test data in plain English. Get linked rows with every condition '
            "<b>proven</b>.</span></div>")


# ---- header and settings -------------------------------------------------------

st.markdown(STYLE, unsafe_allow_html=True)
if HAS_LOGO:
    st.logo(str(LOGO), size="large")
st.markdown(brand_header(), unsafe_allow_html=True)

status = pipeline.llm_status()
samples = pipeline.sample_scenarios()

with st.sidebar:
    if status["configured"]:
        mode_options = [LIVE, OFFLINE]
    else:
        st.warning("Live AI is not set up. Ready-made examples are available.")
        mode_options = [OFFLINE]
    if st.session_state.get("mode") not in mode_options:
        st.session_state.mode = pipeline.default_mode(mode_options)
    mode_text = {LIVE: ("Live AI", "Plan any request"), OFFLINE: ("Ready-made examples", "Instant demo scenarios")}
    st.radio(
        "Planning mode",
        mode_options,
        key="mode",
        format_func=lambda m: mode_text[m][0],
        captions=[mode_text[m][1] for m in mode_options],
        help=f"Live AI: {status['provider']} ({status['model'] or 'not set up'}) plans any scenario you type, "
             "using 1–4 free-tier API calls. Ready-made examples replay recorded AI replies for the 19 examples.",
    )
    connection_status = st.empty()  # filled at the end of the run, after any live plan in this run
    live_budget = st.empty()  # "Live AI plans left in this session", also filled at the end of the run
    with st.expander("Advanced settings"):
        seed = int(st.number_input(
            "Seed", min_value=0, max_value=1_000_000, value=DEFAULT_SEED, step=1,
            help="Same scenario + seed + date gives exactly the same data every time. "
                 "Change the seed for a fresh dataset with the same counts.",
        ))
        as_of = st.date_input(
            "As-of date (\"today\" for the data)", value=date.today(),
            help="Overdue invoices are past due and cancel dates are in the past relative to this date. "
                 "Keep it fixed to reproduce the same dataset later.",
        )
        st.caption("Leave these as they are unless you need to reproduce or vary a dataset.")
    st.button("Reset / clear all", key="reset_btn", on_click=reset, width="stretch")

if st.session_state.notice:
    st.info(st.session_state.notice)
    st.session_state.notice = ""
tracker = st.empty()  # step tracker, filled at the end of the run when the current step is known

# ---- step 1: scenario ------------------------------------------------------------

with st.container(border=True):
    st.subheader("1 · Describe the scenario")
    show_examples = st.session_state.mode == OFFLINE  # Live AI plans any text, so the examples panel is not needed
    left, right = st.columns([3, 2], gap="large") if show_examples else (st.container(), None)
    with left:
        st.text_area(
            "Your test scenario (plain English)",
            key="scenario",
            height=110,
            max_chars=MAX_SCENARIO_CHARS,
            placeholder="Type your scenario here…  e.g. 12 payments: 8 succeeded and 4 failed",
            help="Do not paste real customer data, keys or SQL; such input is refused before any API call.",
        )
        hint = ("Use an example below or from Ready-made examples. Edited text needs Live AI."
                if show_examples else
                "Start from an example below and edit it, or write your own: name the records, how many, and any statuses.")
        st.markdown(f'<p class="qs-hint">{hint}</p>', unsafe_allow_html=True)
        with st.container(horizontal=True, wrap=True, gap="small"):  # buttons size to their text and wrap on small screens
            for number, (label, example_id) in enumerate(QUICK_EXAMPLES):
                st.button(label, key=f"quick_example_{number}", on_click=use_quick_example, args=(example_id,),
                          width="content", help="Copies this supported scenario into the box so you can edit it.")
        plan_clicked = st.button("Plan scenario", key="plan_btn", type="primary")
if show_examples:
    with right:
        st.selectbox(
            "Ready-made examples",
            samples,
            index=None,
            key="sample_choice",
            on_change=use_sample,
            placeholder="Choose an example to load it…",
            format_func=lambda s: f"{s['id']} · {EXAMPLE_PURPOSE.get(s['expected'], s['expected'])} — {s['scenario'][:55]}",
            help="Choosing an example copies it into the scenario box. Examples run offline from recorded AI replies, "
                 "so they cost no API quota.",
        )
        st.markdown(
            '<p class="qs-hint">✅ valid · ⛔ invalid · ❓ incomplete. '
            "Invalid requests are blocked before any data is created.</p>",
            unsafe_allow_html=True,
        )

if plan_clicked:
    key = plan_key()
    if st.session_state.plan_outcome is not None and st.session_state.plan_key == key:
        st.info("Already planned (no new request).")
        log.info("event=plan status=reused")
    elif key[1] == LIVE and not live_plan_allowed():
        st.warning("Live AI limit reached: try a ready-made example.")
        log.warning("event=plan mode=live status=limited")
    else:
        clear_generation()
        st.session_state.plan_outcome = None
        label = "Asking Gemini to plan the scenario (up to 4 requests)…" if key[1] == LIVE else "Planning from the built-in sample reply…"
        with st.spinner(label):
            try:
                st.session_state.plan_outcome = pipeline.plan_request(st.session_state.scenario, key[1])
                st.session_state.plan_key = key
                record_live_check(st.session_state.plan_outcome)
            except Exception as exc:  # unexpected local failure; planner problems come back as plan issues
                log.error("event=plan status=failed category=unexpected error_type=%s", type(exc).__name__)
                st.error("Planning failed: try again.")

# ---- step 2: plan review ------------------------------------------------------------

outcome = st.session_state.plan_outcome
if outcome is not None:
    with st.container(border=True):
        plan = outcome.plan
        stale = st.session_state.plan_key != plan_key()
        st.subheader("2 · Review the AI plan")
        meta = plan.metadata
        source = {
            "llm": f"live {meta.model or 'model'}",
            "mock": "recorded AI reply for a ready-made example (no API call)",
            "none": "not sent to the AI (stopped by QASeed's own checks)",
        }[plan.source]
        details = [f"source: {source}"]
        if plan.source == "llm":
            details += [f"prompt {meta.prompt_id}", f"{meta.requests} request(s)", f"{meta.latency_ms} ms"]
        st.caption(" · ".join(details))
        if stale:
            st.warning("Scenario changed: press Plan scenario again.")

        if plan.status == "valid":
            st.success("Plan is valid: every table, column, value and count matches the schema.")
        else:
            level, line = plan_message(plan, st.session_state.mode, status["configured"])
            (st.warning if level == "warning" else st.error)(line)

        if plan.tables:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Tables** (generation order: " + " → ".join(plan.generation_order or ["-"]) + ")")
                st.dataframe([{"table": t.table, "rows": t.count, "count": t.count_source} for t in plan.tables],
                             hide_index=True, width="stretch")
            with c2:
                st.markdown("**Requested conditions**")
                if plan.conditions:
                    st.dataframe([{"condition": f"{c.table}.{c.column} = {c.value}", "rows": c.count} for c in plan.conditions],
                                 hide_index=True, width="stretch")
                else:
                    st.caption("None: every row gets the normal status.")
        if plan.assumptions and plan.status == "valid":  # a rejected plan shows only its one-line reason
            st.markdown("**Assumptions**")
            st.markdown("\n".join(f"- {a}" for a in plan.assumptions))
        with st.expander("Plan JSON"):
            st.code(plan.model_dump_json(indent=2), language="json")

        # ---- step 3: generate and validate --------------------------------------------
        if plan.status == "valid" and not stale:
            st.subheader("3 · Generate and validate")
            gen_key = (st.session_state.plan_key, seed, as_of)
            if st.button("Generate & validate data", key="generate_btn", type="primary"):
                if st.session_state.gen_result is not None and st.session_state.gen_key == gen_key:
                    st.info("Already generated with these settings.")
                    log.info("event=generate status=reused")
                else:
                    clear_generation()
                    with st.spinner("Generating linked rows and running independent data checks…"):
                        try:
                            st.session_state.gen_result = pipeline.run_generation(plan, seed, as_of)
                            st.session_state.gen_key = gen_key
                        except pipeline.PipelineError as exc:
                            st.session_state.gen_error = GENERATION_ERROR_LINE.get(
                                exc.category, "Generation failed: nothing was exported.")
            if st.session_state.gen_error:
                st.error(st.session_state.gen_error)

            result = st.session_state.gen_result
            if result is not None:
                if st.session_state.gen_key != gen_key:
                    st.caption("Seed or date changed since this run; press Generate again to use the new settings.")
                rows_total = sum(result.row_counts.values())
                if result.passed:
                    st.success(f"**Verified:** all {result.total_checks} data checks passed for {rows_total} linked rows. "
                               "Every requested count and condition is in the data.")
                else:
                    st.error(f"Not verified: {result.total_checks - result.passed_count} of {result.total_checks} "
                             "checks failed; download blocked.")
                st.markdown("**Requested vs actual**")
                st.dataframe(
                    [{"what": r["what"], "requested": r["requested"], "actual": r["generated"],
                      "result": "✅ PASS" if r["verified"] else "❌ FAIL"} for r in result.coverage],
                    hide_index=True, width="stretch",
                    column_config={name: st.column_config.Column(width="small") for name in ("requested", "actual", "result")},
                )
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Rows generated", rows_total)
                m2.metric("Data checks passed", f"{result.passed_count}/{result.total_checks}")
                m3.metric("Seed", result.seed, help=f"As-of date {result.as_of.isoformat()}")
                m4.metric("Time", f"{result.elapsed_ms} ms")
                st.caption(f"As-of date {result.as_of.isoformat()} · the same plan, seed and date always give identical data.")
                with st.expander(f"All data checks ({result.passed_count}/{result.total_checks} passed)"):
                    st.dataframe([{**c, "passed": "PASS" if c["passed"] else "FAIL"} for c in result.checks],
                                 hide_index=True, width="stretch")
                with st.expander(f"Sample rows (first {pipeline.SAMPLE_ROWS} per table)"):
                    tabs = st.tabs([f"{t} ({result.row_counts[t]})" for t in result.samples])
                    for tab, rows in zip(tabs, result.samples.values()):
                        tab.dataframe(rows, hide_index=True, width="stretch")

                # ---- step 4: download -----------------------------------------------------
                st.subheader("4 · Download")
                if result.exportable:
                    stem = f"qaseed_{outcome.sample_id or 'scenario'}_seed{result.seed}".lower()
                    with st.container(horizontal=True, wrap=True, gap="small"):
                        st.download_button("Download CSV files + validation report (ZIP)", result.csv_zip,
                                           f"{stem}_csv.zip", key="download_csv_zip", mime="application/zip",
                                           on_click=mark_downloaded, type="primary")
                        st.download_button("Download validation report (JSON)", result.report_json,
                                           f"{stem}_validation_report.json", key="download_report_json",
                                           mime="application/json", on_click=mark_downloaded)
                    files = ", ".join(f"`{f['file']}`" + (f" ({f['rows']} rows)" if f["rows"] is not None else "")
                                      for f in result.zip_contents)
                    st.caption(f"ZIP contents: {files}.")
                    st.success(f"Download verified: the CSV files were reloaded into a fresh database and passed "
                               f"{result.export_checks} checks again (counts, IDs, foreign keys, requested conditions).")
                    st.caption("SQL export is planned as a post-MVP enhancement.")
                else:
                    st.error("Export blocked: verification failed.")

tracker.markdown(step_tracker(step_states()), unsafe_allow_html=True)
if status["configured"]:
    check = st.session_state.live_check
    if check is None:
        connection_status.caption("Gemini key found · the connection is verified by your first live plan")
    elif check["ok"]:
        connection_status.caption(f"✓ Gemini connected · verified {check['at']} ({check['ms']:,} ms)")
    else:
        connection_status.caption(f"⚠ Last live request failed ({check['kind']}). Try again, or use Ready-made examples.")
    if st.session_state.mode == LIVE:
        left = max(0, pipeline.live_limits()["per_session"] - st.session_state.live_plans_used)
        live_budget.caption(f"Live AI plans left in this session: {left}")

# ---- about: project status and sample schema -------------------------------------------

with st.expander("About QASeed · project status and sample schema"):
    st.markdown(
        f"**QASeed v{__version__}** · synthetic test data only · no production connections.\n\n"
        "**Project status**\n"
        "- ✅ AI planning, schema checks, linked data generation, independent data checks, verified CSV export\n"
        "- ⏭️ Day 6: automatic repair of failed checks\n"
        "- ⏭️ Day 7: hosted demo\n"
        "- ⏭️ Post-MVP: SQL export"
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

