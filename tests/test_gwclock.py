from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from v2.gwclock import NoUpcomingDeadline, next_gw, upcoming_event
from v2.news_pipeline import _context
from v2.should_refresh import decide_mode


NOW = datetime(2026, 9, 12, 12, 30, tzinfo=timezone.utc)
EVENTS = [
    {'id': 5, 'deadline_time': '2026-09-19T10:00:00Z'},
    {'id': 4, 'deadline_time': '2026-09-12T12:30:00Z', 'is_next': True},
]


def test_exact_deadline_and_stale_flag_roll_over_in_all_python_consumers():
    with patch.dict('os.environ', {}, clear=True):
        assert next_gw(EVENTS, now=NOW) == (5, EVENTS[0]['deadline_time'])
        assert _context({'events': EVENTS}, NOW)[0] == 5
    assert upcoming_event(EVENTS, NOW)['id'] == 5


def test_calendar_order_and_flags_do_not_skip_an_open_deadline():
    before = NOW.replace(hour=11)
    assert upcoming_event(EVENTS, before)['id'] == 4


def test_missing_calendar_and_season_end_do_not_invent_a_deadline():
    with pytest.raises(ValueError, match='empty'):
        upcoming_event([], NOW)
    assert upcoming_event(EVENTS[1:], NOW) is None
    with patch.dict('os.environ', {}, clear=True), pytest.raises(NoUpcomingDeadline):
        next_gw(EVENTS[1:], now=NOW)


def test_thursday_catch_up_cannot_bypass_deadline_lock():
    thursday = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    for hours in (0.5, 0, -1):
        assert decide_mode(hours, thursday) == ('noop', 'deadline-lock')
