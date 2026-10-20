"""The agent's only access to the data: named tools over HTTP (SPEC §7).

    uvicorn tools.server:app --port 8000

n8n calls these with HTTP Request tool nodes. There is deliberately no
free-form SQL endpoint in Phase 1 (SPEC §11 open decision 1): named tools make
"which tool answered" visible in every trace, and raw data stays behind a guardrail.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from db import connect, db_path
from tools import derived, journal, queries
from tools.derived import MAX_RANGE_DAYS

# Retries for a request that lands while the loader is checkpointing the file.
OPEN_ATTEMPTS = 3
OPEN_RETRY_DELAY_S = 0.5


def get_conn() -> Iterator[duckdb.DuckDBPyConnection]:
    """A fresh read-only connection per request, closed when the request ends.

    The server used to hold one connection from startup, and DuckDB never
    re-reads a file it already has open, so every daily load needed a container
    restart, and a stack that was down during the sync served stale rows with no
    warning. Opening costs about 3 ms. Once the last connection closes DuckDB
    drops its cached instance, so the next request reads the file as it is now.
    """
    for attempt in range(1, OPEN_ATTEMPTS + 1):
        try:
            conn = connect(read_only=True)
            break
        except (duckdb.Error, FileNotFoundError):
            if attempt == OPEN_ATTEMPTS:
                raise HTTPException(
                    status_code=503,
                    detail="The database could not be opened. This is a server problem, not a "
                           "bad request: do not retry this call, and tell the user the data is "
                           "unavailable.",
                )
            time.sleep(OPEN_RETRY_DELAY_S)
    try:
        yield conn
    finally:
        conn.close()


app = FastAPI(
    title="Wearable Coach tools",
    version="0.1.0",
    summary="Named, read-only tools over a Garmin-derived DuckDB.",
)


def _validate_range(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail=(
                f"end_date ({end_date}) is before start_date ({start_date}). "
                "Swap them and call again."
            ),
        )
    span = (end_date - start_date).days + 1
    if span > MAX_RANGE_DAYS:
        # Error bodies are read by the agent, not a developer, so they name the
        # limit *and* the corrected call. Without the suggestion the model tends
        # to retry the identical request (observed in Langfuse, 2026-09-15).
        earliest = end_date - timedelta(days=MAX_RANGE_DAYS - 1)
        raise HTTPException(
            status_code=422,
            detail=(
                f"Requested {span} days; the cap is {MAX_RANGE_DAYS} days inclusive of both "
                f"endpoints. Retry with start_date={earliest.isoformat()} for the widest "
                f"allowed window ending {end_date.isoformat()}, or split the range across "
                "several calls."
            ),
        )


@app.get("/health", summary="Liveness plus data coverage")
def health(conn: duckdb.DuckDBPyConnection = Depends(get_conn)) -> dict[str, Any]:
    tables = ("daily", "sleep", "hrv", "activities", "user_metrics")
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    latest = conn.execute("SELECT max(date) FROM daily").fetchone()[0]
    return {
        "status": "ok",
        "db": str(db_path()),
        "rows": counts,
        "latest_date": latest.isoformat() if latest else None,
    }


@app.get("/tools/get_daily_metrics", summary="Daily wellness rows for a date range")
def get_daily_metrics(
    start_date: date,
    end_date: date,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
) -> dict[str, Any]:
    _validate_range(start_date, end_date)
    return queries.get_daily_metrics(conn, start_date, end_date)


@app.get("/tools/get_sleep", summary="Nightly sleep rows, including the validation flag")
def get_sleep(
    start_date: date,
    end_date: date,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
) -> dict[str, Any]:
    _validate_range(start_date, end_date)
    return queries.get_sleep(conn, start_date, end_date)


@app.get("/tools/get_hrv_trend", summary="Nightly HRV with 7- and 60-day means and the baseline band")
def get_hrv_trend(
    days: int = Query(default=30, ge=1, le=MAX_RANGE_DAYS),
    as_of: date | None = None,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
) -> dict[str, Any]:
    return queries.get_hrv_trend(conn, days=days, as_of=as_of)


@app.get("/tools/list_activities", summary="Activities in a date range, flagged hard/easy")
def list_activities(
    start_date: date,
    end_date: date,
    type: str | None = Query(default=None, description="running | walking | cycling | ..."),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
) -> dict[str, Any]:
    _validate_range(start_date, end_date)
    return queries.list_activities(conn, start_date, end_date, activity_type=type)


@app.get("/tools/get_readiness_inputs", summary="Every input the readiness rubric needs, for one date")
def get_readiness_inputs(
    date_: date | None = Query(default=None, alias="date"),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
) -> dict[str, Any]:
    return queries.get_readiness_inputs(conn, as_of=date_)


# ------------------------------------------------------------------ journal
# The only writes the agent can make. The wearable database stays read-only.

def get_journal_conn() -> Iterator[duckdb.DuckDBPyConnection]:
    try:
        conn = journal.connect()
    except duckdb.Error:
        raise HTTPException(
            status_code=503,
            detail="The journal could not be opened. This is a server problem: do not retry, "
                   "and tell the user the entry was not saved.",
        )
    try:
        yield conn
    finally:
        conn.close()


class JournalEntry(BaseModel):
    # Aliased: a field called `date` would shadow the type it is annotated with.
    day: date = Field(alias="date", description="The day the entry is about, YYYY-MM-DD")
    kind: str = Field(description=" | ".join(journal.KINDS))
    # n8n's $fromAI sends "" for an omitted optional value.
    value: float | str | None = None
    note: str | None = None
    activity_id: int | str | None = Field(
        default=None, description="rpe only: the session it rates; omit if that day had one session")


def _sessions_on(conn: duckdb.DuckDBPyConnection, day: date) -> list[dict[str, Any]]:
    """That day's sessions, a run saved in parts counted once (derived.merge_split_sessions)."""
    cursor = conn.execute(
        "SELECT activity_id, type, strftime(start_time, '%Y-%m-%dT%H:%M:%S') AS start_time, "
        "duration_min FROM activities WHERE CAST(start_time AS DATE) = ? ORDER BY start_time",
        [day])
    names = [d[0] for d in cursor.description]
    sessions = derived.merge_split_sessions([dict(zip(names, row)) for row in cursor.fetchall()])
    return [{"activity_id": s["activity_id"], "parts": s["parts"], "type": s["type"],
             "start_time": s["start_time"][11:16], "duration_min": s["duration_min"]}
            for s in sessions]


