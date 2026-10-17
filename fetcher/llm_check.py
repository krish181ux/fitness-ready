"""After a sync, say whether every model behind LiteLLM still answers.

    python -m fetcher.llm_check        # prints one line per problem, exit 1
                                       # or nothing, exit 0

`scripts/daily_sync.sh` turns the output into a macOS notification. The
Anthropic and OpenAI keys expired on the same day in October 2026 and nobody
noticed for days: the fallback chain quietly moved chat onto Gemini's free
tier, which then ran out after a few questions.

Each model is called once with fallbacks disabled, so a pass means that
provider answered, not that Gemini covered for it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from db import REPO_ROOT

LITELLM_URL = "http://localhost:4000"
# Every model in litellm/config.yaml: the agent and its fallbacks.
MODELS = ("claude-sonnet-5", "gpt-5.1", "gemini-3.5-flash")
TIMEOUT_S = 60

# (status, body) for one chat call; injectable so tests need no proxy.
Transport = Callable[[str, dict, str], tuple[int, str]]


def master_key(env_file: Path = REPO_ROOT / ".env") -> str:
    if os.environ.get("LITELLM_MASTER_KEY"):
        return os.environ["LITELLM_MASTER_KEY"]
    for line in env_file.read_text().splitlines():
        if line.startswith("LITELLM_MASTER_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("LITELLM_MASTER_KEY is not set and not in .env")


def http_post(url: str, payload: dict, key: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace")


def model_problem(model: str, status: int, body: str) -> str | None:
    """One line naming the fix, or None when the model answered."""
    if status == 200:
        return None
    text = body.lower()
    if status in (401, 403) or "authentication" in text or "api key" in text or "api_key" in text:
        provider = {"claude": "Anthropic", "gpt": "OpenAI", "gemini": "Gemini"}.get(
            model.split("-")[0], model)
        return (f"{model}: {provider} key rejected. Create a new key, put it in .env, "
                "then run: docker compose up -d litellm")
    if status == 429 or "rate limit" in text or "quota" in text:
        return f"{model}: rate-limited or out of quota (HTTP {status})"
    return f"{model}: HTTP {status}"


def problems(base_url: str = LITELLM_URL, key: str | None = None,
             transport: Transport = http_post,
             models: tuple[str, ...] = MODELS) -> list[str]:
    key = key if key is not None else master_key()
    found = []
    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word ok"}],
            "max_tokens": 200,
            "disable_fallbacks": True,
        }
        try:
            status, body = transport(f"{base_url}/v1/chat/completions", payload, key)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            # The proxy itself is down: one line says it all.
            return [f"LiteLLM is not reachable at {base_url} ({err}). "
                    "Is Docker running? docker compose up -d"]
        problem = model_problem(model, status, body)
        if problem:
            found.append(problem)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=LITELLM_URL)
    args = parser.parse_args(argv)
    found = problems(args.url)
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
