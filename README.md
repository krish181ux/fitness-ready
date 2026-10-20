# Wearable Coach

A coach that reads your wearable data, answers questions grounded in the
numbers, and is explicit that those numbers are approximations.

Consumer wearables give figures that are roughly right and occasionally wrong.
Most apps present them as exact. This one reasons over trends, respects the
device's own data-quality flags, and says what it does not know. `SPEC.md` is
the source of truth; this file is how to run it.

## Layout

```
loader/    Garmin export or API pull -> DuckDB
fetcher/   Garmin Connect API client and daily pull
tools/     the named HTTP tools the agent calls, and the journal it writes
prompts/   system_v1 (baseline), v2, v3 (reports the tool's verdict), v4 (live: v3 + journal)
n8n/       build sheet and exported workflows
litellm/   model routing and Langfuse tracing
scripts/   daily sync, prompt switching, dataset loading
tests/     the definitions in SPEC §6.3, the loader, the tool contracts, the journal
```

## Setup

```bash
pip install -e ".[dev,api]"
make test        # builds its own synthetic data in a temp dir; touches no database
```

There is one database, `wearable-real.duckdb`, with your Garmin data. The
synthetic dataset and the evals built on it were removed on 17 Oct 2026; the
tests still generate synthetic rows in memory, because a test needs data whose
answers are known.

## With the real export

1. garmin.com → Account → Data Management → Export Your Data. The zip arrives by
   email; unzip it into `raw/`.
2. `python -m loader.inventory raw/` — check every file maps to a table and read
   the unmapped-field list. Extend `loader/discovery.py` (patterns) or
   `loader/fieldmap.py` (columns) until nothing important is unmapped.
3. `make load` — idempotent; re-running a later export converges rather
   than duplicating.

### Or via the Connect API

```bash
pip install -e ".[api]"
make probe DATE=2026-09-16   # one day of raw responses, to inspect field names
make load-api                # pull the last 30 days, load into wearable-real.duckdb
make pull START=2026-06-01 END=2026-09-16   # a specific range (at most 366 days)
```

The first login asks for your Garmin password (and an MFA code, if enabled) in
the terminal; later runs reuse the token cached in `~/.garminconnect`. Re-running
is cheap: only days not yet on disk, plus the last two, are fetched. API and
export data for the same day or activity converge on the same row.

`raw/` and `*.duckdb` are gitignored. This is personal health data; keep it that
way.

## What the data can and cannot do

 Activities, resting HR and steps are good
throughout. Scored sleep and nightly HRV exist from 2026-09-28, when the watch
started being worn overnight; before that there is a gap back to 2026-03-02.
Garmin needs about three weeks of consecutive nights before it reports an HRV
baseline, so until then the readiness rubric in SPEC §8 treats the HRV check as
missing. A night the watch did not record (battery, charging, off-wrist) is a
permanent gap. The watch reports no training effect or recovery time for
strength sessions, so they count as hard on duration and average HR instead
(SPEC §6.3).

## Daily sync

`scripts/daily_sync.sh` pulls the last 30 days from the Connect API, loads
`wearable-real.duckdb`. The tools server opens the database per request, so new
rows reach the agent without a restart. A launchd agent runs it at 09:00, 11:00
and 21:00. The 09:00 run only loads, so a morning question sees last night; the
sleep and model-key checks run at 11:00 and 21:00, when the phone has had time
to upload:

```bash
launchctl kickstart gui/$(id -u)/com.fitness-ready.daily-sync   # run it now
tail ~/Library/Logs/fitness-ready-sync.log
launchctl bootout gui/$(id -u)/com.fitness-ready.daily-sync     # remove it
```

It stays quiet when all is well. It posts a macOS notification when the sync
fails (most often an expired Garmin login: run `python -m fetcher.pull` in a
terminal) or when last night's sleep is missing or untrustworthy
(`python -m fetcher.check`), or when a model key behind LiteLLM is rejected
(`python -m fetcher.llm_check`; the Anthropic and OpenAI keys expire around
15 Jan 2027). Notifications come from Script Editor, so allow
those in System Settings → Notifications.

The plist lives at `~/Library/LaunchAgents/com.fitness-ready.daily-sync.plist`,
outside the repo. Garmin only has what the phone has uploaded: open Garmin
Connect after waking, or the night is not there to pull.

## The journal

The watch can't see effort, soreness, illness or alcohol. Tell the agent ("that
session was an 8", "three beers last night") and prompt v4 logs it with
`log_note` into `journals/wearable-real.journal.duckdb`. Entries are typed and
range-checked (SPEC §7.1), never free-form memories. `journals/` is personal
data and gitignored.

Effort (`rpe`, 1–10) rates one session: on a day with a single activity it links
itself, on a day with several the agent asks which. `get_journal` then returns
`session_load` = effort × minutes, the session-RPE load (Foster 2001) — the
best per-session load figure this watch allows, as it reports no training
effect. Soreness (0–10) is a morning, whole-body rating. Both are optional; a
day with nothing logged is unknown, never 0.

```bash
curl -s 'localhost:8000/tools/get_journal?start_date=2026-10-01&end_date=2026-10-16'
```

## The agent

`docker compose up -d` brings up the tools server, LiteLLM, n8n
(<http://localhost:5678>) and Langfuse (<http://localhost:3000>).
`n8n/workflow_agent.json` is an export of the live agent; `scripts/set_prompt.py
v4` switches its prompt. Save nothing from an n8n tab opened before a script
changed the workflow: the stale copy overwrites it (this deleted the journal
tools once).

## Design decisions worth knowing

- **Named tools, no free-form SQL.** Which tool answered is visible in every
  trace, and raw data stays behind a guardrail (SPEC §7, §11).
- **The derived definitions live in one module.** `tools/derived.py` defines
  hard session, resting-HR delta, HRV vs baseline and sleep trust once, as a SQL
  fragment and as a Python predicate, and the tests assert the two agree.
- **`get_readiness_inputs` anchors to 18:00 on the as-of date.** Evals pin
  `as_of_date`, so "hours since the last hard session" must not move with the
  wall clock.
- **No Training Readiness or Training Status.** The vivoactive 5 does not
  produce them. `get_readiness_inputs` reconstructs a Green/Amber/Red verdict
  from raw inputs with a visible rubric (`tools/derived.py`), and the agent
  reports it. The model used to apply the rubric itself and got the colour
  wrong in 9 of 12 stored v2 answers.
- **Not medical advice.** Red-flag inputs are escalated, not answered.