@app.post("/tools/log_note", summary="Record something the watch can't see, in the journal")
def log_note(
    entry: JournalEntry,
    conn: duckdb.DuckDBPyConnection = Depends(get_journal_conn),
    wearable: duckdb.DuckDBPyConnection = Depends(get_conn),
) -> dict[str, Any]:
    activity_id = entry.activity_id
    if isinstance(activity_id, str):
        if not activity_id.strip():
            activity_id = None
        elif activity_id.strip().isdigit():
            activity_id = int(activity_id)
        else:
            raise HTTPException(status_code=422, detail=(
                f"activity_id {activity_id!r} is not a number. Use the activity_id from "
                "list_activities, or omit it."))
    value = entry.value
    if isinstance(value, str):
        try:
            value = float(value) if value.strip() else None
        except ValueError:
            raise HTTPException(status_code=422, detail=(
                f"value {value!r} is not a number. Send the number alone, e.g. value=7, "
                "and put any words in note."))
    try:
        return journal.log_note(conn, entry.day, entry.kind, value, entry.note,
                                activity_id=activity_id,
                                sessions=_sessions_on(wearable, entry.day))
    except journal.JournalError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/tools/get_journal", summary="Journal entries in a date range, optionally one kind")
def get_journal(
    start_date: date,
    end_date: date,
    kind: str | None = Query(default=None, description=" | ".join(journal.KINDS)),
    conn: duckdb.DuckDBPyConnection = Depends(get_journal_conn),
    wearable: duckdb.DuckDBPyConnection = Depends(get_conn),
) -> dict[str, Any]:
    _validate_range(start_date, end_date)
    try:
        result = journal.get_journal(conn, start_date, end_date, kind=kind or None)
    except journal.JournalError as error:
        raise HTTPException(status_code=422, detail=str(error))
    linked_days = {date.fromisoformat(r["date"]) for r in result["rows"] if r.get("activity_id")}
    minutes = {s["activity_id"]: s["duration_min"]
               for day in linked_days for s in _sessions_on(wearable, day)}
    journal.add_session_load(result["rows"], minutes)
    return result


def main() -> None:  # pragma: no cover - convenience entrypoint
    import uvicorn

    uvicorn.run(
        "tools.server:app",
        host=os.environ.get("TOOLS_HOST", "127.0.0.1"),
        port=int(os.environ.get("TOOLS_PORT", "8000")),
        reload=bool(os.environ.get("TOOLS_RELOAD")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
