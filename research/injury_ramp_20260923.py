"""Undated-injury return ramp: the evidence behind availability.py's constants.

No historical FPL status/news exists before 2026/27, so the only series is
this season's: every refresh commit of data/projections.json (status, news,
chance per player) plus today's v2/fpl.db. Outcomes are 2026/27 gw_stat
starts. For each deadline n (the last snapshot before it) a first-choice
player (archived deadline baseline_start >= 0.5) flagged 'i' with no
parseable return date is followed into GW n+1..5; his starts are compared
with what equally rated AVAILABLE players did at the same horizon (the
control ratio absorbs the baseline's own drift with horizon). The ramp
1 - exp(-max(0, days - LAG) / TAU) is fitted by maximum likelihood on the
calendar gap between deadlines. Also: Kaplan-Meier time for undated flags to
clear, and the doubtful ('d') carry into the following gameweek.

    .venv/bin/python research/injury_ramp_20260923.py
"""
import json
import math
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v2'))
from availability import _dated_return  # noqa: E402

UNDATED = re.compile(r'unknown return', re.I)


def status_series():
    """{snapshot time: {player id: row}} from git history + the live db."""
    log = subprocess.run(['git', '-C', str(ROOT), 'log', '--format=%H %cI', '--',
                          'data/projections.json'], capture_output=True, text=True,
                         check=True).stdout.split('\n')
    snaps = defaultdict(dict)
    for line in filter(None, log):
        sha, when = line.split()
        blob = subprocess.run(['git', '-C', str(ROOT), 'show', f'{sha}:data/projections.json'],
                              capture_output=True, check=True).stdout
        try:
            players = json.loads(blob.decode('utf-8')).get('players', [])
        except (UnicodeDecodeError, ValueError):
            continue                           # one early commit is not valid UTF-8
        for r in players:
            snaps[datetime.fromisoformat(when)][r['id']] = dict(
                name=r.get('name'), status=r.get('status'), news=r.get('news') or '',
                chance=r.get('chance'))
    cx = sqlite3.connect(ROOT / 'v2' / 'fpl.db')
    now = datetime.fromisoformat('2026-09-23T10:00:00+00:00')
    for pid, name, status, news, chance in cx.execute(
            'SELECT id, web_name, status, news, chance FROM player'):
        snaps[now][pid] = dict(name=name, status=status, news=news or '', chance=chance)
    return snaps


def kind(r):
    if r['status'] == 'a':
        return 'a'
    if r['status'] == 'd':
        return 'd'
    if r['status'] in ('i', 's'):
        return r['status'] + ('_dated' if _dated_return(r['news']) else
                              '_unknown' if UNDATED.search(r['news']) else '_other')
    return None


