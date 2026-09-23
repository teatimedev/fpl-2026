"""
Decide whether this scheduled run should rebuild, and why.

The workflow fires hourly; almost every run should exit here in a few seconds.
A rebuild happens in one of these windows, and only once per window per
gameweek (data/last_refresh.json remembers):

  T-24h          22.5–26.5 hours before the next deadline — bookmaker odds are
                 usually posted by now and the pressers are still to come
  T-2h           1.5–3.5 hours before the deadline — the last word: late injury
                 news, the closing line. A full run takes up to ~30 minutes, so
                 it must start early enough to publish well before the lock.
  post-deadline  from 1.5 hours after the previous deadline — the submitted
                 picks become public, so the next gameweek's plan can be built
                 from the real squad, bank and free transfers straight away
  graded         once FPL marks the previous gameweek finished and data-checked
                 — the scorecard and retrospective grade it, and the plan picks
                 up the final points and minutes
  daily          once per day after FPL's overnight price changes (01:00 UTC)
                 if nothing else has rebuilt since — keeps prices, bank and
                 the app's freshness inside a day through international breaks
  manual         workflow_dispatch: always

Windows are wider than the hourly cadence because GitHub's cron can slip by
half an hour or more at busy times — and it drops runs outright: on Thu 27 Aug
2026 nothing fired between 23:46 and 10:00 UTC. So every full window catches
up: a missed T-24h is taken late (any hour before the T-2h window opens), and
post-deadline, graded and daily stay open until a run succeeds. A failed run
is never marked, so the next hourly tick retries it. A window already recorded
in data/last_refresh.json falls through to the news cadence instead.

Writes GitHub Actions outputs: run=true|false, mode=full|news|noop,
reason=<window>, gw=<n>, hours=<h to deadline>.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .gwclock import upcoming_event
except ImportError:  # direct script execution in the lightweight CI gate
    from gwclock import upcoming_event
ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / 'data' / 'last_refresh.json'
FPL = 'https://fantasy.premierleague.com/api'
WINDOWS = (('T-2h', 1.5, 3.5), ('T-24h', 22.5, 26.5))
T2_HI, T24_LO = WINDOWS[0][2], WINDOWS[1][1]


POST_DEADLINE_HOURS = 1.5   # FPL answers 503 while it processes a deadline
PRICE_HOUR = 1              # FPL's overnight price changes have landed by 01:00 UTC


def _price_day_start(now):
    """Start of the current price day: the most recent 01:00 UTC."""
    start = now.replace(hour=PRICE_HOUR, minute=0, second=0, microsecond=0)
    return start if now >= start else start - timedelta(days=1)


def decide_mode(hours, now, done=(), previous=None, last_full_at=None):
    """Return the scheduled work mode and its stable reason.

    `done` lists the full windows already rebuilt for this gameweek; a done
    window is skipped so the tick falls through to the news cadence.
    `previous` describes the gameweek whose deadline most recently passed
    ({'hours_since', 'finished', 'data_checked'}); `last_full_at` is the time
    of the last successful full rebuild, for the daily price refresh."""
    # Every catch-up must obey the deadline lock.
    if hours < 0.75:
        return 'noop', 'deadline-lock'
    for name, lo, hi in WINDOWS:
        if lo <= hours <= hi and name not in done:
            return 'full', name
    # catch-up: the T-24h window went by without a rebuild (dropped cron)
    if T2_HI < hours < T24_LO and 'T-24h' not in done:
        return 'full', 'T-24h'
    if previous and hours > T2_HI:
        graded_ready = previous.get('finished') and previous.get('data_checked')
        if graded_ready and 'graded' not in done:
            return 'full', 'graded'
        # a grading rebuild also reads the post-deadline picks: never run both
        if (not graded_ready and previous['hours_since'] >= POST_DEADLINE_HOURS
                and 'post-deadline' not in done and 'graded' not in done):
            return 'full', 'post-deadline'
    # inside six hours the T-2h rebuild is imminent and news runs hourly
    if hours > 6 and (last_full_at is None or last_full_at < _price_day_start(now)):
        return 'full', 'daily'
    if 0.75 <= hours <= 6:
        return 'news', 'news-hourly'
    if 6 < hours <= 30:
        return ('news', 'news-3h') if now.hour % 3 == 0 else ('noop', 'news-cadence')
    return 'noop', 'outside-windows'


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def previous_event(events, now):
    """The gameweek whose deadline most recently passed, or None pre-season."""
    passed = []
    for event in events:
        deadline = datetime.fromisoformat(event['deadline_time'].replace('Z', '+00:00'))
        if deadline <= now:
            passed.append((deadline, event))
    if not passed:
        return None
    deadline, event = max(passed, key=lambda item: item[0])
    return {'id': event['id'],
            'hours_since': (now - deadline).total_seconds() / 3600,
            'finished': bool(event.get('finished')),
            'data_checked': bool(event.get('data_checked'))}


def out(**kv):
    path = os.environ.get('GITHUB_OUTPUT')
    lines = [f'{k}={v}' for k, v in kv.items()]
    print('\n'.join(lines))
    if path:
        with open(path, 'a') as f:
            f.write('\n'.join(lines) + '\n')


def main():
    forced = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    now = datetime.now(timezone.utc)
    # GitHub's hourly cron meets a real CDN sometimes; one transient 5xx must
    # not push us into the fail-open rebuild from a stale cache. Two retries
    # with ~5s backoff, then fail open (refresh anyway) — an unnecessary full
    # rebuild costs ~10 min of CI; a missed deadline window costs decisions.
    events = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(f'{FPL}/bootstrap-static/',
                                         headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                events = json.loads(r.read())['events']
            break
        except Exception:
            if attempt < 2:
                time.sleep(5)
    if events is None:
        # if the API is down we would rather refresh than not. gw=0 tells the
        # workflow to skip mark() and the ntfy push: mark('api-unreachable', 0)
        # would wipe the done-list for the real GW (mark resets on gw change),
        # and the notification step would fire a bogus GW0 push.
        out(run='true', mode='full', reason='api-unreachable', gw='0', hours='0')
        return
    nxt = upcoming_event(events, now)
    if nxt is None:
        out(run='false', mode='noop', reason='season-finished', gw=0, hours='0')
        return
    gw = nxt['id']
    dl = datetime.fromisoformat(nxt['deadline_time'].replace('Z', '+00:00'))
    hours = (dl - now).total_seconds() / 3600

    # windows already rebuilt for this gameweek fall through to the news
    # cadence — a completed T-2h must not suppress the hourly news checks,
    # which are precisely where last-minute press updates arrive.
    last = json.loads(MARKER.read_text()) if MARKER.exists() else {}
    done = tuple(last.get('done', [])) if last.get('gw') == gw else ()
    last_full_at = _parse_time(last.get('at'))
    mode, window = decide_mode(hours, now, done, previous_event(events, now), last_full_at)
    if forced:
        mode = os.environ.get('INPUT_MODE') or 'full'
        window = f'manual-{mode}'

    if mode == 'noop':
        out(run='false', mode='noop', reason=window, gw=gw, hours=f'{hours:.1f}')
        return
    out(run='true', mode=mode, reason=window, gw=gw, hours=f'{hours:.1f}')


def mark(window, gw):
    """Record that `window` ran for `gw` (called by the workflow after a
    successful rebuild)."""
    last = json.loads(MARKER.read_text()) if MARKER.exists() else {}
    if last.get('gw') != gw:
        last = {'gw': gw, 'done': []}
    if window not in last['done']:
        last['done'].append(window)
    last['at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(last, indent=1) + '\n')
    print(f'marked {window} done for GW{gw}')


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == 'mark':
        mark(sys.argv[2], int(sys.argv[3]))
    else:
        main()
