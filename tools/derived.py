"""Derived definitions from SPEC §6.3.

These are the single source of truth for "hard session", "resting HR delta",
"HRV vs baseline", "sleep is trustworthy" and the readiness verdict. The tools server
and the tests both import from here, so a definition can only change in one
place. Each rule is expressed twice -- once as a SQL fragment for DuckDB, once as
a Python predicate for row-level use -- and the tests assert the two agree.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Sequence

# Tool contract (SPEC §7). 366, not 365: ranges are inclusive of both endpoints,
# so "the past year" ending on the as-of date spans 366 days
# (2025-09-13..2026-09-13). A 365-day cap rejected every "past year" question.
MAX_RANGE_DAYS = 366

# Rolling windows (SPEC §6.3).
RESTING_HR_WINDOW_DAYS = 30
HRV_SHORT_WINDOW_DAYS = 7
HRV_LONG_WINDOW_DAYS = 60

# Sleep the agent must not trust (SPEC §6.3).
UNTRUSTWORTHY_VALIDATIONS = ("OFF_WRIST", "MANUAL")

# `get_readiness_inputs` reports "hours ago" for the last hard session. Evals pin
# `as_of_date`, so the reference instant must not depend on wall-clock time:
# we anchor every as-of date to early evening, when the next day gets planned.
REFERENCE_HOUR = 18

# --- hard session ----------------------------------------------------------
HARD_SESSION_AEROBIC_TE = 3.0
HARD_SESSION_ANAEROBIC_TE = 3.0
HARD_SESSION_RECOVERY_HOURS = 24

# Training effect and recovery time are absent from the account export -- it
# carries only message enums like "IMPROVING_LACTATE_THRESHOLD_12", whose
# suffixes are message ids, not values. So the rule also accepts time at or
# above HR zone 4 (153 bpm on this profile), which every activity records.
# 20 minutes is the standard threshold for a genuinely hard session and marks
# 11% of the real history, concentrated in badminton and running.
HARD_SESSION_ZONE4_MINUTES = 20.0

# Strength sessions never reach zone 4 for long (median 0.75 min) and the watch
# reports no training effect or recovery time for them, so under the arms above
# 1 of 140 real strength sessions counted as hard. A long session at a sustained
# elevated heart rate is the signal that is left. Calibrated on the real history
# (140 sessions, Apr 2025 - Oct 2026): 50 min and 115 bpm flag 29 (21%), about
# the top fifth. 45 / 110 flagged 41%, too loose to mean "hard". Average HR
# includes rest between sets and wrist HR is noisy under load, so this is a
# proxy, not a measurement.
HARD_STRENGTH_TYPE = "strength_training"
HARD_STRENGTH_MIN_DURATION = 50.0
HARD_STRENGTH_MIN_AVG_HR = 115

HARD_SESSION_SQL = (
    f"(coalesce(aerobic_te, 0) >= {HARD_SESSION_AEROBIC_TE}"
    f" OR coalesce(anaerobic_te, 0) >= {HARD_SESSION_ANAEROBIC_TE}"
    f" OR coalesce(recovery_time_hours, 0) >= {HARD_SESSION_RECOVERY_HOURS}"
    f" OR coalesce(hard_minutes, 0) >= {HARD_SESSION_ZONE4_MINUTES}"
    f" OR (type = '{HARD_STRENGTH_TYPE}'"
    f" AND coalesce(duration_min, 0) >= {HARD_STRENGTH_MIN_DURATION}"
    f" AND coalesce(avg_hr, 0) >= {HARD_STRENGTH_MIN_AVG_HR}))"
)


def is_hard_session(activity: Mapping[str, Any]) -> bool:
    """SPEC §6.3. Applies to every activity type, plus a strength-only arm."""
    return (
        (activity.get("aerobic_te") or 0) >= HARD_SESSION_AEROBIC_TE
        or (activity.get("anaerobic_te") or 0) >= HARD_SESSION_ANAEROBIC_TE
        or (activity.get("recovery_time_hours") or 0) >= HARD_SESSION_RECOVERY_HOURS
        or (activity.get("hard_minutes") or 0) >= HARD_SESSION_ZONE4_MINUTES
        or (
            activity.get("type") == HARD_STRENGTH_TYPE
            and (activity.get("duration_min") or 0) >= HARD_STRENGTH_MIN_DURATION
            and (activity.get("avg_hr") or 0) >= HARD_STRENGTH_MIN_AVG_HR
        )
    )


# --- sleep trust -----------------------------------------------------------
SLEEP_TRUSTWORTHY_SQL = (
    "(validation NOT IN ("
    + ", ".join(f"'{v}'" for v in UNTRUSTWORTHY_VALIDATIONS)
    + ") AND coalesce(total_min, 0) > 0)"
)


def is_sleep_trustworthy(validation: str | None, total_min: float | None) -> bool:
    """SPEC §6.3. A NULL validation is not trustworthy: we don't know how it was recorded."""
    if validation is None:
        return False
    return validation.upper() not in UNTRUSTWORTHY_VALIDATIONS and (total_min or 0) > 0


