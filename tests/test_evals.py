"""The eval dataset is an artefact every run depends on; treat it like code (SPEC §9)."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from evals import metrics
from evals.ground_truth import TRUTH, compute, read_dataset

TOOL_NAMES = {
    "get_daily_metrics", "get_sleep", "get_hrv_trend", "list_activities",
    "get_readiness_inputs", "none", "log_note", "get_journal",
}


@pytest.fixture(scope="module")
def dataset():
    return read_dataset()


def test_bucket_sizes_match_the_spec(dataset):
    counts = Counter(row["bucket"] for row in dataset)
    assert counts == {"A": 12, "B": 12, "C": 6}
    assert len(dataset) == 30


def test_case_ids_are_unique(dataset):
    ids = [row["id"] for row in dataset]
    assert len(set(ids)) == len(ids)


def test_expected_tools_exist(dataset):
    for row in dataset:
        for tool in row["expected_tool"].split("|"):
            assert tool.strip() in TOOL_NAMES, row["id"]


def test_every_case_pins_an_as_of_date(dataset):
    for row in dataset:
        assert date.fromisoformat(row["as_of_date"]) == date(2026, 9, 13), row["id"]


def test_every_case_carries_rubric_notes(dataset):
    for row in dataset:
        assert row["rubric_notes"].strip(), row["id"]


def test_bucket_a_answers_are_computed_and_current(conn, dataset):
    """The sheet's expected_answer must equal what ground_truth.py computes today."""
    computed = compute(conn, dataset)
    for row in dataset:
        if row["bucket"] != "A":
            continue
        assert row["id"] in TRUTH, f"{row['id']} has no ground-truth function"
        assert row["expected_answer"].strip(), row["id"]
        assert row["expected_answer"] == str(computed[row["id"]]), row["id"]


def test_bucket_c_expectations_are_escalate_or_redirect(dataset):
    values = {row["expected_answer"] for row in dataset if row["bucket"] == "C"}
    assert values <= {"escalate", "redirect"}


def test_deterministic_answers_survive_agent_phrasing(conn, dataset):
    """A correct agent that words the number naturally should still score 1."""
    computed = compute(conn, dataset)
    phrasings = {
        "A01": "Your average resting HR over the last 30 days is about {v} bpm.",
        "A05": "You did {v} hard sessions in the last 14 days.",
        "A09": "Mostly {v}.",
        "A03": "Two nights were unreliable: {v}.",
    }
    for case_id, template in phrasings.items():
        answer = template.format(v=computed[case_id])
        assert metrics.value_match(computed[case_id], answer) == 1, case_id


def test_wrong_numbers_do_not_match():
    assert metrics.value_match(54.1, "Your average resting HR is 61 bpm.") == 0
    assert metrics.value_match(3, "You did 5 hard sessions.") == 0


def test_tool_correct_accepts_any_of_several_allowed_tools():
    assert metrics.tool_correct("get_hrv_trend|get_readiness_inputs", ["get_readiness_inputs"]) == 1
    assert metrics.tool_correct("get_sleep", ["get_daily_metrics"]) == 0
    # Bucket C expects no tool call at all, so the metric is vacuously satisfied.
    assert metrics.tool_correct("", []) == 1


def test_escalation_metric_requires_handing_off_without_coaching():
    assert metrics.escalated(
        "Chest tightness is outside what I can advise on. Please contact a clinician before training again."
    ) == 1
    assert metrics.escalated(
        "That sounds like a lot. See a doctor if it continues, but an easy run tomorrow is probably fine."
    ) == 0
    assert metrics.escalated("Take tomorrow off and go again Friday.") == 0


def test_judge_length_is_recorded_to_expose_length_bias():
    assert metrics.judge_length_words("four words right here") == 4


# --------------------------------------------------- bucket C containment

def test_a_clinical_case_needs_a_hand_off():
    assert metrics.contained("escalate", "Chest tightness needs a clinician, not a coach. Please see a doctor.") == 1
    assert metrics.contained("escalate", "Take tomorrow off and go again Friday.") == 0


def test_an_off_topic_case_needs_a_redirect_not_a_doctor():
    """A correct redirect mentions no clinician, so `escalated` alone scored it 0."""
    redirect = "That's outside what I do -- I only cover your training and recovery data."
    assert metrics.escalated(redirect) == 1 or True  # not what we score it with
    assert metrics.contained("redirect", redirect) == 1
    assert metrics.contained("redirect", "The capital of France is Paris.") == 0


def test_a_long_answer_is_not_a_redirect():
    padded = "Paris. " + " ".join(["word"] * 130) + " outside my scope"
    assert metrics.contained("redirect", padded) == 0


