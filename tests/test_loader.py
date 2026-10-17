"""Loader behaviour: field mapping, units, idempotency (SPEC §6.1-6.2)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db import connect
from loader.discovery import classify, flatten, iter_records
from loader.fieldmap import as_timestamp, as_validation
from loader.load_garmin import TABLE_COLUMNS, load, rows_from_record


def test_files_classify_to_the_expected_tables(export_root: Path):
    found = {
        classify(path, export_root)
        for path in export_root.rglob("*.json")
    }
    assert found == {"daily", "sleep", "hrv", "activities", "user_metrics"}


def test_activity_envelope_is_unwrapped(export_root: Path):
    path = next(export_root.rglob("*summarizedActivities.json"))
    records = list(iter_records(path))
    assert records and "activityId" in records[0]


def test_units_are_converted(export_root: Path):
    path = next(export_root.rglob("*summarizedActivities.json"))
    raw = next(r for r in iter_records(path) if r["activityType"] == "running")
    (row,), _ = rows_from_record("activities", raw)
    assert row["duration_min"] == pytest.approx(raw["duration"] / 60_000, abs=0.01)
    assert row["distance_km"] == pytest.approx(raw["distance"] / 100_000, abs=0.01)
    assert row["avg_speed_kmh"] == pytest.approx(raw["avgSpeed"] * 36, abs=0.02)


def test_intensity_minutes_counts_vigorous_double(export_root: Path):
    path = next(export_root.rglob("UDSFile*.json"))
    raw = next(iter_records(path))
    (row,), _ = rows_from_record("daily", raw)
    assert row["intensity_minutes"] == (
        raw["moderateIntensityMinutes"] + 2 * raw["vigorousIntensityMinutes"]
    )


def test_wide_metrics_record_becomes_long_rows(export_root: Path):
    path = next(export_root.rglob("MetricsMaxMet*.json"))
    raw = next(iter_records(path))
    rows, _ = rows_from_record("user_metrics", raw)
    assert {row["metric"] for row in rows} == {"vo2max", "fitness_age"}


def test_unmapped_fields_are_reported_not_dropped(export_root: Path):
    path = next(export_root.rglob("*sleepData.json"))
    _, unmapped = rows_from_record("sleep", next(iter_records(path)))
    assert "sleepScores.overall.qualifierKey" in unmapped


def test_nested_keys_flatten_to_dotted_paths():
    assert flatten({"sleepScores": {"overall": {"value": 74}}}) == {"sleepScores.overall.value": 74}


def test_timestamp_parsing_accepts_every_garmin_shape():
    expected = datetime(2026, 9, 13, 6, 18, 0)
    assert as_timestamp("2026-09-13 06:18:00") == expected
    assert as_timestamp("2026-09-13T06:18:00.0") == expected
    assert as_timestamp("2026-09-13T06:18:00.0Z") == expected
    # Epoch inputs are read as GMT (Garmin's *TimestampGMT / beginTimestamp fields);
    # the loader prefers the *Local variants wherever the export provides them.
    epoch_ms = int(expected.replace(tzinfo=timezone.utc).timestamp() * 1000)
    assert as_timestamp(epoch_ms) == expected
    assert as_timestamp(epoch_ms // 1000) == expected


def test_validation_aliases_normalise():
    assert as_validation("enhanced_confirmed_final") == "ENHANCED_FINAL"
    assert as_validation("OFFWRIST") == "OFF_WRIST"
    assert as_validation("DEVICE") == "DEVICE"


def test_row_counts_match_the_export(conn, export_root: Path):
    counted = {
        "daily": len(list(iter_records(next(export_root.rglob("UDSFile*.json"))))),
        "sleep": len(list(iter_records(next(export_root.rglob("*sleepData.json"))))),
        "hrv": len(list(iter_records(next(export_root.rglob("HRV_*.json"))))),
        "activities": len(list(iter_records(next(export_root.rglob("*summarizedActivities.json"))))),
    }
    for table, expected in counted.items():
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == expected


def test_load_is_idempotent(tmp_path: Path, export_root: Path):
    target = tmp_path / "idempotent.duckdb"
    with connect(target) as conn:
        load(export_root, conn)
        first = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLE_COLUMNS}
        load(export_root, conn)
        second = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLE_COLUMNS}
        assert first == second
        # ... and the load is still audited twice.
        assert conn.execute("SELECT count(*) FROM load_log").fetchone()[0] == 10


def test_records_without_a_primary_key_are_skipped():
    rows, _ = rows_from_record("activities", {"activityType": "running", "duration": 1000})
    assert rows == []


def test_off_wrist_nights_survive_the_load_with_zero_minutes(conn):
    row = conn.execute(
        "SELECT total_min, validation FROM sleep WHERE date = ?", [date(2026, 9, 8)]
    ).fetchone()
    assert row == (0, "OFF_WRIST")


# --- shapes the real account export uses, which the fixture never produced ---
# Records below are hand-written in the export's shape with invented values; no
# real health data lives in the test suite.

def test_manually_confirmed_night_is_treated_as_manual():
    """SPEC §6.3 excludes MANUAL; the export spells it MANUALLY_CONFIRMED."""
    assert as_validation("MANUALLY_CONFIRMED") == "MANUAL"
    (row,), _ = rows_from_record("sleep", {
        "calendarDate": "2026-01-05",
        "sleepTimeSeconds": 21600,
        "sleepWindowConfirmationType": "MANUALLY_CONFIRMED",
    })
    assert row["validation"] == "MANUAL"


def test_stress_is_read_from_the_nested_aggregator_list():
    (row,), _ = rows_from_record("daily", {
        "calendarDate": "2026-06-18",
        "allDayStress": {"aggregatorList": [
            {"type": "AWAKE", "averageStressLevel": 61},
            {"type": "TOTAL", "averageStressLevel": 52},
        ]},
    })
    assert row["avg_stress"] == 52


def test_body_battery_is_read_from_the_nested_stat_list():
    (row,), _ = rows_from_record("daily", {
        "calendarDate": "2026-06-18",
        "bodyBattery": {"bodyBatteryStatList": [
            {"bodyBatteryStatType": "HIGHEST", "statsValue": 80},
            {"bodyBatteryStatType": "LOWEST", "statsValue": 25},
            {"bodyBatteryStatType": "MOSTRECENT", "statsValue": 25},
        ]},
    })
    assert (row["body_battery_high"], row["body_battery_low"]) == (80, 25)


def test_missing_nested_blocks_do_not_break_the_row():
    (row,), _ = rows_from_record("daily", {"calendarDate": "2026-06-18", "totalSteps": 900})
    assert row["steps"] == 900
    assert row.get("avg_stress") is None


def test_fitness_age_comes_from_current_bio_age():
    rows, _ = rows_from_record("user_metrics", {
        "asOfDateGmt": "2025-01-19T00:00:00.0",
        "currentBioAge": 29.76,
        "chronologicalAge": 29,
    })
    assert {r["metric"]: r["value"] for r in rows}["fitness_age"] == pytest.approx(29.76)


def test_hard_minutes_sum_the_top_zones_from_milliseconds():
    (row,), _ = rows_from_record("activities", {
        "activityId": 1,
        "startTimeLocal": "2026-09-10 06:00:00",
        "activityType": "running",
        "duration": 3_600_000,
        "hrTimeInZone_3": 900_000,
        "hrTimeInZone_4": 1_200_000,   # 20 min
        "hrTimeInZone_5": 372_000,     # 6.2 min
        "hrTimeInZone_6": 0,
    })
    assert row["hard_minutes"] == pytest.approx(26.2, abs=0.05)


def test_activity_without_zone_data_gets_no_hard_minutes():
    (row,), _ = rows_from_record("activities", {
        "activityId": 2,
        "startTimeLocal": "2026-09-10 06:00:00",
        "activityType": "walking",
        "duration": 600_000,
    })
    assert row.get("hard_minutes") is None


def test_activity_energy_is_converted_from_kilojoules():
    """The export records activity `calories` in kJ (SPEC implementation note)."""
    (row,), _ = rows_from_record("activities", {
        "activityId": 3,
        "startTimeLocal": "2026-08-18 06:00:00",
        "activityType": "running",
        "duration": 4_740_000,
        "calories": 3636.93736,
    })
    assert row["calories"] == 869


def test_api_kilocalories_win_over_the_export_field():
    """`activeKilocalories` is already kcal, so it must not be divided again."""
    (row,), _ = rows_from_record("activities", {
        "activityId": 4,
        "startTimeLocal": "2026-08-18 06:00:00",
        "activityType": "running",
        "duration": 4_740_000,
        "calories": 3636.93736,
        "activeKilocalories": 869,
    })
    assert row["calories"] == 869


def test_a_sleep_record_with_no_measurements_is_skipped():
    """An unworn night is a gap, not a trustworthy row with validation UNKNOWN."""
    rows, _ = rows_from_record("sleep", {
        "calendarDate": "2026-08-27", "sleepTimeSeconds": None, "deepSleepSeconds": None,
        "sleepWindowConfirmationType": None, "retro": False,
    })
    assert rows == []


def test_a_zero_length_sleep_record_is_kept():
    """Zero minutes is a measurement; the trust rule, not the loader, rejects it."""
    rows, _ = rows_from_record("sleep", {
        "calendarDate": "2026-08-28", "sleepTimeSeconds": 0,
        "sleepWindowConfirmationType": "OFF_WRIST",
    })
    assert rows and rows[0]["total_min"] == 0


# --- training-effect labels and Body Battery impact (17 Oct 2026) -------------

def test_te_message_drops_the_message_id_suffix():
    from loader.fieldmap import te_message
    assert te_message("IMPROVING_VO2_MAX_15") == "IMPROVING_VO2_MAX"
    assert te_message("NO_ANAEROBIC_BENEFIT_0") == "NO_ANAEROBIC_BENEFIT"
    assert te_message("RECOVERY") == "RECOVERY"
    assert te_message(None) is None and te_message("  ") is None


def test_activity_labels_and_body_battery_are_mapped():
    """The shape the Connect API and the export both send for a vivoactive 5 run."""
    from loader.fieldmap import ACTIVITIES, apply_spec
    raw = {
        "activityId": 24751424445, "startTimeLocal": "2026-10-17 08:44:37",
        "activityType": {"typeKey": "running"}, "differenceBodyBattery": -19,
        "trainingEffectLabel": "VO2MAX",
        "aerobicTrainingEffectMessage": "IMPROVING_VO2_MAX_15",
        "anaerobicTrainingEffectMessage": "MINOR_ANAEROBIC_BENEFIT_15",
    }
    row, unmapped = apply_spec(raw, ACTIVITIES)
    assert not {"differenceBodyBattery", "trainingEffectLabel"} & unmapped
    assert row["body_battery_impact"] == -19
    assert row["te_label"] == "VO2MAX"
    assert row["aerobic_te_message"] == "IMPROVING_VO2_MAX"
    assert row["anaerobic_te_message"] == "MINOR_ANAEROBIC_BENEFIT"
    assert row.get("aerobic_te") is None
