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
from manager_changes import NEW_MANAGER, new_manager_clubs

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
RATINGS = ROOT / 'v2' / 'team_ratings.json'
OUT = ROOT / 'v2' / 'season_view.json'
CURRENT_SEASON = '2026/27'

# Clubs with a new manager for 2026/27 (manager_changes.NEW_MANAGER); their
# ratings describe a side coached by someone else, so they are pulled towards
# average.
MANAGER_SHRINK = 0.80
PROMOTED = {'COV', 'HUL', 'IPS'}
PROMOTED_BLEND = {'COV': 1.0, 'HUL': 1.0, 'IPS': 0.6}   # weight on the prior at n = 0

# P6: both adjustments RELAX as this season's matches enter the fit. Before
# this the promoted blend discarded Coventry's and Hull's fitted ratings all
# season and the new-manager shrink never changed.
#   promoted    w_prior = w0 * K_T / (K_T + n)
#   new manager shrink  = MANAGER_SHRINK + (1 - MANAGER_SHRINK) * n / (n + K_M)
# with n = this season's matches for that club in the fit. K_T from precision
# arithmetic: the promoted prior's measured spread is 0.158 in attack
# (predict/promoted.py) -> prior precision ~40; a match adds ~1.2 units of
# Fisher information on a log-rate, so the data reaches half weight at ~33
# matches: K_T ~ 30. K_M has no measurement behind it; start at 15 and let
# `season_view.py --validate-decay` (walk-forward on 2022/23-2025/26, scored
# on promoted / new-manager clubs only) move it.
PROMOTED_K = 30.0
MANAGER_K = 15.0


def promoted_prior_weight(n_matches, w0=1.0, k=PROMOTED_K):
    """Weight on the promoted-club prior after n of this season's matches."""
    n = max(0.0, float(n_matches or 0))
    return w0 * k / (k + n)


def manager_shrink(n_matches, base=MANAGER_SHRINK, k=MANAGER_K):
    """Fraction of the fitted deviation from the mean kept after n of this
    season's matches under the new manager (base at n = 0, -> 1 as n grows)."""
    n = max(0.0, float(n_matches or 0))
    return base + (1.0 - base) * n / (n + k)


def matches_this_season(season=CURRENT_SEASON, db=None):
    """{team short: matches of `season` already in the match table}."""
    cx = sqlite3.connect(db or DB)
    out = {}
    try:
        for team, n in cx.execute(
                'SELECT t, COUNT(*) FROM (SELECT home AS t FROM match WHERE season = ? '
                'AND hg IS NOT NULL UNION ALL SELECT away AS t FROM match WHERE season = ? '
                'AND hg IS NOT NULL) GROUP BY t', (season, season)):
            out[team] = n
    finally:
        cx.close()
    return out


def adjust_ratings(model, shorts, n_matches=None, new_manager=None, promoted=None,
                   promoted_blend=None, decay=True):
    """The post-fit adjustments as pure arithmetic on a fitted model, so the
    walk-forward validation applies exactly what production applies.

    `n_matches` = {team: matches of the season in the fit}; with decay=False
    the pre-P6 constants apply regardless of n. Returns (atk, dfn, notes).
    """
    n_matches = n_matches or {}
    new_manager = NEW_MANAGER if new_manager is None else new_manager
    promoted = PROMOTED if promoted is None else promoted
    promoted_blend = PROMOTED_BLEND if promoted_blend is None else promoted_blend
    known = [t for t in shorts if t in model['atk']]
    mean_a = sum(model['atk'][t] for t in known) / len(known)
    mean_d = sum(model['dfn'][t] for t in known) / len(known)
    pa = mean_a + model['promoted_prior']['atk']      # prior is an offset
    pdf = mean_d + model['promoted_prior']['dfn']
    atk, dfn, notes = {}, {}, {}
    for t in shorts:
        a = model['atk'].get(t)
        d = model['dfn'].get(t)
        n = n_matches.get(t, 0)
        why = []
        if a is None:
            a, d = pa, pdf
            why.append('no Premier League record — promoted-club prior')
        elif t in promoted:
            w0 = promoted_blend.get(t, 1.0)
            w = promoted_prior_weight(n, w0) if decay else w0
            a = w * pa + (1 - w) * a
            d = w * pdf + (1 - w) * d
            why.append(f'promoted — blended {w:.0%} towards the promoted prior'
                       + (f' after {n} match{"es" if n != 1 else ""}' if decay else ''))
        if t in new_manager:
            s = manager_shrink(n) if decay else MANAGER_SHRINK
            a = mean_a + (a - mean_a) * s
            d = mean_d + (d - mean_d) * s
            why.append(f'new manager — shrunk {1 - s:.0%} to the mean'
                       + (f' after {n} match{"es" if n != 1 else ""}' if decay else ''))
        atk[t], dfn[t], notes[t] = a, d, '; '.join(why)
    return atk, dfn, notes


