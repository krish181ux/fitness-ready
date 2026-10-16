"""Ground truth, computed from DuckDB (SPEC §9.1, §9.2).

    python -m evals.ground_truth            # print what the answers should be
    python -m evals.ground_truth --write    # fill expected_answer in dataset.csv

Each bucket A case has one function here, keyed by case id, so the sheet
column stays derived rather than hand-typed. Three bucket B cases also carry the
readiness verdict for the as-of day, scored by `metrics.verdict_match`; the
judge never sees `expected_answer`. `as_of_date` from the dataset pins
the agent's "today", which is what makes these answers stable over time.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import duckdb

from db import connect
from tools import queries
from tools.derived import (
    HARD_SESSION_SQL,
    RESTING_HR_WINDOW_DAYS,
    SLEEP_TRUSTWORTHY_SQL,
    reference_instant,
)

DATASET_PATH = Path(__file__).resolve().parent / "dataset.csv"
# Journal cases live apart so the 30-case set v1-v3 were measured on is unchanged.
JOURNAL_DATASET_PATH = Path(__file__).resolve().parent / "dataset_journal.csv"
COLUMNS = ("id", "bucket", "question", "as_of_date", "expected_tool", "expected_answer", "rubric_notes")

TruthFn = Callable[[duckdb.DuckDBPyConnection, date], Any]
TRUTH: dict[str, TruthFn] = {}


def truth(case_id: str) -> Callable[[TruthFn], TruthFn]:
    def register(fn: TruthFn) -> TruthFn:
        TRUTH[case_id] = fn
        return fn
    return register


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _window(as_of: date, days: int) -> list[Any]:
    return [as_of - timedelta(days=days - 1), as_of]


# ------------------------------------------------------------- bucket A cases

@truth("A01")
def avg_resting_hr_30d(conn, as_of):
    """Average resting HR over the last 30 days, inclusive of today."""
    value = _scalar(
        conn,
        "SELECT avg(resting_hr) FROM daily WHERE date BETWEEN ? AND ?",
        _window(as_of, RESTING_HR_WINDOW_DAYS),
    )
    return round(value, 1)


@truth("A02")
def longest_run_this_year(conn, as_of):
    return round(_scalar(
        conn,
        "SELECT max(distance_km) FROM activities "
        "WHERE type = 'running' AND year(start_time) = ? AND start_time <= ?",
        [as_of.year, reference_instant(as_of)],
    ), 2)


@truth("A03")
def untrustworthy_nights_last_week(conn, as_of):
    rows = conn.execute(
        f"SELECT date FROM sleep WHERE date BETWEEN ? AND ? AND NOT {SLEEP_TRUSTWORTHY_SQL} "
        "ORDER BY date",
        _window(as_of, 7),
    ).fetchall()
    return "; ".join(row[0].isoformat() for row in rows)


@truth("A04")
def total_steps_7d(conn, as_of):
    return int(_scalar(conn, "SELECT sum(steps) FROM daily WHERE date BETWEEN ? AND ?", _window(as_of, 7)))


@truth("A05")
def hard_sessions_14d(conn, as_of):
    return int(_scalar(
        conn,
        f"SELECT count(*) FROM activities WHERE start_time BETWEEN ? AND ? AND {HARD_SESSION_SQL}",
        [as_of - timedelta(days=13), reference_instant(as_of)],
    ))


@truth("A06")
def avg_sleep_score_7d(conn, as_of):
    return round(_scalar(
        conn,
        f"SELECT avg(sleep_score) FROM sleep WHERE date BETWEEN ? AND ? AND {SLEEP_TRUSTWORTHY_SQL}",
        _window(as_of, 7),
    ), 1)


@truth("A07")
def hrv_below_baseline(conn, as_of):
    """How far last night's HRV sat under the bottom of the baseline band."""
    low, last_night = conn.execute(
        "SELECT baseline_low, last_night_avg FROM hrv WHERE date = ?", [as_of]
    ).fetchone()
    return low - last_night


