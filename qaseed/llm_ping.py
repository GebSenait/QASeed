"""Gemini connectivity check. This is not the scenario planner.

    python -m qaseed.llm_ping                # one tiny generateContent request
    python -m qaseed.llm_ping --list-models  # models your key can call (no generation)

Set GEMINI_API_KEY and GEMINI_MODEL in the local .env. The check never
guesses a model. A missing key or model prints status=SKIPPED and exits 2.
That is not a pass. PASS is printed only after a real API reply.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from qaseed import llm_client
from qaseed.config import load_local_env
from qaseed.llm_client import KEY_NAME, MODEL_NAME, PROVIDER, RECOMMENDED_MODEL

PROMPT = "Reply with the single word pong."
MAX_OUTPUT_TOKENS = 256
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIPPED = 2

SETUP_HELP = (
    f"Set {KEY_NAME} (from https://aistudio.google.com/apikey) and {MODEL_NAME} "
    f"(e.g. {RECOMMENDED_MODEL}) in .env, then rerun python -m qaseed.llm_ping. "
    "Run python -m qaseed.llm_ping --list-models to see the model IDs your key can use. "
    "Do not commit .env. A missing key is SKIPPED, not a pass."
)


@dataclass(frozen=True)
class CheckResult:
    status: str
    provider: str
    model: str
    result: str
    error: str
    usage: str
    instructions: str
    latency_ms: int | None = None

    def exit_code(self) -> int:
        return {"PASS": EXIT_PASS, "FAIL": EXIT_FAIL, "SKIPPED": EXIT_SKIPPED}[self.status]

    def lines(self) -> str:
        return "\n".join(
            [
                f"status={self.status}",
                f"provider={self.provider}",
                f"model={self.model}",
                f"result={self.result}",
                f"error={self.error}",
                f"usage={self.usage}",
                f"latency_ms={'' if self.latency_ms is None else self.latency_ms}",
                f"instructions={self.instructions}",
            ]
        )


def run_check(env: dict[str, str], post) -> CheckResult:
    """Return PASS, FAIL, or SKIPPED. post(url, payload, api_key) performs the HTTP call."""
    model = llm_client.selected_model(env)
    key = llm_client.api_key(env)
    if not key or not model:
        missing = [name for name, value in ((KEY_NAME, key), (MODEL_NAME, model)) if not value]
        verb = "is" if len(missing) == 1 else "are"
        return _result("SKIPPED", model=model, error=f"{' and '.join(missing)} {verb} not set", instructions=SETUP_HELP)

    payload = llm_client.build_payload("", [{"role": "user", "text": PROMPT}], MAX_OUTPUT_TOKENS, env=env)
    started = time.perf_counter()
    try:
        body = post(llm_client.generate_url(model), payload, key)
    except Exception as exc:
        return _result("FAIL", model=model, error=llm_client.public_text(str(exc), key), latency_ms=_elapsed_ms(started))
    latency = _elapsed_ms(started)
    reply = llm_client.reply_text(body)
    if not reply:
        return _result(
            "FAIL",
            model=model,
            error=f"The API returned an empty reply ({llm_client.empty_reply_reason(body)})",
            usage=llm_client.usage_text(body),
            latency_ms=latency,
        )
    return _result(
        "PASS",
        model=model,
        result=llm_client.public_text(reply, key),
        usage=llm_client.usage_text(body),
        latency_ms=latency,
    )


def run_list_models(env: dict[str, str], get) -> tuple[int, str]:
    """Return (exit code, text) listing models the key can call."""
    key = llm_client.api_key(env)
    if not key:
        return EXIT_SKIPPED, f"status=SKIPPED\nerror={KEY_NAME} is not set\ninstructions={SETUP_HELP}"
    try:
        models = llm_client.list_models(key, get)
    except Exception as exc:
        return EXIT_FAIL, f"status=FAIL\nerror={llm_client.public_text(str(exc), key)}"
    configured = llm_client.selected_model(env)
    lines = [f"models_available={len(models)} (generateContent)"]
    for model in sorted(models, key=lambda m: m["id"]):
        marks = []
        if model["id"] == RECOMMENDED_MODEL:
            marks.append("recommended")
        if model["id"] == configured:
            marks.append("configured")
        suffix = f"  <- {', '.join(marks)}" if marks else ""
        lines.append(f"{model['id']}  ({model['display_name']}){suffix}")
    if configured and configured not in {m["id"] for m in models}:
        lines.append(f"warning: {MODEL_NAME}={configured} is not in this list")
    return EXIT_PASS, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    load_local_env()
    env = dict(os.environ)
    if "--list-models" in args:
        code, text = run_list_models(env, llm_client.get_json)
        print(text)
        return code
    result = run_check(env, llm_client.post_json)
    print(result.lines())
    return result.exit_code()


def _result(
    status: str,
    model: str = "",
    result: str = "",
    error: str = "",
    usage: str = "",
    instructions: str = "",
    latency_ms: int | None = None,
) -> CheckResult:
    return CheckResult(
        status=status,
        provider=PROVIDER,
        model=model,
        result=result,
        error=error,
        usage=usage,
        instructions=instructions,
        latency_ms=latency_ms,
    )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


if __name__ == "__main__":
    sys.exit(main())
