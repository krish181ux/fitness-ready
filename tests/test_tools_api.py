"""Contract tests for the five tool endpoints (SPEC §7)."""

from __future__ import annotations

from datetime import date

import pytest

from tools.derived import MAX_RANGE_DAYS

AS_OF = "2026-09-13"


def test_health_reports_row_counts(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["rows"]["daily"] == 180
    assert body["latest_date"] == AS_OF


def test_daily_metrics_returns_the_requested_range(client):
    body = client.get(
        "/tools/get_daily_metrics", params={"start_date": "2026-09-07", "end_date": AS_OF}
    ).json()
    assert [row["date"] for row in body["rows"]][0] == "2026-09-07"
    assert len(body["rows"]) == 7
    assert body["data_gaps"] == []


def test_sleep_exposes_validation_and_flags_untrustworthy_nights(client):
    body = client.get(
        "/tools/get_sleep", params={"start_date": "2026-09-07", "end_date": AS_OF}
    ).json()
    by_date = {row["date"]: row for row in body["rows"]}
    assert by_date["2026-09-08"]["validation"] == "OFF_WRIST"
    assert by_date["2026-09-08"]["trustworthy"] is False
    assert by_date["2026-09-10"]["validation"] == "MANUAL"
    assert by_date[AS_OF]["trustworthy"] is True
    assert any("not trustworthy" in gap for gap in body["data_gaps"])


def test_hrv_trend_reports_both_means_and_the_gap(client):
    body = client.get("/tools/get_hrv_trend", params={"days": 30, "as_of": AS_OF}).json()
    assert body["baseline_low"] == 50 and body["baseline_high"] == 65
    assert body["mean_7d"] is not None and body["mean_60d"] is not None
    assert body["latest_vs_baseline"] == "below"
    assert "no HRV on 2026-09-11" in body["data_gaps"]


def test_activities_filter_by_type_and_flag_hard_sessions(client):
    body = client.get(
        "/tools/list_activities",
        params={"start_date": "2026-09-01", "end_date": AS_OF, "type": "running"},
    ).json()
    assert body["count"] == len(body["rows"]) > 0
    assert {row["type"] for row in body["rows"]} == {"running"}
    assert body["hard_session_count"] == sum(1 for row in body["rows"] if row["hard_session"])


def test_activities_empty_range_reports_a_gap_not_an_error(client):
    body = client.get(
        "/tools/list_activities",
        params={"start_date": "2019-01-01", "end_date": "2019-01-05"},
    ).json()
    assert body["rows"] == []
    assert body["data_gaps"] and "no activities" in body["data_gaps"][0]


def test_readiness_bundle_shape(client):
    body = client.get("/tools/get_readiness_inputs", params={"date": AS_OF}).json()
    assert set(body) >= {
        "date", "sleep", "hrv", "resting_hr", "last_hard_session", "body_battery", "data_gaps"
    }
    assert body["sleep"]["trustworthy"] is True
    assert body["hrv"]["vs_baseline"] == "below"
    resting = body["resting_hr"]
    assert resting["delta"] == pytest.approx(resting["today"] - resting["mean_30d"], abs=0.05)
    # The fixture's pinned week deliberately sits above baseline (SPEC §8 amber/red).
    assert resting["delta"] > 3
    hard = body["last_hard_session"]
    # Badminton is this profile's hard training, and the rule is sport-agnostic.
    assert hard["type"] == "badminton"
    assert hard["recovery_remaining_hours"] == max(
        0, round(hard["recovery_time_hours"] - hard["hours_ago"])
    )


def test_readiness_on_a_day_with_no_hrv_says_so(client):
    body = client.get("/tools/get_readiness_inputs", params={"date": "2026-09-11"}).json()
    assert body["hrv"]["last_night_avg"] is None
    assert "no HRV on 2026-09-11" in body["data_gaps"]
    # The baseline band still comes from the most recent night that had one.
    assert body["hrv"]["baseline_low"] == 50


def test_readiness_defaults_to_the_latest_loaded_day(client):
    assert client.get("/tools/get_readiness_inputs").json()["date"] == AS_OF


@pytest.mark.parametrize(
    "params, status",
    [
        ({"start_date": "2026-09-13", "end_date": "2026-09-01"}, 422),  # reversed
        ({"start_date": "2020-01-01", "end_date": "2026-09-13"}, 422),  # over the cap
        ({"start_date": "not-a-date", "end_date": "2026-09-13"}, 422),
        ({"start_date": "2026-09-01"}, 422),                            # missing param
    ],
)
def test_range_validation(client, params, status):
    assert client.get("/tools/get_daily_metrics", params=params).status_code == status


def test_a_full_year_inclusive_of_both_ends_is_allowed(client):
    """"The past year" ending on the as-of date spans 366 days, not 365."""
    end, start = date(2026, 9, 13), date(2025, 9, 13)
    assert (end - start).days + 1 == 366 == MAX_RANGE_DAYS
    response = client.get(
        "/tools/get_daily_metrics",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
    )
    assert response.status_code == 200


def test_one_day_past_the_cap_is_rejected(client):
    end, start = date(2026, 9, 13), date(2025, 9, 12)
    assert (end - start).days + 1 == 367
    response = client.get(
        "/tools/get_daily_metrics",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
    )
    assert response.status_code == 422


def test_over_cap_error_tells_the_model_how_to_fix_the_call(client):
    """The agent reads these bodies; a bare limit makes it retry identically."""
    detail = client.get(
        "/tools/get_daily_metrics",
        params={"start_date": "2020-01-01", "end_date": "2026-09-13"},
    ).json()["detail"]
    assert str(MAX_RANGE_DAYS) in detail
    # Names the corrected call, not just the rule.
    assert "start_date=2025-09-13" in detail
    assert "retry" in detail.lower() or "split" in detail.lower()


def test_reversed_range_error_names_both_dates(client):
    detail = client.get(
        "/tools/get_daily_metrics",
        params={"start_date": "2026-09-13", "end_date": "2026-09-01"},
    ).json()["detail"]
    assert "2026-09-13" in detail and "2026-09-01" in detail
    assert "swap" in detail.lower()


def test_hrv_days_bound_matches_the_range_cap(client):
    """get_hrv_trend takes `days` directly, so its bound must track MAX_RANGE_DAYS."""
    assert client.get(
        "/tools/get_hrv_trend", params={"days": MAX_RANGE_DAYS, "as_of": AS_OF}
    ).status_code == 200
    assert client.get(
        "/tools/get_hrv_trend", params={"days": MAX_RANGE_DAYS + 1, "as_of": AS_OF}
    ).status_code == 422


def test_rows_loaded_after_startup_are_served_without_a_restart(
    db_file, tmp_path, monkeypatch
):
    """A daily load must reach the agent with the server still running."""
    import shutil

    from fastapi.testclient import TestClient

    from db import connect
    from tools.server import app

    copy = tmp_path / "live.duckdb"
    shutil.copy(db_file, copy)
    monkeypatch.setenv("WEARABLE_DB", str(copy))
    with TestClient(app) as client:
        before = client.get("/health").json()
        assert before["latest_date"] == AS_OF
        with connect(copy) as writer:
            writer.execute(
                "INSERT INTO daily (date) SELECT max(date) + 1 FROM daily"
            )
        after = client.get("/health").json()
    assert after["latest_date"] == "2026-09-14"
    assert after["rows"]["daily"] == before["rows"]["daily"] + 1


def test_missing_database_is_a_503_the_agent_can_read(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import tools.server as server

    monkeypatch.setenv("WEARABLE_DB", str(tmp_path / "absent.duckdb"))
    monkeypatch.setattr(server, "OPEN_RETRY_DELAY_S", 0)
    with TestClient(server.app) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert "do not retry" in response.json()["detail"]


def test_readiness_inputs_carry_the_verdict(client):
    """The fixture's as-of day: HRV below band, RHR +3.3, 18 h recovery left."""
    verdict = client.get("/tools/get_readiness_inputs", params={"date": AS_OF}).json()["verdict"]
    assert verdict["colour"] == "red"
    assert verdict["failed"] == ["hrv", "resting_hr", "recovery"]
    assert verdict["missing"] == []


def test_a_hard_session_without_recovery_time_is_not_counted_as_recovered(
    db_file, tmp_path, monkeypatch
):
    """0 of 260 real activities carry recovery time; reading that as 0 h passed every day."""
    import shutil

    from fastapi.testclient import TestClient

    from db import connect
    from tools.derived import ASSUMED_RECOVERY_HOURS
    from tools.server import app

    copy = tmp_path / "no_recovery.duckdb"
    shutil.copy(db_file, copy)
    with connect(copy) as writer:
        # A hard run on the morning of the as-of day, 9 h before the 18:00 reference.
        writer.execute(
            "INSERT INTO activities (activity_id, start_time, type, duration_min, avg_hr, "
            "hard_minutes, recovery_time_hours) VALUES (999, ?, 'running', 60, 150, 31.9, NULL)",
            [f"{AS_OF} 09:00:00"],
        )
    monkeypatch.setenv("WEARABLE_DB", str(copy))
    with TestClient(app) as client:
        body = client.get("/tools/get_readiness_inputs", params={"date": AS_OF}).json()
    hard = body["last_hard_session"]
    assert hard["activity_id"] == 999 and hard["recovery_basis"] == "assumed"
    assert hard["recovery_remaining_hours"] == ASSUMED_RECOVERY_HOURS - 9
    assert "recovery" in body["verdict"]["failed"]


def test_the_watchs_recovery_time_is_used_when_present(client):
    hard = client.get("/tools/get_readiness_inputs", params={"date": AS_OF}).json()["last_hard_session"]
    assert hard["recovery_basis"] == "watch"