# --- resting HR ------------------------------------------------------------
def resting_hr_delta(today: int | None, trailing_mean: float | None) -> float | None:
    """Today's resting HR minus the trailing 30-day mean, excluding today."""
    if today is None or trailing_mean is None:
        return None
    return round(today - trailing_mean, 1)


# --- HRV -------------------------------------------------------------------
def hrv_vs_baseline(
    last_night_avg: int | None, baseline_low: int | None, baseline_high: int | None
) -> str:
    """Returns 'below' | 'within' | 'above' | 'unknown' relative to the baseline band."""
    if last_night_avg is None or baseline_low is None or baseline_high is None:
        return "unknown"
    if last_night_avg < baseline_low:
        return "below"
    if last_night_avg > baseline_high:
        return "above"
    return "within"


# --- split sessions ---------------------------------------------------------
# Stopping and restarting the watch mid-run saves two activities. On 15 Sep 2026
# a 12.4 km run was saved as 3.4 km + 9.0 km, two minutes apart, so "longest run"
# read 9 km. Consecutive activities of the same type that start within this many
# minutes of the previous one ending are one session.
SESSION_MERGE_GAP_MIN = 10.0


def _end(row: Mapping[str, Any]) -> datetime:
    # Timer time, so a paused part ends early and the gap reads long: errs
    # toward keeping parts apart.
    return datetime.fromisoformat(row["start_time"]) + timedelta(minutes=row.get("duration_min") or 0)


def merge_split_sessions(rows: list[Mapping[str, Any]],
                         max_gap_min: float = SESSION_MERGE_GAP_MIN) -> list[dict[str, Any]]:
    """Join activities saved in parts into one session row. `rows` sorted by start_time.

    A merged row keeps the first part's activity_id and start, sums duration,
    distance, calories, ascent, hard minutes and Body Battery impact, weights
    average HR by duration, and lists every part in `parts`. Rows that were not
    split come back unchanged (plus `parts` of one).
    """
    groups: list[list[Mapping[str, Any]]] = []
    for row in rows:
        last = groups[-1][-1] if groups else None
        if (last is not None and row["type"] == last["type"]
                and 0 <= (datetime.fromisoformat(row["start_time"]) - _end(last)).total_seconds() / 60
                <= max_gap_min):
            groups[-1].append(row)
        else:
            groups.append([row])
    return [_merge(group) for group in groups]


