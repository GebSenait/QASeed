"""Gemini connectivity-check behavior. These tests do not call the live API."""

import pytest

import qaseed.llm_client as llm_client
import qaseed.llm_ping as llm_ping
from qaseed.llm_client import KEY_NAME, MODEL_NAME, describe_http_error
from qaseed.llm_ping import PROMPT, run_check, run_list_models

SECRET = "AIzaTEST-not-a-real-key-0000"
CONFIGURED = {KEY_NAME: SECRET, MODEL_NAME: "gemini-3.5-flash-lite"}


def gemini_reply(text, usage=None, finish="STOP"):
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]}
    if usage:
        body["usageMetadata"] = usage
    return body


def test_missing_key_is_skipped_and_not_passed():
    called = False

    def post(url, payload, api_key):
        nonlocal called
        called = True
        return {}

    result = run_check({}, post)
    text = result.lines()
    assert result.status == "SKIPPED"
    assert result.exit_code() == 2
    assert "status=PASS" not in text
    assert KEY_NAME in result.error
    assert MODEL_NAME in result.error
    assert "aistudio.google.com/apikey" in text
    assert "--list-models" in text
    assert called is False


def test_blank_key_is_skipped():
    result = run_check({KEY_NAME: "   ", MODEL_NAME: "gemini-3.5-flash-lite"}, lambda *args: {})
    assert result.status == "SKIPPED"


def test_key_without_model_is_skipped():
    called = False

    def post(*args):
        nonlocal called
        called = True

    result = run_check({KEY_NAME: SECRET}, post)
    assert result.status == "SKIPPED"
    assert MODEL_NAME in result.error
    assert called is False


def test_successful_reply_records_usage_without_the_key():
    seen = {}

    def post(url, payload, api_key):
        seen.update(url=url, payload=payload, api_key=api_key)
        return gemini_reply("pong", {"promptTokenCount": 8, "candidatesTokenCount": 1, "totalTokenCount": 9})

    result = run_check(CONFIGURED, post)
    text = result.lines()
    assert result.status == "PASS"
    assert result.exit_code() == 0
    assert result.provider == "Google Gemini"
    assert result.result == "pong"
    assert result.usage == "promptTokenCount=8 candidatesTokenCount=1 totalTokenCount=9"
    assert isinstance(result.latency_ms, int)
    assert SECRET not in text
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert SECRET not in seen["url"]
    assert seen["api_key"] == SECRET
    assert seen["payload"]["contents"] == [{"role": "user", "parts": [{"text": PROMPT}]}]
    assert "thinkingConfig" not in seen["payload"]["generationConfig"]


def test_models_prefix_is_accepted():
    seen = {}

    def post(url, payload, api_key):
        seen["url"] = url
        return gemini_reply("pong")

    run_check({KEY_NAME: SECRET, MODEL_NAME: "models/gemini-3.5-flash-lite"}, post)
    assert seen["url"].endswith("/models/gemini-3.5-flash-lite:generateContent")


