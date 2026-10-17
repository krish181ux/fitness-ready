"""SQL behind the five tools (SPEC §7).

Every function takes a DuckDB connection and returns plain JSON-safe dicts.
No SQL lives anywhere else -- the agent never writes a query, it calls a tool.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

import duckdb

from tools import derived
from tools.derived import (
    HARD_SESSION_SQL,
    HRV_LONG_WINDOW_DAYS,
    HRV_SHORT_WINDOW_DAYS,
    RESTING_HR_WINDOW_DAYS,
    SLEEP_TRUSTWORTHY_SQL,
)

DAILY_COLUMNS = (
    "date", "resting_hr", "min_hr", "max_hr", "steps", "intensity_minutes",
    "avg_stress", "body_battery_high", "body_battery_low", "active_calories",
)
SLEEP_COLUMNS = (
    "date", "sleep_start", "sleep_end", "total_min", "deep_min", "light_min",
    "rem_min", "awake_min", "sleep_score", "avg_spo2", "avg_respiration", "validation",
)
ACTIVITY_COLUMNS = (
    "activity_id", "start_time", "type", "duration_min", "distance_km", "avg_hr",
    "max_hr", "calories", "aerobic_te", "anaerobic_te", "recovery_time_hours",
    "avg_speed_kmh", "elevation_gain_m", "hard_minutes",
)


# ------------------------------------------------------------------ plumbing

def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(conn: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, list(params))
    columns = [d[0] for d in cursor.description]
    return [{c: _jsonable(v) for c, v in zip(columns, row)} for row in cursor.fetchall()]


def _one(conn: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    found = _rows(conn, sql, params)
    return found[0] if found else None


def _present_dates(rows: list[dict[str, Any]], key: str = "date") -> list[date]:
    return [date.fromisoformat(row[key]) for row in rows if row.get(key)]


# --------------------------------------------------------------- the 5 tools

def get_daily_metrics(conn: duckdb.DuckDBPyConnection, start_date: date, end_date: date) -> dict[str, Any]:
    rows = _rows(
        conn,
        f"SELECT {', '.join(DAILY_COLUMNS)} FROM daily "
        "WHERE date BETWEEN ? AND ? ORDER BY date",
        (start_date, end_date),
    )
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rows": rows,
        "data_gaps": derived.describe_gaps(
            "daily metrics", derived.missing_dates(start_date, end_date, _present_dates(rows))
        ),
    }


def get_sleep(conn: duckdb.DuckDBPyConnection, start_date: date, end_date: date) -> dict[str, Any]:
    rows = _rows(
        conn,
        f"SELECT {', '.join(SLEEP_COLUMNS)}, {SLEEP_TRUSTWORTHY_SQL} AS trustworthy "
        "FROM sleep WHERE date BETWEEN ? AND ? ORDER BY date",
        (start_date, end_date),
    )
    gaps = derived.describe_gaps(
        "sleep", derived.missing_dates(start_date, end_date, _present_dates(rows))
    )
    untrusted = [row["date"] for row in rows if not row["trustworthy"]]
    if untrusted:
        gaps.append(
            "sleep not trustworthy on " + ", ".join(untrusted[:5])
            + (f" (+{len(untrusted) - 5} more)" if len(untrusted) > 5 else "")
        )
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rows": rows,
        "data_gaps": gaps,
    }


def get_hrv_trend(conn: duckdb.DuckDBPyConnection, days: int = 30, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or _latest_date(conn, "hrv") or date.today()
    start_date = as_of - timedelta(days=days - 1)
    rows = _rows(
        conn,
        "SELECT date, last_night_avg, last_night_5min_high, weekly_avg, status, "
        "baseline_low, baseline_high FROM hrv WHERE date BETWEEN ? AND ? ORDER BY date",
        (start_date, as_of),
    )
    baseline = _one(
        conn,
        "SELECT baseline_low, baseline_high FROM hrv "
        "WHERE date <= ? AND baseline_low IS NOT NULL ORDER BY date DESC LIMIT 1",
        (as_of,),
    ) or {"baseline_low": None, "baseline_high": None}
    latest = rows[-1] if rows else {}
    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "rows": rows,
        "mean_7d": _hrv_mean(conn, as_of, HRV_SHORT_WINDOW_DAYS),
        "mean_60d": _hrv_mean(conn, as_of, HRV_LONG_WINDOW_DAYS),
        "baseline_low": baseline["baseline_low"],
        "baseline_high": baseline["baseline_high"],
        "latest_vs_baseline": derived.hrv_vs_baseline(
            latest.get("last_night_avg"), baseline["baseline_low"], baseline["baseline_high"]
        ),
        "data_gaps": derived.describe_gaps(
            "HRV", derived.missing_dates(start_date, as_of, _present_dates(rows))
        ),
    }


def list_activities(
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    activity_type: str | None = None,
) -> dict[str, Any]:
    where = ["start_time >= ?", "start_time < ?"]
    params: list[Any] = [
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
    ]
    if activity_type:
        where.append("lower(type) = lower(?)")
        params.append(activity_type)
    rows = _rows(
        conn,
        f"SELECT {', '.join(ACTIVITY_COLUMNS)}, {HARD_SESSION_SQL} AS hard_session "
        f"FROM activities WHERE {' AND '.join(where)} ORDER BY start_time",
        params,
    )
    gaps: list[str] = []
    if not rows:
        gaps.append(
            f"no activities recorded between {start_date.isoformat()} and {end_date.isoformat()}"
            + (f" for type={activity_type}" if activity_type else "")
        )
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "type": activity_type,
        "count": len(rows),
        "hard_session_count": sum(1 for row in rows if row["hard_session"]),
        "rows": rows,
        "data_gaps": gaps,
    }


def get_readiness_inputs(conn: duckdb.DuckDBPyConnection, as_of: date | None = None) -> dict[str, Any]:
    """One bundle of everything the readiness rubric in SPEC §8 needs."""
    as_of = as_of or _latest_date(conn, "daily") or date.today()
    gaps: list[str] = []

    sleep_row = _one(
        conn,
        f"SELECT sleep_score, total_min, validation, {SLEEP_TRUSTWORTHY_SQL} AS trustworthy "
        "FROM sleep WHERE date = ?",
        (as_of,),
    )
    if sleep_row is None:
        gaps.append(f"no sleep on {as_of.isoformat()}")
        sleep = {"score": None, "total_min": None, "trustworthy": False, "validation": None}
    else:
        sleep = {
            "score": sleep_row["sleep_score"],
            "total_min": sleep_row["total_min"],
            "trustworthy": bool(sleep_row["trustworthy"]),
            "validation": sleep_row["validation"],
        }

    hrv_row = _one(
        conn,
        "SELECT last_night_avg, status, baseline_low, baseline_high FROM hrv WHERE date = ?",
        (as_of,),
    )
    baseline = _one(
        conn,
        "SELECT baseline_low, baseline_high FROM hrv "
        "WHERE date <= ? AND baseline_low IS NOT NULL ORDER BY date DESC LIMIT 1",
        (as_of,),
    ) or {"baseline_low": None, "baseline_high": None}
    if hrv_row is None:
        gaps.append(f"no HRV on {as_of.isoformat()}")
    hrv = {
        "last_night_avg": (hrv_row or {}).get("last_night_avg"),
        "mean_7d": _hrv_mean(conn, as_of, HRV_SHORT_WINDOW_DAYS),
        "mean_60d": _hrv_mean(conn, as_of, HRV_LONG_WINDOW_DAYS),
        "status": (hrv_row or {}).get("status"),
        "baseline_low": (hrv_row or baseline)["baseline_low"],
        "baseline_high": (hrv_row or baseline)["baseline_high"],
    }
    hrv["vs_baseline"] = derived.hrv_vs_baseline(
        hrv["last_night_avg"], hrv["baseline_low"], hrv["baseline_high"]
    )

    daily_row = _one(
        conn,
        "SELECT resting_hr, body_battery_high, body_battery_low FROM daily WHERE date = ?",
        (as_of,),
    )
    if daily_row is None:
        gaps.append(f"no daily metrics on {as_of.isoformat()}")
    trailing = _one(
        conn,
        "SELECT avg(resting_hr) AS mean_30d, count(*) AS n FROM daily "
        "WHERE date BETWEEN ? AND ? AND resting_hr IS NOT NULL",
        (as_of - timedelta(days=RESTING_HR_WINDOW_DAYS), as_of - timedelta(days=1)),
    )
    mean_30d = round(trailing["mean_30d"], 1) if trailing and trailing["mean_30d"] is not None else None
    if trailing and trailing["n"] < RESTING_HR_WINDOW_DAYS:
        gaps.append(
            f"resting HR baseline built from {trailing['n']} of {RESTING_HR_WINDOW_DAYS} days"
        )
    resting = {
        "today": (daily_row or {}).get("resting_hr"),
        "mean_30d": mean_30d,
        "delta": derived.resting_hr_delta((daily_row or {}).get("resting_hr"), mean_30d),
    }

    reference = derived.reference_instant(as_of)
    hard = _one(
        conn,
        f"SELECT {', '.join(ACTIVITY_COLUMNS)} FROM activities "
        f"WHERE start_time <= ? AND {HARD_SESSION_SQL} ORDER BY start_time DESC LIMIT 1",
        (reference,),
    )
    if hard is None:
        last_hard = None
        gaps.append("no hard session on record")
    else:
        hours_ago = derived.hours_between(datetime.fromisoformat(hard["start_time"]), reference)
        watch_hours = hard["recovery_time_hours"]
        recovery = watch_hours if watch_hours is not None else derived.ASSUMED_RECOVERY_HOURS
        last_hard = {
            "activity_id": hard["activity_id"],
            "type": hard["type"],
            "start_time": hard["start_time"],
            "hours_ago": hours_ago,
            "recovery_time_hours": hard["recovery_time_hours"],
            "recovery_remaining_hours": max(0, round(recovery - hours_ago)),
            "recovery_basis": "watch" if watch_hours is not None else "assumed",
            "aerobic_te": hard["aerobic_te"],
            "anaerobic_te": hard["anaerobic_te"],
        }

    return {
        "date": as_of.isoformat(),
        "reference_time": reference.isoformat(sep=" ", timespec="minutes"),
        "verdict": derived.readiness_verdict(sleep, hrv, resting, last_hard),
        "sleep": sleep,
        "hrv": hrv,
        "resting_hr": resting,
        "last_hard_session": last_hard,
        "body_battery": {
            "high": (daily_row or {}).get("body_battery_high"),
            "low": (daily_row or {}).get("body_battery_low"),
        },
        "data_gaps": gaps,
    }


# ------------------------------------------------------------------ internals

def _hrv_mean(conn: duckdb.DuckDBPyConnection, as_of: date, window_days: int) -> float | None:
    row = _one(
        conn,
        "SELECT avg(last_night_avg) AS mean FROM hrv "
        "WHERE date BETWEEN ? AND ? AND last_night_avg IS NOT NULL",
        (as_of - timedelta(days=window_days - 1), as_of),
    )
    return round(row["mean"], 1) if row and row["mean"] is not None else None


def _latest_date(conn: duckdb.DuckDBPyConnection, table: str) -> date | None:
    row = _one(conn, f"SELECT max(date) AS latest FROM {table}")
    latest = row["latest"] if row else None
    return date.fromisoformat(latest) if latest else None
