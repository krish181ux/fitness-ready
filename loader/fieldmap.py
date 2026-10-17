"""Declarative source-field -> column mapping (SPEC §6.2).

Garmin's export field names differ by account age, device and locale, so each
column names *several* candidate source keys with an explicit unit converter.
First candidate present with a non-null value wins; everything left over is
reported as an unmapped field rather than silently dropped.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Callable

Converter = Callable[[Any], Any]

# ---------------------------------------------------------------- converters

def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def as_int(value: Any) -> int | None:
    n = _num(value)
    return None if n is None else int(round(n))


def as_float(value: Any) -> float | None:
    return _num(value)


def as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def te_message(value: Any) -> str | None:
    """`IMPROVING_VO2_MAX_15` -> `IMPROVING_VO2_MAX`.

    The trailing number is a message id, not a training-effect value (SPEC
    implementation note, 17 Oct 2026), so it is dropped rather than stored
    where it could be read as one.
    """
    text = as_str(value)
    if text is None:
        return None
    return re.sub(r"_\d+$", "", text) or None


def sec_to_min(value: Any) -> float | None:
    n = _num(value)
    return None if n is None else round(n / 60.0, 2)


def ms_to_min(value: Any) -> float | None:
    n = _num(value)
    return None if n is None else round(n / 60_000.0, 2)


def sec_to_hours(value: Any) -> int | None:
    n = _num(value)
    return None if n is None else int(round(n / 3600.0))


def cm_to_km(value: Any) -> float | None:
    n = _num(value)
    return None if n is None else round(n / 100_000.0, 4)


def m_to_km(value: Any) -> float | None:
    n = _num(value)
    return None if n is None else round(n / 1000.0, 4)


def kj_to_kcal(value: Any) -> int | None:
    """The account export records activity energy in kilojoules.

    Verified against a 10.02 km / 79 min run: `calories` 3636.9 -> 869 kcal, and
    `bmrCalories` 481.9 -> 115 kcal for the same 79 minutes at rest. Both are
    right at 4.184 kJ/kcal and absurd without it. The API reports kcal under
    `activeKilocalories`, which is mapped separately.

    Garmin truncates rather than rounds: the same two activities read 619.89 ->
    619 and 373.54 -> 373 kcal from the API. The 0.01 margin keeps a value that
    was itself rounded from whole kcal (as the fixture writes it) on its integer.
    """
    n = _num(value)
    return None if n is None else int(n / 4.184 + 0.01)


def cm_to_m(value: Any) -> float | None:
    n = _num(value)
    return None if n is None else round(n / 100.0, 1)


def m_to_m(value: Any) -> float | None:
    """Metres, rounded like `cm_to_m` so API and export elevations agree."""
    n = _num(value)
    return None if n is None else round(n, 1)


def damps_to_kmh(value: Any) -> float | None:
    """The export's `avgSpeed` is in tenths of the API's m/s (decametres per second).

    Same run, both sources: export `avgSpeed` 0.1985, API `averageSpeed` 1.985 m/s,
    and 7.1566 km in 60.08 min is 7.15 km/h -- which only x36 reproduces.
    """
    n = _num(value)
    return None if n is None else round(n * 36.0, 2)


def mps_to_kmh(value: Any) -> float | None:
    n = _num(value)
    return None if n is None else round(n * 3.6, 2)


def as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    ts = as_timestamp(value)
    return ts.date() if ts else None


def as_timestamp(value: Any) -> datetime | None:
    """Parse Garmin timestamps: epoch millis, epoch seconds, or ISO-8601 text."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000.0 if abs(value) > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return as_timestamp(int(text))
    text = text.replace("Z", "+00:00").replace(".0+00:00", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed.replace(tzinfo=None)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# Garmin spells the same sleep-confirmation state several ways; normalise to the
# vocabulary in SPEC §6.2 so the trust rule in §6.3 stays a simple set test.
VALIDATION_ALIASES = {
    "ENHANCED_CONFIRMED_FINAL": "ENHANCED_FINAL",
    "ENHANCED_CONFIRMED_TENTATIVE": "ENHANCED_TENTATIVE",
    "AUTO_CONFIRMED_FINAL": "AUTO_FINAL",
    "AUTO_CONFIRMED_TENTATIVE": "AUTO_TENTATIVE",
    "OFFWRIST": "OFF_WRIST",
    "OFF_WRIST_TENTATIVE": "OFF_WRIST",
    # Seen in the real export: a night the user confirmed by hand, which the
    # SPEC §6.3 trust rule must treat exactly like MANUAL.
    "MANUALLY_CONFIRMED": "MANUAL",
    "MANUALLY_CORRECTED": "MANUAL",
}


def as_validation(value: Any) -> str | None:
    text = as_str(value)
    if text is None:
        return None
    text = text.upper().replace("-", "_").replace(" ", "_")
    return VALIDATION_ALIASES.get(text, text)


def as_activity_type(value: Any) -> str | None:
    """Garmin gives either a string or ``{"typeKey": "running", ...}``."""
    if isinstance(value, dict):
        value = value.get("typeKey") or value.get("key") or value.get("typeId")
    text = as_str(value)
    if text is None:
        return None
    return text.lower().replace(" ", "_")


# ------------------------------------------------------------- column specs
# column -> ((source_key, converter), ...) in priority order.
Spec = dict[str, tuple[tuple[str, Converter], ...]]

DAILY: Spec = {
    "date": (("calendarDate", as_date), ("calendar_date", as_date), ("summaryDate", as_date)),
    "resting_hr": (("restingHeartRate", as_int), ("restingHeartRateTimestamp.value", as_int)),
    "min_hr": (("minHeartRate", as_int), ("minAvgHeartRate", as_int)),
    "max_hr": (("maxHeartRate", as_int), ("maxAvgHeartRate", as_int)),
    "steps": (("totalSteps", as_int), ("steps", as_int)),
    "avg_stress": (("averageStressLevel", as_int), ("avgStressLevel", as_int)),
    "body_battery_high": (("bodyBatteryHighestValue", as_int), ("bodyBatteryHigh", as_int)),
    "body_battery_low": (("bodyBatteryLowestValue", as_int), ("bodyBatteryLow", as_int)),
    "active_calories": (("activeKilocalories", as_int), ("activeCalories", as_int)),
    # intensity_minutes is derived below (Garmin weights vigorous double).
    "intensity_minutes": (("intensityMinutes", as_int), ("totalIntensityMinutes", as_int)),
}

SLEEP: Spec = {
    "date": (("calendarDate", as_date), ("sleepStartTimestampLocal", as_date)),
    "sleep_start": (("sleepStartTimestampLocal", as_timestamp), ("sleepStartTimestampGMT", as_timestamp)),
    "sleep_end": (("sleepEndTimestampLocal", as_timestamp), ("sleepEndTimestampGMT", as_timestamp)),
    "total_min": (("sleepTimeSeconds", sec_to_min), ("totalSleepSeconds", sec_to_min)),
    "deep_min": (("deepSleepSeconds", sec_to_min),),
    "light_min": (("lightSleepSeconds", sec_to_min),),
    "rem_min": (("remSleepSeconds", sec_to_min),),
    "awake_min": (("awakeSleepSeconds", sec_to_min), ("awakeSeconds", sec_to_min)),
    "sleep_score": (
        ("sleepScores.overall.value", as_int),
        ("sleepScores.overallScore", as_int),
        ("overallSleepScore", as_int),
        ("sleepScore", as_int),
    ),
    "avg_spo2": (
        ("spo2SleepSummary.averageSPO2", as_float),
        ("averageSpO2Value", as_float),
        ("averageSpo2", as_float),
    ),
    "avg_respiration": (("averageRespirationValue", as_float), ("averageRespiration", as_float)),
    "validation": (("sleepWindowConfirmationType", as_validation), ("validation", as_validation)),
}

HRV: Spec = {
    "date": (("calendarDate", as_date), ("startTimestampLocal", as_date)),
    "last_night_avg": (("lastNightAvg", as_int), ("lastNightAverage", as_int)),
    "last_night_5min_high": (("lastNight5MinHigh", as_int), ("lastNightFiveMinHigh", as_int)),
    "weekly_avg": (("weeklyAvg", as_int), ("weeklyAverage", as_int)),
    "status": (("status", as_str), ("hrvStatus", as_str)),
    "baseline_low": (("baseline.balancedLow", as_int), ("baselineBalancedLow", as_int), ("baseline.lowUpper", as_int)),
    "baseline_high": (("baseline.balancedUpper", as_int), ("baselineBalancedUpper", as_int)),
}

ACTIVITIES: Spec = {
    "activity_id": (("activityId", as_int), ("id", as_int)),
    "start_time": (
        ("startTimeLocal", as_timestamp),
        ("beginTimestamp", as_timestamp),
        ("startTimeGMT", as_timestamp),
    ),
    "type": (("activityType", as_activity_type), ("activityTypeDTO.typeKey", as_activity_type)),
    "duration_min": (("duration", ms_to_min), ("durationSeconds", sec_to_min), ("elapsedDuration", ms_to_min)),
    "distance_km": (("distance", cm_to_km), ("distanceMeters", m_to_km)),
    "avg_hr": (("avgHr", as_int), ("averageHR", as_int)),
    "max_hr": (("maxHr", as_int), ("maxHR", as_int)),
    "calories": (("activeKilocalories", as_int), ("calories", kj_to_kcal)),
    "aerobic_te": (("aerobicTrainingEffect", as_float),),
    "anaerobic_te": (("anaerobicTrainingEffect", as_float),),
    "recovery_time_hours": (
        ("activityRecoveryHours", as_int),
        ("recoveryTimeHours", as_int),
        ("recoveryTime", as_int),
        ("recoveryTimeSeconds", sec_to_hours),
    ),
    "avg_speed_kmh": (("avgSpeed", damps_to_kmh), ("averageSpeedMps", mps_to_kmh)),
    "elevation_gain_m": (("elevationGain", cm_to_m), ("elevationGainMeters", m_to_m)),
    # Minutes at or above zone 4 (153 bpm for this profile). The account export
    # carries no numeric training effect, so this is what backs the hard-session
    # rule in SPEC §6.3 -- see loader.load_garmin._derive_activities.
    "hard_minutes": (("hardMinutes", as_float),),
    # The vivoactive 5 sends no numeric training effect or recovery time, only
    # these labels, and the Body Battery a session cost (17 Oct 2026).
    "body_battery_impact": (("differenceBodyBattery", as_int),),
    "te_label": (("trainingEffectLabel", as_str),),
    "aerobic_te_message": (("aerobicTrainingEffectMessage", te_message),),
    "anaerobic_te_message": (("anaerobicTrainingEffectMessage", te_message),),
}

SPECS: dict[str, Spec] = {
    "daily": DAILY,
    "sleep": SLEEP,
    "hrv": HRV,
    "activities": ACTIVITIES,
}

# Source keys that carry no signal for our schema; excluded from the unmapped report.
NOISE_KEYS = {
    "userProfilePK", "uuid", "deviceId", "timeOffsetSleepRespiration", "sleepLevels",
    "wellnessEndTimeGmt", "wellnessStartTimeGmt", "durationInMilliseconds",
    # Consumed by the derivers in loader.load_garmin rather than by a column spec.
    "moderateIntensityMinutes", "vigorousIntensityMinutes",
    "hrTimeInZone_4", "hrTimeInZone_5", "hrTimeInZone_6",
}


def apply_spec(record: dict[str, Any], spec: Spec) -> tuple[dict[str, Any], set[str]]:
    """Map one flattened record. Returns (row, unmapped source keys)."""
    row: dict[str, Any] = {}
    # Every candidate key is part of our known vocabulary, even the ones a given
    # record did not need -- otherwise a lower-priority alias reads as unmapped.
    known = {key for candidates in spec.values() for key, _ in candidates}
    for column, candidates in spec.items():
        for key, convert in candidates:
            if key in record and record[key] is not None:
                value = convert(record[key])
                if value is not None:
                    row[column] = value
                    break
    unmapped = {
        key for key, value in record.items()
        if key not in known and value is not None and key not in NOISE_KEYS
    }
    return row, unmapped
