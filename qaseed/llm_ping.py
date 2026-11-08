"""One-shot OpenAI connectivity check. This is not the scenario planner.

Set OPENAI_MODEL to an ID this account can call. The check does not
guess a model. The request asks for one harmless word, with reasoning
effort set to none.

A missing key or model prints status=SKIPPED and exits 2. That is not a pass.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from qaseed.config import load_local_env

PROVIDER = "OpenAI"
API_URL = "https://api.openai.com/v1/chat/completions"
KEY_NAME = "OPENAI_API_KEY"
MODEL_NAME = "OPENAI_MODEL"
PROMPT = "Reply with the single word pong."
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIPPED = 2

_SECRET_RE = re.compile(r"(Bearer\s+)\S+|sk-[A-Za-z0-9_\-]+")


@dataclass(frozen=True)
class CheckResult:
    status: str
    provider: str
    model: str
    result: str
    error: str
    usage: str
    instructions: str

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
                f"instructions={self.instructions}",
            ]
        )


def selected_model(env: dict[str, str]) -> str:
    """Return OPENAI_MODEL. An empty value means no model is configured."""
    return env.get(MODEL_NAME, "").strip()


def run_check(env: dict[str, str], post) -> CheckResult:
    """Return PASS, FAIL, or SKIPPED. post(url, payload, api_key) performs the HTTP call."""
    model = selected_model(env)
    api_key = env.get(KEY_NAME, "").strip()
    if not api_key or not model:
        missing = []
        if not api_key:
            missing.append(KEY_NAME)
        if not model:
            missing.append(MODEL_NAME)
        verb = "is" if len(missing) == 1 else "are"
        return _result(
            "SKIPPED",
            model=model,
            error=f"{' and '.join(missing)} {verb} not set",
            instructions=(
                "Set OPENAI_API_KEY and OPENAI_MODEL in .env, then rerun "
                "python -m qaseed.llm_ping. Use a model ID from this account's "
                "model list. Do not commit .env. A missing key is SKIPPED, not a pass."
            ),
        )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_completion_tokens": 32,
        "reasoning_effort": "none",
    }
    try:
        body = post(API_URL, payload, api_key)
    except Exception as exc:
        return _result("FAIL", model=model, error=_public_text(str(exc), api_key))
    reply = _reply_text(body)
    if not reply:
        return _result(
            "FAIL",
            model=model,
            error="The API returned an empty reply",
            usage=_usage_text(body),
        )
    return _result(
        "PASS",
        model=model,
        result=_public_text(reply, api_key),
        usage=_usage_text(body),
    )


def main() -> int:
    load_local_env()
    result = run_check(dict(os.environ), _post_json)
    print(result.lines())
    return result.exit_code()


def _post_json(url: str, payload: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(describe_http_error(exc.code, raw, api_key)) from None
    except TimeoutError:
        raise RuntimeError("network error: request timed out") from None
    except urllib.error.URLError:
        raise RuntimeError("network error: could not reach the OpenAI API") from None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("OpenAI returned a response that was not JSON") from None
    if not isinstance(body, dict):
        raise RuntimeError("OpenAI returned an unexpected response")
    return body


def describe_http_error(status: int, raw: str, api_key: str) -> str:
    """Turn an HTTP error body into a short label without the API key."""
    code, message = _error_parts(raw, api_key)
    return f"{_error_kind(status, code, message)}: {message}"


def _result(
    status: str,
    model: str = "",
    result: str = "",
    error: str = "",
    usage: str = "",
    instructions: str = "",
) -> CheckResult:
    return CheckResult(
        status=status,
        provider=PROVIDER,
        model=model,
        result=result,
        error=error,
        usage=usage,
        instructions=instructions,
    )


def _reply_text(body: dict) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return ""
    return " ".join(content.split())


def _usage_text(body: dict) -> str:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return ""
    parts = []
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(name)
        if isinstance(value, int):
            parts.append(f"{name}={value}")
    return " ".join(parts)


def _error_parts(raw: str, api_key: str) -> tuple[str, str]:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = None
    code = ""
    message = ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        if isinstance(error.get("code"), str):
            code = error["code"]
        elif isinstance(error.get("type"), str):
            code = error["type"]
        if isinstance(error.get("message"), str):
            message = error["message"]
    if not message:
        message = "request failed"
    return code, _public_text(message, api_key)


def _error_kind(status: int, code: str, message: str) -> str:
    blob = f"{code} {message}".lower()
    if status in (401, 403) or any(word in blob for word in ("invalid_api_key", "authentication", "unauthorized")):
        return "authentication failed"
    if status == 404 or any(word in blob for word in ("model_not_found", "does not exist", "invalid model")):
        return "invalid model"
    if status == 429 or any(word in blob for word in ("insufficient_quota", "rate_limit", "quota", "rate limit")):
        return "quota or rate limit"
    return f"HTTP {status}"


def _public_text(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "[redacted]")
    text = _SECRET_RE.sub(lambda match: match.group(1) + "[redacted]" if match.group(1) else "[redacted]", text)
    return " ".join(text.split())[:300]


if __name__ == "__main__":
    sys.exit(main())
