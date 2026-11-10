"""Minimal Google Gemini API client (REST, standard library only).

QASeed's runtime LLM is Google Gemini on the free tier of Google AI Studio.
Configuration comes only from the local, gitignored .env:

    GEMINI_API_KEY         key from https://aistudio.google.com/apikey
    GEMINI_MODEL           model ID, e.g. gemini-3.5-flash-lite
    GEMINI_THINKING_LEVEL  optional: minimal | low | medium | high

The key is sent in the x-goog-api-key header (never in the URL) and is
redacted from every error message this module produces.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

PROVIDER = "Google Gemini"
API_BASE = "https://generativelanguage.googleapis.com/v1beta"
KEY_NAME = "GEMINI_API_KEY"
MODEL_NAME = "GEMINI_MODEL"
THINKING_NAME = "GEMINI_THINKING_LEVEL"
RECOMMENDED_MODEL = "gemini-3.5-flash-lite"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
TIMEOUT_SECONDS = 60

# Error kinds. Retryable ones are worth another request after a pause;
# the rest fail fast because repeating the same request cannot succeed.
RETRYABLE_KINDS = frozenset({"rate_limit", "server", "timeout", "network", "bad_response"})
ERROR_KINDS = RETRYABLE_KINDS | {"auth", "permission", "invalid_model", "bad_request", "blocked"}
MAX_RETRY_AFTER_SECONDS = 60.0


class LLMError(RuntimeError):
    """A Gemini call failed. str(error) is safe to show: it never contains the key."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "bad_response"
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


_SECRET_RE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}|(x-goog-api-key:?\s*)\S+|([?&]key=)[^&\s]+", re.IGNORECASE)


def selected_model(env: dict[str, str]) -> str:
    """Return GEMINI_MODEL without a leading 'models/'. Empty means not configured."""
    value = env.get(MODEL_NAME, "").strip()
    return value[len("models/"):] if value.startswith("models/") else value


def api_key(env: dict[str, str]) -> str:
    return env.get(KEY_NAME, "").strip()


def generate_url(model: str) -> str:
    return f"{API_BASE}/models/{urllib.parse.quote(model, safe='-._')}:generateContent"


def build_payload(
    system: str,
    turns: list[dict],
    max_output_tokens: int,
    json_output: bool = False,
    env: dict[str, str] | None = None,
) -> dict:
    """Build a generateContent body.

    turns: [{"role": "user" | "model", "text": "..."}] in conversation order.
    """
    config: dict = {"maxOutputTokens": max_output_tokens}
    if json_output:
        config["responseMimeType"] = "application/json"
    level = (env or {}).get(THINKING_NAME, "").strip().lower()
    if level in THINKING_LEVELS:
        config["thinkingConfig"] = {"thinkingLevel": level.upper()}
    payload: dict = {
        "contents": [{"role": turn["role"], "parts": [{"text": turn["text"]}]} for turn in turns],
        "generationConfig": config,
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    return payload


def post_json(url: str, payload: dict, key: str) -> dict:
    """POST JSON to the Gemini API. Errors are raised as RuntimeError without the key."""
    return _send(urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    ), key)


def get_json(url: str, key: str) -> dict:
    return _send(urllib.request.Request(url, method="GET", headers={"x-goog-api-key": key}), key)


def list_models(key: str, get=None) -> list[dict]:
    """Models this key can call with generateContent. Lists metadata only; generates nothing."""
    get = get or get_json
    models: list[dict] = []
    token = ""
    for _ in range(20):  # page guard
        query = {"pageSize": "1000"}
        if token:
            query["pageToken"] = token
        body = get(f"{API_BASE}/models?{urllib.parse.urlencode(query)}", key)
        for model in body.get("models", []) if isinstance(body, dict) else []:
            if "generateContent" in model.get("supportedGenerationMethods", []):
                name = str(model.get("name", ""))
                models.append({
                    "id": name[len("models/"):] if name.startswith("models/") else name,
                    "display_name": str(model.get("displayName", "")),
                })
        token = body.get("nextPageToken", "") if isinstance(body, dict) else ""
        if not token:
            break
    return models


