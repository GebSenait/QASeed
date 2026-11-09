"""Gemini REST client helpers (qaseed/llm_client.py). No live API calls."""

from qaseed import llm_client
from qaseed.llm_client import (
    API_BASE,
    build_payload,
    empty_reply_reason,
    finish_reason,
    generate_url,
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
