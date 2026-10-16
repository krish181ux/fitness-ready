"""The journal: what the watch can't see, logged by the agent from chat (SPEC §7.1).

    python -m tools.journal --seed-fixture     # the fixture journal the evals read

Effort, soreness, illness, alcohol and the like. Entries are typed, not free
"memories": every kind has a fixed meaning and a validated range, so a logged
value can be checked by an eval and queried by SQL like any wearable number.
Free text is kept, as the user's own words, never as a fact the model inferred.

The journal is its own DuckDB file, never the wearable database: the loader
writes that from the host while the tools server reads it, and two writers on
one file is how a database gets corrupted. The file is paired with the dataset
it annotates -- `journals/wearable-real.journal.duckdb` for the real data,
`journals/wearable.journal.duckdb` for the fixture -- so an eval run against the
fixture can never write test entries into the real journal.
"""

from __future__ import annotations

import argparse
import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from db import REPO_ROOT, db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
  entry_id  VARCHAR PRIMARY KEY,
  date      DATE NOT NULL,       -- the day the entry is about, not the day it was logged
  kind      VARCHAR NOT NULL,    -- one of KINDS
  value     DOUBLE,              -- on the kind's scale; NULL for kinds without one
  note      VARCHAR,             -- the user's own words, short
  logged_at TIMESTAMP NOT NULL,
  source    VARCHAR NOT NULL     -- chat | fixture
);
"""

# kind -> (unit or scale, low, high, value required). A kind with no scale
# records that something happened; its note says what.
KINDS: dict[str, tuple[str | None, float | None, float | None, bool]] = {
    "rpe":      ("session effort, 1 = very easy, 10 = maximal", 1, 10, True),
    "soreness": ("0 = none, 10 = worst", 0, 10, True),
    "energy":   ("1 = exhausted, 10 = excellent", 1, 10, True),
    "stress":   ("0 = none, 10 = extreme", 0, 10, True),
    "alcohol":  ("standard drinks", 0, 30, True),
    "caffeine": ("cups of coffee or equivalent", 0, 20, True),
    "illness":  (None, None, None, False),
    "injury":   (None, None, None, False),
    "note":     (None, None, None, False),
}
# Kinds that mean nothing without words: which injury, which illness.
NOTE_REQUIRED = ("injury", "note")
NOTE_MAX_CHARS = 280
MAX_RANGE_DAYS = 366

# Fixed ids, so reseeding converges instead of duplicating. The evals' read
# cases are answered from these rows (evals/ground_truth.py).
FIXTURE_ENTRIES = (
    ("fixture-01", date(2026, 9, 9), "rpe", 7, "strength felt heavy"),
    ("fixture-02", date(2026, 9, 5), "alcohol", 2, "two glasses of wine at dinner"),
    ("fixture-03", date(2026, 9, 11), "soreness", 5, "calves after badminton"),
    ("fixture-04", date(2026, 9, 7), "energy", 4, None),
)


class JournalError(ValueError):
    """A rejected entry. The message is read by the agent, so it names the fix."""


def journal_path() -> Path:
    """`$JOURNAL_DB` if set; else the journal paired with the served dataset.

    In the container the dataset is always mounted at /data/wearable.duckdb, so
    compose passes the host file name in `WEARABLE_DB_FILE` to pick the pair.
    """
    if os.environ.get("JOURNAL_DB"):
        return Path(os.environ["JOURNAL_DB"])
    dataset = Path(os.environ.get("WEARABLE_DB_FILE") or db_path())
    directory = Path(os.environ.get("JOURNAL_DIR", REPO_ROOT / "journals"))
    return directory / f"{dataset.stem}.journal.duckdb"


def connect(path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    target = Path(path) if path is not None else journal_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(target))
    conn.execute(SCHEMA)
    return conn


def validate(kind: str, value: float | None, note: str | None, day: date,
             today: date) -> tuple[str, float | None, str | None]:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise JournalError(
            f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}. "
            "Anything that fits none of them goes in kind='note' with the user's words."
        )
    scale, low, high, needs_value = KINDS[kind]
    note = (note or "").strip() or None
    if needs_value:
        if value is None:
            raise JournalError(f"kind={kind} needs a value on its scale ({scale}), "
                               f"from {low:g} to {high:g}. Ask the user if they did not say.")
        if not low <= value <= high:
            raise JournalError(f"value {value:g} is outside {kind}'s scale ({scale}), "
                               f"{low:g} to {high:g}. Do not rescale it yourself: ask the user.")
    elif value is not None:
        raise JournalError(f"kind={kind} takes no value. Put the detail in note and call again.")
    if kind in NOTE_REQUIRED and not note:
        raise JournalError(f"kind={kind} needs a note saying what it is, in the user's words.")
    if note and len(note) > NOTE_MAX_CHARS:
        raise JournalError(f"note is {len(note)} characters; keep it under {NOTE_MAX_CHARS}.")
    if day > today:
        raise JournalError(f"date {day} is in the future. Log what has happened, dated the day "
                           "it happened; 'last night' is the day before today.")
    return kind, value, note


def log_note(conn: duckdb.DuckDBPyConnection, day: date, kind: str, value: float | None = None,
             note: str | None = None, *, today: date | None = None,
             source: str = "chat") -> dict[str, Any]:
    """Write one entry, or return the identical one already there."""
    kind, value, note = validate(kind, value, note, day, today or date.today())
    existing = conn.execute(
        "SELECT entry_id FROM journal WHERE date = ? AND kind = ? "
        "AND value IS NOT DISTINCT FROM ? AND note IS NOT DISTINCT FROM ?",
        [day, kind, value, note],
    ).fetchone()
    if existing:
        # A retried tool call must not count the same drink twice.
        return {"status": "already_logged", "entry": _entry(conn, existing[0])}
    entry_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO journal VALUES (?, ?, ?, ?, ?, ?, ?)",
        [entry_id, day, kind, value, note, datetime.now(), source],
    )
    return {"status": "logged", "entry": _entry(conn, entry_id)}


def get_journal(conn: duckdb.DuckDBPyConnection, start_date: date, end_date: date,
                kind: str | None = None) -> dict[str, Any]:
    if kind is not None and kind not in KINDS:
        raise JournalError(f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}, or omit it.")
    sql = ("SELECT entry_id, date, kind, value, note, logged_at FROM journal "
           "WHERE date BETWEEN ? AND ?")
    params: list[Any] = [start_date, end_date]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    rows = [_row(conn.description, r) for r in conn.execute(sql + " ORDER BY date, logged_at", params).fetchall()]
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "kind": kind,
        "rows": rows,
        # An empty journal means nothing was logged, not that nothing happened.
        "data_gaps": [] if rows else ["no journal entries in this range; absence of an "
                                      "entry is not evidence that nothing happened"],
    }


def seed_fixture(conn: duckdb.DuckDBPyConnection) -> int:
    for entry_id, day, kind, value, note in FIXTURE_ENTRIES:
        conn.execute(
            "INSERT OR REPLACE INTO journal VALUES (?, ?, ?, ?, ?, ?, 'fixture')",
            [entry_id, day, kind, value, note, datetime(2026, 9, 13, 12, 0)],
        )
    return len(FIXTURE_ENTRIES)


def _entry(conn: duckdb.DuckDBPyConnection, entry_id: str) -> dict[str, Any]:
    cursor = conn.execute(
        "SELECT entry_id, date, kind, value, note, logged_at FROM journal WHERE entry_id = ?",
        [entry_id],
    )
    return _row(cursor.description, cursor.fetchone())


def _row(description, values) -> dict[str, Any]:
    row = dict(zip((d[0] for d in description), values))
    row["date"] = row["date"].isoformat()
    row["logged_at"] = row["logged_at"].isoformat(sep=" ", timespec="minutes")
    if row["value"] is not None and float(row["value"]).is_integer():
        row["value"] = int(row["value"])
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed-fixture", action="store_true",
                        help="write the fixture entries into the fixture's journal")
    args = parser.parse_args()
    if args.seed_fixture:
        path = REPO_ROOT / "journals" / "wearable.journal.duckdb"
        with connect(path) as conn:
            print(f"seeded {seed_fixture(conn)} entries into {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