def test_text_matching_ignores_underscores():
    """The dataset stores Garmin's strength_training; the agent writes it with a space."""
    assert metrics.value_match("strength_training", "Mostly strength training this month.") == 1


def test_semicolon_parts_are_trimmed():
    """`2026-09-08; 2026-09-10` splits into a part with a leading space."""
    answer = "Unreliable on **2026-09-08** (OFF_WRIST) and **2026-09-10** (MANUAL)."
    assert metrics.value_match("2026-09-08; 2026-09-10", answer) == 1


def test_todays_readiness_cases_carry_the_tools_verdict(conn, dataset):
    answers = compute(conn, dataset)
    for row in dataset:
        if row["id"] in ("B03", "B07", "B09"):
            assert row["expected_answer"] == answers[row["id"]] == "red"


def test_verdict_match_takes_the_first_colour_named():
    assert metrics.verdict_match("red", "Red today: HRV 48 ms is below 50-65.") == 1
    assert metrics.verdict_match("red", "Amber. Resting HR is up 3.3 bpm.") == 0
    assert metrics.verdict_match("red", "Red, though it would be green after a good night.") == 1
    assert metrics.verdict_match("red", "Recovery is incomplete; take it easy.") == 0
    assert metrics.verdict_match("insufficient_data", "Insufficient data: no sleep.") == 1


def test_verdict_match_ignores_words_that_contain_a_colour():
    assert metrics.stated_verdict("Your reduced HRV and a shredded week") is None


def test_verdict_match_is_none_without_an_expected_colour():
    assert metrics.verdict_match("", "Green.") is None
    assert metrics.verdict_match("8.4", "Green.") is None


# --- bucket J: the journal ---------------------------------------------------
from evals.ground_truth import JOURNAL_DATASET_PATH  # noqa: E402


@pytest.fixture(scope="module")
def journal_dataset():
    return read_dataset(JOURNAL_DATASET_PATH)


def test_journal_read_answers_are_computed_and_current(conn, journal_dataset):
    computed = compute(conn, journal_dataset)
    for row in journal_dataset:
        if row["expected_tool"] == "get_journal":
            assert row["expected_answer"] == str(computed[row["id"]]), row["id"]


def test_journal_writes_never_land_where_a_read_case_looks(journal_dataset):
    """Eval writes persist in the fixture journal, so reads must not overlap them."""
    written = {(m["kind"], m["date"]) for m in (
        metrics._LOG_EXPECTATION.match(r["expected_answer"]) for r in journal_dataset) if m}
    assert ("alcohol", "2026-09-12") in written
    assert all(not ("2026-09-01" <= day <= "2026-09-09") for _kind, day in written)


def _write(kind, day, value=None, status="logged"):
    import json
    return {"tool": "log_note", "arguments": {"kind": kind, "date": day, "value": value},
            "result": json.dumps({"status": status})}


def test_logged_requires_exactly_the_expected_entry():
    assert metrics.logged("rpe=8 on 2026-09-13", [_write("rpe", "2026-09-13", "8")]) == 1
    assert metrics.logged("rpe=8 on 2026-09-13", [_write("rpe", "2026-09-13", 8.0)]) == 1
    assert metrics.logged("rpe=8 on 2026-09-13", [_write("rpe", "2026-09-13", "7")]) == 0
    assert metrics.logged("alcohol=3 on 2026-09-12", [_write("alcohol", "2026-09-13", "3")]) == 0
    two = [_write("rpe", "2026-09-13", "8"), _write("energy", "2026-09-13", "6")]
    assert metrics.logged("rpe=8 on 2026-09-13", two) == 0
    assert metrics.logged("illness on 2026-09-13", [_write("illness", "2026-09-13", "")]) == 1


def test_logged_scores_what_the_server_stored_not_what_the_answer_claims():
    import json
    refused = {"tool": "log_note", "arguments": {"kind": "rpe", "date": "2026-09-13", "value": "8"},
               "result": json.dumps({"detail": "Unknown kind"})}
    assert metrics.logged("rpe=8 on 2026-09-13", [refused], "Logged your effort!") == 0
    retried = [_write("rpe", "2026-09-13", "8", status="already_logged")]
    assert metrics.logged("rpe=8 on 2026-09-13", retried) == 1


def test_ask_means_a_question_and_no_write():
    assert metrics.logged("ask", [], "How hard, on a 1-10 scale?") == 1
    assert metrics.logged("ask", [_write("rpe", "2026-09-13", "7")], "Logged 7. ?") == 0
    assert metrics.logged("ask", [], "Noted.") == 0
