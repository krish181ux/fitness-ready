"""Connect API responses -> records the loader already understands (SPEC §6.2).

The API and the account export use the same key names in different units:
`duration` is seconds here and milliseconds there, `distance` metres here and
centimetres there, and so on (measured on the same activity from both sources;
see the SPEC implementation note). Passing an API record through unchanged
would load a 9 km run as 0.09 km.

So every normaliser *builds* a record from an allow-list instead of copying
the response. Unit-bearing values are emitted only under keys that the export
never uses (`durationSeconds`, `distanceMeters`, ...), each mapped in
`loader.fieldmap` with its own converter. Unit-free values (bpm, steps, stress,
epoch timestamps, sleep seconds) keep their shared names, because both sources
agree on them. The unmodified response stays on disk next to the record.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Iterable

Record = dict[str, Any]

# Garmin's per-activity HR zones, in seconds from the API (milliseconds in the export).
HARD_ZONE_KEYS = ("hrTimeInZone_4", "hrTimeInZone_5", "hrTimeInZone_6")


def _pick(source: dict[str, Any], keys: Iterable[str]) -> Record:
    return {key: source[key] for key in keys if source.get(key) is not None}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# -------------------------------------------------------------------- daily

DAILY_KEYS = (
    "calendarDate", "restingHeartRate", "minHeartRate", "maxHeartRate", "totalSteps",
    "averageStressLevel", "bodyBatteryHighestValue", "bodyBatteryLowestValue",
    "activeKilocalories", "moderateIntensityMinutes", "vigorousIntensityMinutes",
)
DAILY_MEASURES = DAILY_KEYS[1:]


def daily_summary(payload: Any, day: date) -> list[Record]:
    if not isinstance(payload, dict):
        return []
    record = _pick(payload, DAILY_KEYS)
    # Stress is a 0-100 scale; anything below zero is a sentinel, not a level.
    stress = _number(record.get("averageStressLevel"))
    if stress is not None and stress < 0:
        del record["averageStressLevel"]
    if not any(key in record for key in DAILY_MEASURES):
        return []  # a day the watch recorded nothing for (or one not yet synced)
    record.setdefault("calendarDate", day.isoformat())
    return [record]


# -------------------------------------------------------------------- sleep

SLEEP_KEYS = (
    "calendarDate", "sleepStartTimestampLocal", "sleepEndTimestampLocal",
    "sleepTimeSeconds", "deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds",
    "awakeSleepSeconds", "averageSpO2Value", "averageRespirationValue",
    "sleepWindowConfirmationType",
)
SLEEP_MEASURES = ("sleepTimeSeconds", "deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds")


def sleep(payload: Any, day: date) -> list[Record]:
    dto = payload.get("dailySleepDTO") if isinstance(payload, dict) else None
    if not isinstance(dto, dict):
        return []
    record = _pick(dto, SLEEP_KEYS)
    score = ((dto.get("sleepScores") or {}).get("overall") or {}).get("value")
    if score is not None:
        record["sleepScores"] = {"overall": {"value": score}}
    # An all-null night would load as validation UNKNOWN with no duration --
    # a row that looks present but says nothing. Leave the gap visible instead.
    if not any(key in record for key in SLEEP_MEASURES) and score is None:
        return []
    record.setdefault("calendarDate", day.isoformat())
    return [record]


# ---------------------------------------------------------------------- hrv

HRV_KEYS = ("calendarDate", "lastNightAvg", "lastNight5MinHigh", "weeklyAvg", "status")


def hrv(payload: Any, day: date) -> list[Record]:
    summary = payload.get("hrvSummary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or summary.get("lastNightAvg") is None:
        return []
    record = _pick(summary, HRV_KEYS)
    baseline = _pick(summary.get("baseline") or {}, ("balancedLow", "balancedUpper"))
    if baseline:
        record["baseline"] = baseline
    record.setdefault("calendarDate", day.isoformat())
    return [record]


# --------------------------------------------------------------- activities

# API key -> the API-only key loader.fieldmap reads it under.
ACTIVITY_RENAMES = {
    "duration": "durationSeconds",        # s   (export: ms)
    "distance": "distanceMeters",         # m   (export: cm)
    "averageSpeed": "averageSpeedMps",    # m/s (export `avgSpeed`: dam/s)
    "elevationGain": "elevationGainMeters",  # m (export: cm)
    "calories": "activeKilocalories",     # kcal (export: kJ)
}
ACTIVITY_KEYS = (
    "activityId", "startTimeLocal", "averageHR", "maxHR",
    "aerobicTrainingEffect", "anaerobicTrainingEffect",
    # What the vivoactive 5 sends instead of numeric training effect (17 Oct 2026).
    "differenceBodyBattery", "trainingEffectLabel",
    "aerobicTrainingEffectMessage", "anaerobicTrainingEffectMessage",
)


def activity(item: dict[str, Any]) -> Record | None:
    if item.get("activityId") is None:
        return None
    record = _pick(item, ACTIVITY_KEYS)
    kind = item.get("activityType")
    type_key = kind.get("typeKey") if isinstance(kind, dict) else kind
    if type_key:
        record["activityType"] = type_key
    for source, target in ACTIVITY_RENAMES.items():
        value = _number(item.get(source))
        if value is not None:
            record[target] = value
    zones = [_number(item.get(key)) for key in HARD_ZONE_KEYS]
    if any(value is not None for value in zones):
        record["hardMinutes"] = round(sum(value or 0.0 for value in zones) / 60.0, 1)
    return record


def activities(payload: Any, day: date) -> list[Record]:
    if not isinstance(payload, list):
        return []
    return [record for record in map(activity, payload) if record is not None]


def activity_day(item: dict[str, Any]) -> date | None:
    """The local calendar day an API activity belongs to."""
    start = item.get("startTimeLocal")
    if not isinstance(start, str) or len(start) < 10:
        return None
    try:
        return date.fromisoformat(start[:10])
    except ValueError:
        return None


# ------------------------------------------------------------- user metrics

def max_metrics(payload: Any, day: date) -> list[Record]:
    records: list[Record] = []
    for item in payload if isinstance(payload, list) else []:
        generic = (item or {}).get("generic") or {}
        record = _pick(generic, ("calendarDate", "vo2MaxPreciseValue", "vo2MaxValue"))
        if "vo2MaxPreciseValue" in record or "vo2MaxValue" in record:
            record.setdefault("calendarDate", day.isoformat())
            records.append(record)
    return records


def fitness_age(payload: Any, day: date) -> list[Record]:
    value = _number(payload.get("fitnessAge")) if isinstance(payload, dict) else None
    if value is None:
        return []
    # The response carries no calendar date of its own; it is as of the day asked.
    return [{"calendarDate": day.isoformat(), "fitnessAge": round(value, 1)}]


NORMALISERS: dict[str, Callable[[Any, date], list[Record]]] = {
    "daily_summary": daily_summary,
    "sleep": sleep,
    "hrv": hrv,
    "activities": activities,
    "max_metrics": max_metrics,
    "fitness_age": fitness_age,
}


def normalise(endpoint: str, payload: Any, day: date) -> list[Record]:
    return NORMALISERS[endpoint](payload, day)
