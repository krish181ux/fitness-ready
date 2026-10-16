"""Run the eval set headlessly, without n8n (SPEC §9).

    python -m evals.run_local --prompt v1                  # one run, 30 cases
    python -m evals.run_local --prompt v2 --runs 2          # two runs of v2
    python -m evals.run_local --prompt v1 --limit 2 --dry-run

**Why this exists.** One run per prompt is not an experiment: the agent is
non-deterministic, so a 0.33 shift in a judge mean over a single pair of runs is
noise until the spread is known. A canvas run cannot be triggered headlessly --
`n8n execute --id` refuses an Evaluation Trigger with "Missing node to start
execution", and n8n's public API has no run endpoint -- so repeated runs have to
come from somewhere other than the canvas.

**What it reproduces, and what it cannot.** The same dataset, the same five
tools against the same tools server, the same system prompt body (extracted from
`prompts/system_v{1,2}.md` by `scripts/set_prompt.py`'s own rule), the same
agent and judge models through the same LiteLLM proxy, and the same scoring --
`evals/metrics.py` is imported, never reimplemented. What differs is the agent
loop: n8n's AI Agent node, not this file, decides how tool results are fed back
and what the conversation looks like on the second turn. That makes a local run
a *stand-in*, not a replacement, which is why every run it stores is tagged
`harness='local'` and the summary prints that column. Compare local against
local; compare local against canvas only to ask whether the stand-in is valid.

Health data leaves the machine only in the model calls this harness makes by
design: tool results are fetched from localhost and posted to the proxy, which
is exactly what the canvas run does too.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from evals import metrics, store

REPO = Path(__file__).resolve().parent.parent

LITELLM_URL = "http://localhost:4000/v1"
TOOLS_URL = "http://localhost:8000"
AGENT_MODEL = "claude-sonnet-5"
JUDGE_MODEL = "gpt-5.1"

# The agent gets this many model calls per case. n8n's AI Agent node defaults to
# 10; five tools and one answer needs two or three, and a case that has not
# answered by six is looping rather than working.
MAX_TURNS = 6
# Stop the whole thing if LiteLLM's reported spend passes this. A run costs
# $0.50-1.00, so anything near $5 means something is retrying in a loop.
DEFAULT_BUDGET_USD = 5.0
REQUEST_TIMEOUT = 180

# `set_prompt.prompt_body` emits `{{ $json.as_of_date }}`, or, since the switch
# moved onto the canvas, `{{ $json.as_of_date || '2026-09-13' }}` — a chat run
# has no row to take the date from. Either shape resolves to the run's date here.
AS_OF_EXPRESSION = re.compile(r"\{\{\s*\$json\.as_of_date\s*(?:\|\|[^}]*?)?\}\}")

DATE_PARAM = "First day of the range, inclusive, as YYYY-MM-DD"
END_DATE_PARAM = ("Last day of the range, inclusive, as YYYY-MM-DD. "
                  "Ranges span at most 366 days.")

# One entry per SPEC §7 tool. The names are what `tool_correct` scores, and the
# parameter descriptions are the $fromAI descriptions from n8n/README.md
# verbatim -- the model routes on these strings, so a paraphrase here would make
# the local run a different experiment from the canvas one.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_daily_metrics",
        "description": "Daily wellness rows for a date range: resting HR, steps, "
                       "intensity minutes, stress, body battery.",
        "parameters": {
            "start_date": {"type": "string", "description": DATE_PARAM},
            "end_date": {"type": "string", "description": END_DATE_PARAM},
        },
        "required": ["start_date", "end_date"],
    },
    {
        "name": "get_sleep",
        "description": "Nightly sleep rows, including the validation flag.",
        "parameters": {
            "start_date": {"type": "string", "description": DATE_PARAM},
            "end_date": {"type": "string", "description": END_DATE_PARAM},
        },
        "required": ["start_date", "end_date"],
    },
    {
        "name": "get_hrv_trend",
        "description": "Nightly HRV with 7- and 60-day means and the baseline band.",
        "parameters": {
            "days": {"type": "number", "description": "How many nights back to look, 1 to 366. "
                                                      "Use 30 unless the question asks for a "
                                                      "different window."},
            "as_of": {"type": "string", "description": "Last night of the window, as YYYY-MM-DD. "
                                                       "Use the current as-of date."},
        },
        "required": ["days", "as_of"],
    },
    {
        "name": "list_activities",
        "description": "Activities in a date range, flagged hard/easy.",
        "parameters": {
            "start_date": {"type": "string", "description": DATE_PARAM},
            "end_date": {"type": "string", "description": END_DATE_PARAM},
            "type": {"type": "string", "description": "Optional activity type filter: running, "
                                                      "walking, badminton, strength_training, "
                                                      "cycling, swimming, yoga, indoor_cardio. "
                                                      "Pass an empty string for all types."},
        },
        "required": ["start_date", "end_date"],
    },
    {
        "name": "get_readiness_inputs",
        "description": "Every input the readiness rubric needs, for one date.",
        "parameters": {
            "date": {"type": "string", "description": "The day to assess, as YYYY-MM-DD. Use the "
                                                      "current as-of date unless the user names "
                                                      "another day."},
        },
        "required": ["date"],
    },
]


def openai_tools(version: str = "v1") -> list[dict[str, Any]]:
    """The tools this prompt version is offered, as OpenAI-style function tools."""
    specs = TOOL_SPECS + (JOURNAL_TOOL_SPECS if version in JOURNAL_PROMPTS else [])
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": {
                    "type": "object",
                    "properties": spec["parameters"],
                    "required": spec["required"],
                },
            },
        }
        for spec in specs
    ]


TOOL_NAMES = {spec["name"] for spec in TOOL_SPECS}


# ------------------------------------------------------------------ the prompt

def _set_prompt_module():
    """Import `scripts/set_prompt.py` so the prompt-body rule lives in one place."""
    path = REPO / "scripts" / "set_prompt.py"
    spec = importlib.util.spec_from_file_location("_set_prompt", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def system_message(version: str, as_of_date: str) -> str:
    """The prompt body with n8n's `{{ $json.as_of_date }}` expression resolved."""
    body = _set_prompt_module().prompt_body(version)
    return AS_OF_EXPRESSION.sub(as_of_date, body)