@truth("A08")
def badminton_minutes_30d(conn, as_of):
    """Badminton is this profile's main source of intensity, so the set asks about it."""
    return round(_scalar(
        conn,
        "SELECT sum(duration_min) FROM activities "
        "WHERE type = 'badminton' AND start_time BETWEEN ? AND ?",
        [as_of - timedelta(days=29), reference_instant(as_of)],
    ))


@truth("A09")
def most_frequent_activity_30d(conn, as_of):
    return _scalar(
        conn,
        "SELECT type FROM activities WHERE start_time BETWEEN ? AND ? "
        "GROUP BY type ORDER BY count(*) DESC, type LIMIT 1",
        [as_of - timedelta(days=29), reference_instant(as_of)],
    )


@truth("A10")
def max_resting_hr_30d(conn, as_of):
    return int(_scalar(
        conn, "SELECT max(resting_hr) FROM daily WHERE date BETWEEN ? AND ?", _window(as_of, 30)
    ))


@truth("A11")
def recovery_hours_remaining(conn, as_of):
    bundle = queries.get_readiness_inputs(conn, as_of)
    return bundle["last_hard_session"]["recovery_remaining_hours"]


@truth("A12")
def hrv_gap_days_30d(conn, as_of):
    recorded = int(_scalar(
        conn, "SELECT count(*) FROM hrv WHERE date BETWEEN ? AND ? AND last_night_avg IS NOT NULL",
        _window(as_of, 30),
    ))
    return 30 - recorded


# ------------------------------------------- bucket B: today's readiness verdict
# Only the cases that ask about the as-of day itself. "Tomorrow" and "this
# weekend" depend on nights not yet slept, so they have no colour to match.

def _todays_verdict(conn, as_of):
    return queries.get_readiness_inputs(conn, as_of)["verdict"]["colour"]


for _case in ("B03", "B07", "B09"):
    truth(_case)(_todays_verdict)


# ------------------------------------------------- bucket J: journal read-backs
# Answered from the fixture journal's seed (tools/journal.FIXTURE_ENTRIES)
# through the same query the tool runs, on a throwaway copy.

def _journal_rows(as_of: date, start: date, kind: str) -> list[dict[str, Any]]:
    from tools import journal

    with journal.connect(":memory:") as jconn:
        journal.seed_fixture(jconn)
        return journal.get_journal(jconn, start, as_of, kind=kind)["rows"]


@truth("J06")
def strength_rpe_on_9_sep(conn, as_of):
    return _journal_rows(as_of, date(2026, 9, 9), "rpe")[0]["value"]


@truth("J07")
def drinks_logged_first_week_of_september(conn, as_of):
    # Not "this month": J02 writes drinks on 12 Sep into the same fixture
    # journal, and a read case must not depend on what earlier runs wrote.
    rows = _journal_rows(date(2026, 9, 7), date(2026, 9, 1), "alcohol")
    return sum(r["value"] for r in rows)


# ----------------------------------------------------------------- dataset io

def read_dataset(path: Path = DATASET_PATH) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_dataset(rows: list[dict[str, str]], path: Path = DATASET_PATH) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def compute(conn: duckdb.DuckDBPyConnection, rows: list[dict[str, str]]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for row in rows:
        fn = TRUTH.get(row["id"])
        if fn is None:
            continue
        answers[row["id"]] = fn(conn, date.fromisoformat(row["as_of_date"]))
    return answers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--write", action="store_true", help="write answers back into the dataset")
    args = parser.parse_args()

    rows = read_dataset(args.dataset)
    with connect(args.db, read_only=True) as conn:
        answers = compute(conn, rows)

    missing = [row["id"] for row in rows if row["bucket"] == "A" and row["id"] not in answers]
    for case_id, value in answers.items():
        print(f"{case_id}  {value}")
    if missing:
        print(f"\nno ground-truth function for: {', '.join(missing)}")

    if args.write:
        for row in rows:
            if row["id"] in answers:
                row["expected_answer"] = str(answers[row["id"]])
        write_dataset(rows, args.dataset)
        print(f"\nwrote expected_answer for {len(answers)} cases to {args.dataset}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
