"""Fetcher stage 2: API normalisation, incremental pull, and loading the result.

Every record here is synthetic. The unit ratios mirror what the real export and
API returned for the same activity (SPEC implementation note), but no value is
taken from anyone's data. No network: the session is a fake.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db import connect
from fetcher import normalise as nz
from fetcher.client import FetcherError
from fetcher.pull import pull, record_path, response_path
from fetcher.endpoints import BY_NAME
from loader.discovery import classify
from loader.load_garmin import load, rows_from_record

START_LOCAL = "2026-08-02 07:00:00"

# One synthetic 10 km run as the Connect API reports it: s, m, m/s, m, kcal.
API_RUN = {
    "activityId": 7_000_000_001,
    "activityName": "Morning Run",
    "startTimeLocal": START_LOCAL,
    "startTimeGMT": "2026-08-02 01:30:00",
    "activityType": {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
    "duration": 3000.0,
    "distance": 10000.0,
    "averageSpeed": 3.3333,
    "elevationGain": 42.5,
    "averageHR": 150.0,
    "maxHR": 178.0,
    "calories": 700.0,
    "aerobicTrainingEffect": None,
    "anaerobicTrainingEffect": None,
    "hrTimeInZone_4": 900.0,
    "hrTimeInZone_5": 300.0,
}

# The same run as the account export records it: ms, cm, dam/s, cm, kJ, and a
# local-wall-clock epoch. Garmin truncated 700.4 kcal to 700 in the API.
EXPORT_RUN = {
    "activityId": 7_000_000_001,
    "startTimeLocal": datetime(2026, 8, 2, 7, 0, tzinfo=timezone.utc).timestamp() * 1000,
    "activityType": "running",
    "duration": 3_000_000.0,
    "distance": 1_000_000.0,
    "avgSpeed": 0.33333,
    "elevationGain": 4250.0,
    "avgHr": 150.0,
    "maxHr": 178.0,
    "calories": 700.4 * 4.184,
    "hrTimeInZone_4": 900_000,
    "hrTimeInZone_5": 300_000,
    "hrTimeInZone_6": 0,
}


def _row(record):
    (row,), _ = rows_from_record("activities", record)
    return row


# --------------------------------------------------------------- activities

def test_api_run_loads_in_the_right_units():
    row = _row(nz.activity({**API_RUN, "duration": 4498.0, "distance": 9012.87}))
    assert row["distance_km"] == pytest.approx(9.01, abs=0.01)
    assert row["duration_min"] == pytest.approx(75.0, abs=0.1)


def test_api_run_maps_every_column():
    row = _row(nz.activity(API_RUN))
    assert row["type"] == "running"
    assert row["avg_speed_kmh"] == pytest.approx(12.0, abs=0.01)
    assert row["elevation_gain_m"] == pytest.approx(42.5)
    assert row["calories"] == 700
    assert row["avg_hr"] == 150 and row["max_hr"] == 178
    assert row["hard_minutes"] == pytest.approx(20.0)
    assert row["start_time"] == datetime(2026, 8, 2, 7, 0)


def test_no_unit_bearing_api_key_survives_under_its_export_name():
    record = nz.activity(API_RUN)
    for shared in ("duration", "distance", "averageSpeed", "elevationGain", "calories",
                   "hrTimeInZone_4", "hrTimeInZone_5"):
        assert shared not in record


def test_export_and_api_records_converge_to_the_same_row():
    assert _row(nz.activity(API_RUN)) == _row(EXPORT_RUN)


def test_activities_without_an_id_are_dropped():
    assert nz.activities([{**API_RUN, "activityId": None}, API_RUN], date(2026, 8, 2)) == [
        nz.activity(API_RUN)
    ]


# ------------------------------------------------------------ daily, sleep

DAY = date(2026, 8, 2)


def test_daily_summary_keeps_flat_fields_and_drops_negative_stress():
    (record,) = nz.daily_summary({
        "calendarDate": "2026-08-02", "restingHeartRate": 55, "totalSteps": 8000,
        "averageStressLevel": -1, "bodyBatteryHighestValue": 80, "userProfileId": 1,
    }, DAY)
    assert "averageStressLevel" not in record and "userProfileId" not in record
    (row,), _ = rows_from_record("daily", record)
    assert (row["resting_hr"], row["steps"], row["body_battery_high"]) == (55, 8000, 80)
    assert row.get("avg_stress") is None


def test_a_daily_summary_with_no_measurements_is_not_a_row():
    assert nz.daily_summary({"calendarDate": "2026-08-02", "restingHeartRate": None}, DAY) == []


def test_all_null_sleep_is_dropped_rather_than_loaded_as_trustworthy():
    payload = {"dailySleepDTO": {
        "calendarDate": "2026-08-02", "sleepTimeSeconds": None, "deepSleepSeconds": None,
        "sleepWindowConfirmationType": None, "sleepScores": None,
    }}
    assert nz.sleep(payload, DAY) == []


def test_sleep_is_unwrapped_with_its_score():
    payload = {"dailySleepDTO": {
        "calendarDate": "2026-08-02", "sleepTimeSeconds": 25_200, "deepSleepSeconds": 3_600,
        "sleepWindowConfirmationType": "ENHANCED_CONFIRMED_FINAL",
        "sleepScores": {"overall": {"value": 81, "qualifierKey": "GOOD"}},
    }, "sleepLevels": [{"x": 1}]}
    (record,) = nz.sleep(payload, DAY)
    (row,), _ = rows_from_record("sleep", record)
    assert (row["total_min"], row["deep_min"], row["sleep_score"]) == (420, 60, 81)
    assert row["validation"] == "ENHANCED_FINAL"


# ------------------------------------------------------------- hrv, metrics

def test_hrv_summary_is_unwrapped():
    payload = {"hrvSummary": {
        "calendarDate": "2026-08-02", "lastNightAvg": 52, "weeklyAvg": 55, "status": "BALANCED",
        "baseline": {"balancedLow": 48, "balancedUpper": 62, "markerValue": 0.4},
    }, "hrvReadings": []}
    (record,) = nz.hrv(payload, DAY)
    (row,), _ = rows_from_record("hrv", record)
    assert (row["last_night_avg"], row["baseline_low"], row["baseline_high"]) == (52, 48, 62)


@pytest.mark.parametrize("payload", [None, {}, {"hrvSummary": {"lastNightAvg": None}}])
def test_nights_without_hrv_produce_nothing(payload):
    assert nz.hrv(payload, DAY) == []


def test_fitness_age_is_dated_by_the_day_asked():
    (record,) = nz.fitness_age({"fitnessAge": 29.5, "chronologicalAge": 31}, DAY)
    rows, _ = rows_from_record("user_metrics", record)
    assert rows == [{"date": DAY, "metric": "fitness_age", "value": 29.5}]


def test_vo2max_comes_from_the_generic_block():
    (record,) = nz.max_metrics([{"generic": {"calendarDate": "2026-08-02", "vo2MaxPreciseValue": 47.3}}], DAY)
    rows, _ = rows_from_record("user_metrics", record)
    assert rows == [{"date": DAY, "metric": "vo2max", "value": 47.3}]


# ---------------------------------------------------------------- discovery

def test_normalised_records_classify_by_directory(tmp_path):
    for table in ("daily", "sleep", "hrv", "activities", "user_metrics"):
        assert classify(tmp_path / f"garmin-api-{table}" / "x_2026-08-02.json", tmp_path) == table


def test_raw_api_responses_are_never_loaded(tmp_path):
    assert classify(tmp_path / "api-responses" / "hrv" / "2026-08-02.json", tmp_path) is None
    assert classify(tmp_path / "api-responses" / "sleep" / "2026-08-02.json", tmp_path) is None


# --------------------------------------------------------------------- pull

class FakeSession:
    def __init__(self, fail: set[tuple[str, str]] | None = None):
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.fail = fail or set()

    def call(self, method, *args, **kwargs):
        self.calls.append((method, args))
        if (method, args[0]) in self.fail:
            raise FetcherError(f"{method} boom")
        day = args[0]
        if method == "get_user_summary":
            return {"calendarDate": day, "restingHeartRate": 55, "totalSteps": 7000}
        if method == "get_sleep_data":
            return {"dailySleepDTO": {"calendarDate": day, "sleepTimeSeconds": 25_200}}
        if method == "get_hrv_data":
            return None
        if method == "get_activities_by_date":
            return [API_RUN]
        if method == "get_max_metrics":
            return []
        if method == "get_fitnessage_data":
            return {"fitnessAge": 30.0}
        raise AssertionError(method)


def _methods(session):
    return [method for method, _ in session.calls]


def test_pull_writes_responses_and_records(tmp_path):
    session = FakeSession()
    stats = pull(session, date(2026, 8, 1), date(2026, 8, 3), tmp_path, today=date(2026, 9, 1))
    assert _methods(session).count("get_activities_by_date") == 1
    assert _methods(session).count("get_user_summary") == 3
    # The ranged activity call is split back into one file per day, empty days included.
    activities = BY_NAME["activities"]
    assert json.loads(response_path(tmp_path, activities, date(2026, 8, 1)).read_text()) == []
    assert json.loads(response_path(tmp_path, activities, date(2026, 8, 2)).read_text()) == [API_RUN]
    assert record_path(tmp_path, activities, date(2026, 8, 2)).exists()
    assert not record_path(tmp_path, BY_NAME["hrv"], date(2026, 8, 2)).exists()
    assert stats.records == {"daily": 3, "sleep": 3, "activities": 1, "user_metrics": 3}
    assert not stats.failed


def test_second_pull_reuses_old_days_but_refetches_the_last_two(tmp_path):
    today = date(2026, 8, 5)
    pull(FakeSession(), date(2026, 8, 1), today, tmp_path, today=today)
    again = FakeSession()
    stats = pull(again, date(2026, 8, 1), today, tmp_path, today=today)
    refetched = {args[0] for method, args in again.calls if method == "get_user_summary"}
    assert refetched == {"2026-08-04", "2026-08-05"}
    assert stats.reused["daily_summary"] == 3
    assert ("get_activities_by_date", ("2026-08-04", "2026-08-05")) in again.calls


def test_force_refetches_everything(tmp_path):
    today = date(2026, 8, 5)
    pull(FakeSession(), date(2026, 8, 1), today, tmp_path, today=today)
    again = FakeSession()
    pull(again, date(2026, 8, 1), today, tmp_path, today=today, force=True)
    assert _methods(again).count("get_user_summary") == 5


def test_a_failed_day_is_reported_left_missing_and_retried(tmp_path):
    today = date(2026, 9, 1)
    failing = FakeSession(fail={("get_user_summary", "2026-08-02")})
    stats = pull(failing, date(2026, 8, 1), date(2026, 8, 3), tmp_path, today=today)
    assert stats.failed == ["daily_summary 2026-08-02"]
    assert not response_path(tmp_path, BY_NAME["daily_summary"], date(2026, 8, 2)).exists()
    assert stats.records["daily"] == 2

    retry = FakeSession()
    pull(retry, date(2026, 8, 1), date(2026, 8, 3), tmp_path, today=today)
    assert [args for method, args in retry.calls if method == "get_user_summary"] == [("2026-08-02",)]


def test_pull_refuses_backwards_or_oversized_ranges(tmp_path):
    with pytest.raises(ValueError):
        pull(FakeSession(), date(2026, 8, 3), date(2026, 8, 1), tmp_path)
    with pytest.raises(ValueError):
        pull(FakeSession(), date(2025, 1, 1), date(2026, 8, 1), tmp_path)


def test_pulled_files_load_and_converge_with_the_export(tmp_path):
    out = tmp_path / "api"
    pull(FakeSession(), date(2026, 8, 1), date(2026, 8, 3), out, today=date(2026, 9, 1))
    export = tmp_path / "export" / "DI_CONNECT" / "DI-Connect-Fitness"
    export.mkdir(parents=True)
    (export / "someone_summarizedActivities.json").write_text(
        json.dumps([{"summarizedActivitiesExport": [EXPORT_RUN]}])
    )

    columns = "type, duration_min, distance_km, avg_speed_kmh, elevation_gain_m, calories, hard_minutes"
    with connect(tmp_path / "real.duckdb") as conn:
        load(out, conn)
        from_api = conn.execute(f"SELECT {columns} FROM activities").fetchall()
        load(tmp_path / "export", conn)
        after_export = conn.execute(f"SELECT {columns} FROM activities").fetchall()
        daily = conn.execute("SELECT count(*) FROM daily").fetchone()[0]
        unmapped = conn.execute(
            "SELECT count(*) FROM load_log WHERE file LIKE 'garmin-api-%' AND unmapped_fields <> ''"
        ).fetchone()[0]

    assert from_api == after_export == [("running", 50.0, 10.0, 12.0, 42.5, 700, 20.0)]
    assert daily == 3
    assert unmapped == 0  # the normaliser emits only keys the loader maps


def test_api_and_export_both_carry_the_labels_and_body_battery():
    """The normaliser used to drop these, so 0 of 260 real activities had them."""
    labels = {
        "differenceBodyBattery": -19, "trainingEffectLabel": "VO2MAX",
        "aerobicTrainingEffectMessage": "IMPROVING_VO2_MAX_15",
        "anaerobicTrainingEffectMessage": "MINOR_ANAEROBIC_BENEFIT_15",
    }
    api = _row(nz.activity({**API_RUN, **labels}))
    export = _row({**EXPORT_RUN, **labels})
    for row in (api, export):
        assert row["body_battery_impact"] == -19
        assert row["te_label"] == "VO2MAX"
        assert row["aerobic_te_message"] == "IMPROVING_VO2_MAX"
        assert row["anaerobic_te_message"] == "MINOR_ANAEROBIC_BENEFIT"
