from privacyfence_cockpit.events import Event, EventKind, EventSink, ListSink, NullSink
from privacyfence_cockpit.models import Outcome, utc_now


def test_list_sink_records_events_in_order():
    sink = ListSink()
    first = Event(EventKind.TEST, utc_now())
    second = Event(EventKind.RUN_FAILED, utc_now(), recipe="a/b", outcome=Outcome.FAILED, exit_code=2)
    sink.emit(first)
    sink.emit(second)
    assert sink.events == [first, second]


def test_list_sinks_do_not_share_state():
    a, b = ListSink(), ListSink()
    a.emit(Event(EventKind.TEST, utc_now()))
    assert b.events == []


def test_null_sink_drops_events():
    sink: EventSink = NullSink()
    sink.emit(Event(EventKind.TEST, utc_now()))


def test_event_defaults():
    event = Event(EventKind.TOKEN_EXPIRING, utc_now())
    assert event.recipe is None and event.days_left is None and event.detail is None