def _merge(parts: list[Mapping[str, Any]]) -> dict[str, Any]:
    if len(parts) == 1:
        return {**parts[0], "parts": [parts[0]["activity_id"]]}

    def total(key: str) -> float | None:
        values = [p.get(key) for p in parts if p.get(key) is not None]
        return round(sum(values), 2) if values else None

    def biggest(key: str) -> Any:
        values = [p.get(key) for p in parts if p.get(key) is not None]
        return max(values) if values else None

    duration = total("duration_min") or 0
    weighted = [(p["avg_hr"], p.get("duration_min") or 0) for p in parts if p.get("avg_hr")]
    longest = max(parts, key=lambda p: p.get("duration_min") or 0)
    merged = {
        **{k: longest.get(k) for k in ("te_label", "aerobic_te_message", "anaerobic_te_message")},
        "activity_id": parts[0]["activity_id"],
        "start_time": parts[0]["start_time"],
        "type": parts[0]["type"],
        "duration_min": duration,
        "distance_km": total("distance_km"),
        "avg_hr": round(sum(h * d for h, d in weighted) / sum(d for _, d in weighted))
                  if weighted and sum(d for _, d in weighted) else None,
        "max_hr": biggest("max_hr"),
        "calories": total("calories"),
        "aerobic_te": biggest("aerobic_te"),
        "anaerobic_te": biggest("anaerobic_te"),
        "recovery_time_hours": biggest("recovery_time_hours"),
        "elevation_gain_m": total("elevation_gain_m"),
        "hard_minutes": total("hard_minutes"),
        "body_battery_impact": total("body_battery_impact"),
        "parts": [p["activity_id"] for p in parts],
        "gap_min": round(sum(
            (datetime.fromisoformat(b["start_time"]) - _end(a)).total_seconds() / 60
            for a, b in zip(parts, parts[1:])), 1),
    }
    distance = merged["distance_km"]
    merged["avg_speed_kmh"] = round(distance / duration * 60, 2) if distance and duration else None
    merged["hard_session"] = is_hard_session(merged) or any(p.get("hard_session") for p in parts)
    return merged


# --- readiness verdict (SPEC §8) -------------------------------------------
# The Green/Amber/Red rubric used to live only in the prompt, so the same inputs
# could come back a different colour on a different run. It is computed here
# and returned by `get_readiness_inputs`; the agent reports it, not derives it.
READINESS_SLEEP_SCORE_MIN = 70
READINESS_RESTING_HR_DELTA_MAX = 3.0
READINESS_MAX_MISSING = 1  # more missing checks than this -> insufficient_data
# The real watch reports recovery time for no activity at all (0 of 260), so
# remaining recovery read as 0 and the Recovery check could never fail: a hard
# run that morning counted as fully recovered. When the watch gives no figure,
# assume this window -- the same 24 h that makes a session hard under §6.3.
# A proxy, labelled as one in the verdict.
ASSUMED_RECOVERY_HOURS = 24