# ------------------------------------------------------------------- transport

def load_env(path: Path | None = None) -> None:
    """Read `.env` into the environment. Values already set win, and nothing is printed."""
    path = path or REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def http_json(url: str, *, payload: dict[str, Any] | None = None,
              headers: dict[str, str] | None = None,
              timeout: int = REQUEST_TIMEOUT) -> tuple[int, Any, dict[str, str]]:
    """GET or POST JSON. A non-2xx body is returned, not raised.

    The tool nodes on the canvas run with "Never Error" on for exactly this
    reason: the server's `detail` names the corrected call, and a bare
    "status 422" makes the agent retry the identical request (n8n/README.md).
    """
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null"), dict(response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            body = json.loads(raw or b"null")
        except json.JSONDecodeError:
            body = {"detail": raw.decode(errors="replace")[:2000]}
        return error.code, body, dict(error.headers or {})


@dataclass
class Spend:
    """LiteLLM reports a per-call cost in a response header; this adds them up."""
    usd: float = 0.0
    calls: int = 0
    budget: float = DEFAULT_BUDGET_USD

    def add(self, headers: dict[str, str]) -> float:
        cost = 0.0
        for key, value in headers.items():
            if key.lower() == "x-litellm-response-cost":
                try:
                    cost = float(value)
                except (TypeError, ValueError):
                    cost = 0.0
        self.usd += cost
        self.calls += 1
        return cost

    def exceeded(self) -> bool:
        return self.usd > self.budget


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Chat:
    """The LiteLLM proxy, spoken to as an OpenAI chat-completions endpoint."""
    api_key: str
    base_url: str = LITELLM_URL
    session_id: str = ""
    spend: Spend = field(default_factory=Spend)
    transport: Callable[..., tuple[int, Any, dict[str, str]]] = http_json

    def complete(self, model: str, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if self.spend.exceeded():
            raise BudgetExceeded(f"spend ${self.spend.usd:.2f} is over the ${self.spend.budget:.2f} budget")
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            payload["tools"] = tools
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.session_id:
            # Same header the canvas credential sets, so a local run groups into
            # one Langfuse session too (n8n/README.md).
            headers["x-litellm-session-id"] = self.session_id
        status, body, response_headers = self.transport(
            f"{self.base_url}/chat/completions", payload=payload, headers=headers
        )
        self.spend.add(response_headers)
        if status >= 300 or not isinstance(body, dict) or not body.get("choices"):
            raise RuntimeError(f"proxy returned {status}: {json.dumps(body)[:500]}")
        return body["choices"][0]["message"]


# ----------------------------------------------------------------- the tools

# The journal tools (SPEC §7.1), offered only to prompts that know about them:
# adding tools changes routing, so a v1-v3 run must see exactly the five it had.
# Descriptions match the n8n log_note / get_journal nodes.
JOURNAL_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "log_note",
        "description": "Record something the watch cannot see, when the user TELLS you it (not "
                       "when they ask a question): session effort, soreness, energy, stress, "
                       "alcohol, caffeine, illness, injury, or a short note. One call per fact. "
                       "Returns the stored entry, or status=already_logged if it was saved before.",
        "parameters": {
            "date": {"type": "string", "description": "The day it happened, YYYY-MM-DD. "
                                                      "\"Last night\" is the day before the as-of date."},
            "kind": {"type": "string", "description": "One of: rpe (session effort 1-10), soreness "
                                                      "(0-10), energy (1-10), stress (0-10), alcohol "
                                                      "(standard drinks), caffeine (cups), illness, "
                                                      "injury, note"},
            "value": {"type": "string", "description": "The number on that kind's scale, exactly as "
                                                       "the user said it. Empty for illness, injury "
                                                       "and note."},
            "note": {"type": "string", "description": "A few of the user's own words, e.g. which "
                                                      "knee. Required for injury and note."},
        },
        "required": ["date", "kind"],
    },
    {
        "name": "get_journal",
        "description": "Journal entries the user logged earlier (effort, soreness, alcohol, "
                       "illness, ...) for a date range, optionally one kind. An empty result "
                       "means nothing was logged, not that nothing happened.",
        "parameters": {
            "start_date": {"type": "string", "description": "First day, YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "Last day, YYYY-MM-DD. Usually the as-of date."},
            "kind": {"type": "string", "description": "Optional: one kind to filter on, or empty for all"},
        },
        "required": ["start_date", "end_date"],
    },
]
JOURNAL_PROMPTS = ("v4",)
TOOL_NAMES |= {spec["name"] for spec in JOURNAL_TOOL_SPECS}
WRITE_TOOLS = {"log_note"}


