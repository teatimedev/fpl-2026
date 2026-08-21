"""
Chips: what each one is worth in every week you could still play it.

FPL 2026/27 gives two of every chip — one usable in the first half of the
season (GW1/2–19), one in the second (GW20–38); the exact windows are read from
the API's `chips` list, not hard-coded. Bench Boost and Triple Captain can be
played from GW1, Wildcard and Free Hit from GW2.

The season-long projection (`projections_season.json`, same model, minutes held
constant) is what makes timing possible: it says which week your bench is worth
most, which week one player is worth most, and how far your squad sits behind
the best squad money can buy in each week. Doubles and blanks, once the fixture
list has them, flow through automatically (a double is two fixtures summed).

For each chip: its value in every eligible week, the best week, this week, and
a plain recommendation. The thresholds are heuristics and are labelled as such;
the numbers are the model's.

  Bench Boost   points from your four bench players (they all score)
  Triple Captain  your best player's points (the extra 1x on top of the double)
  Free Hit      the best possible XI+captain that week minus yours — big in a
                blank week or an injury crisis, when many of yours don't play
  Wildcard      the planner's gain from unlimited transfers now versus what
                your free transfers can do anyway, plus the trend of the gap
                between your squad and the best squad (a rising gap is decay)

    python v2/chips.py                # from v2/my_squad.txt or --team
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from squad_evaluator import pick_lineup as shared_pick_lineup  # noqa: E402

SEASON = HERE / 'projections_season.json'
BOOT = HERE / 'cache' / 'bootstrap.json'
FIXTURES = HERE / 'cache' / 'fixtures.json'
OUT = ROOT / 'data' / 'chips.json'

SQUAD_SHAPE = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
POS_ORDER = ('GKP', 'DEF', 'MID', 'FWD')
NAMES = {'bboost': 'Bench Boost', '3xc': 'Triple Captain', 'freehit': 'Free Hit',
         'wildcard': 'Wildcard'}

# --- heuristics (labelled as such in the output) -----------------------------
BB_PLAY_MIN = 12.0        # bench worth at least this many points this week
TC_PLAY_MIN = 8.0         # the extra 1x worth at least this many points
FH_PLAY_MIN = 12.0        # your XI at least this far behind the best possible
WC_PLAY_MIN = 20.0        # unlimited transfers worth at least this over the window
NEAR_BEST = 0.9           # "this week is as good as it gets" tolerance


def load_season():
    d = json.loads(SEASON.read_text())
    return {p['id']: p for p in d['players']}, d['start_gw'], d['last_gw']


def chip_windows(boot=None):
    """{'bboost': [(1,19),(20,38)], ...} from the API's chips list."""
    boot = boot or json.loads(BOOT.read_text())
    out = {}
    for c in boot.get('chips', []):
        out.setdefault(c['name'], []).append((c['start_event'], c['stop_event']))
    if not out:                                   # very old bootstrap: assume 25/26 rules
        out = {'wildcard': [(2, 19), (20, 38)], 'freehit': [(2, 19), (20, 38)],
               'bboost': [(1, 19), (20, 38)], '3xc': [(1, 19), (20, 38)]}
    return out


def doubles_and_blanks(boot=None, fixtures=None):
    """{gw: [teams with 2+ fixtures]}, {gw: [teams with none]}."""
    boot = boot or json.loads(BOOT.read_text())
    fixtures = fixtures or json.loads(FIXTURES.read_text())
    short = {t['id']: t['short_name'] for t in boot['teams']}
    per = {}
    for f in fixtures:
        if not f.get('event'):
            continue
        for side in ('team_h', 'team_a'):
            per.setdefault(f['event'], {}).setdefault(short[f[side]], 0)
            per[f['event']][short[f[side]]] += 1
    dgw = {g: sorted(t for t, n in d.items() if n > 1) for g, d in per.items()}
    bgw = {g: sorted(set(short.values()) - set(d)) for g, d in per.items()}
    return {g: v for g, v in dgw.items() if v}, {g: v for g, v in bgw.items() if v}


def gw_pts(p, g):
    v = p['by_gw']
    return v[g - 1] if 0 <= g - 1 < len(v) else 0.0


def pick_xi(squad, g):
    lineup = shared_pick_lineup(squad, g, points=gw_pts)
    return lineup.xi, lineup.bench, lambda p: gw_pts(p, g)


def your_week(squad, g):
    """(XI + captain points, bench points, captain) for one week."""
    xi, bench, key = pick_xi(squad, g)
    if not xi:
        return 0.0, 0.0, None
    cap = max(xi, key=key)
    return sum(key(p) for p in xi) + key(cap), sum(key(p) for p in bench), cap


