"""Service layer behind the Streamlit app: plan -> generate -> check -> CSV export.

The UI calls only these functions; business rules stay in the planner,
validator, generator, data checks and export modules, which are reused
unchanged.

Planner modes:
- "live":    qaseed.planner with Google Gemini (1-4 API requests per plan).
             Needs GEMINI_API_KEY and GEMINI_MODEL in the local .env.
- "offline": the 19 built-in sample scenarios (18 planning cases + the Day 5
             payment scenario), planned from recorded
             model replies (source=mock). No network, no key. Any other text
             still goes through the input guardrails and is then refused
             honestly ("needs the live planner"); nothing is invented.

Safe logging: one line per step on the "qaseed" logger with status, timings,
counts and error categories. The scenario text, generated values and the API
key are never logged; a last-resort filter redacts key-, email- and
number-shaped text from every record.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date

from qaseed import llm_client
from qaseed.config import ROOT, load_local_env
from qaseed.data_checks import CheckReport
from qaseed.export import REPORT_NAME, ExportBlocked, csv_zip, read_csv_zip, verify_csv_zip
from qaseed.generator import DEFAULT_SEED, GenerationError, build_and_check, coverage_rows, sample_rows
from qaseed.guardrails import clean_text, redact_sensitive
from qaseed.plan_models import Issue, ScenarioPlan

MODES = ("live", "offline")
DAY5_SCENARIOS_PATH = ROOT / "tests" / "fixtures" / "day5_ui_scenarios.json"
SAMPLE_ROWS = 5
LOGGER_NAME = "qaseed"
log = logging.getLogger(LOGGER_NAME)
_sleep = time.sleep  # retry backoff; tests replace it

# Hosted demo settings (environment variables or Streamlit secrets; all optional).
LIVE_PER_SESSION_ENV = "QASEED_LIVE_PLANS_PER_SESSION"
LIVE_PER_DAY_ENV = "QASEED_LIVE_PLANS_PER_DAY"
DEFAULT_MODE_ENV = "QASEED_DEFAULT_MODE"
DEFAULT_LIVE_PER_SESSION = 3
DEFAULT_LIVE_PER_DAY = 40
# Only these secret names are copied into the environment; anything else in st.secrets is ignored.
SECRET_NAMES = (llm_client.KEY_NAME, llm_client.MODEL_NAME, llm_client.THINKING_NAME,
                LIVE_PER_SESSION_ENV, LIVE_PER_DAY_ENV, DEFAULT_MODE_ENV)


class PipelineError(RuntimeError):
    """A step failed. str(error) is safe to show to the user; .category is for logs."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass
class PlanOutcome:
    plan: ScenarioPlan
    mode: str
    sample_id: str = ""
    elapsed_ms: int = 0


@dataclass
class GenerationResult:
    seed: int
    as_of: date
    row_counts: dict[str, int]
    coverage: list[dict]
    checks: list[dict]
    passed: bool
    passed_count: int
    total_checks: int
    samples: dict[str, list[dict]] = field(default_factory=dict)
    csv_zip: bytes | None = None  # 4 table CSVs + validation_report.json, offered only when verified
    report_json: bytes | None = None  # the same validation_report.json, offered as its own download
    zip_contents: list[dict] = field(default_factory=list)  # [{"file", "rows"}] read back from the ZIP
    export_checks: str = ""  # e.g. "23/23": the same checks re-run on the CSV files reloaded into a fresh DB
    export_note: str = ""
    elapsed_ms: int = 0

    @property
    def exportable(self) -> bool:
        return self.passed and self.csv_zip is not None


# ---- configuration ------------------------------------------------------------


def load_secrets(secrets, env=None) -> list[str]:
    """Copy known top-level secrets (e.g. Streamlit Cloud) into the environment. Returns names only, never values.

    Values already in the environment (for example from a local .env) are kept.
    """
    env = os.environ if env is None else env
    loaded = []
    for name in SECRET_NAMES:
        try:
            value = secrets[name]
        except Exception:  # missing key, or no secrets configured at all
            continue
        if isinstance(value, (str, int)) and str(value).strip() and not env.get(name):
            env[name] = str(value).strip()
            loaded.append(name)
    return loaded


def live_limits(env=None) -> dict[str, int]:
    """Live AI plans allowed per visitor session and per day (whole app). Bad values fall back to the defaults."""
    env = os.environ if env is None else env

    def number(name, default):
        try:
            return max(0, int(str(env.get(name, default)).strip()))
        except ValueError:
            return default

    return {"per_session": number(LIVE_PER_SESSION_ENV, DEFAULT_LIVE_PER_SESSION),
            "per_day": number(LIVE_PER_DAY_ENV, DEFAULT_LIVE_PER_DAY)}


