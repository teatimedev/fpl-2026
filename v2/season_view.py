"""
Turn fitted team ratings into per-team, per-gameweek match parameters for
2026/27, and compare them against FPL's own fixture difficulty rating.

Two adjustments are needed before last season's ratings can be used:

  1. PROMOTED CLUBS. Coventry and Hull have no Premier League record at all, so
     they get the empirically estimated promoted-club prior rather than a guess.
     Ipswich do have 2024/25 data, but at a 365-day half-life it carries little
     weight, so they are blended towards the same prior.

  2. NEW MANAGERS. Ten clubs changed manager this summer, and a rating fitted on
     results under the previous manager is a weaker guide to how the side will
     play. Those clubs are shrunk towards the league mean. This deliberately
     gives up edge in exchange for not over-trusting stale information -- there
     is no way to know how Maresca's City will defend until they have played.
"""
import json
import sqlite3
from pathlib import Path

import numpy as np

import teams_model as TM
from gwclock import window as gw_window

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
RATINGS = ROOT / 'v2' / 'team_ratings.json'
OUT = ROOT / 'v2' / 'season_view.json'

# Clubs with a new manager for 2026/27; their ratings describe a side coached by
# someone else, so they are pulled towards average.
NEW_MANAGER = {'BOU', 'CHE', 'CRY', 'FUL', 'IPS', 'LIV', 'MCI', 'NEW', 'NFO', 'TOT'}
MANAGER_SHRINK = 0.80
PROMOTED = {'COV', 'HUL', 'IPS'}
PROMOTED_BLEND = {'COV': 1.0, 'HUL': 1.0, 'IPS': 0.6}   # weight on the prior


def build_ratings(model):
    """2026/27 attack and defence for all 20 clubs, after both adjustments.

    Both adjustments must be expressed RELATIVE TO THE LEAGUE MEAN. Defence
    ratings are deliberately not centred on zero — their level carries the
    overall goal rate of the league (see teams_model.fit) — so multiplying a
    defence rating by 0.8 would shrink it towards zero rather than towards
    average, which is a different and wrong operation. The promoted-club prior
    is likewise stored as an offset from the mean, not as an absolute rating.
    """
    cx = sqlite3.connect(DB)
    shorts = [r[0] for r in cx.execute('SELECT short FROM team').fetchall()]
    cx.close()

    # league means of the clubs that actually have a record
    known = [t for t in shorts if t in model['atk']]
    mean_a = sum(model['atk'][t] for t in known) / len(known)
    mean_d = sum(model['dfn'][t] for t in known) / len(known)

    pa = mean_a + model['promoted_prior']['atk']      # prior is an offset
    pdf = mean_d + model['promoted_prior']['dfn']

    atk, dfn, notes = {}, {}, {}
    for t in shorts:
        a = model['atk'].get(t)
        d = model['dfn'].get(t)
        why = []
        if a is None:
            a, d = pa, pdf
            why.append('no Premier League record — promoted-club prior')
        elif t in PROMOTED:
            w = PROMOTED_BLEND[t]
            a = w * pa + (1 - w) * a
            d = w * pdf + (1 - w) * d
            why.append(f'promoted — blended {w:.0%} towards the promoted prior')
        if t in NEW_MANAGER:
            a = mean_a + (a - mean_a) * MANAGER_SHRINK
            d = mean_d + (d - mean_d) * MANAGER_SHRINK
            why.append(f'new manager — shrunk {1 - MANAGER_SHRINK:.0%} to the mean')
        atk[t], dfn[t], notes[t] = a, d, '; '.join(why)
    return atk, dfn, notes