def best_possible_week(players, g, budget, pool_n=45):
    """Best XI + captain any legal 15 could field in week g within `budget`.

    A one-week integer program: 15 players (2/5/5/3, max 3 a club, budget),
    11 start, one is captained. Only the XI and captain score, so the other
    four are whatever is cheapest — the solver works that out.
    """
    import pulp
    pool = []
    for pos in POS_ORDER:
        cand = [p for p in players.values() if p['pos'] == pos and p['status'] != 'u']
        cand.sort(key=lambda p: -gw_pts(p, g))
        top = cand[:pool_n]
        cheap = sorted(cand, key=lambda p: (p['price'], -gw_pts(p, g)))[:4]
        seen = set()
        for p in top + cheap:
            if p['id'] not in seen:
                seen.add(p['id']); pool.append(p)
    ids = [p['id'] for p in pool]
    P = {p['id']: p for p in pool}
    prob = pulp.LpProblem('fh', pulp.LpMaximize)
    x = {i: pulp.LpVariable(f'x{i}', cat='Binary') for i in ids}
    y = {i: pulp.LpVariable(f'y{i}', cat='Binary') for i in ids}
    c = {i: pulp.LpVariable(f'c{i}', cat='Binary') for i in ids}
    prob += pulp.lpSum((y[i] + c[i]) * gw_pts(P[i], g) for i in ids)
    prob += pulp.lpSum(x[i] for i in ids) == 15
    for pos, n in SQUAD_SHAPE.items():
        prob += pulp.lpSum(x[i] for i in ids if P[i]['pos'] == pos) == n
        k = pulp.lpSum(y[i] for i in ids if P[i]['pos'] == pos)
        prob += k >= XI_MIN[pos]
        prob += k <= XI_MAX[pos]
    prob += pulp.lpSum(x[i] * int(round(P[i]['price'] * 10)) for i in ids) <= int(round(budget * 10))
    for club in {p['team'] for p in pool}:
        prob += pulp.lpSum(x[i] for i in ids if P[i]['team'] == club) <= 3
    prob += pulp.lpSum(y[i] for i in ids) == 11
    prob += pulp.lpSum(c[i] for i in ids) == 1
    for i in ids:
        prob += y[i] <= x[i]
        prob += c[i] <= y[i]
    prob.solve(pulp.HiGHS(msg=False, timeLimit=20))
    val = pulp.value(prob.objective) or 0.0
    xi = [P[i] for i in ids if (y[i].value() or 0) > 0.5]
    cap = next((P[i] for i in ids if (c[i].value() or 0) > 0.5), None)
    return float(val), xi, cap


def used_chips(history):
    """{'bboost': [events...], ...} from entry history."""
    out = {}
    for c in (history or {}).get('chips', []):
        out.setdefault(c['name'], []).append(c['event'])
    return out


