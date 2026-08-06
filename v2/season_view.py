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
    """2026/27 attack and defence for all 20 clubs, after both adjustments."""
    cx = sqlite3.connect(DB)
    shorts = [r[0] for r in cx.execute('SELECT short FROM team').fetchall()]
    cx.close()

    pa = model['promoted_prior']['atk']
    pdf = model['promoted_prior']['dfn']

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
            a *= MANAGER_SHRINK
            d *= MANAGER_SHRINK
            why.append(f'new manager — shrunk {1 - MANAGER_SHRINK:.0%} to the mean')
        atk[t], dfn[t], notes[t] = a, d, '; '.join(why)
    return atk, dfn, notes


def season_parameters(model, atk, dfn, horizon=None):
    """Per-club, per-gameweek expected goals, goals conceded and clean-sheet
    probability, straight from the fitted scoreline distribution."""
    cx = sqlite3.connect(DB)
    short = {r[0]: r[1] for r in cx.execute('SELECT id, short FROM team')}
    fixtures = cx.execute(
        'SELECT event, team_h, team_a, fdr_h, fdr_a FROM fixture '
        'WHERE event IS NOT NULL ORDER BY event').fetchall()
    cx.close()

    view = {t: {} for t in short.values()}
    for ev, th, ta, fh, fa in fixtures:
        if horizon and ev > horizon:
            continue
        h, a = short[th], short[ta]
        both = TM.team_view({**model, 'atk': atk, 'dfn': dfn}, h, a)
        both[h]['fdr'] = fh
        both[a]['fdr'] = fa
        view[h].setdefault(ev, []).append(both[h])
        view[a].setdefault(ev, []).append(both[a])
    return view


if __name__ == '__main__':
    model = json.loads(RATINGS.read_text())
    atk, dfn, notes = build_ratings(model)
    view = season_parameters(model, atk, dfn, horizon=6)

    print('2026/27 ratings after adjustment\n')
    print(f"{'team':<6}{'attack':>9}{'defence':>9}   note")
    for t in sorted(atk, key=lambda t: -(atk[t] + dfn[t])):
        print(f'{t:<6}{atk[t]:>+9.3f}{dfn[t]:>+9.3f}   {notes[t]}')

    print('\n\nClean-sheet probability, GW1-6 — model vs FPL difficulty rating\n')
    print(f"{'team':<6}" + ''.join(f'{"GW"+str(g):>13}' for g in range(1, 7))
          + f"{'CS总':>0}")
    print(f"{'':6}" + ''.join(f'{"cs%  fdr":>13}' for _ in range(1, 7)))
    rows = []
    for t in sorted(view):
        cells, tot = '', 0.0
        for g in range(1, 7):
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
               'view': {t: {str(g): v for g, v in d.items()} for t, d in view.items()}},
              open(OUT, 'w'), indent=1)
    print(f'\nwrote {OUT}')
