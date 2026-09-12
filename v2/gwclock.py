"""Which gameweek is next, and when its deadline is.

Every model in v2 projects a rolling window that starts at the NEXT gameweek —
the one whose deadline has not passed yet. This is the one place that decides
which gameweek that is, from the bootstrap fetch.py just cached, so the team
model, the player model and the exporters cannot disagree about it.

    from gwclock import next_gw
    gw, deadline = next_gw()          # e.g. (7, '2026-10-03T10:00:00Z')

Falls back to the live API only if there is no cache. Missing deadlines fail
explicitly; a stale API flag must never reopen a closed gameweek.
"""
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache' / 'bootstrap.json'
FPL = 'https://fantasy.premierleague.com/api'
WINDOW = 6          # gameweeks projected ahead, including the next one


def _events():
    if CACHE.exists():
        return json.loads(CACHE.read_text())['events']
    try:
        req = urllib.request.Request(f'{FPL}/bootstrap-static/',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())['events']
    except Exception:
        return []


class NoUpcomingDeadline(ValueError):
    """No future deadline is present in the supplied season calendar."""


def upcoming_event(events, now=None):
    """Earliest future deadline, regardless of delayed FPL current/next flags.

    Return None after the season. An empty or malformed calendar is an error,
    not evidence that the season ended or that GW1 should be invented.
    """
    if not events:
        raise ValueError('FPL event calendar is unavailable or empty')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Deadline clock requires a timezone-aware time')
    future = []
    for event in events:
        deadline = datetime.fromisoformat(event['deadline_time'].replace('Z', '+00:00'))
        if deadline.tzinfo is None:
            raise ValueError('FPL deadline is missing a timezone')
        if deadline > now:
            future.append((deadline, int(event['id']), event))
    return min(future, key=lambda item: item[:2])[2] if future else None


def next_gw(events=None, *, now=None):
    """(gameweek id, deadline ISO string) of the next deadline still to pass."""
    events = events if events is not None else _events()
    # FPL_GW_OVERRIDE=7 pretends it is the run-up to Gameweek 7 — for testing
    # the rolling window without waiting for the season to get there.
    forced = os.environ.get('FPL_GW_OVERRIDE')
    if forced:
        gw = int(forced)
        ev = next((e for e in events if e['id'] == gw), None)
        if ev is None:
            raise ValueError(f'FPL_GW_OVERRIDE={gw} is absent from the calendar')
        return gw, ev['deadline_time']
    nxt = upcoming_event(events, now)
    if nxt is None:
        raise NoUpcomingDeadline('No future FPL deadline remains in this season')
    return nxt['id'], nxt['deadline_time']


def window(events=None):
    """(start_gw, end_gw) of the modelled window, clipped to the season."""
    events = events if events is not None else _events()
    start, _ = next_gw(events)
    last = max((e['id'] for e in events), default=38)
    return start, min(start + WINDOW - 1, last)


if __name__ == '__main__':
    gw, dl = next_gw()
    s, e = window()
    print(f'next gameweek {gw}, deadline {dl}; modelled window GW{s}-{e}')