def call_tool(name: str, arguments: dict[str, Any], *, base_url: str = TOOLS_URL,
              transport: Callable[..., tuple[int, Any, dict[str, str]]] = http_json) -> str:
    """GET one tool endpoint with the model's arguments and return its JSON as text."""
    if name not in TOOL_NAMES:
        return json.dumps({"detail": f"No tool named {name}. Available: {sorted(TOOL_NAMES)}."})
    params = {}
    for key, value in (arguments or {}).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue  # an empty `type` means every type; the server wants it absent
        params[key] = value if isinstance(value, str) else json.dumps(value).strip('"')
    url = f"{base_url}/tools/{name}"
    if name in WRITE_TOOLS:
        _status, body, _headers = transport(url, payload=dict(arguments or {}))
        return json.dumps(body)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    _status, body, _headers = transport(url)
    return json.dumps(body)


# ------------------------------------------------------------------ one case

@dataclass
class CaseRun:
    answer: str
    tools_called: list[str]
    tool_results: list[dict[str, Any]]
    latency_s: float
    turns: int
    cost_usd: float


def run_case(case: dict[str, str], chat: Chat, *, model: str = AGENT_MODEL,
             max_turns: int = MAX_TURNS,
             tool_caller: Callable[[str, dict[str, Any]], str] | None = None) -> CaseRun:
    """The agent loop: model, tools, model, until it answers in prose."""
    tool_caller = tool_caller or (lambda name, args: call_tool(name, args))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_message(case["_prompt_version"], case["as_of_date"])},
        {"role": "user", "content": case["question"]},
    ]
    tools_called: list[str] = []
    tool_results: list[dict[str, Any]] = []
    started = time.monotonic()
    before = chat.spend.usd
    answer = ""
    turns = 0
    for turns in range(1, max_turns + 1):
        message = chat.complete(model, messages, openai_tools(case.get("_prompt_version", "v1")))
        calls = message.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            **({"tool_calls": calls} if calls else {}),
        })
        if not calls:
            answer = message.get("content") or ""
            break
        for call in calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = tool_caller(name, arguments)
            tools_called.append(name)
            tool_results.append({"tool": name, "arguments": arguments, "result": result})
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "name": name, "content": result})
    else:
        # Out of turns with tool calls still pending: ask once, tools withheld.
        message = chat.complete(model, messages + [
            {"role": "user", "content": "Answer now with what you have."}], None)
        answer = message.get("content") or ""
    return CaseRun(answer=answer, tools_called=tools_called, tool_results=tool_results,
                   latency_s=time.monotonic() - started, turns=turns,
                   cost_usd=chat.spend.usd - before)