def build_ratings(model, n_matches=None):
    """2026/27 attack and defence for all 20 clubs, after both adjustments.

    Both adjustments must be expressed RELATIVE TO THE LEAGUE MEAN. Defence
    ratings are deliberately not centred on zero — their level carries the
    overall goal rate of the league (see teams_model.fit) — so multiplying a
    defence rating by 0.8 would shrink it towards zero rather than towards
    average, which is a different and wrong operation. The promoted-club prior
    is likewise stored as an offset from the mean, not as an absolute rating.
    Both adjustments decay with this season's matches (P6, adjust_ratings()).
    """
    cx = sqlite3.connect(DB)
    shorts = [r[0] for r in cx.execute('SELECT short FROM team').fetchall()]
    cx.close()
    if n_matches is None:
        n_matches = matches_this_season()
    return adjust_ratings(model, shorts, n_matches)


def validate_decay(seasons=('2022/23', '2023/24', '2024/25', '2025/26'), k_t=PROMOTED_K,
                   k_m=MANAGER_K):
    """P6 validation: teams_model.walk_forward_adjusted on past seasons, scoring
    only matches involving promoted / new-manager clubs, fixed vs decaying."""
    matches = TM.load_matches()
    print(f'{len(matches)} matches; scoring only fixtures involving promoted or '
          f'new-manager clubs, fixed adjustments vs decaying (K_T={k_t:g}, K_M={k_m:g})')

    by_season = {}
    for m in matches:
        by_season.setdefault(m['season'], set()).update([m['home'], m['away']])
    order = sorted(by_season)

    def context(season):
        i = order.index(season) if season in order else -1
        promoted_s = (by_season[season] - by_season[order[i - 1]]) if i > 0 else set()
        return promoted_s, new_manager_clubs(season), by_season.get(season, set())

    for label, decay in (('fixed', False), ('decaying', True)):
        def adjust(model, season, n_matches, decay=decay):
            promoted_s, new_mgr, clubs = context(season)
            # the league mean is over THAT season's 20 clubs, as production
            # takes it over the current 20 — not over every club in the fit
            atk, dfn, _ = adjust_ratings(
                model, sorted(clubs), n_matches,
                new_manager=new_mgr, promoted=promoted_s,
                promoted_blend={t: 1.0 for t in promoted_s}, decay=decay)
            return atk, dfn, promoted_s | new_mgr
        v = TM.walk_forward_adjusted(matches, TM.DEFAULT_HALF_LIFE, adjust, seasons)
        print(f'  {label:<9} n={v["n"]:>4}  model log-loss {v["model_ll"]:.4f}  '
              f'bookmaker {v["book_ll"]:.4f}  Brier {v["model_brier"]:.4f}')
    print('Read: if decaying does not beat fixed on these fixtures, K_T/K_M are too '
          'small (the data is being trusted too early).')


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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--validate-decay', action='store_true',
                    help='walk-forward the fixed vs decaying post-fit adjustments on '
                         'past seasons (P6); slow, prints and exits')
    args = ap.parse_args()
    if args.validate_decay:
        validate_decay()
        raise SystemExit(0)
    model = json.loads(RATINGS.read_text())
    n_played = matches_this_season()
    atk, dfn, notes = build_ratings(model, n_played)
    print(f'{sum(n_played.values()) // 2 if n_played else 0} matches of {CURRENT_SEASON} '
          f'in the fit; promoted blend and new-manager shrink decay with them (P6)')
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
