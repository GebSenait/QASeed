"""Connectivity-check behavior. These tests do not call the live API."""

import pytest

from qaseed.llm_ping import KEY_NAME, MODEL_NAME, PROMPT, describe_http_error, run_check
import qaseed.llm_ping as llm_ping

CONFIGURED = {KEY_NAME: "sk-test-secret-value", MODEL_NAME: "gpt-4o-mini"}


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
    assert "status=SKIPPED" in text
    assert "status=PASS" not in text
    assert KEY_NAME in result.error
    assert MODEL_NAME in result.error
    assert "python -m qaseed.llm_ping" in text
    assert called is False


def test_blank_key_is_skipped():
    result = run_check({KEY_NAME: "   "}, lambda *args: {})
    assert result.status == "SKIPPED"
    assert result.exit_code() == 2


def test_key_without_model_is_skipped_and_not_passed():
    called = False

    def post(url, payload, api_key):
        nonlocal called
        called = True
        return {}

    result = run_check({KEY_NAME: "sk-test-secret-value"}, post)
    assert result.status == "SKIPPED"
    assert result.exit_code() == 2
    assert "status=PASS" not in result.lines()
    assert MODEL_NAME in result.error
    assert called is False


def test_successful_reply_records_usage_without_the_key():
    secret = "sk-test-secret-value"
    seen = {}

    def post(url, payload, api_key):
        seen["url"] = url
        seen["payload"] = payload
        seen["api_key"] = api_key
        return {
            "choices": [{"message": {"content": "pong"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        }

    result = run_check({KEY_NAME: secret, MODEL_NAME: "gpt-4o-mini"}, post)
    text = result.lines()
    assert result.status == "PASS"
    assert result.exit_code() == 0
    assert result.result == "pong"
    assert result.usage == "prompt_tokens=10 completion_tokens=1 total_tokens=11"
    assert secret not in text
    assert "Authorization" not in text
    assert seen["payload"]["model"] == "gpt-4o-mini"
    assert seen["payload"]["reasoning_effort"] == "none"
    assert seen["payload"]["messages"] == [{"role": "user", "content": PROMPT}]
    assert seen["api_key"] == secret


def test_http_error_is_fail_and_redacts_the_key():
    secret = "sk-test-secret-value"

    def post(url, payload, api_key):
        raise RuntimeError(f"HTTP 401: invalid key {api_key}")

    result = run_check({KEY_NAME: secret, MODEL_NAME: "gpt-4o-mini"}, post)
    text = result.lines()
    assert result.status == "FAIL"
    assert result.exit_code() == 1
    assert "status=PASS" not in text
    assert secret not in text
    assert "[redacted]" in result.error


def test_empty_reply_is_fail():
    def post(url, payload, api_key):
        return {"choices": [{"message": {"content": "  "}}], "usage": {"total_tokens": 4}}

    result = run_check(CONFIGURED, post)
    assert result.status == "FAIL"
    assert result.usage == "total_tokens=4"


def test_main_skips_without_calling_the_network(monkeypatch, capsys):
    monkeypatch.setattr(llm_ping, "load_local_env", lambda: None)
    monkeypatch.delenv(KEY_NAME, raising=False)
    monkeypatch.delenv(MODEL_NAME, raising=False)

    def explode(*args):
        raise AssertionError("network was called")

    monkeypatch.setattr(llm_ping, "_post_json", explode)
    code = llm_ping.main()
    output = capsys.readouterr().out
    assert code == 2
    assert "status=SKIPPED" in output
    assert "status=PASS" not in output


def test_http_errors_name_auth_model_and_quota_without_the_key():
    secret = "sk-test-secret-value"
    auth = describe_http_error(401, '{"error":{"message":"bad key sk-test-secret-value","code":"invalid_api_key"}}', secret)
    model = describe_http_error(404, '{"error":{"message":"The model does not exist","code":"model_not_found"}}', secret)
    quota = describe_http_error(429, '{"error":{"message":"exceeded quota","code":"insufficient_quota"}}', secret)
    assert auth.startswith("authentication failed")
    assert model.startswith("invalid model")
    assert quota.startswith("quota or rate limit")
    assert secret not in auth + model + quota


def test_network_error_hides_the_key(monkeypatch):
    def raise_url(*args, **kwargs):
        raise llm_ping.urllib.error.URLError("no route")

    monkeypatch.setattr(llm_ping.urllib.request, "urlopen", raise_url)
    with pytest.raises(RuntimeError) as caught:
        llm_ping._post_json(llm_ping.API_URL, {}, "sk-test-secret-value")
    message = str(caught.value)
    assert message == "network error: could not reach the OpenAI API"
    assert "sk-test-secret-value" not in message
