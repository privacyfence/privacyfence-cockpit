"""Notification-worthy events and the sinks that receive them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from privacyfence_cockpit.models import Outcome


class EventKind(StrEnum):
    RUN_FAILED = "run_failed"
    SCHEDULE_MISSED = "schedule_missed"
    TOKEN_EXPIRING = "token_expiring"  # noqa: S105  # nosec B105  # an event name, not a credential
    TOKEN_INVALID = "token_invalid"  # noqa: S105  # nosec B105  # an event name, not a credential
    VENV_FAILED = "venv_failed"
    GIT_FETCH_FAILING = "git_fetch_failing"
    TEST = "test"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    time: datetime
    recipe: str | None = None
    schedule: str | None = None
    outcome: Outcome | None = None
    exit_code: int | None = None
    run_id: int | None = None
    token_name: str | None = None
    days_left: int | None = None
    detail: str | None = None


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...


class NullSink:
    def emit(self, event: Event) -> None:
        return None


@dataclass
class ListSink:
    events: list[Event] = field(default_factory=list)

    def emit(self, event: Event) -> None:
        self.events.append(event)