def test_thinking_level_is_optional_and_validated():
    seen = []

    def post(url, payload, api_key):
        seen.append(payload["generationConfig"])
        return gemini_reply("pong")

    run_check({**CONFIGURED, "GEMINI_THINKING_LEVEL": "low"}, post)
    run_check({**CONFIGURED, "GEMINI_THINKING_LEVEL": "bogus"}, post)
    assert seen[0]["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert "thinkingConfig" not in seen[1]


def test_thought_parts_are_not_the_answer():
    body = {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": "pong"}]}}]}
    assert llm_client.reply_text(body) == "pong"


def test_http_error_is_fail_and_redacts_the_key():
    def post(url, payload, api_key):
        raise RuntimeError(f"HTTP 400: API key not valid {api_key}")

    result = run_check(CONFIGURED, post)
    assert result.status == "FAIL"
    assert result.exit_code() == 1
    assert SECRET not in result.lines()
    assert "[redacted]" in result.error


def test_empty_reply_is_fail_with_reason():
    blocked = {"promptFeedback": {"blockReason": "SAFETY"}}
    result = run_check(CONFIGURED, lambda *args: blocked)
    assert result.status == "FAIL"
    assert "prompt blocked (SAFETY)" in result.error
    truncated = run_check(CONFIGURED, lambda *args: gemini_reply("", finish="MAX_TOKENS"))
    assert "finishReason=MAX_TOKENS" in truncated.error


def test_main_skips_without_calling_the_network(monkeypatch, capsys):
    monkeypatch.setattr(llm_ping, "load_local_env", lambda: None)
    monkeypatch.delenv(KEY_NAME, raising=False)
    monkeypatch.delenv(MODEL_NAME, raising=False)

    def explode(*args):
        raise AssertionError("network was called")

    monkeypatch.setattr(llm_client, "post_json", explode)
    monkeypatch.setattr(llm_client, "get_json", explode)
    assert llm_ping.main([]) == 2
    assert "status=SKIPPED" in capsys.readouterr().out
    assert llm_ping.main(["--list-models"]) == 2
    assert "status=SKIPPED" in capsys.readouterr().out


def test_list_models_filters_and_marks_configured():
    pages = {
        "": {"models": [
            {"name": "models/gemini-3.5-flash-lite", "displayName": "Gemini 3.5 Flash-Lite", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/text-embedding-x", "displayName": "Embedding", "supportedGenerationMethods": ["embedContent"]},
        ], "nextPageToken": "p2"},
        "p2": {"models": [
            {"name": "models/gemini-3.6-flash", "displayName": "Gemini 3.6 Flash", "supportedGenerationMethods": ["generateContent"]},
        ]},
    }
    urls = []

    def get(url, key):
        urls.append(url)
        token = url.split("pageToken=")[1] if "pageToken=" in url else ""
        return pages[token]

    code, text = run_list_models({**CONFIGURED, MODEL_NAME: "gemini-3.6-flash"}, get)
    assert code == 0
    assert "models_available=2" in text
    assert "gemini-3.5-flash-lite  (Gemini 3.5 Flash-Lite)  <- recommended" in text
    assert "gemini-3.6-flash  (Gemini 3.6 Flash)  <- configured" in text
    assert "embedding" not in text.lower()
    assert len(urls) == 2
    assert all(SECRET not in url for url in urls)


def test_list_models_warns_about_unknown_configured_model():
    code, text = run_list_models({**CONFIGURED, MODEL_NAME: "gemini-made-up"}, lambda url, key: {"models": []})
    assert code == 0
    assert "warning: GEMINI_MODEL=gemini-made-up is not in this list" in text


def test_list_models_error_is_fail_without_key():
    def get(url, key):
        raise RuntimeError(f"authentication failed: {key}")

    code, text = run_list_models(CONFIGURED, get)
    assert code == 1
    assert SECRET not in text


@pytest.mark.parametrize(
    "status,raw,prefix",
    [
        (400, '{"error":{"code":400,"message":"API key not valid. Please pass a valid API key.","status":"INVALID_ARGUMENT","details":[{"reason":"API_KEY_INVALID"}]}}', "authentication failed"),
        (401, '{"error":{"code":"authentication","message":"The API key is missing, invalid, or expired."}}', "authentication failed"),
        (403, '{"error":{"code":403,"message":"denied","status":"PERMISSION_DENIED"}}', "permission denied"),
        (404, '{"error":{"code":404,"message":"models/x is not found","status":"NOT_FOUND"}}', "invalid model"),
        (429, '{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}', "quota or rate limit"),
        (503, '{"error":{"code":503,"message":"overloaded","status":"UNAVAILABLE"}}', "Gemini service error"),
        (400, "not json", "HTTP 400"),
    ],
)
def test_http_errors_are_labelled(status, raw, prefix):
    assert describe_http_error(status, raw, SECRET).startswith(prefix)


def test_error_text_redacts_key_shaped_values():
    raw = '{"error":{"message":"bad key AIzaSyOTHER-key-value-abcdefghij in ?key=AIzaSyOTHER"}}'
    message = describe_http_error(400, raw, SECRET)
    assert "AIzaSyOTHER" not in message
    assert "[redacted]" in message


def test_network_error_hides_the_key(monkeypatch):
    def raise_url(*args, **_kwargs):
        raise llm_client.urllib.error.URLError("no route")

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", raise_url)
    with pytest.raises(RuntimeError) as caught:
        llm_client.post_json(llm_client.generate_url("gemini-3.5-flash-lite"), {}, SECRET)
    assert str(caught.value) == "network error: could not reach the Gemini API"


def test_key_is_sent_in_header_not_url(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"candidates": []}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return FakeResponse()

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    llm_client.post_json(llm_client.generate_url("gemini-3.5-flash-lite"), {}, SECRET)
    assert SECRET not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == SECRET
