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
evals/     dataset, ground truth, metrics, judge, run store
prompts/   system_v1 (baseline), v2, v3 (reports the tool's verdict), v4 (live: v3 + journal)
n8n/       build sheet and exported workflows
litellm/   model routing and Langfuse tracing
scripts/   daily sync, prompt switching, dataset loading
tests/     the definitions in SPEC §6.3, the loader, the tool contracts, the dataset
```

## Quick start (no Garmin export needed)

```bash
pip install -e ".[dev]"
make fixture     # synthetic export, calibrated to the real training profile
make load        # -> wearable.duckdb
make test
make serve       # http://localhost:8000/docs
```

```bash
curl 'localhost:8000/tools/get_readiness_inputs?date=2026-09-13'
```

## Two databases, on purpose

`wearable.duckdb` is built from the fixture and is what the tools server, n8n and
the eval dataset point at. `wearable-real.duckdb` holds the real account export.
They are kept apart because the real data cannot support the readiness rubric —
see the implementation note in `SPEC.md` — and because mixing synthetic and real
rows in one table would make every eval answer unexplainable.

```bash
make load        # fixture   -> wearable.duckdb
make load-real   # export    -> wearable-real.duckdb
WEARABLE_DB=wearable-real.duckdb make serve   # point the tools at real data
```

## With the real export

1. garmin.com → Account → Data Management → Export Your Data. The zip arrives by
   email; unzip it into `raw/`.
2. `python -m loader.inventory raw/` — check every file maps to a table and read
   the unmapped-field list. Extend `loader/discovery.py` (patterns) or
   `loader/fieldmap.py` (columns) until nothing important is unmapped.
3. `make load-real` — idempotent; re-running a later export converges rather
   than duplicating.
4. `python -m evals.ground_truth --write` — bucket A answers are derived from
   the database, never hand-typed.

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

## Pointing the agent at real data

The tools server serves whichever database `WEARABLE_DB_FILE` names:

```bash
# real Garmin data
echo 'WEARABLE_DB_FILE=./wearable-real.duckdb' >> .env && docker compose up -d tools
# back to the fixture, which the eval answer key is pinned to
sed -i '' 's|^WEARABLE_DB_FILE=.*|WEARABLE_DB_FILE=./wearable.duckdb|' .env && docker compose up -d tools
curl -s localhost:8000/health        # `rows` tells you which one is live
```

The agent's `as_of_date` falls back to the real clock (`$now`) when no eval row
supplies one, so chat follows today while eval runs stay pinned.

**Switch back before running evals.** Bucket A's answer key is computed from the
fixture (`evals/ground_truth.py`), so scoring a run against real data compares
answers to the wrong truth. `make evals` reads the fixture directly and is
unaffected; it is the n8n eval workflow, which goes through the tools server,
that would silently score nonsense.

**What real data can and cannot do.** Activities, resting HR and steps are good
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
rows reach the agent without a restart. A launchd agent runs it at 11:00 and
21:00:

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
`log_note` into `journals/<dataset>.journal.duckdb`. Entries are typed and
range-checked (SPEC §7.1), never free-form memories. `journals/` is personal
data and gitignored. `make load` seeds the fixture's journal, which the
journal evals read.

```bash
curl -s 'localhost:8000/tools/get_journal?start_date=2026-10-01&end_date=2026-10-16'
```

## The agent and the eval loop

`docker compose up -d` brings up the tools server, n8n (<http://localhost:5678>)
and Langfuse (<http://localhost:3000>). Build the two workflows from
`n8n/README.md`, then run the dataset through the Evaluations tab. The iteration
loop — run v1, diagnose from Langfuse traces, change the prompt, run v2, compare
— is SPEC §9.5, and the measured v1 → v2 results are in SPEC's implementation
notes.

## Eval results

Run results are collected out of n8n into their own DuckDB, one row per case per
run, so comparisons are queries rather than hand-extracted CSVs:

```bash
make eval-store WORKFLOW=<eval workflow id> LABELS="1=v1:claude-opus-5 2=v1 3=v2"
make eval-summary
python -m evals.store compare v1 v2
```

A local run (`python -m evals.run_local`) needs a tools server on the fixture,
and refuses to start against one serving real data:

```bash
WEARABLE_DB=wearable.duckdb uvicorn tools.server:app --port 8001 &
python -m evals.run_local --prompt v3 --tools-url http://localhost:8001
python -m evals.store rescore     # re-apply today's metrics to every stored answer
```

Query it live, in a terminal or a browser:

```bash
duckdb -readonly evals.duckdb -markdown -c "SELECT * FROM eval_runs ORDER BY ran_at"
duckdb -readonly evals.duckdb            # interactive shell; .mode markdown, .tables
duckdb -ui evals.duckdb                  # local web UI (installs the ui extension once)
python -m evals.store classify           # precision and recall per run
python notebooks/eval_dashboard.py       # six charts -> notebooks/eval_dashboard.html
```

`evals/results/` holds the same numbers as committed CSVs — `runs.csv` (one row
per run), `cases.csv` (one row per case, with the answers) and
`classification.csv` (the confusion matrices). Every stored run answered against
the **fixture**, so those files carry synthetic health numbers and are safe to
publish; real Garmin data lives in `wearable-real.duckdb` and never enters the
eval store. Regenerate with `make eval-export`.

`evals.duckdb` is deliberately separate from `wearable.duckdb`: the tools server
holds that file open, and eval output must never be mistakable for wearable data.
Stored answers are re-scored with today's metric definitions on load, so a later
metric fix does not make old runs incomparable. `make clean` leaves it alone.

## Design decisions worth knowing

- **Five named tools, no free-form SQL.** "Did it call the right tool" stays a
  clean pass/fail, and raw data stays behind a guardrail (SPEC §7, §11).
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
