"""The model check run after each sync (fetcher/llm_check.py)."""

from __future__ import annotations

import json
import urllib.error

from fetcher.llm_check import model_problem, problems

OK = (200, json.dumps({"choices": [{"message": {"content": "ok"}}]}))


def fake(responses):
    calls = []

    def transport(url, payload, key):
        calls.append(payload)
        return responses[payload["model"]]
    transport.calls = calls
    return transport


def test_all_models_answering_is_silent():
    transport = fake({"a": OK, "b": OK})
    assert problems(key="k", transport=transport, models=("a", "b")) == []


def test_every_call_disables_fallbacks():
    """Otherwise a dead Claude key passes because Gemini answered for it."""
    transport = fake({"claude-sonnet-5": OK})
    problems(key="k", transport=transport, models=("claude-sonnet-5",))
    assert transport.calls[0]["disable_fallbacks"] is True


def test_a_rejected_key_names_the_provider_and_the_fix():
    body = '{"error": {"message": "litellm.AuthenticationError: invalid x-api-key"}}'
    line = model_problem("claude-sonnet-5", 401, body)
    assert "Anthropic key rejected" in line and "docker compose up -d litellm" in line
    assert "OpenAI key rejected" in model_problem("gpt-5.1", 400, "Incorrect API key provided")


def test_a_rate_limit_is_not_reported_as_a_bad_key():
    line = model_problem("gemini-3.5-flash", 429, "RESOURCE_EXHAUSTED: quota exceeded")
    assert "rate-limited" in line and "key" not in line


def test_only_the_failing_model_is_reported():
    transport = fake({"claude-sonnet-5": OK, "gpt-5.1": (401, "Incorrect API key")})
    found = problems(key="k", transport=transport, models=("claude-sonnet-5", "gpt-5.1"))
    assert len(found) == 1 and found[0].startswith("gpt-5.1")


def test_a_proxy_that_is_down_is_one_line_not_four():
    def down(url, payload, key):
        raise urllib.error.URLError("Connection refused")
    found = problems(key="k", transport=down)
    assert len(found) == 1 and "not reachable" in found[0]
