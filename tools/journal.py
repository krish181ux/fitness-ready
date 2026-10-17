"""The journal: what the watch can't see, logged by the agent from chat (SPEC §7.1).

Effort, soreness, illness, alcohol and the like. Entries are typed, not free
"memories": every kind has a fixed meaning and a validated range, so a logged
value can be checked by an eval and queried by SQL like any wearable number.
Free text is kept, as the user's own words, never as a fact the model inferred.

The journal is its own DuckDB file, never the wearable database: the loader
writes that from the host while the tools server reads it, and two writers on
one file is how a database gets corrupted. The file is paired with the dataset
it annotates: `journals/wearable-real.journal.duckdb` for the real data.

Effort (`rpe`) belongs to a session, not a day: rpe x minutes is session-RPE
load (Foster 2001), the one per-session load figure this watch can support, as
it reports no training effect. So an rpe entry carries the activity it rates.
"""

from __future__ import annotations

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
  source    VARCHAR NOT NULL,    -- chat
  activity_id BIGINT             -- rpe only: the session it rates; NULL if the watch did not record it
);
ALTER TABLE journal ADD COLUMN IF NOT EXISTS activity_id BIGINT;
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
SESSION_KINDS = ("rpe",)


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


def resolve_session(kind: str, activity_id: int | None,
                    sessions: list[dict[str, Any]] | None) -> int | None:
    """The activity an entry rates, or None. `sessions` are that day's activities.

    One session that day links itself. Several need the agent to say which, so
    effort is never pinned on the wrong one. None that day is allowed: the watch
    may not have recorded it, and the effort is still worth keeping.
    """
    if kind not in SESSION_KINDS:
        if activity_id is not None:
            raise JournalError(f"kind={kind} is about the day, not a session; omit activity_id.")
        return None
    if sessions is None:
        return activity_id
    ids = {int(a["activity_id"]) for a in sessions}
    listing = "; ".join(f"activity_id={a['activity_id']} {a['type']} at {a['start_time']} "
                        f"({a['duration_min']:.0f} min)" for a in sessions)
    if activity_id is not None:
        if int(activity_id) not in ids:
            raise JournalError(f"activity_id {activity_id} is not a session on that date. "
                               f"Sessions that day: {listing or 'none'}.")
        return int(activity_id)
    if len(sessions) == 1:
        return int(sessions[0]["activity_id"])
    if len(sessions) > 1:
        raise JournalError(f"There were {len(sessions)} sessions that day: {listing}. "
                           "Ask the user which one this effort is for, then call again "
                           "with its activity_id.")
    return None


def log_note(conn: duckdb.DuckDBPyConnection, day: date, kind: str, value: float | None = None,
             note: str | None = None, *, today: date | None = None, source: str = "chat",
             activity_id: int | None = None,
             sessions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Write one entry, or return the identical one already there."""
    kind, value, note = validate(kind, value, note, day, today or date.today())
    activity_id = resolve_session(kind, activity_id, sessions)
    existing = conn.execute(
        "SELECT entry_id FROM journal WHERE date = ? AND kind = ? "
        "AND value IS NOT DISTINCT FROM ? AND note IS NOT DISTINCT FROM ? "
        "AND activity_id IS NOT DISTINCT FROM ?",
        [day, kind, value, note, activity_id],
    ).fetchone()
    if existing:
        # A retried tool call must not count the same drink twice.
        return {"status": "already_logged", "entry": _entry(conn, existing[0])}
    entry_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO journal (entry_id, date, kind, value, note, logged_at, source, activity_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [entry_id, day, kind, value, note, datetime.now(), source, activity_id],
    )
    return {"status": "logged", "entry": _entry(conn, entry_id)}


def get_journal(conn: duckdb.DuckDBPyConnection, start_date: date, end_date: date,
                kind: str | None = None) -> dict[str, Any]:
    if kind is not None and kind not in KINDS:
        raise JournalError(f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}, or omit it.")
    sql = ("SELECT entry_id, date, kind, value, note, activity_id, logged_at FROM journal "
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


def add_session_load(rows: list[dict[str, Any]], minutes: dict[int, float]) -> None:
    """rpe x session minutes, in arbitrary units (Foster's session-RPE load)."""
    for row in rows:
        duration = minutes.get(row.get("activity_id"))
        if row["kind"] == "rpe" and row["value"] is not None and duration:
            row["session_minutes"] = round(duration)
            row["session_load"] = round(row["value"] * duration)


def _entry(conn: duckdb.DuckDBPyConnection, entry_id: str) -> dict[str, Any]:
    cursor = conn.execute(
        "SELECT entry_id, date, kind, value, note, activity_id, logged_at FROM journal "
        "WHERE entry_id = ?",
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