def season_parameters(model, atk, dfn, horizon=None):
    """Per-club, per-gameweek expected goals, goals conceded and clean-sheet
    probability, straight from the fitted scoreline distribution."""
    cx = sqlite3.connect(DB)
    short = {r[0]: r[1] for r in cx.execute('SELECT id, short FROM team')}
    fixtures = cx.execute(
        'SELECT event, team_h, team_a, fdr_h, fdr_a, kickoff FROM fixture '
        'WHERE event IS NOT NULL ORDER BY event').fetchall()
    # forward bookmaker odds, when posted (fetch.py fills `market` from
    # football-data ~a week out and, with ODDS_API_KEY, from the-odds-api).
    # A home/away pair meets once a season at each venue, so the pair alone
    # identifies the fixture; the date guards against a stale row.
    market = {}
    for date, h, a, oh, od, oa, oo, ou in cx.execute(
            'SELECT date, home, away, odds_h, odds_d, odds_a, odds_o25, odds_u25 '
            'FROM market'):
        market.setdefault((h, a), []).append((date, (oh, od, oa, oo, ou)))
    cx.close()

    m = {**model, 'atk': atk, 'dfn': dfn}
    view = {t: {} for t in short.values()}
    n_market = 0
    for ev, th, ta, fh, fa, kickoff in fixtures:
        if horizon and ev > horizon:
            continue
        h, a = short[th], short[ta]
        odds = None
        for date, o in market.get((h, a), []):
            if not kickoff or not date or abs(_days_between(date, kickoff)) <= 10:
                odds = o
        if odds and odds[0]:
            both = TM.market_view(m, h, a, odds)
            n_market += both[h].get('src') == 'market'
        else:
            both = TM.team_view(m, h, a)
        both[h]['fdr'] = fh
        both[a]['fdr'] = fa
        view[h].setdefault(ev, []).append(both[h])
        view[a].setdefault(ev, []).append(both[a])
    season_parameters.n_market = n_market
    return view


def _days_between(iso_a, iso_b):
    from datetime import datetime
    try:
        da = datetime.fromisoformat(iso_a[:10])
        db = datetime.fromisoformat(iso_b[:10])
        return (da - db).days
    except ValueError:
        return 0


if __name__ == '__main__':
    model = json.loads(RATINGS.read_text())
    atk, dfn, notes = build_ratings(model)
    # The window rolls: next gameweek to six ahead (see gwclock.py). The view
    # itself covers EVERY gameweek, because chip timing (Bench Boost, Triple
    # Captain, Free Hit) needs the whole half-season of fixtures; the player
    # model projects the window in detail and the rest of the season coarsely.
    start_gw, horizon = gw_window()
    view = season_parameters(model, atk, dfn, horizon=None)
    n_fix = sum(len(v) for t in view.values() for g, v in t.items()
                if start_gw <= g <= horizon) // 2
    print(f'{season_parameters.n_market} fixtures priced from bookmaker odds; '
          f'{n_fix} fixtures in the GW{start_gw}-{horizon} window '
          f'(the rest from fitted ratings)\n')

    print('2026/27 ratings after adjustment\n')
    print(f"{'team':<6}{'attack':>9}{'defence':>9}   note")
    for t in sorted(atk, key=lambda t: -(atk[t] + dfn[t])):
        print(f'{t:<6}{atk[t]:>+9.3f}{dfn[t]:>+9.3f}   {notes[t]}')

    print(f'\n\nClean-sheet probability, GW{start_gw}-{horizon} — model vs FPL '
          f'difficulty rating\n')
    gws = range(start_gw, horizon + 1)
    print(f"{'team':<6}" + ''.join(f'{"GW"+str(g):>13}' for g in gws))
    print(f"{'':6}" + ''.join(f'{"cs%  fdr":>13}' for _ in gws))
    rows = []
    for t in sorted(view):
        cells, tot = '', 0.0
        for g in gws:
            fx = view[t].get(g)
            if not fx:
                cells += f'{"—":>13}'
                continue
            f0 = fx[0]
            cells += f'{f0["cs"]*100:>8.0f}%{f0["fdr"]:>4}'
            tot += f0['cs']
        rows.append((t, cells, tot))
    for t, cells, tot in sorted(rows, key=lambda r: -r[2]):
        print(f'{t:<6}{cells}   {tot:.2f} expected clean sheets')

    json.dump({'atk': atk, 'dfn': dfn, 'notes': notes,
               'home_adv': model['home_adv'], 'rho': model['rho'],
               'start_gw': start_gw, 'horizon': horizon,
               'view': {t: {str(g): v for g, v in d.items()} for t, d in view.items()}},
              open(OUT, 'w'), indent=1)
    print(f'\nwrote {OUT}')
