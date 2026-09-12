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

  Bench Boost   all-15 score minus the ordinary XI, captain and autosub total
  Triple Captain  extra captain copy with vice fallback and lineup re-selection
  Free Hit      the optimised one-week squad+captain that week minus yours — big in a
                blank week or an injury crisis, when many of yours don't play
  Wildcard      the planner's gain from unlimited transfers now versus what
                your free transfers can do anyway, plus the trend of the gap
                between your squad and the best squad (a rising gap is decay)

    python v2/chips.py --team 3415101  # verified public account inputs required
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from squad_evaluator import (pick_lineup as shared_pick_lineup, evaluate_week,
                             gw_points, captain_options)  # noqa: E402
from lineup_search import search as lineup_search
from decision_state import forecast_id

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
BB_PLAY_MIN = 12.0        # incremental chip points above ordinary autosub cover
TC_PLAY_MIN = 8.0         # the extra 1x worth at least this many points
FH_PLAY_MIN = 12.0        # your XI at least this far behind the optimised
WC_PLAY_MIN = 20.0        # unlimited transfers worth at least this over the window
NEAR_BEST = 0.9           # "this week is as good as it gets" tolerance


def load_season():
    d = json.loads(SEASON.read_text())
    if d.get('forecast_id') != forecast_id(HERE / 'projections_v2.json'):
        raise ValueError('Season chip projections do not match the current forecast')
    if any(len(p.get('play_by_gw', [])) != d['last_gw'] for p in d['players']):
        raise ValueError('Season chip projections need full gameweek appearance probabilities')
    return {p['id']: p for p in d['players']}, d['start_gw'], d['last_gw']


def chip_windows(boot=None):
    """{'bboost': [(1,19),(20,38)], ...} from the API's chips list."""
    boot = json.loads(BOOT.read_text()) if boot is None else boot
    out = {}
    for c in boot.get('chips', []):
        out.setdefault(c['name'], []).append((c['start_event'], c['stop_event']))
    if set(out) != set(NAMES):
        raise ValueError('Current FPL chip windows are unavailable or incomplete')
    for windows in out.values():
        if any(not isinstance(lo, int) or not isinstance(hi, int) or not 1 <= lo <= hi <= 38
               for lo, hi in windows):
            raise ValueError('Invalid FPL chip window')
        if any(a[1] >= b[0] for a, b in zip(sorted(windows), sorted(windows)[1:])):
            raise ValueError('Overlapping FPL chip windows')
    return out


def doubles_and_blanks(boot=None, fixtures=None):
    """{gw: [teams with 2+ fixtures]}, {gw: [teams with none]}."""
    boot = json.loads(BOOT.read_text()) if boot is None else boot
    fixtures = json.loads(FIXTURES.read_text()) if fixtures is None else fixtures
    short = {t['id']: t['short_name'] for t in boot['teams']}
    per = {e['id']: {} for e in boot.get('events', [])}
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
    lineup = shared_pick_lineup(squad, g)
    return lineup.xi, lineup.bench, lambda p: gw_pts(p, g)


def your_week(squad, g):
    """(Ordinary total, incremental Bench Boost, ordinary captain)."""
    ordinary = evaluate_week(squad, g)
    # Any two squad members can share captaincy in a legal XI except the two
    # goalkeepers. With Bench Boost every owned player's ordinary points count.
    pairs = []
    for captain in squad:
        eligible = [p for p in squad if captain['pos'] != 'GKP'
                    or p['pos'] != 'GKP' or p['id'] == captain['id']]
        pairs.append(next(row for row in captain_options(eligible, g)
                          if row['captain']['id'] == captain['id']))
    pair = max(pairs, key=lambda row: (row['bonus'], gw_points(row['captain'], g)))
    boosted = sum(gw_points(p, g) for p in squad) + pair['bonus']
    return ordinary.total, boosted - ordinary.total, ordinary.lineup.captain


def triple_captain_gain(squad, g):
    ordinary = evaluate_week(squad, g)
    boosted, lineup = lineup_search(squad, g, captain_copies=2)
    return boosted - ordinary.total, lineup.captain


