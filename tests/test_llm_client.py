"""Gemini REST client helpers (qaseed/llm_client.py). No live API calls."""

import io
import json
import urllib.error

import pytest

from qaseed import llm_client
from qaseed.llm_client import (
    API_BASE,
    LLMError,
    blocked_reason,
    build_payload,
    empty_reply_reason,
    finish_reason,
    generate_url,
    http_error,
    list_models,
    public_text,
    reply_text,
    selected_model,
    usage_text,
)


def test_generate_url():
    assert generate_url("gemini-3.5-flash-lite") == f"{API_BASE}/models/gemini-3.5-flash-lite:generateContent"


def test_selected_model_strips_prefix_and_space():
    assert selected_model({"GEMINI_MODEL": " models/gemini-3.6-flash "}) == "gemini-3.6-flash"
    assert selected_model({}) == ""


def test_build_payload_shape():
    payload = build_payload("sys", [{"role": "user", "text": "hi"}, {"role": "model", "text": "ok"}], 100, json_output=True)
    assert payload == {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}, {"role": "model", "parts": [{"text": "ok"}]}],
        "generationConfig": {"maxOutputTokens": 100, "responseMimeType": "application/json"},
        "systemInstruction": {"parts": [{"text": "sys"}]},
    }
    plain = build_payload("", [{"role": "user", "text": "hi"}], 10)
    assert "systemInstruction" not in plain
    assert "responseMimeType" not in plain["generationConfig"]


def test_reply_helpers_on_odd_bodies():
    for body in ({}, {"candidates": []}, {"candidates": [{}]}, {"candidates": [{"content": {"parts": "x"}}]}, "not a dict"):
        assert reply_text(body) == ""
        assert usage_text(body) == ""
    assert empty_reply_reason({}) == "no candidates returned"
    assert finish_reason({"candidates": [{"finishReason": "SAFETY"}]}) == "SAFETY"
    assert empty_reply_reason({"candidates": [{"finishReason": "SAFETY"}]}) == "finishReason=SAFETY"


def test_usage_text_order():
    body = {"usageMetadata": {"totalTokenCount": 9, "promptTokenCount": 5, "thoughtsTokenCount": 2, "candidatesTokenCount": 2}}
    assert usage_text(body) == "promptTokenCount=5 candidatesTokenCount=2 thoughtsTokenCount=2 totalTokenCount=9"


def test_public_text_redacts_header_and_query_forms():
    text = public_text("x-goog-api-key: abc123 and url?key=abc456&x=1", "")
    assert "abc123" not in text and "abc456" not in text
    assert len(public_text("y" * 1000, "")) == 300


def test_list_models_stops_on_page_guard():
    calls = []

    def get(url, key):
        calls.append(url)
        return {"models": [], "nextPageToken": "again"}

    assert list_models("k", get) == []
    assert len(calls) == 20


def test_timeout_is_reported_as_network_error(monkeypatch):
    def slow(*args, **_kwargs):
        raise TimeoutError()

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", slow)
    try:
        llm_client.get_json(f"{API_BASE}/models", "k")
    except RuntimeError as exc:
        assert str(exc) == "network error: request timed out"
    else:
        raise AssertionError("expected RuntimeError")


# ---- Day 4: typed errors and retry hints ------------------------------------


@pytest.mark.parametrize("status,body,kind,retryable", [
    (401, {"error": {"status": "UNAUTHENTICATED", "message": "bad"}}, "auth", False),
    (400, {"error": {"message": "API key not valid. Please pass a valid API key.", "details": [{"reason": "API_KEY_INVALID"}]}}, "auth", False),
    (403, {"error": {"status": "PERMISSION_DENIED", "message": "no"}}, "permission", False),
    (404, {"error": {"status": "NOT_FOUND", "message": "models/x is not found"}}, "invalid_model", False),
    (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}, "rate_limit", True),
    (503, {"error": {"status": "UNAVAILABLE", "message": "overloaded"}}, "server", True),
    (500, "<html>oops</html>", "server", True),
    (400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad field"}}, "bad_request", False),
])
def test_http_errors_are_classified(status, body, kind, retryable):
    raw = body if isinstance(body, str) else json.dumps(body)
    error = http_error(status, raw, "k")
    assert isinstance(error, LLMError) and isinstance(error, RuntimeError)
    assert error.kind == kind and error.retryable is retryable


def test_retry_after_comes_from_header_or_retry_info_and_is_capped():
    body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "message": "q", "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}]}})
    assert http_error(429, body, "k").retry_after == 17.0
    assert http_error(429, body, "k", retry_after_header="3").retry_after == 3.0
    assert http_error(429, body, "k", retry_after_header="9999").retry_after == llm_client.MAX_RETRY_AFTER_SECONDS
    assert http_error(429, "{}", "k", retry_after_header="Wed, 21 Oct 2026 07:28:00 GMT").retry_after is None


def test_http_error_message_never_contains_the_key():
    key = "AIzaTEST-not-a-real-key-0000"
    error = http_error(400, json.dumps({"error": {"message": f"API key not valid: {key}"}}), key)
    assert key not in str(error) and "[redacted]" in str(error)


def _raise(exc):
    def urlopen(*args, **kwargs):
        raise exc
    return urlopen


@pytest.mark.parametrize("exc,kind", [
    (TimeoutError(), "timeout"),
    (urllib.error.URLError(TimeoutError()), "timeout"),
    (urllib.error.URLError("dns failure"), "network"),
    (ConnectionResetError(), "network"),
    (llm_client.http.client.RemoteDisconnected("closed"), "network"),
])
def test_transport_failures_are_typed_and_retryable(monkeypatch, exc, kind):
    monkeypatch.setattr(llm_client.urllib.request, "urlopen", _raise(exc))
    with pytest.raises(LLMError) as caught:
        llm_client.post_json(f"{API_BASE}/models/m:generateContent", {}, "k")
    assert caught.value.kind == kind and caught.value.retryable


def test_http_error_from_urlopen_carries_retry_after(monkeypatch):
    headers = {"Retry-After": "5"}
    exc = urllib.error.HTTPError("u", 429, "Too Many Requests", headers, io.BytesIO(b'{"error": {"status": "RESOURCE_EXHAUSTED", "message": "q"}}'))
    monkeypatch.setattr(llm_client.urllib.request, "urlopen", _raise(exc))
    with pytest.raises(LLMError) as caught:
        llm_client.post_json(f"{API_BASE}/models/m:generateContent", {}, "k")
    assert caught.value.kind == "rate_limit" and caught.value.retry_after == 5.0


def test_non_json_success_body_is_a_retryable_bad_response(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", lambda *a, **k: Response(b"<html>gateway</html>"))
    with pytest.raises(LLMError) as caught:
        llm_client.post_json(f"{API_BASE}/models/m:generateContent", {}, "k")
    assert caught.value.kind == "bad_response" and caught.value.retryable


def test_blocked_reason():
    assert blocked_reason({"promptFeedback": {"blockReason": "SAFETY"}}) == "prompt blocked: SAFETY"
    assert blocked_reason({"candidates": [{"finishReason": "PROHIBITED_CONTENT"}]}) == "finishReason=PROHIBITED_CONTENT"
    assert blocked_reason({"candidates": [{"finishReason": "MAX_TOKENS"}]}) == ""
    assert blocked_reason({"candidates": [{"finishReason": "STOP"}]}) == ""
    assert blocked_reason([]) == ""


def test_unknown_error_kind_falls_back_to_bad_response():
    assert LLMError("weird", "m").kind == "bad_response"