def default_mode(options: list[str], env=None) -> str:
    """Planning mode a new visitor starts in: QASEED_DEFAULT_MODE if allowed, else the first option."""
    env = os.environ if env is None else env
    wanted = str(env.get(DEFAULT_MODE_ENV, "")).strip().lower()
    return wanted if wanted in options else options[0]


class DailyCounter:
    """Live AI plans used today across all visitors of one app process (thread-safe, resets at midnight)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.day: date | None = None
        self.used = 0

    def try_use(self, limit: int, today: date | None = None) -> bool:
        today = today or date.today()
        with self._lock:
            if self.day != today:
                self.day, self.used = today, 0
            if self.used >= limit:
                return False
            self.used += 1
            return True


def llm_status(env: dict[str, str] | None = None) -> dict:
    """Whether live planning is configured. Returns the model ID only, never the key."""
    if env is None:
        load_local_env()
        env = dict(os.environ)
    model = llm_client.selected_model(env)
    return {"configured": bool(llm_client.api_key(env) and model), "model": model, "provider": llm_client.PROVIDER}


def _sample_cases() -> list[dict]:
    """Day 3 planning scenarios (S01-S18) + Day 5 UI scenarios (recorded live replies)."""
    from qaseed.evaluate import load_scenarios

    return load_scenarios() + load_scenarios(DAY5_SCENARIOS_PATH)


def sample_scenarios() -> list[dict]:
    """The built-in scenarios that have offline replies: id, text, expected status."""
    return [
        {"id": c["id"], "scenario": c["scenario"], "expected": c["expected"]["status"], "category": c.get("category", "")}
        for c in _sample_cases()
    ]


def _offline_plan(sample_id: str) -> ScenarioPlan:
    """Plan a sample from its recorded model reply (labelled source=mock; no network)."""
    from qaseed.llm_client import KEY_NAME, MODEL_NAME
    from qaseed.planner import canned_reply_post, plan_scenario

    case = next(c for c in _sample_cases() if c["id"] == sample_id)
    return plan_scenario(case["scenario"], env={KEY_NAME: "mock-key-not-real", MODEL_NAME: "mock"},
                         post=canned_reply_post(case["mock_reply"]), source="mock")


def blocked_locally(scenario: str) -> bool:
    """True when QASeed's own input checks refuse the scenario, so planning it never reaches the API.

    The app uses this to avoid spending a Live AI plan on input that is refused locally.
    """
    from qaseed.planner import precheck

    return precheck(scenario) is not None


def find_sample(scenario: str) -> dict | None:
    wanted = clean_text(scenario or "")
    return next((s for s in sample_scenarios() if clean_text(s["scenario"]) == wanted), None)


# ---- steps --------------------------------------------------------------------


def plan_request(scenario: str, mode: str, env: dict[str, str] | None = None, post=None, sleep=None) -> PlanOutcome:
    """Plan a scenario. Never raises for a bad scenario: problems come back as plan issues."""
    from qaseed.planner import plan_scenario

    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    started = time.perf_counter()
    sample = find_sample(scenario) if mode == "offline" else None
    if sample:
        plan = _offline_plan(sample["id"])
    elif mode == "offline":
        # Same guardrails and checks as live, but with no key: nothing leaves the machine.
        plan = plan_scenario(scenario, env={}, post=_no_network)
        if plan.issue_codes() == ["llm_not_configured"]:
            plan = plan.model_copy(update={"issues": [Issue(
                severity="error", code="llm_not_configured",
                message="Ready-made examples mode only plans the 19 ready-made examples. Pick one, "
                        "or switch to Live AI to plan your own text.",
            )]})
    else:
        plan = plan_scenario(scenario, env=env, post=post, sleep=sleep or _sleep)
    outcome = PlanOutcome(plan=plan, mode=mode, sample_id=sample["id"] if sample else "", elapsed_ms=_ms(started))
    meta = plan.metadata
    _log(
        logging.INFO if plan.status == "valid" else logging.WARNING,
        "plan", mode=mode, sample=outcome.sample_id or "-", scenario_chars=len(clean_text(scenario or "")),
        status=plan.status, source=plan.source, issues=",".join(plan.issue_codes()) or "-",
        requests=meta.requests, retries=meta.transport_retries, error_kind=meta.error_kind or "-",
        model_ms=meta.latency_ms if meta.latency_ms is not None else "-", total_ms=outcome.elapsed_ms,
    )
    return outcome


def run_generation(plan: ScenarioPlan, seed: int = DEFAULT_SEED, as_of: date | None = None) -> GenerationResult:
    """Generate rows for a valid plan, run the independent checks and build exports if all pass."""
    as_of = as_of or date.today()
    if plan.status != "valid":
        _log(logging.WARNING, "generate", status="blocked", reason="plan_not_valid")
        raise PipelineError("plan_not_valid", f"Only a valid plan can be generated (this plan is {plan.status}).")
    started = time.perf_counter()
    try:
        dataset, report = build_and_check(plan, seed, as_of)
    except GenerationError as exc:
        _log(logging.ERROR, "generate", status="failed", category="generation_invalid", total_ms=_ms(started))
        raise PipelineError("generation_invalid", f"Generation stopped: {redact_sensitive(str(exc))}") from None
    except Exception as exc:  # unexpected: show the type only, never internals
        _log(logging.ERROR, "generate", status="failed", category="unexpected", error_type=type(exc).__name__, total_ms=_ms(started))
        raise PipelineError("unexpected", f"Generation failed unexpectedly ({type(exc).__name__}). Nothing was exported.") from None
    try:
        result = GenerationResult(
            seed=seed, as_of=as_of, row_counts=dict(dataset.row_counts),
            coverage=coverage_rows(plan, report), checks=_check_rows(report),
            passed=report.passed, passed_count=report.passed_count, total_checks=len(report.results),
            samples={t: sample_rows(dataset, t, SAMPLE_ROWS) for t in plan.generation_order},
        )
        try:
            data = csv_zip(dataset.engine, plan.generation_order, report, validation_report(plan, result))
            reloaded = verify_csv_zip(data, plan, as_of)  # verify the file itself, not just the source DB
            result.export_checks = f"{reloaded.passed_count}/{len(reloaded.results)}"
            if reloaded.passed:
                result.csv_zip = data
                result.zip_contents = [{"file": f"{t}.csv", "rows": len(rows)} for t, rows in read_csv_zip(data).items()]
                result.zip_contents.append({"file": REPORT_NAME, "rows": None})
                with zipfile.ZipFile(io.BytesIO(data)) as archive:  # byte-identical to the report inside the ZIP
                    result.report_json = archive.read(REPORT_NAME)
            else:
                result.export_note = (f"Export blocked: the CSV files failed {len(reloaded.failed())} check(s) "
                                      "after reloading them into a fresh database.")
        except ExportBlocked as exc:
            result.export_note = str(exc)
    finally:
        dataset.close()
    result.elapsed_ms = _ms(started)
    _log(
        logging.INFO if result.passed else logging.WARNING,
        "generate", status="verified" if result.passed else "checks_failed", seed=seed, as_of=as_of.isoformat(),
        rows=sum(result.row_counts.values()), checks=f"{result.passed_count}/{result.total_checks}",
        failed=",".join(sorted({c["kind"] for c in result.checks if not c["passed"]})) or "-",
        export="ready" if result.exportable else "blocked",
        export_checks=result.export_checks or "-", zip_bytes=len(result.csv_zip or b""), total_ms=result.elapsed_ms,
    )
    return result


def validation_report(plan: ScenarioPlan, result: GenerationResult) -> dict:
    """The validation report shipped inside the ZIP (synthetic data; no key, no raw model reply)."""
    return {
        "product": "QASeed",
        "export_format": "csv",
        "scenario": plan.scenario,
        "plan": {"version": plan.plan_version, "source": plan.source, "model": plan.metadata.model or None,
                 "prompt_id": plan.metadata.prompt_id or None, "assumptions": plan.assumptions},
        "seed": result.seed,
        "as_of": result.as_of.isoformat(),
        "row_counts": result.row_counts,
        "summary": {"passed": result.passed, "checks_passed": result.passed_count, "checks_total": result.total_checks},
        "requested_vs_generated": result.coverage,
        "checks": result.checks,
        "files": [f"{t}.csv" for t in plan.generation_order],
        "note": "Synthetic test data only. Emails use the reserved example.com domain. SQL export is planned post-MVP.",
    }


# ---- logging ------------------------------------------------------------------


class RedactFilter(logging.Filter):
    """Last line of defence: no key-, email- or long-number-shaped text in any qaseed log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = llm_client.public_text(redact_sensitive(message), "")
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Attach one stderr handler to the qaseed logger (idempotent across Streamlit reruns)."""
    if not any(getattr(h, "_qaseed", False) for h in log.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler._qaseed = True  # type: ignore[attr-defined]
        log.addHandler(handler)
    if not any(isinstance(f, RedactFilter) for f in log.filters):
        log.addFilter(RedactFilter())
    log.setLevel(level)
    return log


def _log(level: int, event: str, **fields) -> None:
    if not any(isinstance(f, RedactFilter) for f in log.filters):
        log.addFilter(RedactFilter())
    log.log(level, "event=%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))


# ---- helpers ------------------------------------------------------------------


def _check_rows(report: CheckReport) -> list[dict]:
    return [
        {"group": r.group, "kind": r.kind, "check": r.description, "expected": r.expected, "actual": r.actual, "passed": r.passed}
        for r in report.results
    ]


def _no_network(url, payload, api_key):  # pragma: no cover - unreachable: offline mode has no key
    raise RuntimeError("offline mode never calls the API")


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