def reply_text(body: dict) -> str:
    """Visible answer text of the first candidate (thought parts are skipped)."""
    candidates = body.get("candidates") if isinstance(body, dict) else None
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return ""
    content = candidates[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""
    texts = [p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought")]
    return "".join(texts).strip()


def empty_reply_reason(body: dict) -> str:
    """Why a reply has no text (blocked prompt, token limit, safety), if the body says."""
    if not isinstance(body, dict):
        return "unexpected response"
    feedback = body.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        return f"prompt blocked ({feedback['blockReason']})"
    candidates = body.get("candidates")
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        reason = candidates[0].get("finishReason")
        if reason and reason != "STOP":
            return f"finishReason={reason}"
    return "no candidates returned"


BLOCK_FINISH_REASONS = ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION")


def blocked_reason(body: dict) -> str:
    """Non-empty when the provider refused the prompt or the answer for safety/policy reasons."""
    if not isinstance(body, dict):
        return ""
    feedback = body.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        return f"prompt blocked: {feedback['blockReason']}"
    reason = finish_reason(body)
    return f"finishReason={reason}" if reason in BLOCK_FINISH_REASONS else ""


def finish_reason(body: dict) -> str:
    candidates = body.get("candidates") if isinstance(body, dict) else None
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        return str(candidates[0].get("finishReason", ""))
    return ""


def usage_text(body: dict) -> str:
    usage = body.get("usageMetadata") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return ""
    parts = []
    for name in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount"):
        value = usage.get(name)
        if isinstance(value, int):
            parts.append(f"{name}={value}")
    return " ".join(parts)


def describe_http_error(status: int, raw: str, key: str) -> str:
    """Turn an HTTP error body into a short labelled message without the key."""
    code, message = _error_parts(raw, key)
    return f"{_error_label(_error_kind(status, code, message), status)}: {message}"


def http_error(status: int, raw: str, key: str, retry_after_header: str | None = None) -> LLMError:
    """Classify an HTTP error response as an LLMError (kind, safe message, retry hint)."""
    code, message = _error_parts(raw, key)
    kind = _error_kind(status, code, message)
    retry_after = _parse_seconds(retry_after_header) if retry_after_header else _retry_delay(raw)
    return LLMError(kind, f"{_error_label(kind, status)}: {message}", retry_after)


def public_text(text: str, secret: str) -> str:
    """Redact the key (and anything key-shaped) and shorten."""
    if secret:
        text = text.replace(secret, "[redacted]")

    def _sub(match: re.Match) -> str:
        prefix = match.group(1) or match.group(2) or ""
        return prefix + "[redacted]"

    text = _SECRET_RE.sub(_sub, text)
    return " ".join(text.split())[:300]


def _send(request: urllib.request.Request, key: str) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        header = exc.headers.get("Retry-After") if exc.headers else None
        raise http_error(exc.code, raw, key, header) from None
    except (TimeoutError, socket.timeout):
        raise LLMError("timeout", "network error: request timed out") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise LLMError("timeout", "network error: request timed out") from None
        raise LLMError("network", "network error: could not reach the Gemini API") from None
    except (http.client.HTTPException, ConnectionError, OSError):
        raise LLMError("network", "network error: the connection to the Gemini API was interrupted") from None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise LLMError("bad_response", "Gemini returned a response that was not JSON") from None
    if not isinstance(body, dict):
        raise LLMError("bad_response", "Gemini returned an unexpected response")
    return body


def _error_parts(raw: str, key: str) -> tuple[str, str]:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = None
    codes: list[str] = []
    message = ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        for field in ("status", "code"):
            if isinstance(error.get(field), str):
                codes.append(error[field])
        for detail in error.get("details", []) if isinstance(error.get("details"), list) else []:
            if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
                codes.append(detail["reason"])
        if isinstance(error.get("message"), str):
            message = error["message"]
    return " ".join(codes), public_text(message or "request failed", key)


def _error_kind(status: int, code: str, message: str) -> str:
    blob = f"{code} {message}".lower()
    if status in (401,) or any(w in blob for w in ("api_key_invalid", "api key not valid", "authentication", "unauthenticated")):
        return "auth"
    if status == 403 or "permission_denied" in blob:
        return "permission"
    if status == 404 or any(w in blob for w in ("not_found", "model_not_found", "is not found")):
        return "invalid_model"
    if status == 429 or any(w in blob for w in ("resource_exhausted", "quota", "rate_limit", "too_many_requests")):
        return "rate_limit"
    if status >= 500 or status == 408:
        return "server"
    return "bad_request"


def _error_label(kind: str, status: int) -> str:
    return {
        "auth": "authentication failed (check GEMINI_API_KEY)",
        "permission": "permission denied (key or project cannot use this model)",
        "invalid_model": "invalid model (check GEMINI_MODEL with --list-models)",
        "rate_limit": "quota or rate limit (free-tier limit reached; wait and retry)",
        "server": f"Gemini service error (HTTP {status}; retry later)",
    }.get(kind, f"HTTP {status}")


def _parse_seconds(text: str) -> float | None:
    """'17', '17s' or '1.5s' -> seconds (capped). Anything else -> None."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*s?\s*", text or "")
    return min(float(match.group(1)), MAX_RETRY_AFTER_SECONDS) if match else None


def _retry_delay(raw: str) -> float | None:
    """Read google.rpc.RetryInfo.retryDelay from an error body, if present."""
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    details = body.get("error", {}).get("details", []) if isinstance(body, dict) and isinstance(body.get("error"), dict) else []
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and isinstance(detail.get("retryDelay"), str):
            return _parse_seconds(detail["retryDelay"])
    return None