def evaluate(players, squad_ids, bank, gw, last_gw, windows, used, wc_now=None,
             fh_weeks=None):
    """The whole chip picture from gameweek `gw`. Returns a dict for the digest
    and for data/chips.json."""
    squad = [players[i] for i in squad_ids if i in players]
    budget = sum(p['price'] for p in squad) + bank
    dgw, bgw = doubles_and_blanks()

    def copies(name):
        """Unused copies of the chip, each as (lo, hi, [weeks >= gw]), in
        order. The first is the one to decide about now; later ones are
        information ("the second Bench Boost's best week is GW34")."""
        out = []
        for lo, hi in sorted(windows.get(name, [])):
            if any(lo <= e <= hi for e in used.get(name, [])):
                continue
            weeks = [g for g in range(max(lo, gw), min(hi, last_gw) + 1)]
            if weeks:
                out.append((lo, hi, weeks))
        return out

    def eligible(name):
        """Weeks of the copy to decide about now (the earliest unused one)."""
        c = copies(name)
        return c[0][2] if c else []

    def later_copies(name):
        return copies(name)[1:]

    out = {'gw': gw, 'dgw': {str(g): v for g, v in dgw.items() if g >= gw},
           'bgw': {str(g): v for g, v in bgw.items() if g >= gw},
           'heuristics': dict(bb_play_min=BB_PLAY_MIN, tc_play_min=TC_PLAY_MIN,
                              fh_play_min=FH_PLAY_MIN, wc_play_min=WC_PLAY_MIN),
           'chips': {}}

    # ---- bench boost
    weeks = eligible('bboost')
    if weeks:
        series = [(g, round(your_week(squad, g)[1], 1)) for g in weeks]
        best_g, best_v = max(series, key=lambda t: t[1])
        now = dict(series).get(gw)
        half_end = max(weeks)
        if now is not None and now >= BB_PLAY_MIN and now >= NEAR_BEST * best_v:
            advice = f'Play it this week: your bench projects {now:.1f}, as good as any week left ({best_v:.1f} in GW{best_g}).'
            play = True
        elif now is not None and gw == half_end:
            advice = f'Last chance this half — play it (bench {now:.1f}).'
            play = True
        else:
            advice = (f'Hold. Best-looking week for this one is GW{best_g} ({best_v:.1f} from the bench)'
                      + (f'; this week is {now:.1f}.' if now is not None else '.'))
            play = False
        later = []
        for lo, hi, wks in later_copies('bboost'):
            s2 = [(g, round(your_week(squad, g)[1], 1)) for g in wks]
            g2, v2 = max(s2, key=lambda t: t[1])
            later.append(dict(lo=lo, hi=hi, best_gw=g2, best=v2))
            advice += f' Second copy (GW{lo}–{hi}): best week GW{g2} ({v2:.1f}).'
        out['chips']['bboost'] = dict(name=NAMES['bboost'], weeks=series, best_gw=best_g,
                                      best=best_v, now=now, play=play, advice=advice,
                                      last_eligible=half_end, later=later)
    else:
        out['chips']['bboost'] = dict(name=NAMES['bboost'], weeks=[], advice='Both used.', play=False)

    # ---- triple captain
    weeks = eligible('3xc')
    if weeks:
        series = []
        for g in weeks:
            xi, _, key = pick_xi(squad, g)
            cap = max(xi, key=key) if xi else None
            series.append((g, round(key(cap), 1) if cap else 0.0, cap['name'] if cap else ''))
        best_g, best_v, best_n = max(series, key=lambda t: t[1])
        now_row = next((s for s in series if s[0] == gw), None)
        now, now_n = (now_row[1], now_row[2]) if now_row else (None, '')
        # what a wildcard/transfer could unlock: best single player in the game
        anyone = max(((g, round(gw_pts(p, g), 1), p['name']) for g in weeks
                      for p in players.values() if p['status'] != 'u'), key=lambda t: t[1])
        half_end = max(weeks)
        if now is not None and now >= TC_PLAY_MIN and now >= NEAR_BEST * best_v:
            advice = f'Play it on {now_n} this week ({now:.1f} extra expected) — no better week left in the half.'
            play = True
        elif now is not None and gw == half_end:
            advice = f'Last chance this half — {now_n} ({now:.1f}).'
            play = True
        else:
            advice = (f'Hold. Best week for this one is GW{best_g}: {best_n} ({best_v:.1f} extra)'
                      + (f'; this week {now_n} {now:.1f}.' if now is not None else '.')
                      + (f' Best in the whole game: {anyone[2]} in GW{anyone[0]} ({anyone[1]:.1f}).'
                         if anyone[1] > best_v + 1.5 else ''))
            play = False
        later = []
        for lo, hi, wks in later_copies('3xc'):
            s2 = []
            for g in wks:
                xi2, _, key2 = pick_xi(squad, g)
                c2 = max(xi2, key=key2) if xi2 else None
                s2.append((g, round(key2(c2), 1) if c2 else 0.0, c2['name'] if c2 else ''))
            g2, v2, n2 = max(s2, key=lambda t: t[1])
            later.append(dict(lo=lo, hi=hi, best_gw=g2, best=v2, best_name=n2))
            advice += f' Second copy (GW{lo}–{hi}): best week GW{g2}, {n2} ({v2:.1f}).'
        out['chips']['3xc'] = dict(name=NAMES['3xc'], weeks=series, best_gw=best_g, best=best_v,
                                   best_name=best_n, now=now, now_name=now_n, play=play,
                                   advice=advice, anyone=anyone, last_eligible=half_end,
                                   later=later)
    else:
        out['chips']['3xc'] = dict(name=NAMES['3xc'], weeks=[], advice='Both used.', play=False)

    # ---- free hit (and the squad-vs-best gap that also informs the wildcard)
    weeks = eligible('freehit')
    gap_weeks = sorted(set(weeks) | set(fh_weeks or []))
    gaps = {}
    for g in gap_weeks:
        best, _, _ = best_possible_week(players, g, budget)
        mine, _, _ = your_week(squad, g)
        gaps[g] = round(best - mine, 1)
    if weeks:
        series = [(g, gaps[g]) for g in weeks]
        best_g, best_v = max(series, key=lambda t: t[1])
        now = gaps.get(gw)
        half_end = max(weeks)
        blank_note = ''
        if any(str(g) in out['bgw'] for g in weeks):
            blank_note = ' Blank weeks: ' + ', '.join(f'GW{g}' for g in weeks if str(g) in out['bgw']) + '.'
        if now is not None and now >= FH_PLAY_MIN and now >= NEAR_BEST * best_v:
            advice = f'Play it: the best possible XI this week beats yours by {now:.1f}.'
            play = True
        elif now is not None and gw == half_end:
            advice = f'Last chance this half — gap {now:.1f}.'
            play = True
        else:
            advice = (f'Hold. Widest gap left is GW{best_g} ({best_v:.1f} behind the best possible XI)'
                      + (f'; this week {now:.1f}.' if now is not None else '.') + blank_note)
            play = False
        out['chips']['freehit'] = dict(name=NAMES['freehit'], weeks=series, best_gw=best_g,
                                       best=best_v, now=now, play=play, advice=advice,
                                       last_eligible=half_end)
    else:
        out['chips']['freehit'] = dict(name=NAMES['freehit'], weeks=[], advice='Both used.', play=False)

    # ---- wildcard
    weeks = eligible('wildcard')
    if weeks:
        half_end = max(weeks)
        trend = [(g, gaps[g]) for g in sorted(gaps) if g in weeks][:8]
        now = wc_now
        if now is not None and now >= WC_PLAY_MIN and gw in weeks:
            advice = f'Worth playing now: unlimited transfers are worth {now:.1f} over the window versus your free transfers.'
            play = True
        elif gw == half_end and gw in weeks:
            advice = f'Last chance this half' + (f' — worth {now:.1f}.' if now is not None else '.')
            play = True
        else:
            rising = len(trend) >= 3 and trend[-1][1] > trend[0][1] + 3
            bits = ['Hold.']
            if now is not None and gw in weeks:
                bits.append(f'Unlimited transfers now are worth {now:.1f} over the window.')
            if rising:
                bits.append(f'The squad is decaying: gap to the best possible XI '
                            f'{trend[0][1]:.1f} → {trend[-1][1]:.1f} by GW{trend[-1][0]}.')
            elif trend:
                bits.append(f'The squad is holding up (gap to the best possible XI '
                            f'{trend[0][1]:.1f}–{max(v for _, v in trend):.1f} over the next weeks).')
            if gw not in weeks and weeks:
                bits.append(f'Playable from GW{weeks[0]}.')
            advice = ' '.join(bits)
            play = False
        out['chips']['wildcard'] = dict(name=NAMES['wildcard'], now=now, gap_trend=trend,
                                        play=play, advice=advice, last_eligible=half_end)
    else:
        out['chips']['wildcard'] = dict(name=NAMES['wildcard'], advice='Both used.', play=False)

    out['gaps'] = {str(g): v for g, v in gaps.items()}
    return out


