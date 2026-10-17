"""SPEC §6.3 definitions, and the SQL/Python pair agreeing (tools/derived.py)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from tools import derived
from tools.queries import ACTIVITY_COLUMNS


@pytest.mark.parametrize(
    "activity, expected",
    [
        ({"aerobic_te": 3.0, "anaerobic_te": 0.4, "recovery_time_hours": 8}, True),
        ({"aerobic_te": 2.9, "anaerobic_te": 3.1, "recovery_time_hours": 8}, True),
        ({"aerobic_te": 2.0, "anaerobic_te": 0.5, "recovery_time_hours": 24}, True),
        ({"aerobic_te": 2.9, "anaerobic_te": 2.9, "recovery_time_hours": 23}, False),
        ({"aerobic_te": None, "anaerobic_te": None, "recovery_time_hours": None}, False),
        # The rule is type-agnostic: a strength session clears it on recovery time alone.
        ({"type": "strength_training", "aerobic_te": 1.1, "recovery_time_hours": 30}, True),
    ],
)
def test_hard_session_rule(activity, expected):
    assert derived.is_hard_session(activity) is expected


def test_hard_session_sql_matches_python(conn):
    columns = ", ".join(ACTIVITY_COLUMNS)
    rows = conn.execute(
        f"SELECT {columns}, {derived.HARD_SESSION_SQL} AS hard FROM activities"
    ).fetchall()
    names = ACTIVITY_COLUMNS + ("hard",)
    assert rows, "fixture produced no activities"
    for row in rows:
        record = dict(zip(names, row))
        assert bool(record["hard"]) is derived.is_hard_session(record), record["activity_id"]


@pytest.mark.parametrize(
    "validation, total_min, expected",
    [
        ("ENHANCED_FINAL", 400, True),
        ("ENHANCED_TENTATIVE", 400, True),
        ("DEVICE", 400, True),
        ("OFF_WRIST", 400, False),
        ("MANUAL", 400, False),
        ("ENHANCED_FINAL", 0, False),
        (None, 400, False),
    ],
)
def test_sleep_trust_rule(validation, total_min, expected):
    assert derived.is_sleep_trustworthy(validation, total_min) is expected


def test_sleep_trust_sql_matches_python(conn):
    rows = conn.execute(
        f"SELECT validation, total_min, {derived.SLEEP_TRUSTWORTHY_SQL} AS ok FROM sleep"
    ).fetchall()
    assert rows
    for validation, total_min, ok in rows:
        assert bool(ok) is derived.is_sleep_trustworthy(validation, total_min)


def test_resting_hr_delta_excludes_today_by_construction():
    assert derived.resting_hr_delta(58, 54.4) == 3.6
    assert derived.resting_hr_delta(None, 54.4) is None
    assert derived.resting_hr_delta(58, None) is None


@pytest.mark.parametrize(
    "value, low, high, expected",
    [(41, 43, 52, "below"), (43, 43, 52, "within"), (52, 43, 52, "within"),
     (55, 43, 52, "above"), (None, 43, 52, "unknown"), (45, None, None, "unknown")],
)
def test_hrv_vs_baseline(value, low, high, expected):
    assert derived.hrv_vs_baseline(value, low, high) == expected


def test_reference_instant_is_deterministic_for_a_pinned_date():
    assert derived.reference_instant(date(2026, 9, 13)) == datetime(2026, 9, 13, 18, 0)


def test_gap_descriptions_collapse_when_long():
    missing = [date(2026, 9, day) for day in range(1, 4)]
    assert derived.describe_gaps("HRV", missing) == [
        "no HRV on 2026-09-01", "no HRV on 2026-09-02", "no HRV on 2026-09-03",
    ]
    many = [date(2026, 9, day) for day in range(1, 12)]
    assert derived.describe_gaps("HRV", many) == [
        "no HRV on 11 days between 2026-09-01 and 2026-09-11"
    ]
    assert derived.describe_gaps("HRV", []) == []


# --- the zone-4 arm of the hard-session rule (added once the real export showed
# --- it carries no numeric training effect at all)

@pytest.mark.parametrize(
    "activity, expected",
    [
        ({"hard_minutes": 20.0}, True),
        ({"hard_minutes": 19.9}, False),
        ({"hard_minutes": 26.2}, True),
        ({"hard_minutes": None}, False),
        # Either arm is sufficient: TE-bearing data still qualifies on its own.
        ({"aerobic_te": 3.4, "hard_minutes": 0.0}, True),
        ({"aerobic_te": 1.0, "hard_minutes": 0.0}, False),
    ],
)
def test_zone_minutes_arm_of_the_hard_session_rule(activity, expected):
    assert derived.is_hard_session(activity) is expected


def test_zone_threshold_is_stated_once():
    assert f">= {derived.HARD_SESSION_ZONE4_MINUTES}" in derived.HARD_SESSION_SQL


@pytest.mark.parametrize(
    "activity, expected",
    [
        # 8 Oct 2026: 68.75 min at 122 bpm, 10.3 zone-4 minutes. Hard only via this arm.
        ({"type": "strength_training", "duration_min": 68.75, "avg_hr": 122, "hard_minutes": 10.3}, True),
        # 7 Oct 2026: long but easy.
        ({"type": "strength_training", "duration_min": 70.75, "avg_hr": 100}, False),
        ({"type": "strength_training", "duration_min": 50.0, "avg_hr": 115}, True),
        ({"type": "strength_training", "duration_min": 49.9, "avg_hr": 130}, False),
        ({"type": "strength_training", "duration_min": 90.0, "avg_hr": 114}, False),
        ({"type": "strength_training", "duration_min": None, "avg_hr": None}, False),
        # Strength only: a long walk at the same heart rate is not hard.
        ({"type": "walking", "duration_min": 70.0, "avg_hr": 120}, False),
    ],
)
def test_strength_arm_of_the_hard_session_rule(activity, expected):
    assert derived.is_hard_session(activity) is expected


# --- readiness verdict (SPEC §8) -------------------------------------------
GOOD_SLEEP = {"score": 80, "total_min": 450, "trustworthy": True, "validation": "ENHANCED_FINAL"}
GOOD_HRV = {"last_night_avg": 58, "baseline_low": 50, "baseline_high": 65}
GOOD_RHR = {"delta": 1.0}
RECOVERED = {"recovery_remaining_hours": 0}


def _colour(sleep=GOOD_SLEEP, hrv=GOOD_HRV, rhr=GOOD_RHR, hard=RECOVERED):
    return derived.readiness_verdict(sleep, hrv, rhr, hard)


def test_all_four_checks_pass_is_green():
    verdict = _colour()
    assert verdict["colour"] == "green"
    assert verdict["based_on"] == "4 of 4 checks"


def test_one_failure_is_amber_two_is_red():
    assert _colour(rhr={"delta": 3.1})["colour"] == "amber"
    assert _colour(rhr={"delta": 3.1}, hard={"recovery_remaining_hours": 5})["colour"] == "red"


def test_boundaries_are_inclusive_where_the_rubric_says():
    assert _colour(sleep={**GOOD_SLEEP, "score": 70})["colour"] == "green"
    assert _colour(sleep={**GOOD_SLEEP, "score": 69})["colour"] == "amber"
    assert _colour(rhr={"delta": 3.0})["colour"] == "green"
    assert _colour(hrv={**GOOD_HRV, "last_night_avg": 50})["colour"] == "green"


def test_untrustworthy_sleep_is_a_failure_not_missing():
    off_wrist = {"score": None, "total_min": 0, "trustworthy": False, "validation": "OFF_WRIST"}
    verdict = _colour(sleep=off_wrist)
    assert verdict["failed"] == ["sleep"] and verdict["colour"] == "amber"
    assert _colour(sleep=off_wrist, rhr={"delta": 5})["colour"] == "red"


def test_no_hrv_baseline_is_missing_and_does_not_cap_the_colour():
    """The real account's first weeks: HRV nightly, no baseline band yet."""
    verdict = _colour(hrv={"last_night_avg": 65, "baseline_low": None, "baseline_high": None})
    assert verdict["missing"] == ["hrv"]
    assert verdict["colour"] == "green"
    assert verdict["based_on"] == "3 of 4 checks"


def test_two_missing_checks_is_insufficient_data_not_red():
    no_sleep = {"score": None, "total_min": None, "trustworthy": False, "validation": None}
    no_hrv = {"last_night_avg": None, "baseline_low": None, "baseline_high": None}
    assert _colour(sleep=no_sleep, hrv=no_hrv)["colour"] == "insufficient_data"


def test_no_hard_session_on_record_means_nothing_to_recover_from():
    assert _colour(hard=None)["colour"] == "green"


def test_assumed_recovery_is_labelled_in_the_verdict():
    """The real watch reports no recovery time; the verdict must say it assumed one."""
    hard = {"recovery_remaining_hours": 15, "recovery_basis": "assumed", "hours_ago": 9.3}
    verdict = _colour(hard=hard)
    recovery = next(c for c in verdict["checks"] if c["name"] == "recovery")
    assert recovery["result"] == "fail" and verdict["colour"] == "amber"
    assert f"assumed {derived.ASSUMED_RECOVERY_HOURS} h" in recovery["value"]
    watch = _colour(hard={"recovery_remaining_hours": 15, "recovery_basis": "watch"})
    assert "assumed" not in next(c for c in watch["checks"] if c["name"] == "recovery")["value"]
