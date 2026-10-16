#!/usr/bin/env python3
"""Switch the live agent between prompt versions — from the canvas, or here.

    python3 scripts/set_prompt.py --install   # put every prompt on the canvas (once)
    python3 scripts/set_prompt.py --show      # which one is live right now
    python3 scripts/set_prompt.py v1          # the naive baseline
    python3 scripts/set_prompt.py v2          # rubric in the prompt
    python3 scripts/set_prompt.py v3          # verdict computed by the tool
    python3 scripts/set_prompt.py v4          # v3 plus the journal

`--install` moves every prompt body into the **Edit Fields** node as
`prompt_v1`, `prompt_v2`, ..., adds a `prompt_version` field, and points the AI
Agent's System Message at:

    {{ $json['prompt_' + $json.prompt_version] }}

After that, switching is one word in one field on the canvas, and this script
edits the same field, so both routes agree. The eval workflow calls this workflow, so an
eval run picks up whichever version the field names.

Each prompt body is stored as an n8n expression, with `{{ $json.as_of_date }}`
rewritten to `{{ $json.as_of_date || $now.format('yyyy-MM-dd') }}`: an eval run
passes the row's date in, and a hand-driven chat run has none, so it falls back to
today. The fallback must be repeated here because `$json` in the Set node is its
input, not the `as_of_date` the same node writes. The repo-notes under each
prompt are stripped — they name the failures v1 is expected to make.

Reload the n8n tab after switching from here: an open tab saves the old value
back over it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = "workflow_agent"
CONTAINER_PATH = "/tmp/agent_prompt_switch.json"
SET_NODE = "Edit Fields"
FALLBACK_DATE = "$now.format('yyyy-MM-dd')"
VERSIONS = ("v1", "v2", "v3", "v4")
SELECTOR = "={{ $json['prompt_' + $json.prompt_version] }}"
# The two-way selector from before v3; still recognised, replaced on any write.
OLD_SELECTOR = "={{ $json.prompt_version === 'v1' ? $json.prompt_v1 : $json.prompt_v2 }}"
# Checked in order: each version keeps the rules before it, so the newest
# marker must be tried first.
MARKERS = {
    "v4": "a question is not a report",
    "v3": "Report it; do not recompute it",
    "v1": "Be helpful and encouraging",
    "v2": "One action, one sentence",
}


def compose(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *args], cwd=REPO, capture_output=True, text=True, check=True
    )
    return result.stdout


def live_workflows() -> list[dict]:
    raw = compose("exec", "-T", "n8n", "n8n", "export:workflow", "--all")
    return json.loads(raw[raw.index("[") :])


def load(name: str = WORKFLOW) -> dict:
    workflow = next((w for w in live_workflows() if w["name"] == name), None)
    if workflow is None:
        raise SystemExit(f"{name} is not in this n8n instance")
    return workflow


def node_named(workflow: dict, name: str) -> dict:
    for node in workflow["nodes"]:
        if node["name"] == name:
            return node
    raise SystemExit(f"no node called {name!r} in {workflow['name']}")


def agent_node(workflow: dict) -> dict:
    for node in workflow["nodes"]:
        if node["type"].endswith(".agent"):
            return node
    raise SystemExit(f"no AI Agent node in {workflow['name']}")


def assignments(set_node: dict) -> list[dict]:
    return set_node["parameters"].setdefault("assignments", {}).setdefault("assignments", [])


def field(set_node: dict, name: str) -> dict | None:
    return next((a for a in assignments(set_node) if a.get("name") == name), None)


def put_field(set_node: dict, name: str, value: str) -> None:
    existing = field(set_node, name)
    if existing is None:
        assignments(set_node).append(
            {"id": str(uuid.uuid4()), "name": name, "value": value, "type": "string"}
        )
    else:
        existing["value"] = value


def prompt_body(version: str) -> str:
    """The prompt as n8n should hold it: no repo notes, as_of_date resolvable."""
    text = (REPO / "prompts" / f"system_{version}.md").read_text()
    # Everything after a horizontal rule is repo commentary, not prompt.
    text = text.split("\n---\n")[0]
    lines = [line for line in text.splitlines() if not line.startswith("# Wearable Coach")]
    body = "\n".join(lines).strip()
    if version == "v1":
        # v1's trailing paragraph lists the failures it is expected to make.
        cut = body.find("This prompt is the eval baseline")
        if cut != -1:
            body = body[:cut].strip()
    if not body:
        raise SystemExit(f"prompts/system_{version}.md looks empty")
    return body.replace(
        "{{ $json.as_of_date }}", "{{ $json.as_of_date || " + FALLBACK_DATE + " }}"
    )


def installed(workflow: dict) -> bool:
    agent = agent_node(workflow)
    return agent["parameters"]["options"].get("systemMessage", "").strip() in (SELECTOR, OLD_SELECTOR)


def live_version(workflow: dict) -> str:
    """Which prompt the workflow would use right now, either wiring."""
    if installed(workflow):
        chosen = field(node_named(workflow, SET_NODE), "prompt_version")
        return chosen["value"].strip() if chosen else "unset"
    message = agent_node(workflow)["parameters"]["options"].get("systemMessage", "")
    for version, marker in MARKERS.items():
        if marker in message:
            return version
    return "unknown"


def push(workflow: dict) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(workflow, handle, indent=2)
        local = handle.name
    # `docker compose cp` preserves the mode, and n8n runs as uid 1000: a 0600
    # temp file lands unreadable and the import fails with EACCES.
    Path(local).chmod(0o644)
    compose("cp", local, f"n8n:{CONTAINER_PATH}")
    compose("exec", "-T", "n8n", "n8n", "import:workflow", f"--input={CONTAINER_PATH}")
    Path(local).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", nargs="?", choices=VERSIONS)
    parser.add_argument("--show", action="store_true", help="print the live prompt version and exit")
    parser.add_argument("--install", action="store_true",
                        help="move both prompts onto the canvas so the switch is a field")
    args = parser.parse_args()

    workflow = load()

    if args.show or not (args.version or args.install):
        version = live_version(workflow)
        where = "canvas field" if installed(workflow) else "System Message (run --install)"
        print(f"{WORKFLOW} is running {version}, switched from the {where}")
        return 0

    set_node = node_named(workflow, SET_NODE)
    agent = agent_node(workflow)

    def put_prompts() -> None:
        for version in VERSIONS:
            put_field(set_node, f"prompt_{version}", "=" + prompt_body(version))
        agent["parameters"]["options"]["systemMessage"] = SELECTOR

    if args.install:
        current = live_version(workflow)
        put_prompts()
        if field(set_node, "prompt_version") is None:
            put_field(set_node, "prompt_version", current if current in VERSIONS else "v2")

    if args.version:
        if not installed(workflow) and not args.install:
            # Old wiring: keep writing the System Message directly.
            agent["parameters"]["options"]["systemMessage"] = "=" + prompt_body(args.version)
        else:
            put_prompts()
            put_field(set_node, "prompt_version", args.version)

    push(workflow)

    after = load()
    version = live_version(after)
    if args.version and version != args.version:
        print(f"switch failed: live prompt reads as {version}", file=sys.stderr)
        return 1
    fields = [a["name"] for a in assignments(node_named(after, SET_NODE))]
    options = agent_node(after)["parameters"]["options"]
    print(f"{WORKFLOW} now runs {version}; Edit Fields carries {', '.join(fields)}; "
          f"returnIntermediateSteps={options.get('returnIntermediateSteps')}")
    print("Reload the n8n tab before running anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
