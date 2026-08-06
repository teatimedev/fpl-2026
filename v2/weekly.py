"""
The weekly run.

One command, every week, that answers the only four questions that matter:

    who do I captain, who starts, should I transfer, and who is about to
    change price

    python v2/weekly.py --team 1234567

`--team` is your FPL entry id (the number in the URL of your points page). Your
picks become public after each deadline, so from Gameweek 1 onwards this reads
your real squad. Before then, put your 15 in v2/my_squad.txt (one player name
per line) and it will use that instead.

What it does each run:
  1. refreshes prices, availability and fixtures from the FPL API
  2. pulls any newly published bookmaker odds for the coming round and uses
     them in preference to the fitted team ratings — the closing line is the
     sharpest estimate available, and the model is measurably behind it
  3. re-runs the projections over the remaining gameweeks
  4. scores your squad, picks the XI and captain, and rates every transfer
     available to you including whether a hit is worth taking
  5. flags players about to rise or fall in price
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB = HERE / 'fpl.db'
PROJ = HERE / 'projections_v2.json'
SQUAD_FILE = HERE / 'my_squad.txt'
DIGEST = HERE / 'digest.md'
FPL = 'https://fantasy.premierleague.com/api'
UA = {'User-Agent': 'Mozilla/5.0'}

SQUAD_SHAPE = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
HIT = 4.0


def api(path):
    req = urllib.request.Request(f'{FPL}/{path}', headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run(cmd, cwd=HERE):
    return subprocess.run([sys.executable, *cmd], cwd=cwd,
                          capture_output=True, text=True)


# ------------------------------------------------------------- refresh
def refresh(full=False):
    print('· refreshing data')
    args = ['fetch.py'] + ([] if full else ['--skip-histories'])
    r = run(args)
    if r.returncode:
        print(r.stdout[-1500:], r.stderr[-1500:])
        raise SystemExit('fetch failed')
    market = int(re.search(r'forward market odds: (\d+)', r.stdout).group(1)) \
        if 'forward market odds' in r.stdout else 0
    print(f'  prices and fixtures updated; {market} fixtures have bookmaker odds')

    # to_csv.py is part of the chain, not an afterthought: the optimiser, the
    # simulator, the transfer planner and the web app all read
    # data/projections.csv. Refreshing only projections_v2.json leaves every one
    # of them running on last week's numbers.
    for step in (['teams_model.py'], ['season_view.py'], ['player_model.py'],
                 ['to_csv.py']):
        s = run(step)
        if s.returncode:
            print(s.stdout[-1200:], s.stderr[-1200:])
            raise SystemExit(f'{step[0]} failed')
    print('  team ratings refitted, projections rebuilt, shared data updated')
    return market


# --------------------------------------------------------------- state
def current_event():
    boot = api('bootstrap-static/')
    nxt = next((e for e in boot['events'] if e.get('is_next')), None)
    cur = next((e for e in boot['events'] if e.get('is_current')), None)
    return boot, cur, nxt


def load_projections():
    d = json.loads(PROJ.read_text())
    return {p['id']: p for p in d['players']}, d['horizon']


def load_squad(team_id, players, gw):
    """Real picks by entry id, else the local squad file."""
    if team_id:
        for ev in range(gw - 1, 0, -1):
            try:
                picks = api(f'entry/{team_id}/event/{ev}/picks/')
                ids = [p['element'] for p in picks['picks']]
                bank = picks['entry_history']['bank'] / 10
                ft = picks['entry_history'].get('event_transfers', 0)
                print(f'  loaded your real squad from Gameweek {ev}')
                return ids, bank, ft
            except Exception:
                continue
        print('  no public picks yet for that entry (they appear after a deadline)')
    if SQUAD_FILE.exists():
        names = [l.strip() for l in SQUAD_FILE.read_text().splitlines()
                 if l.strip() and not l.startswith('#')]
        byname = {}
        for p in players.values():
            byname.setdefault(p['name'].lower(), p['id'])
            byname.setdefault(p['full_name'].lower(), p['id'])
        ids, missing = [], []
        for n in names:
            pid = byname.get(n.lower())
            (ids.append(pid) if pid else missing.append(n))
        if missing:
            print(f'  could not match: {", ".join(missing)}')
        print(f'  loaded {len(ids)} players from {SQUAD_FILE.name}')
        return ids, 0.0, 1
    return [], 0.0, 1


# ------------------------------------------------------------ analysis
def remaining(p, gw, horizon):
    """Projected points from `gw` to the end of the modelled horizon."""
    v = p['proj_by_gw']
    return sum(v[gw - 1:horizon]) if gw - 1 < len(v) else 0.0


def pick_xi(squad, gw, horizon):
    """Best legal XI for the coming gameweek."""
    def one_gw(p):
        v = p['proj_by_gw']
        return v[gw - 1] if gw - 1 < len(v) else 0.0
    bypos = {}
    for p in squad:
        bypos.setdefault(p['pos'], []).append(p)
    for k in bypos:
        bypos[k].sort(key=one_gw, reverse=True)
    xi, used = [], {k: 0 for k in SQUAD_SHAPE}
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        for p in bypos.get(pos, [])[:XI_MIN[pos]]:
            xi.append(p); used[pos] += 1
    rest = sorted((p for p in squad if p not in xi), key=one_gw, reverse=True)
    for p in rest:
        if len(xi) >= 11:
            break
        if used[p['pos']] < XI_MAX[p['pos']]:
            xi.append(p); used[p['pos']] += 1
    bench = [p for p in squad if p not in xi]
    bench.sort(key=lambda p: (p['pos'] != 'GKP', -one_gw(p)))
    return xi, bench, one_gw


def transfer_options(squad, players, bank, gw, horizon, top=8):
    """Every single transfer available, scored over the remaining horizon."""
    owned = {p['id'] for p in squad}
    club = {}
    for p in squad:
        club[p['team']] = club.get(p['team'], 0) + 1
    out = []
    pool = [p for p in players.values()
            if p['id'] not in owned and p['status'] != 'u'
            and remaining(p, gw, horizon) > 0]
    for o in squad:
        cash = bank + o['price']
        for n in pool:
            if n['pos'] != o['pos'] or n['price'] > cash + 1e-9:
                continue
            if n['team'] != o['team'] and club.get(n['team'], 0) >= 3:
                continue
            gain = remaining(n, gw, horizon) - remaining(o, gw, horizon)
            if gain > 0:
                out.append((gain, o, n))
    out.sort(key=lambda t: -t[0])
    # keep the best upgrade per outgoing player so the list is not all one man
    seen, uniq = set(), []
    for g, o, n in out:
        if o['id'] in seen:
            continue
        seen.add(o['id']); uniq.append((g, o, n))
        if len(uniq) >= top:
            break
    return uniq


def price_watch(boot, players, owned_ids):
    """Who is about to rise or fall, from this gameweek's transfer flow."""
    rows = []
    for e in boot['elements']:
        net = e['transfers_in_event'] - e['transfers_out_event']
        p = players.get(e['id'])
        if not p:
            continue
        rows.append((net, p, e['transfers_in_event'], e['transfers_out_event']))
    rises = sorted(rows, key=lambda r: -r[0])[:6]
    falls = sorted(rows, key=lambda r: r[0])[:6]
    return rises, falls


