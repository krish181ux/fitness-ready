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
    journal.seed_fixture(jconn)
    week = journal.get_journal(jconn, date(2026, 9, 7), TODAY)
    assert [r["kind"] for r in week["rows"]] == ["energy", "rpe", "soreness"]
    only = journal.get_journal(jconn, date(2026, 9, 1), TODAY, kind="alcohol")
    assert [r["value"] for r in only["rows"]] == [2]
    empty = journal.get_journal(jconn, date(2026, 8, 1), date(2026, 8, 31))
    assert empty["rows"] == [] and "not evidence" in empty["data_gaps"][0]


def test_reseeding_converges(jconn):
    journal.seed_fixture(jconn)
    journal.seed_fixture(jconn)
    assert jconn.execute("SELECT count(*) FROM journal").fetchone()[0] == len(journal.FIXTURE_ENTRIES)


def test_the_journal_is_paired_with_the_dataset(monkeypatch):
    """Serving the fixture must never write into the real journal."""
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