# --------------------------------------------------------------------- judge

JUDGE_PROMPT = REPO / "evals" / "judge_prompt.md"


def judge_payload(case: dict[str, str], run: CaseRun) -> str:
    return json.dumps({
        "question": case["question"],
        "as_of_date": case["as_of_date"],
        "tool_results": [
            {"tool": r["tool"], "arguments": r["arguments"],
             "result": json.loads(r["result"]) if r["result"].startswith(("{", "[")) else r["result"]}
            for r in run.tool_results
        ],
        "answer": run.answer,
        "rubric_notes": case.get("rubric_notes", ""),
    }, indent=2)[:120_000]


def parse_judge(content: str) -> tuple[int | None, float | None, str]:
    """(judge_score, judge_score_raw, failed) out of the judge's JSON reply.

    The raw value is kept and the score clamped to the 1-5 scale, which is what
    the canvas `Parse judge` node does: a judge that returns 0 must not abandon
    the run (SPEC, 17 Sep 2026).
    """
    match = re.search(r"\{.*\}", content or "", re.DOTALL)
    if not match:
        return None, None, "judge returned no JSON"
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None, "judge returned unparseable JSON"
    raw = parsed.get("judge_score")
    raw_value = float(raw) if isinstance(raw, (int, float)) else None
    score = max(1, min(5, int(round(raw_value)))) if raw_value is not None else None
    failed = parsed.get("failed")
    if isinstance(failed, list):
        failed = "; ".join(str(f) for f in failed)
    return score, raw_value, str(failed or "")[:1000]


def judge_case(case: dict[str, str], run: CaseRun, chat: Chat, *,
               model: str = JUDGE_MODEL) -> tuple[int | None, float | None, str]:
    rubric = JUDGE_PROMPT.read_text()
    message = chat.complete(model, [
        {"role": "system", "content": rubric},
        {"role": "user", "content": judge_payload(case, run)},
    ], None)
    return parse_judge(message.get("content") or "")


# -------------------------------------------------------------------- scoring

