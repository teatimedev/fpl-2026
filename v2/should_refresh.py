"""
Decide whether this scheduled run should rebuild, and why.

The workflow fires hourly; almost every run should exit here in a few seconds.
A rebuild happens in one of four windows, and only once per window per
gameweek (data/last_refresh.json remembers):

  T-24h    22.5–26.5 hours before the next deadline — bookmaker odds are
           usually posted by now and the pressers are still to come
  T-2h     0.75–3.5 hours before the deadline — the last word: late injury
           news, the closing line
  weekly   Thursday 06:00–09:00 UTC — a guaranteed refresh in international
           breaks and any week the deadline windows are missed
  manual   workflow_dispatch: always

Windows are wider than the hourly cadence because GitHub's cron can slip by
half an hour or more at busy times.

Writes GitHub Actions outputs: run=true|false, mode=full|news|noop,
reason=<window>, gw=<n>, hours=<h to deadline>.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / 'data' / 'last_refresh.json'
FPL = 'https://fantasy.premierleague.com/api'
WINDOWS = (('T-2h', 0.75, 3.5), ('T-24h', 22.5, 26.5))


def decide_mode(hours, now):
    """Return the scheduled work mode and its stable reason."""
    for name, lo, hi in WINDOWS:
        if lo <= hours <= hi:
            return 'full', name
    if now.weekday() == 3 and 6 <= now.hour < 9:
        return 'full', 'weekly'
    if hours < 0.75:
        return 'noop', 'deadline-lock'
    if 0.75 <= hours <= 6:
        return 'news', 'news-hourly'
    if 6 < hours <= 30:
        return ('news', 'news-3h') if now.hour % 3 == 0 else ('noop', 'news-cadence')
    return 'noop', 'outside-windows'


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
        # and reason != 'weekly' would fire a bogus GW0 notification.
        out(run='true', mode='full', reason='api-unreachable', gw='0', hours='0')
        return
    nxt = next((e for e in events if e.get('is_next')), None)
    if not nxt:
        nxt = next((e for e in events
                    if datetime.fromisoformat(e['deadline_time'].replace('Z', '+00:00')) > now),
                   events[-1])
    gw = nxt['id']
    dl = datetime.fromisoformat(nxt['deadline_time'].replace('Z', '+00:00'))
    hours = (dl - now).total_seconds() / 3600

    mode, window = decide_mode(hours, now)
    if forced:
        mode = os.environ.get('INPUT_MODE') or 'full'
        window = f'manual-{mode}'

    if mode == 'noop':
        out(run='false', mode='noop', reason=window, gw=gw, hours=f'{hours:.1f}')
        return

    last = json.loads(MARKER.read_text()) if MARKER.exists() else {}
    if mode == 'full' and not window.startswith('manual') and last.get('gw') == gw and window in last.get('done', []):
        # A completed T-2h rebuild must not suppress the later hourly news
        # checks. They are precisely where last-minute press updates arrive.
        if window == 'T-2h' and hours >= 0.75:
            out(run='true', mode='news', reason='news-hourly', gw=gw, hours=f'{hours:.1f}')
            return
        out(run='false', mode='noop', reason=f'{window}-already-done', gw=gw, hours=f'{hours:.1f}')
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
