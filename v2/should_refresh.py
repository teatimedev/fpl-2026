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

Writes GitHub Actions outputs: run=true|false, reason=<window>, gw=<n>,
hours=<h to deadline>.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / 'data' / 'last_refresh.json'
FPL = 'https://fantasy.premierleague.com/api'
WINDOWS = (('T-2h', 0.75, 3.5), ('T-24h', 22.5, 26.5))


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
    try:
        req = urllib.request.Request(f'{FPL}/bootstrap-static/',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            events = json.loads(r.read())['events']
    except Exception as ex:
        # if the API is down we would rather refresh than not
        out(run='true', reason='api-unreachable', gw='0', hours='0')
        return
    nxt = next((e for e in events if e.get('is_next')), None)
    if not nxt:
        nxt = next((e for e in events
                    if datetime.fromisoformat(e['deadline_time'].replace('Z', '+00:00')) > now),
                   events[-1])
    gw = nxt['id']
    dl = datetime.fromisoformat(nxt['deadline_time'].replace('Z', '+00:00'))
    hours = (dl - now).total_seconds() / 3600

    window = None
    for name, lo, hi in WINDOWS:
        if lo <= hours <= hi:
            window = name
            break
    if window is None and now.weekday() == 3 and 6 <= now.hour < 9:
        window = 'weekly'
    if forced:
        window = 'manual'

    if window is None:
        out(run='false', reason='outside-windows', gw=gw, hours=f'{hours:.1f}')
        return

    last = json.loads(MARKER.read_text()) if MARKER.exists() else {}
    if window != 'manual' and last.get('gw') == gw and window in last.get('done', []):
        out(run='false', reason=f'{window}-already-done', gw=gw, hours=f'{hours:.1f}')
        return
    out(run='true', reason=window, gw=gw, hours=f'{hours:.1f}')


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