def best_possible_week(players, g, budget, pool_n=None, owned=(), sell_prices=None):
    """Rescore an optimised one-week squad under the full lineup expectation.

    A one-week integer program: 15 players (2/5/5/3, max 3 a club, budget),
    11 start, one is captained. Only the XI and captain score, so the other
    four are whatever is cheapest — the solver works that out. This linear
    search is a candidate generator, not a proof of global optimum under
    autosubs and captain fallback. Retained holdings cost their sale proceeds;
    budget is bank plus liquidatable value, never the gross market value.
    """
    import pulp
    pool = []
    owned = set(owned)
    sell_prices = sell_prices or {}
    if not math.isfinite(budget) or budget < 0:
        raise ValueError('Free Hit budget must be finite and non-negative')
    if any(pid not in sell_prices or not math.isfinite(sell_prices[pid])
           or not 0 <= sell_prices[pid] <= players[pid]['price'] + 1e-9 for pid in owned):
        raise ValueError('Free Hit selling values must be complete and valid')
    for pos in POS_ORDER:
        cand = [p for p in players.values() if p['pos'] == pos and p['status'] != 'u']
        cand.sort(key=lambda p: -gw_pts(p, g))
        top = cand[:pool_n] if pool_n is not None else cand
        cheap = sorted(cand, key=lambda p: (p['price'], -gw_pts(p, g)))[:4]
        seen = set()
        retained = [players[pid] for pid in owned if players[pid]['pos'] == pos]
        for p in top + cheap + retained:
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
    costs = {i: int(round((sell_prices[i] if i in owned else P[i]['price']) * 10)) for i in ids}
    prob += pulp.lpSum(x[i] * costs[i] for i in ids) <= int(round(budget * 10))
    for club in {p['team'] for p in pool}:
        prob += pulp.lpSum(x[i] for i in ids if P[i]['team'] == club) <= 3
    prob += pulp.lpSum(y[i] for i in ids) == 11
    prob += pulp.lpSum(c[i] for i in ids) == 1
    for i in ids:
        prob += y[i] <= x[i]
        prob += c[i] <= y[i]
    prob.solve(pulp.HiGHS(msg=False, timeLimit=20))
    if pulp.LpStatus[prob.status] != 'Optimal' or prob.sol_status != pulp.LpSolutionOptimal:
        raise ValueError('Free Hit candidate optimisation did not complete')
    selected = [P[i] for i in ids if (x[i].value() or 0) > 0.5]
    if len(selected) != 15 or sum(costs[p['id']] for p in selected) > round(budget * 10):
        raise ValueError('Free Hit solver returned an invalid squad or budget')
    evaluated = evaluate_week(selected, g)
    return evaluated.total, evaluated.lineup.xi, evaluated.lineup.captain


def used_chips(history):
    """{'bboost': [events...], ...} from entry history."""
    out = {}
    for c in (history or {}).get('chips', []):
        out.setdefault(c['name'], []).append(c['event'])
    return out


