"""
The weekly run.

One command, every week, that answers the only questions that matter:

    who do I captain, who starts, should I transfer (and should I hold),
    is my lineup set right, and who is about to change price

    python v2/weekly.py --team 1234567

`--team` is your FPL entry id (the number in the URL of your points page). Your
picks become public after each deadline, so from Gameweek 1 onwards this reads
your real squad, bank, captain, vice and bench order. Before then, put your 15
in v2/my_squad.txt (one player name per line; mark the captain "(C)" and vice
"(V)", and list the bench after a line saying "bench:") and it uses that.

What it does each run:
  1. refreshes prices, availability and fixtures from the FPL API, then
     re-fits the team ratings and rebuilds the projections
  2. uses any published bookmaker odds in preference to the fitted ratings —
     the closing line is the sharpest estimate available
  3. scores your squad, picks the XI, captain and vice, and DIFFS them against
     the lineup you actually have set (captain, vice, bench order, formation)
  4. rates every transfer available to you the way it will actually score —
     by how much it lifts your best XI over the modelled window, captain
     included — then the best TWO-move combination, net of hits, and whether
     to hold this week's free transfer instead
  5. with --plan, solves the whole window as one integer program (planner.py)
     and shows the transfer path week by week
  6. flags who to check at the press conferences: doubtful starters, news,
     new signings; and who is about to rise or fall in price
  7. with --snapshot, archives this gameweek's projections so the model can be
     scored against what actually happened (scorecard.py); with --price-log,
     appends today's prices and transfer flow for the price-change model

  8. with --chips, values every chip in every week you could still play it
     (chips.py); with --json, writes the whole digest as data to
     data/weekly.json, which is what the web app renders

Flags: --no-refresh (skip step 1), --plan, --chips, --snapshot, --price-log,
       --json, --push-file (write a short phone-sized summary to v2/push.txt),
       --ft N (override the free-transfer count), --bank X (override bank).
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from gwclock import next_gw          # noqa: E402
from squad_evaluator import (       # noqa: E402
    deadline_unavailable,
    evaluate_squad,
    pick_lineup as shared_pick_lineup,
)

PROJ = HERE / 'projections_v2.json'
VIEW = HERE / 'season_view.json'
SQUAD_FILE = HERE / 'my_squad.txt'
DIGEST = HERE / 'digest.md'
PUSH = HERE / 'push.txt'
HISTORY = ROOT / 'data' / 'history'
PRICE_LOG = ROOT / 'data' / 'price_log'
WEEKLY_JSON = ROOT / 'data' / 'weekly.json'
FPL = 'https://fantasy.premierleague.com/api'
UA = {'User-Agent': 'Mozilla/5.0'}
APP_URL = 'https://fpl-2026.vercel.app'

SQUAD_SHAPE = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
POS_ORDER = ('GKP', 'DEF', 'MID', 'FWD')
HIT = 4.0
MAX_FT = 5
HOLD_THRESHOLD = 2.0     # a free transfer worth less than this over the window
                         # is usually better banked: next week has more information


def worth_rebuilding(diff, n_moves, unlimited=False):
    """Only overturn a settled squad when the gain clears churn per move.

    Unlimited transfers remove the points cost, not the uncertainty and
    decision cost of replacing most of a confirmed squad.
    """
    _ = unlimited
    return n_moves > 0 and diff >= HOLD_THRESHOLD * n_moves


def _supersede_transfer_recommendation(lines, push_lines, transfers, message,
                                       push_message):
    """Replace an earlier tactical headline with the chosen preseason plan."""
    recommendation_indexes = [
        index for index, line in enumerate(lines)
        if line.startswith('**Recommended:') or line.startswith('**No single')
    ]
    if recommendation_indexes:
        lines[recommendation_indexes[0]] = message
        for index in reversed(recommendation_indexes[1:]):
            del lines[index]
    else:
        lines.append(message)
    tactical_prefixes = (
        'Transfers:', 'Fix unavailable:', 'Best swap:', 'Transfer:',
        'Two-mover worth it:', 'Use a FT',
    )
    push_lines[:] = [line for line in push_lines
                     if not line.startswith(tactical_prefixes)]
    push_lines.append(push_message)
    transfers['advice'] = message.replace('**', '')


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
def load_projections():
    d = json.loads(PROJ.read_text())
    return {p['id']: p for p in d['players']}, d['horizon'], d.get('start_gw', 1)


def infer_free_transfers(history, upto_gw):
    """Free transfers available at the deadline of `upto_gw`, from public
    history. FPL: one a week, unused ones roll over up to five; a wildcard or
    free hit week neither spends nor gains. Gameweek 1 is unlimited and
    everyone starts Gameweek 2 with exactly one."""
    if upto_gw <= 1:
        return 15
    chips = {c['event']: c['name'] for c in history.get('chips', [])}
    made = {e['event']: e.get('event_transfers', 0) for e in history.get('current', [])}
    ft = 1
    for g in range(2, upto_gw):
        if g not in made:
            break
        if chips.get(g) in ('wildcard', 'freehit'):
            ft = min(MAX_FT, ft + 1)
        else:
            ft = min(MAX_FT, max(ft - made[g], 0) + 1)
    return ft


def load_squad(team_id, players, gw):
    """Your 15, bank, free transfers and — if known — the lineup you have set.

    Returns dict(ids, bank, ft, lineup, source). `lineup` is None when unknown,
    else dict(xi=[ids], bench=[ids in order], captain=id, vice=id).
    """
    if team_id:
        for ev in range(gw - 1, 0, -1):
            try:
                picks = api(f'entry/{team_id}/event/{ev}/picks/')
            except Exception:
                continue
            ps = sorted(picks['picks'], key=lambda p: p['position'])
            ids = [p['element'] for p in ps]
            bank = picks['entry_history']['bank'] / 10
            lineup = {
                'xi': [p['element'] for p in ps if p['position'] <= 11],
                'bench': [p['element'] for p in ps if p['position'] > 11],
                'captain': next((p['element'] for p in ps if p['is_captain']), None),
                'vice': next((p['element'] for p in ps if p['is_vice_captain']), None),
            }
            hist = {}
            try:
                hist = api(f'entry/{team_id}/history/')
                ft = infer_free_transfers(hist, gw)
            except Exception:
                ft = 1
            print(f'  loaded your real squad and lineup from Gameweek {ev} '
                  f'(bank £{bank:.1f}m, {ft} free transfer{"s" if ft != 1 else ""})')
            return dict(ids=ids, bank=bank, ft=ft, lineup=lineup, history=hist,
                        source=f'FPL entry {team_id}, picks from GW{ev}')
        print('  no public picks yet for that entry (they appear after a deadline)')

    if SQUAD_FILE.exists():
        squad_text = SQUAD_FILE.read_text()
        source_match = re.search(r'^#\s*Source:\s*(.+?)\s*$', squad_text, re.M | re.I)
        confirmed_match = re.search(r'^#\s*Confirmed at:\s*(.+?)\s*$', squad_text, re.M | re.I)
        entry_match = re.search(r'^#\s*FPL entry:\s*(\d+)\s*$', squad_text, re.M | re.I)
        change_matches = re.findall(r'^#\s*Change:\s*(.+?)\s*$', squad_text, re.M | re.I)
        source = source_match.group(1) if source_match else SQUAD_FILE.name
        confirmed_at = confirmed_match.group(1) if confirmed_match else None
        byname = {}
        for p in players.values():
            byname.setdefault(p['name'].lower(), p['id'])
            byname.setdefault(p['full_name'].lower(), p['id'])
        ids, bench, missing = [], [], []
        cap = vice = None
        in_bench = False
        for raw in squad_text.splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if re.match(r'^-*\s*bench\s*:?\s*-*$', line, re.I):
                in_bench = True
                continue
            m = re.match(r'^(.*?)\s*(\((C|V)\))?\s*$', line, re.I)
            name, mark = m.group(1), (m.group(3) or '').upper()
            pid = byname.get(name.lower())
            if not pid:
                missing.append(name)
                continue
            ids.append(pid)
            if in_bench:
                bench.append(pid)
            if mark == 'C':
                cap = pid
            elif mark == 'V':
                vice = pid
        if missing:
            print(f'  could not match: {", ".join(missing)}')
        lineup = None
        if len(ids) == 15 and (bench or cap):
            xi = [i for i in ids if i not in bench]
            if len(bench) == 4 and len(xi) == 11:
                lineup = dict(xi=xi, bench=bench, captain=cap, vice=vice)
            elif cap:
                lineup = dict(xi=None, bench=None, captain=cap, vice=vice)
        print(f'  loaded {len(ids)} players from {SQUAD_FILE.name}'
              + (' with your lineup' if lineup else ''))
        return dict(ids=ids, bank=0.0, ft=15 if gw <= 1 else 1, lineup=lineup,
                    history={}, source=source, confirmed_at=confirmed_at,
                    entry_id=(int(entry_match.group(1)) if entry_match else None),
                    changes=change_matches)
    return dict(ids=[], bank=0.0, ft=1, lineup=None, history={}, source='none')


# ------------------------------------------------------------ analysis
def gw_pts(p, gw):
    v = p['proj_by_gw']
    return v[gw - 1] if 0 <= gw - 1 < len(v) else 0.0


def remaining(p, gw, horizon):
    """Projected points from `gw` to the end of the modelled window."""
    v = p['proj_by_gw']
    return sum(v[gw - 1:horizon]) if gw - 1 < len(v) else 0.0


def pick_xi(squad, gw):
    """Best legal XI for one gameweek. Returns (xi, bench-in-order, key)."""
    key = lambda p: gw_pts(p, gw)
    lineup = shared_pick_lineup(squad, gw)
    return lineup.xi, lineup.bench, key


def squad_score(squad, gw, horizon):
    """Expected XI, captain fallback and risk-sensitive autosub value."""
    return evaluate_squad(squad, gw, horizon).total


def legal(squad, budget):
    clubs = {}
    for p in squad:
        clubs[p['team']] = clubs.get(p['team'], 0) + 1
    if clubs and max(clubs.values()) > 3:
        return False
    return sum(p['price'] for p in squad) <= budget + 1e-9


def transfer_engine(squad, players, bank, ft, gw, horizon, pool_size=60):
    """XI-aware single moves and the best two-move combinations, net of hits.

    A move's gain is squad_score(after) - squad_score(before): it counts only
    if the newcomer actually starts, and it credits a new captain. Hits are
    charged at 4 for every transfer beyond the free ones available.
    """
    budget = sum(p['price'] for p in squad) + bank
    cache = {}

    def value(candidate):
        key = tuple(sorted(p['id'] for p in candidate))
        if key not in cache:
            cache[key] = evaluate_squad(candidate, gw, horizon)
        return cache[key]

    base_eval = value(squad)
    base = base_eval.total
    owned = {p['id'] for p in squad}
    pool = {}
    for pos in POS_ORDER:
        cand = [p for p in players.values() if p['pos'] == pos and p['id'] not in owned
                and p['status'] != 'u' and remaining(p, gw, horizon) > 0]
        cand.sort(key=lambda p: -remaining(p, gw, horizon))
        pool[pos] = cand[:pool_size]

    def net(gain, moves):
        return gain - HIT * max(0, moves - ft)

    singles = []
    for o in squad:
        for n in pool[o['pos']]:
            new = [n if p is o else p for p in squad]
            if not legal(new, budget):
                continue
            after = value(new)
            g = after.total - base
            if g > 0.05:
                singles.append(dict(gain=round(g, 1), net=round(net(g, 1), 1),
                                    xi_gain=round(after.xi_captain_points
                                                  - base_eval.xi_captain_points, 1),
                                    autosub_gain=round(after.autosub_points
                                                       - base_eval.autosub_points, 1),
                                    out=o, in_=n, moves=1))
    singles.sort(key=lambda t: -t['gain'])
    best_per_out, seen = [], set()
    for s in singles:
        if s['out']['id'] in seen:
            continue
        seen.add(s['out']['id']); best_per_out.append(s)

    pairs = []
    # only pair moves that are individually within sight of worthwhile, or
    # cheap downgrades that free money — that keeps this to a few seconds
    top_in = {pos: pool[pos][:25] for pos in POS_ORDER}
    for o1, o2 in combinations(squad, 2):
        for n1 in top_in[o1['pos']]:
            for n2 in top_in[o2['pos']]:
                if n1 is n2:
                    continue
                new = [n1 if p is o1 else n2 if p is o2 else p for p in squad]
                if not legal(new, budget):
                    continue
                after = value(new)
                g = after.total - base
                if g > 0.5:
                    pairs.append(dict(gain=round(g, 1), net=round(net(g, 2), 1),
                                      xi_gain=round(after.xi_captain_points
                                                    - base_eval.xi_captain_points, 1),
                                      autosub_gain=round(after.autosub_points
                                                         - base_eval.autosub_points, 1),
                                      out=(o1, o2), in_=(n1, n2), moves=2))
    pairs.sort(key=lambda t: -t['net'])
    unavailable = deadline_unavailable(squad, gw)
    unavailable_moves = []
    for player in unavailable:
        move = next((s for s in singles if s['out']['id'] == player['id']), None)
        unavailable_moves.append(dict(player=player, replacement=move))
    return dict(base=base, base_eval=base_eval, singles=best_per_out[:8],
                all_singles=singles, pairs=pairs[:5],
                unavailable=unavailable_moves)


def price_watch(boot, players, owned_ids):
    """Who is about to rise or fall, from this gameweek's transfer flow.

    Pressure is net transfers this gameweek as a share of current owners —
    the shape of FPL's (unpublished) rule. It is UNCALIBRATED until a few
    weeks of --price-log have been collected; treat the ordering as the
    signal and the percentages as rough."""
    total = boot.get('total_players') or 1
    rows = []
    for e in boot['elements']:
        p = players.get(e['id'])
        if not p:
            continue
        net = e['transfers_in_event'] - e['transfers_out_event']
        owners = max(1.0, float(e['selected_by_percent']) / 100 * total)
        rows.append(dict(net=net, p=p, pressure=net / owners,
                         tin=e['transfers_in_event'], tout=e['transfers_out_event'],
                         changed=e.get('cost_change_event', 0)))
    rises = sorted(rows, key=lambda r: -r['pressure'])[:6]
    falls = sorted(rows, key=lambda r: r['pressure'])[:6]
    return rises, falls


def log_prices(boot):
    """One CSV per day of every player's price and transfer flow — the raw
    material for calibrating the price-change model once the season is
    underway. Re-running on the same day overwrites, so several refreshes a
    day cost nothing."""
    PRICE_LOG.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    path = PRICE_LOG / f'{day}.csv'
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'price', 'sel_pct', 'tin', 'tout', 'dcost_event', 'status'])
        for e in boot['elements']:
            w.writerow([e['id'], e['now_cost'] / 10, e['selected_by_percent'],
                        e['transfers_in_event'], e['transfers_out_event'],
                        e.get('cost_change_event', 0), e['status']])
    return path


def snapshot(gw, deadline, players, squad, model, yours):
    """Archive what the model believed before this gameweek's deadline, so
    scorecard.py can grade it afterwards. Overwritten on every refresh; the
    last one before the deadline is the one that counts."""
    HISTORY.mkdir(parents=True, exist_ok=True)
    view = json.loads(VIEW.read_text())['view'] if VIEW.exists() else {}
    team_cs = {}
    for t, byweek in view.items():
        fx = byweek.get(str(gw))
        if fx:
            team_cs[t] = [dict(opp=f['opp'], home=f['home'], cs=round(f['cs'], 4),
                               xg=round(f['xg'], 3), xgc=round(f['xgc'], 3)) for f in fx]
    rows = []
    for p in players.values():
        rows.append(dict(id=p['id'], name=p['name'], team=p['team'], pos=p['pos'],
                         price=p['price'], sel_pct=p['sel_pct'],
                         proj=round(gw_pts(p, gw), 3),
                         start_rate=p['start_rate'], status=p['status']))
    out = dict(gw=gw, deadline=deadline,
               generated=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
               players=rows, team_cs=team_cs,
               squad=[p['id'] for p in squad] if squad else [],
               model=model or {}, yours=yours or {})
    path = HISTORY / f'gw{gw}.json'
    path.write_text(json.dumps(out, separators=(',', ':')))
    return path


# ------------------------------------------------------------- digest
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--team', type=int, help='your FPL entry id')
    ap.add_argument('--full', action='store_true',
                    help='also refresh 4-season player histories (slow, weekly)')
    ap.add_argument('--no-refresh', action='store_true')
    ap.add_argument('--plan', action='store_true',
                    help='solve the multi-week transfer path (planner.py)')
    ap.add_argument('--chips', action='store_true',
                    help='value every chip in every week you could still play it')
    ap.add_argument('--snapshot', action='store_true',
                    help='archive this gameweek\'s projections for the scorecard')
    ap.add_argument('--price-log', action='store_true',
                    help='log today\'s prices and transfer flow')
    ap.add_argument('--push-file', action='store_true',
                    help='write a phone-sized summary to v2/push.txt')
    ap.add_argument('--json', action='store_true',
                    help='write the digest as data for the app to data/weekly.json')
    ap.add_argument('--ft', type=int, help='override free transfers available')
    ap.add_argument('--bank', type=float, help='override money in the bank (£m)')
    args = ap.parse_args()

    if not args.no_refresh:
        refresh(full=args.full)

    try:
        boot = api('bootstrap-static/')
    except Exception:
        cached_boot = HERE / 'cache' / 'bootstrap.json'
        if not args.no_refresh or not cached_boot.exists():
            raise
        boot = json.loads(cached_boot.read_text())
        print(f'  FPL API unavailable; using cached bootstrap from {cached_boot.name}')
    elem = {e['id']: e for e in boot['elements']}
    gw, deadline = next_gw(boot['events'])
    players, horizon, start_gw = load_projections()
    if gw > horizon:
        print(f'  projections end at GW{horizon}; rerun without --no-refresh')
        gw = min(gw, horizon)

    dl = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
    left = dl - datetime.now(timezone.utc)
    days, hrs = max(left.days, 0), (left.seconds // 3600) if left.days >= 0 else 0

    L, P = [], []          # digest lines, push lines
    J = dict(gw=gw, deadline=deadline, horizon=horizon,
             generated=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    L.append(f'# FPL weekly — Gameweek {gw}')
    L.append('')
    L.append(f'Deadline **{dl:%a %d %b, %H:%M UTC}** — {days}d {hrs}h away.')
    L.append(f'Projections cover GW{gw}–{horizon}.')
    L.append('')
    P.append(f'GW{gw} · deadline {dl:%a %H:%M} UTC ({days}d {hrs}h)')

    st = load_squad(args.team, players, gw)
    if args.ft is not None:
        st['ft'] = args.ft
    if args.bank is not None:
        st['bank'] = args.bank
    ids, bank, ft, lineup = st['ids'], st['bank'], st['ft'], st['lineup']
    J['squad'] = dict(ids=list(ids), bank=bank, ft=ft, source=st['source'],
                      lineup={k: v for k, v in (lineup or {}).items() if v is not None})
    if st.get('confirmed_at'):
        J['squad']['confirmed_at'] = st['confirmed_at']
    if st.get('entry_id'):
        J['squad']['entry_id'] = st['entry_id']
    if st.get('changes'):
        J['squad']['changes'] = st['changes']
    squad = [players[i] for i in ids if i in players]
    model_out, yours_out = {}, {}

    if len(squad) != 15:
        L.append('## No squad loaded')
        L.append('')
        L.append(f'Pass `--team <your entry id>`, or list your 15 players in '
                 f'`{SQUAD_FILE.name}`. Meanwhile, here are the best available '
                 f'players over GW{gw}–{horizon}:')
        L.append('')
        for pos in POS_ORDER:
            best = sorted((p for p in players.values() if p['pos'] == pos),
                          key=lambda p: -remaining(p, gw, horizon))[:6]
            L.append(f'**{pos}** — ' + ', '.join(
                f"{p['name']} £{p['price']} ({remaining(p, gw, horizon):.1f})"
                for p in best))
        L.append('')
        P.append('No squad loaded — set your team id in the app.')
    else:
        xi, bench, key = pick_xi(squad, gw)
        ranked = sorted(xi, key=key, reverse=True)
        cap, vice = ranked[0], ranked[1]
        model_out = dict(xi=[p['id'] for p in xi], bench=[p['id'] for p in bench],
                         captain=cap['id'], vice=vice['id'])
        J['model'] = dict(model_out, captain_pts=round(key(cap), 2), vice_pts=round(key(vice), 2),
                          ranked=[dict(id=p['id'], pts=round(key(p), 2)) for p in ranked[:4]],
                          gw_pts={p['id']: round(key(p), 2) for p in squad},
                          remaining={p['id']: round(remaining(p, gw, horizon), 2) for p in squad})

        # ---- captain
        L.append(f'## Captain: **{cap["name"]}** ({cap["team"]})')
        L.append('')
        L.append(f'Projected {key(cap):.1f} this week, doubled to {key(cap)*2:.1f}. '
                 f'Vice: {vice["name"]} ({key(vice):.1f}).')
        L.append('')
        L.append('| rank | player | club | this GW | start % |')
        L.append('|---|---|---|---|---|')
        for i, p in enumerate(ranked[:4], 1):
            L.append(f'| {i} | {p["name"]} | {p["team"]} | {key(p):.1f} | '
                     f'{p["start_rate"]*100:.0f} |')
        L.append('')
        P.append(f'C {cap["name"]} ({key(cap):.1f}) · V {vice["name"]}')

        # ---- XI
        L.append('## Starting XI')
        L.append('')
        L.append(f'| pos | player | club | this GW | GW{gw}–{horizon} |')
        L.append('|---|---|---|---|---|')
        for p in sorted(xi, key=lambda p: (POS_ORDER.index(p['pos']), -key(p))):
            mark = ' (C)' if p is cap else (' (V)' if p is vice else '')
            L.append(f'| {p["pos"]} | {p["name"]}{mark} | {p["team"]} | '
                     f'{key(p):.1f} | {remaining(p, gw, horizon):.1f} |')
        L.append('')
        L.append('**Bench order:** ' + ', '.join(
            f'{p["name"]} ({key(p):.1f})' for p in bench))
        L.append('')
        shape = {pos: sum(1 for p in xi if p['pos'] == pos) for pos in POS_ORDER}
        P.append(f'XI {shape["DEF"]}-{shape["MID"]}-{shape["FWD"]}: '
                 + ', '.join(p['name'] for p in sorted(
                     xi, key=lambda p: (POS_ORDER.index(p['pos']), -key(p)))))
        P.append('Bench: ' + ' → '.join(p['name'] for p in bench))

        # ---- your lineup vs the model
        if lineup:
            yours_out = {k: v for k, v in lineup.items() if v is not None}
            issues = []
            ycap = players.get(lineup.get('captain'))
            yvice = players.get(lineup.get('vice'))
            if ycap and ycap is not cap:
                d = (key(cap) - key(ycap)) * 2
                issues.append(f'**Captain:** you have {ycap["name"]} ({key(ycap):.1f}); '
                              f'the model prefers {cap["name"]} ({key(cap):.1f}) — '
                              f'{d:+.1f} expected once doubled.')
            if yvice:
                if yvice['pos'] == 'GKP':
                    issues.append(f'**Vice on a goalkeeper** ({yvice["name"]}): if the '
                                  f'captain misses, the armband doubles your keeper. '
                                  f'Move it to {vice["name"] if vice is not ycap else cap["name"]}.')
                elif yvice not in ranked[:3]:
                    issues.append(f'**Vice:** {yvice["name"]} ({key(yvice):.1f}) is not one '
                                  f'of your top three; the model would use '
                                  f'{vice["name"] if vice is not ycap else cap["name"]}.')
            if lineup.get('xi'):
                yxi = [players[i] for i in lineup['xi'] if i in players]
                ybench = [players[i] for i in lineup['bench'] if i in players]
                yset = {p['id'] for p in yxi}
                mset = {p['id'] for p in xi}
                for p in xi:
                    if p['id'] not in yset:
                        swap = [q for q in yxi if q['id'] not in mset and q['pos'] == p['pos']]
                        alt = swap[0] if swap else next((q for q in yxi if q['id'] not in mset), None)
                        gap = key(p) - (key(alt) if alt else 0)
                        issues.append(f'**Bench → start:** {p["name"]} ({key(p):.1f}) is on '
                                      f'your bench; the model starts him'
                                      + (f' over {alt["name"]} ({key(alt):.1f}), {gap:+.1f}.'
                                         if alt else '.'))
                yfirst = next((q for q in ybench if q['pos'] != 'GKP'), None)
                mfirst = next((q for q in bench if q['pos'] != 'GKP'), None)
                if yfirst and mfirst and yfirst is not mfirst and mfirst['id'] not in yset:
                    issues.append(f'**Bench order:** your first sub is {yfirst["name"]} '
                                  f'({key(yfirst):.1f}); {mfirst["name"]} ({key(mfirst):.1f}) '
                                  f'is the better first man off.')
                yshape = {pos: sum(1 for p in yxi if p['pos'] == pos) for pos in POS_ORDER}
                if yshape != shape:
                    issues.append(f'Formation: you {yshape["DEF"]}-{yshape["MID"]}-'
                                  f'{yshape["FWD"]}, model {shape["DEF"]}-{shape["MID"]}-'
                                  f'{shape["FWD"]}.')
            J['lineup_issues'] = list(issues)
            L.append(f'## Your lineup vs the model  ({st["source"]})')
            L.append('')
            if issues:
                for s in issues:
                    L.append(f'- {s}')
                P.append(f'⚠ lineup: {len(issues)} thing{"s" if len(issues) != 1 else ""} to fix')
            else:
                L.append('Your captain, vice, XI and bench order all match the model. ✓')
                P.append('Lineup matches the model ✓')
            L.append('')

        # ---- check before the deadline (minutes)
        checks = []
        for p in sorted(squad, key=lambda p: -key(p)):
            e = elem.get(p['id'], {})
            chance = e.get('chance_of_playing_next_round')
            news = (e.get('news') or '').strip()
            flags = []
            if p['status'] != 'a':
                flags.append(f'status {p["status"]}')
            if chance is not None and chance < 100:
                flags.append(f'{chance}% chance')
            if news:
                added = (e.get('news_added') or '')[:10]
                flags.append(f'"{news}"' + (f' ({added})' if added else ''))
            av = (p.get('availability_by_gw') or [])
            av = av[gw - 1] if gw - 1 < len(av) else None
            if av and av.get('confidence') not in (None, 'model'):
                flags.append(
                    f'{p["start_rate"]*100:.0f}% deadline start estimate '
                    f'({av["confidence"]} confidence)'
                    + (f': {av["note"]}' if av.get('note') else '')
                )
            if p in xi and p['start_rate'] < 0.8 and not flags:
                flags.append(f'starts only {p["start_rate"]*100:.0f}% of the time')
            if (p.get('joined') or '') >= '2026-05-01' and p in xi:
                flags.append('new signing — role still settling')
            if flags:
                checks.append((p, flags))
        J['checks'] = [dict(id=p['id'], xi=p in xi, flags=fl) for p, fl in checks]
        L.append('## Check before the deadline')
        L.append('')
        if checks:
            for p, flags in checks:
                where = 'XI' if p in xi else 'bench'
                L.append(f'- **{p["name"]}** ({p["team"]}, {where}) — ' + '; '.join(flags))
            if any(p in xi and any('chance' in f or 'status' in f for f in fl)
                   for p, fl in checks):
                P.append('⚠ ' + ', '.join(p['name'] for p, fl in checks
                                          if p in xi and any('chance' in f or 'status' in f
                                                             for f in fl))
                         + ' flagged — check the pressers')
        else:
            L.append('Nobody in the squad is flagged and every starter is a regular. '
                     'Still glance at Friday\'s press conferences.')
        L.append('')

        # ---- transfers
        L.append(f'## Transfers  (£{bank:.1f}m in the bank, '
                 f'{"unlimited" if ft >= 15 else ft} free)')
        L.append('')
        eng = transfer_engine(squad, players, bank, ft, gw, horizon)
        J['transfers'] = dict(
            base=round(eng['base'], 1),
            base_xi=round(eng['base_eval'].xi_captain_points, 1),
            base_autosub=round(eng['base_eval'].autosub_points, 1),
            unavailable=[u['player']['id'] for u in eng['unavailable']],
            singles=[dict(out=s_['out']['id'], in_=s_['in_']['id'], gain=s_['gain'],
                          xi_gain=s_['xi_gain'], autosub_gain=s_['autosub_gain'], net=s_['net'])
                     for s_ in eng['singles']],
            pairs=[dict(out=[o['id'] for o in pr['out']], in_=[n['id'] for n in pr['in_']],
                        gain=pr['gain'], xi_gain=pr['xi_gain'],
                        autosub_gain=pr['autosub_gain'], net=pr['net'])
                   for pr in eng['pairs']])
        L.append(f'Gain is the lift to your expected starting XI and captain plus '
                 f'modelled auto-sub cover over GW{gw}–{horizon}. The auto-sub term '
                 f'uses each starter\'s non-appearance risk, not a flat bench weight. '
                 f'A hit costs 4.')
        L.append('')
        for unavailable in eng['unavailable']:
            dead = unavailable['player']
            move = unavailable['replacement']
            if move:
                no_route = ('no route to points' if dead.get('status') == 'u'
                            else f'no credible route to minutes in GW{gw}')
                L.append(f'**Availability warning:** {dead["name"]} has effectively '
                         f'{no_route}. {move["in_"]["name"]} is the best legal '
                         f'same-position replacement ({move["gain"]:+.1f}: '
                         f'{move["xi_gain"]:+.1f} XI/captain, '
                         f'{move["autosub_gain"]:+.1f} auto-sub cover).')
            else:
                L.append(f'**Availability warning:** {dead["name"]} has effectively '
                         f'no route to points; replace him when a legal move is available.')
            L.append('')
        if not eng['singles']:
            L.append('**No single transfer improves the squad.**'
                     + (' Nothing to change.' if ft >= 15 else ' Bank the free transfer.'))
            P.append('Transfers: none worth making'
                     + ('' if ft >= 15 else ' — bank it'))
        else:
            L.append('| out | in | £ | XI + captain | auto-sub | total | net |')
            L.append('|---|---|---|---|---|---|---|')
            for s in eng['singles']:
                delta = s['in_']['price'] - s['out']['price']
                L.append(f'| {s["out"]["name"]} ({s["out"]["team"]}) | '
                         f'{s["in_"]["name"]} ({s["in_"]["team"]}) | {delta:+.1f} | '
                         f'{s["xi_gain"]:+.1f} | {s["autosub_gain"]:+.1f} | '
                         f'**{s["gain"]:+.1f}** | {s["net"]:+.1f} |')
            best = eng['singles'][0]
            forced = next((u['replacement'] for u in eng['unavailable']
                           if u['replacement']), None)
            L.append('')
            if ft >= 15 and forced:
                L.append(f'**Recommended:** {forced["out"]["name"]} → '
                         f'{forced["in_"]["name"]} ({forced["gain"]:+.1f}). '
                         f'The outgoing player is unavailable and transfers are free '
                         f'before Gameweek 1, so even a small resilience gain should not '
                         f'be left unused.')
                P.append(f'Fix unavailable: {forced["out"]["name"]}→'
                         f'{forced["in_"]["name"]} {forced["gain"]:+.1f}')
            elif ft >= 15:
                L.append(f'**Recommended:** {best["out"]["name"]} → {best["in_"]["name"]} '
                         f'({best["gain"]:+.1f}); everything is free before Gameweek 1.'
                         if best['gain'] >= HOLD_THRESHOLD else
                         '**Recommended:** nothing compelling — the squad is already '
                         'close enough to the model\'s optimum.')
                P.append(f'Best swap: {best["out"]["name"]}→{best["in_"]["name"]} '
                         f'{best["gain"]:+.1f}' if best['gain'] >= HOLD_THRESHOLD else
                         'Transfers: squad already optimal')
            elif ft >= MAX_FT:
                L.append(f'**Recommended:** you have {MAX_FT} free transfers and cannot '
                         f'bank more — use one. {best["out"]["name"]} → '
                         f'{best["in_"]["name"]} ({best["gain"]:+.1f}).')
                P.append(f'Use a FT (you have {ft}): {best["out"]["name"]}→'
                         f'{best["in_"]["name"]} {best["gain"]:+.1f}')
            elif best['gain'] < HOLD_THRESHOLD:
                L.append(f'**Recommended: hold.** The best free move is only '
                         f'{best["gain"]:+.1f} over the window; a banked transfer is '
                         f'worth more next week when you know more (you would have '
                         f'{min(MAX_FT, ft + 1)}).')
                P.append(f'Transfers: HOLD (best is only {best["gain"]:+.1f})')
            else:
                L.append(f'**Recommended:** {best["out"]["name"]} → {best["in_"]["name"]}, '
                         f'{best["gain"]:+.1f} over the window with a free transfer.')
                P.append(f'Transfer: {best["out"]["name"]}→{best["in_"]["name"]} '
                         f'{best["gain"]:+.1f}')
        if eng['pairs']:
            L.append('')
            L.append('**Best two-move combinations** (net of any hit):')
            L.append('')
            L.append('| out | in | £ | XI + captain | auto-sub | total | net |')
            L.append('|---|---|---|---|---|---|---|')
            for pr in eng['pairs']:
                o1, o2 = pr['out']; n1, n2 = pr['in_']
                delta = n1['price'] + n2['price'] - o1['price'] - o2['price']
                L.append(f'| {o1["name"]} + {o2["name"]} | {n1["name"]} + {n2["name"]} | '
                         f'{delta:+.1f} | {pr["xi_gain"]:+.1f} | '
                         f'{pr["autosub_gain"]:+.1f} | {pr["gain"]:+.1f} | '
                         f'**{pr["net"]:+.1f}** |')
            top = eng['pairs'][0]
            if top['net'] > (eng['singles'][0]['net'] if eng['singles'] else 0) + 1.0 \
                    and top['net'] > 2.0:
                o1, o2 = top['out']; n1, n2 = top['in_']
                L.append('')
                if ft >= 15:
                    L.append(f'The best two-player diagnostic is {o1["name"]}+{o2["name"]} '
                             f'→ {n1["name"]}+{n2["name"]} ({top["net"]:+.1f}). '
                             'The exact full-squad comparison below remains the '
                             'authoritative pre-season decision.')
                else:
                    L.append(f'The pair {o1["name"]}+{o2["name"]} → '
                             f'{n1["name"]}+{n2["name"]} '
                             + (f'beats any single move even after the hit '
                                if ft < 2 else 'is the best two-move package ')
                             + f'({top["net"]:+.1f}).')
                    P.append(f'Two-mover worth it: {o1["name"]}+{o2["name"]}→'
                             f'{n1["name"]}+{n2["name"]} net {top["net"]:+.1f}')
        L.append('')

        J['transfers']['advice'] = next((l for l in reversed(L) if l.startswith('**Recommended')
                                         or l.startswith('**No single')), '').replace('**', '')

        # ---- multi-week path
        wc_now = None
        if args.plan:
            try:
                from planner import plan, describe
                L.append('## The next six weeks, planned')
                L.append('')
                free = plan(players, ids, bank, ft, gw, horizon)
                hold = plan(players, ids, bank, ft, gw, horizon, freeze_this_week=True)
                free_source = 'planner'
                # Before GW1, compare the approximate transfer path with the
                # exact-scored best static build produced by optimise.py. The
                # planner's linear bench proxy must not overrule a better full
                # evaluator score when every initial change is free.
                if ft >= 15 and gw == 1:
                    presets_path = ROOT / 'data' / 'squads.json'
                    if presets_path.exists():
                        presets = json.loads(presets_path.read_text())
                        if presets:
                            best_ids = [int(p['id']) for p in presets[0]['squad']]
                            best_squad = [players[i] for i in best_ids if i in players]
                            if len(best_squad) == 15:
                                exact = evaluate_squad(best_squad, gw, horizon)
                                exact_weeks = []
                                for offset, week in enumerate(exact.weeks):
                                    lineup = week.lineup
                                    exact_weeks.append({
                                        'gw': week.gw, 'pts': round(week.total, 1),
                                        'hits': 0, 'squad': best_ids,
                                        'xi': [p['id'] for p in lineup.xi],
                                        'captain': (lineup.captain['id']
                                                    if lineup.captain else None),
                                        'autosub': round(week.autosub_points, 1),
                                        'in': ([i for i in best_ids if i not in ids]
                                               if offset == 0 else []),
                                        'out': ([i for i in ids if i not in best_ids]
                                                if offset == 0 else []),
                                        'ft': 15 if offset == 0 else min(MAX_FT, offset),
                                        'cost': sum(players[i]['price'] for i in best_ids),
                                    })
                                exact_plan = {
                                    'total': round(exact.total, 1), 'hits': 0,
                                    'weeks': exact_weeks,
                                }
                                if free is None or exact_plan['total'] > free['total']:
                                    free = exact_plan
                                    free_source = 'exact static build'
                if args.chips and free and ft < 15 and gw >= 2:
                    # what a wildcard would add: unlimited moves this week
                    wc = plan(players, ids, bank, 15, gw, horizon)
                    if wc:
                        wc_now = round(wc['total'] - free['total'], 1)
                if free and hold:
                    diff = free['total'] - hold['total']

                    n_now = len(free['weeks'][0]['in'])
                    # Judge the value of acting now PER MOVE: four changes for
                    # +3 is churn, one change for +3 is a transfer. Unlimited
                    # transfers remove the points cost, not the uncertainty of
                    # overturning a settled and manually confirmed squad.
                    unlimited = ft >= 15
                    worth_it = worth_rebuilding(diff, n_now, unlimited)
                    J['plan'] = dict(total=free['total'], hold_total=hold['total'],
                                     diff=round(diff, 1), hits=free['hits'],
                                     n_now=n_now, worth_it=worth_it,
                                     weeks=[dict(gw=w['gw'], pts=w['pts'], hits=w['hits'],
                                                 captain=w['captain'], ft=w['ft'],
                                                 in_=w['in'], out=w['out']) for w in free['weeks']])
                    if worth_it and unlimited and free_source == 'exact static build':
                        recommendation = (
                            '**Recommended:** use the free pre-GW1 rebuild shown in '
                            f'the plan below ({diff:+.1f} versus holding/re-planning). '
                            'The one- and two-move tables are diagnostics, not the '
                            'final action.'
                        )
                        _supersede_transfer_recommendation(
                            L, P, J['transfers'], recommendation,
                            f'Use the free pre-GW1 rebuild ({diff:+.1f})',
                        )
                    lead = ('Best exact-scored pre-season build'
                            if free_source == 'exact static build' else 'Best path from here')
                    L.append(f'{lead}: **{free["total"]:.1f}** pts '
                             f'({free["hits"]} hit{"s" if free["hits"] != 1 else ""}). '
                             f'Making no move this week and re-planning: '
                             f'{hold["total"]:.1f}. Acting now is worth **{diff:+.1f}**'
                             + (f' across {n_now} moves' if n_now > 1 else '')
                             + (' — use the free pre-GW1 rebuild.'
                                if worth_it and unlimited else
                                '.' if worth_it else ' — not enough; hold.'))
                    L.append('')
                    L.extend(describe(free, players))
                    L.append('')
                    L.append('_The multiweek planner uses a linear bench proxy and small '
                             'fixture swings can cause churn; treat future moves as '
                             'directional rather than scripted._')
                    if worth_it:
                        w = free['weeks'][0]
                        paired = []
                        for pos in POS_ORDER:
                            incoming = [i for i in w['in'] if players[i]['pos'] == pos]
                            outgoing = [o for o in w['out'] if players[o]['pos'] == pos]
                            paired.extend(zip(outgoing, incoming))
                        P.append('Plan this week: ' + ', '.join(
                            f"{players[o]['name']}→{players[i]['name']}"
                            for o, i in paired))
                else:
                    L.append('Planner timed out; single-move advice above stands.')
                L.append('')
            except Exception as ex:      # the planner is optional; never sink the digest
                L.append(f'_Planner unavailable: {ex}_')
                L.append('')

        # ---- chips
        if args.chips:
            try:
                import chips as CH
                season, _, last_gw = CH.load_season()
                res = CH.evaluate(season, ids, bank, gw, last_gw, CH.chip_windows(),
                                  CH.used_chips(st.get('history')), wc_now=wc_now)
                J['chips'] = res
                L.append('## Chips')
                L.append('')
                L.extend(CH.digest_lines(res))
                L.append('')
                CH.OUT.parent.mkdir(parents=True, exist_ok=True)
                CH.OUT.write_text(json.dumps(res, indent=1))
                plays = [c['name'] for c in res['chips'].values() if c.get('play')]
                if plays:
                    P.append('🎯 Chip: play ' + ' / '.join(plays) + ' this week')
            except Exception as ex:      # optional; never sink the digest
                L.append(f'_Chips unavailable: {ex}_')
                L.append('')

    # ---- price watch
    rises, falls = price_watch(boot, players, set(ids))
    J['price'] = dict(
        locked=not rises or all(r['net'] == 0 for r in rises),
        rises=[dict(id=r['p']['id'], net=r['net'], pressure=round(r['pressure'], 4)) for r in rises],
        falls=[dict(id=r['p']['id'], net=r['net'], pressure=round(r['pressure'], 4)) for r in falls])
    L.append('## Price watch')
    L.append('')
    if not rises or all(r['net'] == 0 for r in rises):
        L.append('Prices are locked until the Gameweek 1 deadline, so there is '
                 'no transfer flow to read yet. This section becomes useful '
                 'once the season starts.')
    else:
        L.append('Net transfers this gameweek as a share of current owners. Top of '
                 'the left column rises soonest, top of the right falls. '
                 '(Uncalibrated until a few weeks of price logs exist.)')
        L.append('')
        L.append('| rising | pressure | net | falling | pressure | net |')
        L.append('|---|---|---|---|---|---|')
        for r, f in zip(rises, falls):
            own_r = ' ⭑' if r['p']['id'] in ids else ''
            own_f = ' ⭑' if f['p']['id'] in ids else ''
            L.append(f'| {r["p"]["name"]} £{r["p"]["price"]}{own_r} | '
                     f'{r["pressure"]*100:+.1f}% | {r["net"]:+,} | '
                     f'{f["p"]["name"]} £{f["p"]["price"]}{own_f} | '
                     f'{f["pressure"]*100:+.1f}% | {f["net"]:+,} |')
        L.append('')
        L.append('⭑ = in your squad.')
        mine_f = [f for f in falls if f['p']['id'] in ids and f['pressure'] < -0.05]
        if mine_f:
            P.append('Price: ' + ', '.join(f['p']['name'] for f in mine_f)
                     + ' under selling pressure')
    L.append('')
    L.append('---')
    L.append('')
    L.append(f'_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Projections '
             f'are estimates: hold-out rank correlation is about 0.46, so treat the '
             f'ordering as a strong hint and the point totals as rough._')

    text = '\n'.join(L)
    DIGEST.write_text(text)
    print()
    print(text)
    print(f'\n(also written to {DIGEST})')

    if args.json:
        J['digest_md'] = text
        WEEKLY_JSON.parent.mkdir(parents=True, exist_ok=True)
        WEEKLY_JSON.write_text(json.dumps(J, separators=(',', ':')))
        print(f'(digest data written to {WEEKLY_JSON})')
    if args.push_file:
        P.append(f'Full digest: {APP_URL}')
        PUSH.write_text('\n'.join(P) + '\n')
        print(f'(push summary written to {PUSH}, {len(PUSH.read_text())} chars)')
    if args.snapshot:
        path = snapshot(gw, deadline, players, squad, model_out, yours_out)
        print(f'(projections for GW{gw} archived to {path})')
    if args.price_log:
        path = log_prices(boot)
        print(f'(prices logged to {path})')


if __name__ == '__main__':
    main()