def main():
    snaps = status_series()
    times = sorted(snaps)
    boot = json.loads((ROOT / 'v2' / 'cache' / 'bootstrap.json').read_text())
    deadline = {e['id']: datetime.fromisoformat(e['deadline_time'].replace('Z', '+00:00'))
                for e in boot['events']}
    code_of = {e['id']: e['code'] for e in boot['elements']}
    cx = sqlite3.connect(ROOT / 'v2' / 'fpl.db')
    started = defaultdict(dict)
    for code, rnd, st in cx.execute(
            "SELECT code, round, starts FROM gw_stat WHERE season = '2026/27'"):
        started[code][rnd] = max(started[code].get(rnd, 0), int((st or 0) > 0))
    baseline = {}
    for n in range(1, 6):
        path = ROOT / 'data' / 'history' / f'gw{n}.json'
        if path.exists():
            baseline[n] = {r['id']: r.get('baseline_start')
                           for r in json.loads(path.read_text())['players']}
    last_round = max(r for rows in started.values() for r in rows)
    print(f'{len(times)} status snapshots {times[0].date()} -> {times[-1].date()}; '
          f'outcomes through GW{last_round}')

    obs = []
    for n in sorted(baseline):
        before = [t for t in times if t < deadline[n]]
        if not before:
            continue
        for pid, r in snaps[before[-1]].items():
            base = baseline[n].get(pid)
            k = kind(r)
            if base is None or base < 0.5 or k is None:
                continue
            if k == 'd':                       # split by the stated chance
                k = f"d (chance {r['chance'] if r['chance'] is not None else 'none'})"
            for g in range(n + 1, last_round + 1):
                obs.append(dict(kind=k, chance=r['chance'], name=r['name'], ahead=g - n,
                                gap=(deadline[g] - deadline[n]).total_seconds() / 86400,
                                base=base, st=started.get(code_of.get(pid), {}).get(g, 0)))
    ctrl = defaultdict(lambda: [0, 0.0])
    for o in obs:
        if o['kind'] == 'a':
            ctrl[o['ahead']][0] += o['st']
            ctrl[o['ahead']][1] += o['base']
    ctrl = {k: s / b for k, (s, b) in ctrl.items()}
    print('available regulars, realised / baseline by GWs ahead:',
          ', '.join(f'+{k} {v:.2f}' for k, v in sorted(ctrl.items())))

    print(f"\n{'flag at deadline n':<20}{'GW':>5}{'n':>5}{'started':>9}{'baseline':>10}"
          f"{'rel. to available':>19}")
    tab = defaultdict(list)
    for o in obs:
        if o['kind'] != 'a':
            tab[(o['kind'], o['ahead'])].append(o)
    for (k, ahead), rows in sorted(tab.items()):
        s = sum(o['st'] for o in rows)
        b = sum(o['base'] for o in rows)
        e = sum(o['base'] * ctrl[o['ahead']] for o in rows)
        print(f'{k:<20}{"n+" + str(ahead):>5}{len(rows):>5}{s / len(rows):>9.2f}'
              f'{b / len(rows):>10.2f}{s / e:>19.2f}')

    undated = [o for o in obs if o['kind'] == 'i_unknown']

    def nll(lag, tau):
        total = 0.0
        for o in undated:
            f = 1 - math.exp(-max(0.0, o['gap'] - lag) / tau)
            p = min(0.999, max(1e-4, o['base'] * ctrl[o['ahead']] * f))
            total -= math.log(p) if o['st'] else math.log(1 - p)
        return total

    best = min((nll(lag, tau), lag, tau) for lag in range(15) for tau in range(10, 400, 5))
    band = [tau for tau in range(10, 400, 5) if nll(best[1], tau) - best[0] < 1.92]
    flat = sum(-math.log(min(0.999, o['base'] * ctrl[o['ahead']])) if o['st'] else
               -math.log(1 - min(0.999, o['base'] * ctrl[o['ahead']])) for o in undated)
    print(f'\nundated ramp MLE over {len(undated)} player-weeks '
          f'({len({o["name"] for o in undated})} players): LAG {best[1]} d, TAU {best[2]} d '
          f'(TAU 95% profile {min(band)}-{max(band)}); NLL {best[0]:.1f} vs {flat:.1f} for '
          'the old "fit from the next gameweek" rule')

    # Kaplan-Meier: days for an undated flag to clear ('a', or 'd' at >= 75%)
    episodes = []
    for pid in {pid for t in times for pid in snaps[t]}:
        seq = [(t, snaps[t][pid]) for t in times if pid in snaps[t]]
        start = None
        for t, r in seq:
            if start is None and kind(r) == 'i_unknown':
                start = t
            elif start is not None and (r['status'] == 'a' or (
                    r['status'] == 'd' and (r['chance'] or 0) >= 75)):
                if start > times[0]:           # left-censored episodes are dropped
                    episodes.append(((t - start).days, True))
                start = None
        if start is not None and start > times[0]:
            episodes.append(((seq[-1][0] - start).days, False))
    at_risk, surv, median = len(episodes), 1.0, None
    for d in sorted({e[0] for e in episodes}):
        cleared = sum(1 for e in episodes if e[0] == d and e[1])
        if cleared:
            surv *= 1 - cleared / at_risk
            if median is None and surv <= 0.5:
                median = d
        at_risk -= sum(1 for e in episodes if e[0] == d)
    print(f'undated flags: {len(episodes)} episodes, {sum(e[1] for e in episodes)} cleared; '
          f'Kaplan-Meier median time to clear {median} days')


if __name__ == '__main__':
    main()