def evaluate(players, squad_ids, bank, gw, last_gw, windows, used, wc_now=None,
             fh_weeks=None, sell_prices=None, first_gw=1):
    """The whole chip picture from gameweek `gw`. Returns a dict for the digest
    and for data/chips.json."""
    squad = [players[i] for i in squad_ids if i in players]
    if len(squad) != 15 or len(set(squad_ids)) != 15:
        raise ValueError('Chip evaluation requires a complete squad')
    if not sell_prices or any(pid not in sell_prices for pid in squad_ids):
        raise ValueError('Chip evaluation requires verified selling values')
    budget = sum(sell_prices[pid] for pid in squad_ids) + bank
    dgw, bgw = doubles_and_blanks()

    def copies(name):
        """Unused copies of the chip, each as (lo, hi, [weeks >= gw]), in
        order. The first is the one to decide about now; later ones are
        information ("the second Bench Boost's best week is GW34")."""
        out = []
        for lo, hi in sorted(windows.get(name, [])):
            if any(lo <= e <= hi for e in used.get(name, [])):
                continue
            weeks = [g for g in range(max(lo, gw), min(hi, last_gw) + 1)
                     if (name not in ('wildcard', 'freehit') or g > first_gw)
                     and (name != 'freehit' or g - 1 not in used.get('freehit', []))]
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
            advice = f'Play it this week: the chip adds {now:.1f}, as good as any week left ({best_v:.1f} in GW{best_g}).'
            play = True
        elif now is not None and now > 0 and gw == half_end:
            advice = f'Last chance this half — play it ({now:.1f} extra).'
            play = True
        else:
            advice = (f'Hold. Best-looking week for this one is GW{best_g} ({best_v:.1f} extra points)'
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
            gain, cap = triple_captain_gain(squad, g)
            series.append((g, round(gain, 1), cap['name']))
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
        elif now is not None and now > 0 and gw == half_end:
            advice = f'Last chance this half — {now_n} ({now:.1f}).'
            play = True
        else:
            advice = (f'Hold. Best week for this one is GW{best_g}: {best_n} ({best_v:.1f} extra)'
                      + (f'; this week {now_n} {now:.1f}.' if now is not None else '.'))
            play = False
        later = []
        for lo, hi, wks in later_copies('3xc'):
            s2 = []
            for g in wks:
                value, c2 = triple_captain_gain(squad, g)
                s2.append((g, round(value, 1), c2['name']))
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
        best, _, _ = best_possible_week(players, g, budget, owned=squad_ids, sell_prices=sell_prices)
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
            advice = f'Play it: the optimised one-week squad this week beats yours by {now:.1f}.'
            play = True
        elif now is not None and now > 0 and gw == half_end:
            advice = f'Last chance this half — gap {now:.1f}.'
            play = True
        else:
            advice = (f'Hold. Widest gap left is GW{best_g} ({best_v:.1f} behind the optimised one-week squad)'
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
        elif gw == half_end and gw in weeks and now is not None and now > 0:
            advice = f'Last chance this half' + (f' — worth {now:.1f}.' if now is not None else '.')
            play = True
        else:
            rising = len(trend) >= 3 and trend[-1][1] > trend[0][1] + 3
            bits = ['Hold.']
            if now is not None and gw in weeks:
                bits.append(f'Unlimited transfers now are worth {now:.1f} over the window.')
            if rising:
                bits.append(f'The squad is decaying: gap to the optimised one-week squad '
                            f'{trend[0][1]:.1f} → {trend[-1][1]:.1f} by GW{trend[-1][0]}.')
            elif trend:
                bits.append(f'The squad is holding up (gap to the optimised one-week squad '
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
    # Chip timings are separate static scenarios, not a jointly solved season
    # policy. Still enforce the game's one-chip-per-week limit in the advice.
    suggested = [c for c in out['chips'].values() if c.get('play')]
    if len(suggested) > 1:
        selected = max(suggested, key=lambda c: c.get('now') or 0)
        for c in suggested:
            if c is selected:
                continue
            c['play'] = False
            c['advice'] = f"Only one chip can be played this week. {selected['name']} has the larger estimated gain; this is an alternative."
    out['method'] = ('Incremental points relative to ordinary XI, captain fallback and autosubs. '
        'Free Hit uses a linear candidate search followed by full lineup scoring. '
        'Timing assumes the current squad and forecasts persist; future transfers and joint chip scheduling are not modelled.')
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
    L.append(res.get('method', ''))
    return L


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--gw', type=int)
    ap.add_argument('--team', type=int, required=True, help='Public FPL entry ID')
    args = ap.parse_args()
    players, start_gw, last_gw = load_season()
    gw = args.gw or start_gw
    import weekly as W
    proj, _, _ = W.load_projections()
    boot = W.api('bootstrap-static/')
    current_gw, _ = W.next_gw(boot['events'])
    if gw != current_gw or gw != start_gw:
        raise ValueError('Chip advice requires a fresh forecast for the next deadline')
    W.validate_forecast_inputs(proj, boot)
    st = W.load_squad(args.team, proj, gw)
    prices, unknown = W.public_selling_prices(st['ids'], {e['id']: e for e in boot['elements']},
        W.api(f'entry/{args.team}/transfers/'), st['history'], HERE / 'fpl.db')
    if unknown:
        raise ValueError('Some purchase prices are unknown; chip affordability cannot be verified')
    res = evaluate(players, st['ids'], st['bank'], gw, last_gw, chip_windows(boot),
                   used_chips(st['history']), sell_prices=prices,
                   first_gw=min(row['event'] for row in st['history']['current']))
    print('\n'.join(digest_lines(res)))
    W.atomic_json(OUT, res)
    print(f'\n-> {OUT}')