# ------------------------------------------------------------- digest
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--team', type=int, help='your FPL entry id')
    ap.add_argument('--full', action='store_true',
                    help='also refresh 4-season player histories (slow, weekly)')
    ap.add_argument('--no-refresh', action='store_true')
    args = ap.parse_args()

    if not args.no_refresh:
        refresh(full=args.full)

    boot, cur, nxt = current_event()
    gw = (nxt or cur)['id']
    deadline = (nxt or cur)['deadline_time']
    players, horizon = load_projections()

    dl = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
    left = dl - datetime.now(timezone.utc)
    days, hrs = left.days, left.seconds // 3600

    L = []
    L.append(f'# FPL weekly — Gameweek {gw}')
    L.append('')
    L.append(f'Deadline **{dl:%a %d %b, %H:%M UTC}** — {days}d {hrs}h away.')
    L.append(f'Projections cover GW{gw}–{horizon}.' if gw <= horizon else
             f'Note: the modelled horizon ends at GW{horizon}; rerun with a '
             f'longer horizon to plan further.')
    L.append('')

    ids, bank, _ = load_squad(args.team, players, gw)
    squad = [players[i] for i in ids if i in players]

    if len(squad) != 15:
        L.append('## No squad loaded')
        L.append('')
        L.append(f'Pass `--team <your entry id>`, or list your 15 players in '
                 f'`{SQUAD_FILE.name}`. Meanwhile, here are the best available '
                 f'players over GW{gw}–{horizon}:')
        L.append('')
        for pos in ('GKP', 'DEF', 'MID', 'FWD'):
            best = sorted((p for p in players.values() if p['pos'] == pos),
                          key=lambda p: -remaining(p, gw, horizon))[:6]
            L.append(f'**{pos}** — ' + ', '.join(
                f"{p['name']} £{p['price']} ({remaining(p, gw, horizon):.1f})"
                for p in best))
        L.append('')
    else:
        xi, bench, one_gw = pick_xi(squad, gw, horizon)
        cap = max(xi, key=one_gw)
        vice = sorted(xi, key=one_gw, reverse=True)[1]

        L.append(f'## Captain: **{cap["name"]}** ({cap["team"]})')
        L.append('')
        L.append(f'Projected {one_gw(cap):.1f} this week, doubled to '
                 f'{one_gw(cap)*2:.1f}. Vice: {vice["name"]} ({one_gw(vice):.1f}).')
        alt = sorted(xi, key=one_gw, reverse=True)[:4]
        L.append('')
        L.append('| rank | player | club | this GW |')
        L.append('|---|---|---|---|')
        for i, p in enumerate(alt, 1):
            L.append(f'| {i} | {p["name"]} | {p["team"]} | {one_gw(p):.1f} |')
        L.append('')

        L.append('## Starting XI')
        L.append('')
        L.append('| pos | player | club | this GW | GW%d–%d |' % (gw, horizon))
        L.append('|---|---|---|---|---|')
        for p in sorted(xi, key=lambda p: ('GKP DEF MID FWD'.split().index(p['pos']),
                                           -one_gw(p))):
            mark = ' (C)' if p is cap else (' (V)' if p is vice else '')
            L.append(f'| {p["pos"]} | {p["name"]}{mark} | {p["team"]} | '
                     f'{one_gw(p):.1f} | {remaining(p, gw, horizon):.1f} |')
        L.append('')
        L.append('**Bench order:** ' + ', '.join(
            f'{p["name"]} ({one_gw(p):.1f})' for p in bench))
        L.append('')

        flagged = [p for p in squad if p['status'] != 'a']
        if flagged:
            L.append('## Availability')
            L.append('')
            for p in flagged:
                L.append(f'- **{p["name"]}** — {p["news"] or p["status"]}')
            L.append('')

        L.append(f'## Transfers  (£{bank:.1f}m in the bank)')
        L.append('')
        opts = transfer_options(squad, players, bank, gw, horizon)
        if not opts:
            L.append('No transfer improves the squad over the remaining '
                     'gameweeks. Bank it.')
        else:
            L.append(f'Gain is over GW{gw}–{horizon}. A hit costs 4, so only '
                     f'act on a second transfer above that line.')
            L.append('')
            L.append('| out | in | cost | gain | worth a hit? |')
            L.append('|---|---|---|---|---|')
            for gain, o, n in opts:
                delta = n['price'] - o['price']
                worth = 'yes' if gain > HIT else 'free transfer only'
                L.append(f'| {o["name"]} ({o["team"]}) | {n["name"]} ({n["team"]}) '
                         f'| {delta:+.1f} | **{gain:+.1f}** | {worth} |')
            best = opts[0]
            L.append('')
            L.append(f'**Recommended:** {best[1]["name"]} → {best[2]["name"]}, '
                     f'worth {best[0]:+.1f} over the remaining weeks.'
                     if best[0] > 1.0 else
                     '**Recommended:** nothing compelling — bank the transfer.')
        L.append('')

    rises, falls = price_watch(boot, players, set(ids))
    L.append('## Price watch')
    L.append('')
    if not rises or rises[0][0] == 0:
        L.append('Prices are locked until the Gameweek 1 deadline, so there is '
                 'no transfer flow to read yet. This section becomes useful '
                 'once the season starts.')
    else:
        L.append('Net transfers this gameweek. Large positive numbers rise '
                 'tonight, negative fall.')
        L.append('')
        L.append('| rising | net | falling | net |')
        L.append('|---|---|---|---|')
        for (rn, rp, _, _), (fn, fp, _, _) in zip(rises, falls):
            own_r = ' ⭑' if rp['id'] in ids else ''
            own_f = ' ⭑' if fp['id'] in ids else ''
            L.append(f'| {rp["name"]} £{rp["price"]}{own_r} | {rn:+,} | '
                     f'{fp["name"]} £{fp["price"]}{own_f} | {fn:+,} |')
        L.append('')
        L.append('⭑ = in your squad.')
    L.append('')
    L.append('---')
    L.append('')
    L.append(f'_Generated {datetime.now():%Y-%m-%d %H:%M}. Projections are '
             f'estimates: hold-out rank correlation is about 0.46, so treat the '
             f'ordering as a strong hint and the point totals as rough._')

    text = '\n'.join(L)
    DIGEST.write_text(text)
    print()
    print(text)
    print(f'\n(also written to {DIGEST})')


if __name__ == '__main__':
    main()