def readiness_verdict(
    sleep: Mapping[str, Any],
    hrv: Mapping[str, Any],
    resting_hr: Mapping[str, Any],
    last_hard_session: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Score the four readiness checks and the colour they add up to.

    Each check passes, fails, or is missing. A missing input is not a failure:
    the watch reports no HRV baseline for its first weeks of overnight wear, and
    counting that as a failed check made every such day Amber at best. Instead,
    one missing check leaves the colour to the other three, and two or more give
    `insufficient_data`. Untrustworthy sleep is a failure, not missing data: the
    night happened and was recorded badly. Red is two or more failures; the old
    "untrustworthy sleep plus any miss" clause is the same thing.
    """
    checks: list[dict[str, Any]] = []

    def check(name: str, result: str, value: str, rule: str) -> None:
        checks.append({"name": name, "result": result, "value": value, "rule": rule})

    sleep_rule = f"trustworthy and score >= {READINESS_SLEEP_SCORE_MIN}"
    if sleep.get("validation") is None and not sleep.get("total_min"):
        check("sleep", "missing", "no sleep recorded", sleep_rule)
    elif not sleep.get("trustworthy"):
        check("sleep", "fail", f"untrustworthy ({sleep.get('validation')})", sleep_rule)
    elif sleep.get("score") is None:
        check("sleep", "missing", "no sleep score", sleep_rule)
    else:
        result = "pass" if sleep["score"] >= READINESS_SLEEP_SCORE_MIN else "fail"
        check("sleep", result, f"score {sleep['score']}", sleep_rule)

    hrv_rule = "last night inside the baseline band"
    position = hrv_vs_baseline(
        hrv.get("last_night_avg"), hrv.get("baseline_low"), hrv.get("baseline_high")
    )
    if position == "unknown":
        why = "no HRV last night" if hrv.get("last_night_avg") is None else "no baseline yet"
        check("hrv", "missing", why, hrv_rule)
    else:
        check(
            "hrv",
            "pass" if position == "within" else "fail",
            f"{hrv['last_night_avg']} ms, {position} "
            f"{hrv['baseline_low']}-{hrv['baseline_high']}",
            hrv_rule,
        )

    rhr_rule = f"delta <= +{READINESS_RESTING_HR_DELTA_MAX:g} bpm vs the 30-day mean"
    delta = resting_hr.get("delta")
    if delta is None:
        check("resting_hr", "missing", "no resting HR or no baseline", rhr_rule)
    else:
        result = "pass" if delta <= READINESS_RESTING_HR_DELTA_MAX else "fail"
        check("resting_hr", result, f"{delta:+g} bpm", rhr_rule)

    recovery_rule = "no recovery time remaining from the last hard session"
    if last_hard_session is None:
        check("recovery", "pass", "no hard session on record", recovery_rule)
    else:
        remaining = last_hard_session.get("recovery_remaining_hours") or 0
        value = f"{remaining} h remaining"
        if last_hard_session.get("recovery_basis") == "assumed":
            value += (f" (assumed {ASSUMED_RECOVERY_HOURS} h after a hard session "
                      f"{last_hard_session.get('hours_ago')} h ago; the watch gave no recovery time)")
        check("recovery", "pass" if remaining == 0 else "fail", value, recovery_rule)

    failed = [c["name"] for c in checks if c["result"] == "fail"]
    missing = [c["name"] for c in checks if c["result"] == "missing"]
    if len(missing) > READINESS_MAX_MISSING:
        colour = "insufficient_data"
    elif len(failed) >= 2:
        colour = "red"
    elif len(failed) == 1:
        colour = "amber"
    else:
        colour = "green"
    return {
        "colour": colour,
        "failed": failed,
        "missing": missing,
        "based_on": f"{len(checks) - len(missing)} of {len(checks)} checks",
        "checks": checks,
    }


# --- helpers shared by the tool endpoints ----------------------------------
def reference_instant(as_of: date) -> datetime:
    return datetime.combine(as_of, time(hour=REFERENCE_HOUR))


def hours_between(earlier: datetime, later: datetime) -> float:
    return round((later - earlier).total_seconds() / 3600.0, 1)


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def night_label(wake_day: date) -> str:
    """Sleep is dated by the wake day: the row dated D is the night of D-1 into D."""
    return f"night of {(wake_day - timedelta(days=1)).isoformat()} into {wake_day.isoformat()}"


def night_not_synced(wake_day: date) -> str:
    return (
        f"no sleep for the {night_label(wake_day)} yet. If that is last night, it is not "
        "synced: Garmin has it once the phone uploads, and the sync runs at 11:00 and 21:00. "
        "Say so; never report an earlier night as last night."
    )


def missing_dates(start: date, end: date, present: Iterable[date]) -> list[date]:
    have = set(present)
    return [day for day in date_range(start, end) if day not in have]


def describe_gaps(label: str, missing: Sequence[date], *, max_listed: int = 5) -> list[str]:
    """Human-readable data gaps, e.g. "no HRV on 2026-09-11" (SPEC §7)."""
    if not missing:
        return []
    if len(missing) <= max_listed:
        return [f"no {label} on {day.isoformat()}" for day in missing]
    return [
        f"no {label} on {len(missing)} days between "
        f"{missing[0].isoformat()} and {missing[-1].isoformat()}"
    ]
