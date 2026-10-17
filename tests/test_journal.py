"""The journal: typed entries the agent logs from chat (SPEC §7.1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tools import journal

TODAY = date(2026, 9, 13)


@pytest.fixture()
def jconn(journal_file):
    with journal.connect(journal_file) as conn:
        yield conn


def test_an_entry_is_stored_with_its_day_and_scale(jconn):
    result = journal.log_note(jconn, date(2026, 9, 12), "alcohol", 3, "beers", today=TODAY)
    assert result["status"] == "logged"
    assert result["entry"]["date"] == "2026-09-12"
    assert result["entry"]["value"] == 3


def test_a_retried_call_does_not_count_twice(jconn):
    journal.log_note(jconn, TODAY, "rpe", 8, today=TODAY)
    again = journal.log_note(jconn, TODAY, "rpe", 8, today=TODAY)
    assert again["status"] == "already_logged"
    assert jconn.execute("SELECT count(*) FROM journal").fetchone()[0] == 1


@pytest.mark.parametrize("kind, value, note, fragment", [
    ("mood", 5, None, "Unknown kind"),
    ("rpe", None, None, "needs a value"),
    ("rpe", 11, None, "Do not rescale"),
    ("soreness", -1, None, "outside"),
    ("illness", 1, "cold", "takes no value"),
    ("injury", None, None, "needs a note"),
    ("note", None, "x" * 300, "keep it under"),
])
def test_bad_entries_are_refused_with_the_fix(jconn, kind, value, note, fragment):
    with pytest.raises(journal.JournalError, match=fragment):
        journal.log_note(jconn, TODAY, kind, value, note, today=TODAY)


def test_future_dates_are_refused(jconn):
    with pytest.raises(journal.JournalError, match="future"):
        journal.log_note(jconn, date(2026, 9, 14), "energy", 6, today=TODAY)


def test_reads_filter_by_range_and_kind_and_say_when_empty(jconn):
    journal.log_note(jconn, date(2026, 9, 5), "alcohol", 2, "wine", today=TODAY)
    journal.log_note(jconn, date(2026, 9, 7), "energy", 4, today=TODAY)
    journal.log_note(jconn, date(2026, 9, 9), "rpe", 7, today=TODAY)
    journal.log_note(jconn, date(2026, 9, 11), "soreness", 5, "calves", today=TODAY)
    week = journal.get_journal(jconn, date(2026, 9, 7), TODAY)
    assert [r["kind"] for r in week["rows"]] == ["energy", "rpe", "soreness"]
    only = journal.get_journal(jconn, date(2026, 9, 1), TODAY, kind="alcohol")
    assert [r["value"] for r in only["rows"]] == [2]
    empty = journal.get_journal(jconn, date(2026, 8, 1), date(2026, 8, 31))
    assert empty["rows"] == [] and "not evidence" in empty["data_gaps"][0]


# ------------------------------------------------------ effort rates a session

RUN = {"activity_id": 11, "type": "running", "start_time": "08:44", "duration_min": 59.9}
LIFT = {"activity_id": 12, "type": "strength_training", "start_time": "18:10", "duration_min": 66.5}


def test_effort_on_a_one_session_day_links_itself(jconn):
    entry = journal.log_note(jconn, TODAY, "rpe", 8, today=TODAY, sessions=[RUN])["entry"]
    assert entry["activity_id"] == 11


def test_effort_on_a_two_session_day_asks_which(jconn):
    with pytest.raises(journal.JournalError, match="Ask the user which one"):
        journal.log_note(jconn, TODAY, "rpe", 8, today=TODAY, sessions=[RUN, LIFT])
    entry = journal.log_note(jconn, TODAY, "rpe", 6, today=TODAY, activity_id=12,
                             sessions=[RUN, LIFT])["entry"]
    assert entry["activity_id"] == 12


def test_effort_for_an_unrecorded_session_is_kept_unlinked(jconn):
    entry = journal.log_note(jconn, TODAY, "rpe", 5, today=TODAY, sessions=[])["entry"]
    assert entry["activity_id"] is None


def test_a_session_from_another_day_is_refused(jconn):
    with pytest.raises(journal.JournalError, match="not a session on that date"):
        journal.log_note(jconn, TODAY, "rpe", 8, today=TODAY, activity_id=99, sessions=[RUN])


def test_soreness_is_about_the_day_not_a_session(jconn):
    with pytest.raises(journal.JournalError, match="omit activity_id"):
        journal.log_note(jconn, TODAY, "soreness", 3, today=TODAY, activity_id=11, sessions=[RUN])
    assert journal.log_note(jconn, TODAY, "soreness", 0, today=TODAY,
                            sessions=[RUN])["entry"]["activity_id"] is None


def test_session_load_is_effort_times_minutes():
    rows = [{"kind": "rpe", "value": 8, "activity_id": 11},
            {"kind": "rpe", "value": 5, "activity_id": None},
            {"kind": "soreness", "value": 4, "activity_id": None}]
    journal.add_session_load(rows, {11: 59.9})
    assert rows[0]["session_load"] == 479 and rows[0]["session_minutes"] == 60
    assert "session_load" not in rows[1] and "session_load" not in rows[2]


def test_an_old_journal_gains_the_activity_column(journal_file):
    import duckdb
    with duckdb.connect(str(journal_file)) as old:
        old.execute("CREATE TABLE journal (entry_id VARCHAR PRIMARY KEY, date DATE NOT NULL, "
                    "kind VARCHAR NOT NULL, value DOUBLE, note VARCHAR, logged_at TIMESTAMP "
                    "NOT NULL, source VARCHAR NOT NULL)")
    with journal.connect(journal_file) as conn:
        assert journal.log_note(conn, TODAY, "rpe", 7, today=TODAY,
                                sessions=[RUN])["entry"]["activity_id"] == 11


def test_the_journal_is_paired_with_the_dataset(monkeypatch):
    """Each dataset gets its own journal file."""
    monkeypatch.delenv("JOURNAL_DB")
    monkeypatch.setenv("JOURNAL_DIR", "/journals")
    monkeypatch.setenv("WEARABLE_DB_FILE", "./wearable-real.duckdb")
    assert journal.journal_path() == Path("/journals/wearable-real.journal.duckdb")
    monkeypatch.setenv("WEARABLE_DB_FILE", "./wearable.duckdb")
    assert journal.journal_path() == Path("/journals/wearable.journal.duckdb")


# --------------------------------------------------------------- over HTTP

def test_log_note_round_trips_through_the_api(client):
    posted = client.post("/tools/log_note", json={
        "date": "2026-09-12", "kind": "alcohol", "value": "3", "note": "beers"})
    assert posted.status_code == 200, posted.text
    read = client.get("/tools/get_journal",
                      params={"start_date": "2026-09-12", "end_date": "2026-09-12"}).json()
    assert [(r["kind"], r["value"]) for r in read["rows"]] == [("alcohol", 3)]


def test_an_empty_value_from_n8n_means_none(client):
    posted = client.post("/tools/log_note", json={
        "date": "2026-09-13", "kind": "illness", "value": "", "note": "head cold"})
    assert posted.status_code == 200, posted.text


def test_api_errors_tell_the_agent_what_to_change(client):
    bad = client.post("/tools/log_note", json={"date": "2026-09-13", "kind": "rpe", "value": "hard"})
    assert bad.status_code == 422 and "Send the number alone" in bad.json()["detail"]
    bad = client.post("/tools/log_note", json={"date": "2026-09-13", "kind": "mood", "value": 3})
    assert bad.status_code == 422 and "Use one of" in bad.json()["detail"]


def test_effort_over_the_api_links_the_session_and_reports_its_load(client):
    day = client.get("/tools/list_activities",
                     params={"start_date": "2026-09-01", "end_date": "2026-09-13"}).json()["rows"]
    by_day: dict[str, list] = {}
    for a in day:
        by_day.setdefault(a["start_time"][:10], []).append(a)
    single = next(acts[0] for d, acts in sorted(by_day.items()) if len(acts) == 1)
    posted = client.post("/tools/log_note", json={
        "date": single["start_time"][:10], "kind": "rpe", "value": "7", "activity_id": ""})
    assert posted.status_code == 200, posted.text
    assert posted.json()["entry"]["activity_id"] == single["activity_id"]
    read = client.get("/tools/get_journal", params={
        "start_date": single["start_time"][:10], "end_date": single["start_time"][:10]}).json()
    assert read["rows"][0]["session_load"] == round(7 * single["duration_min"])
