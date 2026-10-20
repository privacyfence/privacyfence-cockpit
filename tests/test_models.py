from datetime import UTC, datetime, timedelta, timezone

import pytest

from privacyfence_cockpit.models import (
    EXIT_CODE_OUTCOMES,
    NOTIFY_OUTCOMES,
    OUTCOME_LABELS,
    RESULT_FILE_OUTCOMES,
    SUCCESS_OUTCOMES,
    Mode,
    Outcome,
    iso,
    parse_iso,
    utc_now,
)


def test_result_file_mapping():
    assert RESULT_FILE_OUTCOMES == {
        "ok": Outcome.OK,
        "no_change": Outcome.NO_CHANGE,
        "recipe_error": Outcome.RECIPE_ERROR,
        "failed": Outcome.FAILED,
        "denied": Outcome.DENIED,
        "not_signed_in": Outcome.NOT_SIGNED_IN,
    }


def test_exit_code_mapping():
    assert EXIT_CODE_OUTCOMES == {
        0: Outcome.OK,
        1: Outcome.RECIPE_ERROR,
        2: Outcome.FAILED,
        3: Outcome.DENIED,
        4: Outcome.NOT_SIGNED_IN,
    }


def test_labels_cover_every_outcome():
    assert set(OUTCOME_LABELS) == set(Outcome)
    assert OUTCOME_LABELS[Outcome.NO_CHANGE] == "no change"
    assert OUTCOME_LABELS[Outcome.NOT_SIGNED_IN] == "not signed in"
    assert OUTCOME_LABELS[Outcome.TIMED_OUT] == "timed out"


def test_outcome_sets():
    assert SUCCESS_OUTCOMES == {Outcome.OK, Outcome.NO_CHANGE}
    assert NOTIFY_OUTCOMES == {
        Outcome.RECIPE_ERROR,
        Outcome.FAILED,
        Outcome.DENIED,
        Outcome.NOT_SIGNED_IN,
        Outcome.TIMED_OUT,
    }


def test_mode_values():
    assert [m.value for m in Mode] == ["attended", "unattended", "replay", "preflight", "check"]


def test_utc_now_is_aware_without_microseconds():
    now = utc_now()
    assert now.utcoffset() == timedelta(0)
    assert now.microsecond == 0


def test_iso_round_trip():
    dt = datetime(2026, 10, 20, 6, 15, tzinfo=UTC)
    assert iso(dt) == "2026-10-20T06:15:00Z"
    assert parse_iso("2026-10-20T06:15:00Z") == dt
    assert parse_iso(iso(dt)) == dt


def test_iso_converts_to_utc():
    dt = datetime(2026, 10, 20, 8, 15, tzinfo=timezone(timedelta(hours=2)))
    assert iso(dt) == "2026-10-20T06:15:00Z"


def test_iso_rejects_naive():
    with pytest.raises(ValueError):
        iso(datetime(2026, 10, 20, 6, 15))
