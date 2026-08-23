"""
Season-totals hold-out: does the shipped projection stack get LEVELS right?

backtest.py scores pts90 rate estimators and conditions on >=900 minutes in the
target season -- selection on the outcome. It therefore cannot judge the things
now being changed in production: the volume multiplier (predict/volume_test.py
measured slope 0.56 against the 1.00 project() applies) and calibrate()'s
refit. Both act on season TOTALS, so this script scores season totals.

Design
------
For each target season S in the panel except the newest (and except 2022/23,
which has no training window behind it), rebuild every player's projection
using ONLY what was knowable before S started:

  * histories: season_stat rows strictly earlier than S. The shrinkage
    machinery itself is imported unchanged from player_model (shrink(),
    positional_priors(), minutes_model()); exclusion of S is done by simply
    not putting S-or-later rows in p['hist']. Weights are season-keyed
    (SEASON_WEIGHT), so no cutoff argument is needed anywhere.
  * team strength: the Dixon-Coles model refit on matches played BEFORE S
    (teams_model.load_matches() filtered by date), then scored against the
    real S fixture list. The pairings were known at the deadline; the
    results are never touched.
  * calibration: calibrate()-style per-position rescale fitted on the LAST
    TRAINING season's actuals (what production does against 2025/26),
    never on S.
  * price: start-of-S price from season_stat.start_cost, never today's price
    (the leak backtest.py documents under naive_price).

Scored against realised S points for ALL players with both training history
and an S record -- no target-minutes filter, so fringe players count.

Window choice: FULL-SEASON TOTALS. season_stat holds season aggregates only --
there are no per-gameweek histories, so a realised first-6-GW window cannot be
built at all, and scaling full-season totals by 6/38 would be a positive
constant that changes neither Spearman nor proj/actual ratios. Full-season
totals also make the >150-point counts meaningful.

Variants
--------
  current          vol = f['xg']/1.45 exactly as project() applies it
  vol_lambda_0_56  the season-level club volume attenuated: attacking points
                   multiplied by vol_t^(lambda-1), renormalised league-wide so
                   total attacking points are unchanged -- predict/components.py's
                   VOL_LAMBDA pattern with the measured 0.56 slope

No look-ahead guarantee, mechanically: (1) p['hist'] contains no row with
season >= S, and every downstream consumer (shrink, positional_priors,
minutes_model) reads only p['hist']; (2) the Dixon-Coles fit consumes matches
with date < 1 July of S's opening year -- enforced by construction in
asof_team_model(); (3) calibration multipliers are fitted on season S-1
actuals only; (4) prices come from season_stat.start_cost of S (set at the S
deadline); player.price, status, news and chance (all 2026 facts) are never
read.
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
sys.path.insert(0, str(Path(__file__).resolve().parent))

import player_model as PM      # noqa: E402  (shrinkage machinery, reused as-is)
import teams_model as TM       # noqa: E402  (Dixon-Coles refit per hold-out)

SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26']
# 2022/23 cannot be a target: nothing precedes it to fit on. The newest season
# is excluded by design (see docstring).
TARGETS = ['2023/24', '2024/25']
CAMEO_RATE = 0.20              # P(a non-starter comes on), as components.py
VOL_LAMBDA = 0.56              # measured slope, predict/volume_test.py
PROJ_LINE = 150.0              # "projected >150 pts" headline count
POSITIONS = ('GKP', 'DEF', 'MID', 'FWD')


# --------------------------------------------------------------- loading
def load_panel():
    """({code: {name, dob, cur_team}}, {code: {season: stat}})."""
    cx = sqlite3.connect(DB)
    meta = {}
    for code, name, dob, team in cx.execute(
            'SELECT code, web_name, birth_date, team FROM player'):
        meta[code] = dict(name=name, dob=dob, cur_team=team)
    rows = defaultdict(dict)
    q = ("SELECT code, season, pos, minutes, starts, points, xg, xa, defcon,"
         " bonus, saves, yellow, start_cost"
         f" FROM season_stat WHERE season IN ({','.join('?' * len(SEASONS))})")
    for (code, season, pos, mins, starts, pts, xg, xa, dc, bonus, saves,
         yellow, cost) in cx.execute(q, SEASONS):
        if not mins:
            continue
        p90 = mins / 90.0
        rows[code][season] = dict(
            season=season, pos=pos, mins=mins, starts=starts or 0, pts=pts or 0,
            pts90=(pts or 0) / p90, xg90=(xg or 0) / p90, xa90=(xa or 0) / p90,
            dc90=(dc or 0) / p90, bonus90=(bonus or 0) / p90,
            saves90=(saves or 0) / p90, yellow90=(yellow or 0) / p90,
            start_cost=cost)
    cx.close()
    return meta, rows


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d else float('nan')


# ------------------------------------------------------------ as-of pieces
def make_player(code, meta, hist_rows, price, pos):
    """A dict shaped exactly like player_model.load() output, so shrink(),
    positional_priors() and minutes_model() run unmodified. The synthetic
    string id cannot collide with the overlay's element-id keys, and joined=''
    keeps minutes_model's summer-arrival cap (a 2026 fact about the CURRENT
    club) switched off."""
    return dict(
        id=f'holdout-{code}', code=code, pos=pos, price=price, joined='',
        dob=meta[code]['dob'], name=meta[code]['name'],
        team=meta[code]['cur_team'], hist=hist_rows, now=None)


def asof_players(meta, rows, target):
    """Every player with training history before S and an S record, priced and
    positioned as of S. Histories exclude S and later -- this is the leak
    barrier every downstream consumer reads through."""
    train_seasons = SEASONS[:SEASONS.index(target)]
    out = {}
    for code, by in rows.items():
        tgt = by.get(target)
        hist = sorted((by[s] for s in train_seasons if s in by),
                      key=lambda h: h['season'])
        assert all(h['season'] < target for h in hist)  # leak barrier
        if not hist or not tgt:
            continue
        # position as of S: the registration that existed at the deadline
        pos = tgt['pos'] or next(
            (h['pos'] for h in reversed(hist) if h['pos']), 'MID')
        out[code] = make_player(code, meta, hist, tgt['start_cost'] / 10.0, pos)
    return out

_MATCH_CACHE = {}


def load_matches_cached():
    if not _MATCH_CACHE:
        _MATCH_CACHE.update(dict(matches=TM.load_matches()))
    return _MATCH_CACHE['matches']


def asof_team_view(target):
    """{team: [{xg, xgc, cs} per fixture]} for season S, scored by a
    Dixon-Coles fit that has seen no match played on or after 1 July of S's
    opening year. Promoted clubs absent from the fit take the promoted_prior
    offset off the fitted league mean."""
    cutoff = datetime(int(target[:4]), 7, 1)
    matches = load_matches_cached()
    train = [m for m in matches if m['date'] < cutoff]
    assert max(m['date'] for m in train) < cutoff   # leak barrier
    model = TM.fit(train, half_life_days=TM.DEFAULT_HALF_LIFE,
                   ref_date=max(m['date'] for m in train))
    pa, pdf = TM.promoted_prior(train)
    mean_atk = float(np.mean(list(model['atk'].values())))
    mean_dfn = float(np.mean(list(model['dfn'].values())))

    sched = defaultdict(list)          # team -> [(home, away)]
    for m in matches:
        if m['season'] == target:
            sched[m['home']].append((m['home'], m['away']))
            sched[m['away']].append((m['home'], m['away']))

    atk, dfn = dict(model['atk']), dict(model['dfn'])
    for t in sched:
        if t not in atk:
            atk[t] = mean_atk + pa
            dfn[t] = mean_dfn + pdf
    patched = dict(model, atk=atk, dfn=dfn)

    view = {}
    for team, pairs in sched.items():
        fx = []
        for home, away in pairs:
            M, lam, mu = TM.score_matrix(patched, home, away)
            if team == home:
                fx.append(dict(xg=lam, xgc=mu, cs=float(M[:, 0].sum())))
            else:
                fx.append(dict(xg=mu, xgc=lam, cs=float(M[0, :].sum())))
        view[team] = fx
    return view


# ------------------------------------------------------------- projection
def project_totals(players, priors, view):
    """Season-total projection per player, following project()/components.py
    accounting, with everyone fully available (status/news/chance are 2026
    facts and must not reach a historical week). Returns each player's raw
    total plus his attack subtotal separately, because the attenuated variant
    rescales only the club-volume-exposed part."""
    for pos in POSITIONS:
        prices = sorted(q['price'] for q in players.values() if q['pos'] == pos)
        PM.PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5

    out = {}
    for p in players.values():
        pos = p['pos']
        start_rate, mps = PM.minutes_model(p, players)
        p_play = start_rate + (1 - start_rate) * CAMEO_RATE
        frac = mps / 90.0

        xg90, w_xg = PM.shrink(p, 'xg90', priors)
        xa90, _ = PM.shrink(p, 'xa90', priors)
        dc90, w_dc = PM.shrink(p, 'dc90', priors)
        bonus90, _ = PM.shrink(p, 'bonus90', priors)
        saves90, _ = PM.shrink(p, 'saves90', priors)
        yellow90, _ = PM.shrink(p, 'yellow90', priors)
        # aging relative to where the player was during training -- the same
        # ratio backtest.m_v2_shrunk applies
        train_year = int(p['hist'][-1]['season'][:4])
        af = (PM.age_factor(p['dob'], int(p['target'][:4]))
              / max(PM.age_factor(p['dob'], train_year), 1e-9))
        xg90 *= af
        xa90 *= af

        fx_list = view.get(p['team'], [])
        pts = attack = 0.0
        for f in fx_list:
            vol = f['xg'] / 1.45
            att = ((xg90 * frac * vol * PM.GOAL_PTS[pos]
                    + xa90 * frac * vol * 3.0)) * p_play
            attack += att
            pts += att
            if PM.CS_PTS[pos]:
                pts += PM.CS_PTS[pos] * f['cs'] * start_rate
            if pos in ('GKP', 'DEF'):
                pts -= PM.expected_floor_div(f['xgc'], 2) * start_rate
            if pos == 'GKP':
                pts += PM.expected_floor_div(saves90 * frac, 3) * start_rate
            thr = PM.DC_THRESHOLD[pos]
            if thr and dc90 > 0:
                pts += 2.0 * PM.defcon_hit_prob(dc90 * frac, thr, w_dc) * p_play
            pts += start_rate * 2.0 + (p_play - start_rate)
            pts += bonus90 * frac * p_play * 0.85
            pts -= yellow90 * frac * p_play
        out[p['id']] = dict(
            code=p['code'], pos=pos, total=max(pts, 0.0), attack=attack,
            club_vol=(float(np.mean([f['xg'] / 1.45 for f in fx_list]))
                      if fx_list else 1.0))
    return out


def attenuate(totals):
    """components.py's renormalisation at the measured slope: multiply each
    player's attacking points by vol_t^(lambda-1), then rescale league-wide so
    total attacking points are unchanged (a level-preserving reweighting).
    Fixture-to-fixture variation is irrelevant here by construction: every
    fixture of a club carries the same volume over a season, so this moves
    exactly the season-average club level that volume_test.py flagged."""
    raw = sum(r['attack'] for r in totals.values())
    if raw <= 0:
        return totals
    expo = -(1 - VOL_LAMBDA)
    norm = raw / max(sum(r['attack'] * r['club_vol'] ** expo
                         for r in totals.values()), 1e-9)
    for r in totals.values():
        m = r['club_vol'] ** expo * norm
        r['total'] = r['total'] - r['attack'] * (1 - m)
    return totals


def calibrate(totals, rows, target, two_season=False):
    """Production-style positional level correction, computed as-of the
    target season instead of at runtime.

    Default: single-season anchor — established players (2,000+ minutes in
    S-1) project to what they actually delivered that season. This mirrors
    production before the 2026-08 anchor change and baselines every variant.

    two_season=True mirrors player_model.calibrate() as shipped 2026-08-23:
    outfield multipliers are refit against the mean pts/38 across the LAST
    TWO completed seasons (latest must clear 2,000 minutes to establish the
    player; each contributing stint clears CAL_STINT_MINS=900). GKP keeps
    the single-season k — pooling dragged keepers off parity. Targets with
    only one training season behind them fall back to the single anchor,
    which is why 2023/24 is bit-identical between the two variants.
    """
    idx = SEASONS.index(target)
    prev = SEASONS[idx - 1]

    def fit(actual):
        ks = {}
        for pos in POSITIONS:
            proj, act = [], []
            for r in totals.values():
                if r['pos'] != pos or r['code'] not in actual:
                    continue
                proj.append(r['total'] / 38.0)
                act.append(actual[r['code']])
            if len(proj) < 6:
                continue
            ratio = (sum(proj) / len(proj)) / (sum(act) / len(act))
            if ratio <= 0:
                continue
            ks[pos] = round(max(0.7, min(1.45, 1.0 / ratio)), 3)
        return ks

    ks = fit({code: r[prev]['pts'] / 38.0 for code, r in rows.items()
              if prev in r and r[prev]['mins'] >= 2000})
    if two_season:
        if idx < 2:
            return ks
        older = SEASONS[idx - 2]
        hist = {}
        for code, r in rows.items():
            if prev not in r or older not in r or r[prev]['mins'] < 2000:
                continue
            stints = [o for o in (r[prev], r[older]) if o['mins'] >= 900]
            if not stints:
                continue
            hist[code] = sum(o['pts'] for o in stints) / len(stints) / 38.0
        outfield = fit(hist)
        for pos in ('DEF', 'MID', 'FWD'):
            if pos in outfield:
                ks[pos] = outfield[pos]
    for r in totals.values():
        k = ks.get(r['pos'], 1.0)
        r['total'] *= k
        r['attack'] *= k
    return ks


# ------------------------------------------------------------------ scoring
def score(totals, rows, target):
    cases = [(t['pos'], t['total'], rows[t['code']][target]['pts'])
             for t in totals.values()]

    def block(cs):
        proj = np.array([c[1] for c in cs])
        act = np.array([c[2] for c in cs])
        mask = act > 0
        return dict(
            n=len(cs), rho=spearman(proj, act),
            ratio=float(np.mean(proj[mask] / act[mask])) if mask.any()
            else float('nan'),
            agg=float(proj.sum() / max(act.sum(), 1e-9)),
            mean_proj=float(np.mean(proj)), mean_act=float(np.mean(act)),
            n_proj_line=int((proj > PROJ_LINE).sum()),
            n_act_line=int((act > PROJ_LINE).sum()))

    out = {'ALL': block(cases)}
    for pos in POSITIONS:
        cs = [c for c in cases if c[0] == pos]
        if cs:
            out[pos] = block(cs)
    return out


def print_table(title, res):
    print('\n' + '=' * 78)
    print(title)
    print('=' * 78)
    header = (f"{'variant':<16}{'grp':<5}{'n':>5}{'Spearman':>10}"
              f"{'mean p/a':>10}{'sum p/a':>9}{'E[proj]':>9}{'E[act]':>8}"
              f"{'>150 proj':>11}{'>150 act':>9}")
    for target, by_variant in res.items():
        print(f'\n--- target {target}   (trained on '
              f'{", ".join(SEASONS[:SEASONS.index(target)])}) ---')
        print(header)
        for variant, groups in by_variant.items():
            for grp, b in groups.items():
                print(f"{variant:<16}{grp:<5}{b['n']:>5}{b['rho']:>10.3f}"
                      f"{b['ratio']:>10.2f}{b['agg']:>9.2f}"
                      f"{b['mean_proj']:>9.1f}{b['mean_act']:>8.1f}"
                      f"{b['n_proj_line']:>11}{b['n_act_line']:>9}")


# --------------------------------------------------------------------- main
def run_target(target, meta, rows):
    players = asof_players(meta, rows, target)
    for p in players.values():
        p['target'] = target          # read by project_totals for aging
    priors = PM.positional_priors(players)
    view = asof_team_view(target)
    # A player whose CURRENT club was not in S at all (signed from abroad in a
    # later window) cannot be placed in a fixture list -- the club-attribution
    # gap again. Projecting him against an empty schedule would manufacture a
    # zero, so he is excluded and counted instead.
    unplaced = [c for c, p in players.items() if p['team'] not in view]
    for c in unplaced:
        del players[c]
    totals_cur = project_totals(players, priors, view)
    raw = {pid: dict(t) for pid, t in totals_cur.items()}   # pre-calibration
    ks = calibrate(totals_cur, rows, target)

    def as_scoring(totals):
        return {pid: dict(pos=t['pos'], code=t['code'], total=t['total'])
                for pid, t in totals.items()}

    by_variant = {'current': score(as_scoring(totals_cur), rows, target)}

    totals_att = attenuate({pid: dict(t) for pid, t in totals_cur.items()})
    # production applies calibration after every projection change, so the
    # variant is re-calibrated on the same S-1 evidence before scoring
    ks_att = calibrate(totals_att, rows, target)

    # production's shipped anchor (2026-08): outfield fitted on the TWO
    # completed seasons behind S, applied once to the same raw projections
    totals_ts = {pid: dict(t) for pid, t in raw.items()}
    ks_ts = calibrate(totals_ts, rows, target, two_season=True)
    by_variant['anchor_2season'] = score(as_scoring(totals_ts), rows, target)
    by_variant['vol_lambda_0_56'] = score(as_scoring(totals_att), rows, target)

    print(f'\n[{target}] n={len(players)} players scored '
          f'({len(unplaced)} unplaceable, excluded); calibration current: '
          + ', '.join(f'{p} {k:.3f}' for p, k in sorted(ks.items()))
          + ' | attenuated: '
          + ', '.join(f'{p} {k:.3f}' for p, k in sorted(ks_att.items()))
          + ' | anchor_2season: '
          + ', '.join(f'{p} {k:.3f}' for p, k in sorted(ks_ts.items())))
    return by_variant


def main():
    meta, rows = load_panel()
    res = {target: run_target(target, meta, rows) for target in TARGETS}
    print_table('SEASON-TOTALS HOLD-OUT (full-season totals, all players '
                'with training history)', res)


if __name__ == '__main__':
    main()
