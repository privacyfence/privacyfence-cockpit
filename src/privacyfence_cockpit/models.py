"""Run outcomes, run modes and UTC timestamp helpers shared by every module."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum


class Outcome(StrEnum):
    OK = "ok"
    NO_CHANGE = "no_change"
    RECIPE_ERROR = "recipe_error"
    FAILED = "failed"
    DENIED = "denied"
    NOT_SIGNED_IN = "not_signed_in"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"
    MISSED = "missed"


OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.OK: "ok",
    Outcome.NO_CHANGE: "no change",
    Outcome.RECIPE_ERROR: "recipe error",
    Outcome.FAILED: "failed",
    Outcome.DENIED: "denied",
    Outcome.NOT_SIGNED_IN: "not signed in",
    Outcome.STOPPED: "stopped",
    Outcome.TIMED_OUT: "timed out",
    Outcome.MISSED: "missed",
}

SUCCESS_OUTCOMES = frozenset({Outcome.OK, Outcome.NO_CHANGE})

NOTIFY_OUTCOMES = frozenset(
    {Outcome.RECIPE_ERROR, Outcome.FAILED, Outcome.DENIED, Outcome.NOT_SIGNED_IN, Outcome.TIMED_OUT}
)

RESULT_FILE_OUTCOMES: dict[str, Outcome] = {
    "ok": Outcome.OK,
    "no_change": Outcome.NO_CHANGE,
    "recipe_error": Outcome.RECIPE_ERROR,
    "failed": Outcome.FAILED,
    "denied": Outcome.DENIED,
    "not_signed_in": Outcome.NOT_SIGNED_IN,
}

EXIT_CODE_OUTCOMES: dict[int, Outcome] = {
    0: Outcome.OK,
    1: Outcome.RECIPE_ERROR,
    2: Outcome.FAILED,
    3: Outcome.DENIED,
    4: Outcome.NOT_SIGNED_IN,
}


class Mode(StrEnum):
    ATTENDED = "attended"
    UNATTENDED = "unattended"
    REPLAY = "replay"
    PREFLIGHT = "preflight"
    CHECK = "check"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("iso() needs a timezone-aware datetime")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
