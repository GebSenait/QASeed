"""Drive the real Streamlit app (app.py) end to end, headless, and verify the CSV download.

    python scripts/day5_ui_e2e.py                    # offline: the recorded reply for the payment scenario (no API call)
    python scripts/day5_ui_e2e.py --live --confirm   # live Gemini: ONE plan (1 request normally, at most 4)
    python scripts/day5_ui_e2e.py --live --confirm --out reports/day5/ui_e2e_payment_live.txt

Steps, exactly as a user clicks them: type the scenario -> Plan scenario ->
Generate & validate data -> take the ZIP the download button serves. The ZIP
is then checked independently of the app: four CSV files, record counts,
unique IDs, every payment -> invoice -> subscription -> customer link, the
requested payment statuses, and the payment/invoice story (a succeeded
payment's invoice is paid, a failed payment's invoice is unpaid).

Live mode needs GEMINI_API_KEY and GEMINI_MODEL in .env and --confirm, so a
free-tier request is never sent by accident. The key is never printed; the
raw model reply is printed so the run can be reviewed.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAYMENT_SCENARIO = (
    "Generate 12 subscription payments: 8 successful and 4 failed. Each payment must have a valid invoice, "
    "subscription, and customer. Every failed invoice must remain unpaid, and every successful invoice must be paid."
)
UNPAID = ("open", "overdue")


def verify_zip(data: bytes, expected_payments: dict[str, int] | None = None) -> list[tuple[str, bool, str]]:
    """Independent checks on the downloaded ZIP: [(check, passed, detail)]."""
    from qaseed.export import read_csv_zip

    t = read_csv_zip(data)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    check("ZIP holds the 4 table CSVs + validation_report.json",
          names == ["customers.csv", "subscriptions.csv", "invoices.csv", "payments.csv", "validation_report.json"], ", ".join(names))
    for table, key in (("customers", "customer_id"), ("subscriptions", "subscription_id"), ("invoices", "invoice_id"), ("payments", "payment_id")):
        ids = [r[key] for r in t.get(table, [])]
        check(f"{table}: {len(ids)} rows, unique {key}", ids and len(set(ids)) == len(ids))
    customers = {r["customer_id"] for r in t["customers"]}
    subs = {r["subscription_id"]: r for r in t["subscriptions"]}
    invoices = {r["invoice_id"]: r for r in t["invoices"]}
    chain = [
        p["invoice_id"] in invoices
        and invoices[p["invoice_id"]]["subscription_id"] in subs
        and subs[invoices[p["invoice_id"]]["subscription_id"]]["customer_id"] in customers
        for p in t["payments"]
    ]
    check("every payment -> invoice -> subscription -> customer link resolves", all(chain), f"{sum(chain)}/{len(chain)}")
    statuses = Counter(p["status"] for p in t["payments"])
    if expected_payments:
        check("payment statuses as requested", all(statuses[k] == v for k, v in expected_payments.items()), dict(statuses).__repr__())
    story = [(p["status"], invoices[p["invoice_id"]]["status"]) for p in t["payments"] if p["invoice_id"] in invoices]
    check("every succeeded payment's invoice is paid", all(i == "paid" for s, i in story if s == "succeeded"),
          repr(Counter(i for s, i in story if s == "succeeded")))
    check("every failed payment's invoice is unpaid (open/overdue)", all(i in UNPAID for s, i in story if s == "failed"),
          repr(Counter(i for s, i in story if s == "failed")))
    report = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("validation_report.json"))
    check("validation report says every check passed", report["summary"]["passed"],
          f"{report['summary']['checks_passed']}/{report['summary']['checks_total']}")
    return results


def run(scenario: str, live: bool, seed: int = 42, as_of: date | None = None) -> dict:
    """Click through app.py with AppTest. Returns what the UI showed plus the ZIP checks."""
    from streamlit.testing.v1 import AppTest

    import qaseed.llm_client as llm_client

    replies: list[str] = []
    real_post = llm_client.post_json

    def recording_post(url, payload, key):
        body = real_post(url, payload, key)
        replies.append(llm_client.reply_text(body))
        return body

    llm_client.post_json = recording_post
    try:
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
        mode = "live" if live else "offline"
        if live and not any(o.startswith("Live AI") for o in app.radio(key="mode").options):  # options are labels
            return {"status": "SKIPPED", "reason": "live planner not configured (GEMINI_API_KEY / GEMINI_MODEL)"}
        app.radio(key="mode").set_value(mode).run()
        if as_of:
            app.date_input[0].set_value(as_of).run()
        app.number_input[0].set_value(seed).run()
        app.text_area(key="scenario").input(scenario).run()
        app.button(key="plan_btn").click().run()
        outcome = app.session_state["plan_outcome"]
        shown = {
            "status": "OK",
            "mode": mode,
            "plan_status": outcome.plan.status if outcome else "none",
            "plan_source": outcome.plan.source if outcome else "none",
            "issues": [f"{i.code}: {i.message}" for i in outcome.plan.issues] if outcome else [],
            "requests": outcome.plan.metadata.requests if outcome else 0,
            "model_latency_ms": outcome.plan.metadata.latency_ms if outcome else None,
            "usage": outcome.plan.metadata.usage if outcome else "",
            "model_replies": replies,
            "tables": [(t.table, t.count, t.count_source) for t in outcome.plan.tables] if outcome else [],
            "conditions": [(c.table, c.column, c.value, c.count) for c in outcome.plan.conditions] if outcome else [],
            "assumptions": list(outcome.plan.assumptions) if outcome else [],
            "ui_errors": [e.value for e in app.error],
            "exceptions": [e.value for e in app.exception],
        }
        if "generate_btn" not in [b.key for b in app.button]:
            shown["generated"] = False
            return shown
        app.button(key="generate_btn").click().run()
        result = app.session_state["gen_result"]
        downloads = app.get("download_button")
        shown.update({
            "generated": result is not None,
            "metrics": {m.label: m.value for m in app.metric},
            "coverage": result.coverage if result else [],
            "failed_checks": [c["check"] for c in result.checks if not c["passed"]] if result else [],
            "download_buttons": [d.proto.label for d in downloads],
            "zip_checks": verify_zip(result.csv_zip, {"succeeded": 8, "failed": 4} if scenario == PAYMENT_SCENARIO else None)
            if result and result.exportable else [],
            "ui_errors": [e.value for e in app.error],
            "exceptions": [e.value for e in app.exception],
        })
        return shown
    finally:
        llm_client.post_json = real_post


def passed(shown: dict) -> bool:
    return (shown.get("status") == "OK" and shown.get("plan_status") == "valid" and shown.get("generated")
            and not shown.get("failed_checks") and not shown.get("exceptions")
            and bool(shown.get("zip_checks")) and all(ok for _, ok, _ in shown["zip_checks"]))


def render(shown: dict, scenario: str) -> str:
    lines = [f"scenario: {scenario}"]
    if shown.get("status") == "SKIPPED":
        return "\n".join(lines + [f"status=SKIPPED reason={shown['reason']}", "No API call was made. This is not a pass."])
    lines += [
        f"mode={shown['mode']} plan_status={shown['plan_status']} source={shown['plan_source']} requests={shown['requests']} "
        f"model_latency_ms={shown['model_latency_ms']} usage={shown['usage'] or '-'}",
        "model reply (raw):" if shown["model_replies"] else "model reply: none (offline recorded reply or no request)",
        *[f"  {reply}" for reply in shown["model_replies"]],
        "plan tables: " + ", ".join(f"{t}={c} ({s})" for t, c, s in shown["tables"]),
        "plan conditions: " + ", ".join(f"{t}.{col}={v} x{n}" for t, col, v, n in shown["conditions"]),
        *[f"assumption: {a}" for a in shown["assumptions"]],
        *[f"issue: {i}" for i in shown["issues"]],
    ]
    if shown.get("generated"):
        lines.append("ui metrics: " + ", ".join(f"{k}={v}" for k, v in shown["metrics"].items()))
        lines.append("| requested vs generated | requested | generated | verified |")
        lines.append("|---|---|---|---|")
        lines += [f"| {r['what']} | {r['requested']} | {r['generated']} | {'PASS' if r['verified'] else 'FAIL'} |" for r in shown["coverage"]]
        lines.append(f"download buttons: {shown['download_buttons']}")
        lines.append("downloaded ZIP checks (independent of the app):")
        lines += [f"  {'PASS' if ok else 'FAIL'}  {name}{f'  [{detail}]' if detail else ''}" for name, ok, detail in shown["zip_checks"]]
    lines += [f"ui error: {e}" for e in shown.get("ui_errors", [])] + [f"exception: {e}" for e in shown.get("exceptions", [])]
    lines.append(f"result={'PASS' if passed(shown) else 'FAIL'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default=PAYMENT_SCENARIO)
    parser.add_argument("--live", action="store_true", help="plan with live Gemini (uses free-tier quota)")
    parser.add_argument("--confirm", action="store_true", help="required with --live: confirms one live plan request")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--as-of", default="", help="YYYY-MM-DD (default today)")
    parser.add_argument("--out", default="", help="also write the result text to this file")
    args = parser.parse_args(argv)
    if args.live and not args.confirm:
        print("Live mode sends 1-4 Gemini requests. Add --confirm to run it. No request was sent.")
        return 2
    if not args.live:
        from qaseed.pipeline import find_sample

        if find_sample(args.scenario) is None:
            print("Offline mode only runs built-in samples (with a recorded reply). Use --live --confirm for other text.")
            return 2
    import os

    if not args.live:
        os.environ["GEMINI_API_KEY"] = ""  # offline: make the live planner unavailable, so no request is possible
    shown = run(args.scenario, args.live, args.seed, date.fromisoformat(args.as_of) if args.as_of else None)
    text = render(shown, args.scenario)
    print(text)
    if args.out:
        out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"saved={out.name}")
    if shown.get("status") == "SKIPPED":
        return 2
    return 0 if passed(shown) else 1


if __name__ == "__main__":
    sys.exit(main())