def digest_lines(res):
    """Markdown for weekly.py."""
    L = []
    if res['dgw'] or res['bgw']:
        parts = []
        if res['dgw']:
            parts.append('doubles: ' + ', '.join(f'GW{g} ({", ".join(v)})' for g, v in res['dgw'].items()))
        if res['bgw']:
            parts.append('blanks: ' + ', '.join(f'GW{g} ({len(v)} teams out)' for g, v in res['bgw'].items()))
        L.append('Fixture list: ' + '; '.join(parts) + '.')
    else:
        L.append('No double or blank gameweeks in the fixture list yet — they appear '
                 'when cup ties force rescheduling, usually from midwinter.')
    L.append('')
    L.append('| chip | this week | best week left | advice |')
    L.append('|---|---|---|---|')
    for key in ('bboost', '3xc', 'freehit', 'wildcard'):
        c = res['chips'][key]
        if key == 'wildcard':
            now = f'{c["now"]:+.1f} over the window' if c.get('now') is not None else '—'
            best = '—'
        else:
            now = (f'{c["now"]:.1f}' + (f' ({c["now_name"]})' if c.get('now_name') else '')) if c.get('now') is not None else '—'
            best = (f'GW{c["best_gw"]}: {c["best"]:.1f}' + (f' ({c["best_name"]})' if c.get('best_name') else '')) if c.get('best_gw') else '—'
        mark = '**' if c.get('play') else ''
        L.append(f'| {c["name"]} | {now} | {best} | {mark}{c["advice"]}{mark} |')
    L.append('')
    L.append(f'_Thresholds are heuristics: bench ≥ {res["heuristics"]["bb_play_min"]:.0f}, '
             f'captain extra ≥ {res["heuristics"]["tc_play_min"]:.0f}, free-hit gap ≥ '
             f'{res["heuristics"]["fh_play_min"]:.0f}, wildcard ≥ {res["heuristics"]["wc_play_min"]:.0f} '
             f'over the window, and "as good as any week left" means within 10%. The season '
             f'outlook holds minutes constant, so weeks far out are fixture strength, not form._')
    return L


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--gw', type=int)
    args = ap.parse_args()
    players, start_gw, last_gw = load_season()
    gw = args.gw or start_gw
    # squad from my_squad.txt via weekly's loader (names -> ids)
    import weekly as W
    proj, _, _ = W.load_projections()
    st = W.load_squad(None, proj, gw)
    res = evaluate(players, st['ids'], st['bank'], gw, last_gw, chip_windows(), {})
    print('\n'.join(digest_lines(res)))
    OUT.write_text(json.dumps(res, indent=1))
    print(f'\n-> {OUT}')
