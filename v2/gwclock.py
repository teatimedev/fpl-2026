"""Which gameweek is next, and when its deadline is.

Every model in v2 projects a rolling window that starts at the NEXT gameweek —
the one whose deadline has not passed yet. This is the one place that decides
which gameweek that is, from the bootstrap fetch.py just cached, so the team
model, the player model and the exporters cannot disagree about it.

    from gwclock import next_gw
    gw, deadline = next_gw()          # e.g. (7, '2026-10-03T10:00:00Z')

Falls back to the live API only if there is no cache, and to Gameweek 1 if
there is no network either, so it always answers.
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


def next_gw(events=None):
    """(gameweek id, deadline ISO string) of the next deadline still to pass."""
    events = events if events is not None else _events()
    # FPL_GW_OVERRIDE=7 pretends it is the run-up to Gameweek 7 — for testing
    # the rolling window without waiting for the season to get there.
    forced = os.environ.get('FPL_GW_OVERRIDE')
    if forced:
        gw = int(forced)
        ev = next((e for e in events if e['id'] == gw), None)
        return gw, (ev['deadline_time'] if ev else '2027-01-01T00:00:00Z')
    if not events:
        return 1, '2026-08-21T17:30:00Z'
    now = datetime.now(timezone.utc)
    nxt = next((e for e in events if e.get('is_next')), None)
    if nxt:
        return nxt['id'], nxt['deadline_time']
    # between the last deadline of the season and its final whistle, or a
    # bootstrap without flags: first event whose deadline is still ahead
    for e in events:
        dl = datetime.fromisoformat(e['deadline_time'].replace('Z', '+00:00'))
        if dl > now:
            return e['id'], e['deadline_time']
    last = events[-1]
    return last['id'], last['deadline_time']


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