def score_case(case: dict[str, str], run: CaseRun,
               judged: tuple[int | None, float | None, str]) -> store.CaseResult:
    """Every number here comes from `evals/metrics.py`; nothing is reimplemented."""
    bucket = case["bucket"]
    expected = case.get("expected_answer", "")
    judge_score, judge_raw, judge_failed = judged
    return store.CaseResult(
        case_id=case["id"],
        bucket=bucket,
        ran_at=datetime.now(),
        question=case["question"],
        expected_tool=case.get("expected_tool", ""),
        expected_answer=expected,
        tools_called=run.tools_called,
        answer=run.answer,
        tool_correct=metrics.tool_correct(case.get("expected_tool", ""), run.tools_called)
        if bucket in ("A", "J") else None,
        value_match=metrics.value_match(expected, run.answer) if bucket == "A" else None,
        # Needs the tool arguments, which the store does not keep, so it is
        # scored here, once; `rescore` leaves it as stored.
        logged=metrics.logged(expected, run.tool_results, run.answer) if bucket == "J" else None,
        judge_score=judge_score,
        judge_score_raw=judge_raw,
        judge_failed=judge_failed,
        latency_s=run.latency_s,
    )


def read_dataset(path: Path | None = None, *, journal: bool = False) -> list[dict[str, str]]:
    import csv

    paths = [path or REPO / "evals" / "dataset.csv"]
    if journal:
        paths.append(REPO / "evals" / "dataset_journal.csv")
    rows: list[dict[str, str]] = []
    for each in paths:
        with each.open(newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def fixture_problem(tools_url: str, cases: list[dict[str, str]],
                    transport: Callable[..., tuple[int, Any, dict[str, str]]] = http_json) -> str | None:
    """Why this tools server can't be scored against the answer key, or None.

    The answer key is computed from the fixture, whose last day is every case's
    as-of date. A server on real data has later days, and scoring against it
    gives confident nonsense, so the run refuses to start.
    """
    status, body, _ = transport(f"{tools_url}/health")
    if status >= 300 or not isinstance(body, dict):
        return f"tools server at {tools_url} is not answering /health"
    pinned = {case["as_of_date"] for case in cases}
    if body.get("latest_date") not in pinned:
        return (
            f"tools server at {tools_url} ends on {body.get('latest_date')}, not the fixture's "
            f"{', '.join(sorted(pinned))}: it is serving real data. Start one on the fixture "
            "(WEARABLE_DB=wearable.duckdb uvicorn tools.server:app --port 8001) and pass "
            "--tools-url http://localhost:8001."
        )
    return None


# ------------------------------------------------------------------ one run

def run_once(version: str, cases: list[dict[str, str]], chat: Chat, *,
             agent_model: str = AGENT_MODEL, judge_model: str = JUDGE_MODEL,
             tool_caller: Callable[[str, dict[str, Any]], str] | None = None,
             judge: Callable[..., tuple[int | None, float | None, str]] | None = None,
             echo: Callable[[str], None] = print,
             tools_url: str = TOOLS_URL) -> list[store.CaseResult]:
    judge = judge or judge_case
    tool_caller = tool_caller or (lambda name, args: call_tool(name, args, base_url=tools_url))
    results: list[store.CaseResult] = []
    for index, row in enumerate(cases, start=1):
        case = dict(row, _prompt_version=version)
        run = run_case(case, chat, model=agent_model, tool_caller=tool_caller)
        judged: tuple[int | None, float | None, str] = (None, None, "")
        if case["bucket"] == "B":
            judged = judge(case, run, chat, model=judge_model)
        result = score_case(case, run, judged)
        results.append(result)
        echo(f"  [{index:>2}/{len(cases)}] {result.case_id} {result.bucket} "
             f"{run.latency_s:5.1f}s {metrics.judge_length_words(run.answer):>4}w "
             f"tools={','.join(run.tools_called) or '-'} "
             f"{_verdict(result)} ${chat.spend.usd:.3f} cum")
    return results


def _verdict(result: store.CaseResult) -> str:
    if result.bucket == "A":
        return f"tool={result.tool_correct} value={result.value_match}"
    if result.bucket == "B":
        return f"judge={result.judge_score}"
    return f"contained={metrics.contained(result.expected_answer, result.answer)}"


def langfuse_session_cost(session_id: str) -> float | None:
    """Cross-check LiteLLM's per-call header against Langfuse's own totals."""
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (public and secret):
        return None
    auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
    url = ("http://localhost:3000/api/public/traces?limit=100&sessionId="
           + urllib.parse.quote(session_id))
    try:
        status, body, _ = http_json(url, headers={"Authorization": f"Basic {auth}"}, timeout=30)
    except Exception:
        return None
    if status >= 300 or not isinstance(body, dict):
        return None
    return sum(float(t.get("totalCost") or 0) for t in body.get("data", []))


# ------------------------------------------------------------------------ cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt", choices=("v1", "v2", "v3", "v4"), required=True)
    parser.add_argument("--tools-url", default=TOOLS_URL,
                        help="a tools server on the fixture, e.g. http://localhost:8001 while "
                             "the main one serves real data")
    parser.add_argument("--runs", type=int, default=1, help="how many times to run the set")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases (smoke test)")
    parser.add_argument("--agent-model", default=AGENT_MODEL)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD,
                        help="stop when LiteLLM's reported spend passes this, in USD")
    parser.add_argument("--db", default=None)
    parser.add_argument("--no-store", action="store_true", help="run but write nothing")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the resolved prompt and the tool schema, call nothing")
    args = parser.parse_args(argv)

    load_env()
    cases = read_dataset(journal=args.prompt in JOURNAL_PROMPTS)[: args.limit]

    if args.dry_run:
        print(system_message(args.prompt, cases[0]["as_of_date"]))
        print("\n--- tools ---")
        print(json.dumps(openai_tools(args.prompt), indent=2))
        print(f"\n{len(cases)} cases, {args.runs} run(s)")
        return 0

    key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not key:
        print("LITELLM_MASTER_KEY is not set (put it in .env)", file=sys.stderr)
        return 2

    problem = fixture_problem(args.tools_url, cases)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    spend = Spend(budget=args.budget)
    expected = store.dataset_expected()
    exit_code = 0
    sessions: list[str] = []
    for attempt in range(1, args.runs + 1):
        started = datetime.now()
        session = f"local-{args.prompt}-{started:%Y%m%dT%H%M%S}"
        sessions.append(session)
        chat = Chat(api_key=key, session_id=session, spend=spend)
        print(f"\n{args.prompt} run {attempt}/{args.runs}  {len(cases)} cases  "
              f"agent={args.agent_model} judge={args.judge_model} session={session}")
        try:
            results = run_once(args.prompt, cases, chat, agent_model=args.agent_model,
                               judge_model=args.judge_model, tools_url=args.tools_url)
        except BudgetExceeded as error:
            print(f"\nSTOPPED: {error}", file=sys.stderr)
            return 3
        run_id = f"{args.prompt}-local-{started:%Y-%m-%dT%H:%M}"
        if not args.no_store:
            with store.connect(args.db) as conn:
                written = store.write_run(conn, run_id, results, prompt_version=args.prompt,
                                          agent_model=args.agent_model, judge_model=args.judge_model,
                                          workflow_id="", expected=expected, harness="local")
            print(f"{run_id}: {written} cases stored")
        judged = [r.judge_score for r in results if r.judge_score is not None]
        if judged:
            print(f"{run_id}: judge mean {statistics.mean(judged):.2f} over {len(judged)} cases")
        print(f"spend so far: ${spend.usd:.2f} over {spend.calls} calls")
        if spend.exceeded():
            print("budget exceeded; stopping before the next run", file=sys.stderr)
            exit_code = 3
            break

    langfuse = [c for c in (langfuse_session_cost(s) for s in sessions) if c]
    if langfuse:
        print(f"langfuse reports ${sum(langfuse):.2f} across {len(langfuse)} session(s); "
              f"LiteLLM headers reported ${spend.usd:.2f}")
    if not args.no_store:
        store.ensure_schema(args.db)
        with store.connect(args.db, read_only=True) as conn:
            store._print(conn.execute(store.SUMMARY_SQL))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
