"""
Player model v2.

Every shrinkage weight below comes from the measured year-over-year stability in
stability.py rather than from judgement. The headline findings that shaped it:

  metric        stability   what it means for the model
  ------------  ---------   -----------------------------------------------
  xGI/90            0.91    the most repeatable attacking signal there is
  xG/90             0.90    and it beats goals/90 (0.82) at predicting goals
  xA/90             0.84    beats assists/90 (0.59) everywhere, hugely for FWDs
  DefCon/90         0.56    a real, persistent skill, but needs real shrinkage
  starts            0.46    only moderately repeatable
  clean sheets/90   0.21    ALMOST NO SIGNAL -- 0.09 for MID and FWD
  bonus/90 (DEF)    0.14    defender bonus is close to pure noise

Two of those change the design outright:

  * CLEAN SHEETS COME FROM THE TEAM MODEL, NOT THE PLAYER. A player's own
    clean-sheet history barely predicts his next season (0.21). v1 used it
    directly, which was wrong. v2 takes the clean-sheet probability from the
    fitted Dixon-Coles scoreline distribution for that specific fixture.

  * ATTACK IS BUILT ON xG AND xA, NOT GOALS AND ASSISTS. Finishing regresses;
    chance quality persists.

Rates are shrunk towards a positional prior by an empirical-Bayes weight that
falls out of the stability coefficient and the sample size, with older seasons
discounted and a small age adjustment (peak 24, 13% swing across the range).
"""
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
SEASON_VIEW = ROOT / 'v2' / 'season_view.json'
OUT = ROOT / 'v2' / 'projections_v2.json'

SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26']
SEASON_WEIGHT = {'2022/23': 0.30, '2023/24': 0.50, '2024/25': 0.75, '2025/26': 1.0}
HORIZON = 6

# Measured year-over-year stability, used as the empirical-Bayes reliability of
# a full season of evidence. A metric at 0.90 keeps nearly all of a player's own
# number; one at 0.21 is pulled almost entirely to the positional average.
STABILITY = {'xg90': 0.90, 'xa90': 0.84, 'dc90': 0.56, 'bonus90': 0.54,
             'saves90': 0.70, 'yellow90': 0.45}
STABILITY_DEF_BONUS = 0.14      # defender bonus specifically is near-noise

FULL_SEASON_MINS = 2200.0       # what counts as "one full season of evidence"

# Metrics the API only started recording partway through the panel. Seasons
# before these dates hold a zero that means "not measured", not "measured zero",
# and must be excluded from both the shrinkage and the prior it shrinks towards.
METRIC_FIRST_SEASON = {'dc90': '2024/25'}

GOAL_PTS = {'GKP': 6, 'DEF': 6, 'MID': 5, 'FWD': 4}
CS_PTS = {'GKP': 4, 'DEF': 4, 'MID': 1, 'FWD': 0}
DC_THRESHOLD = {'GKP': None, 'DEF': 10, 'MID': 12, 'FWD': 12}

# Aging: per-90 output relative to the peak at 24, from the measured curve.
AGE_CURVE = {19: 0.94, 20: 0.97, 21: 0.96, 22: 0.99, 23: 0.99, 24: 1.00,
             25: 1.00, 26: 0.97, 27: 0.99, 28: 0.98, 29: 0.95, 30: 0.95,
             31: 0.92, 32: 0.94, 33: 0.95, 34: 0.96}


def age_factor(dob, season_year=2026):
    if not dob:
        return 1.0
    a = season_year - int(dob[:4])
    return AGE_CURVE.get(min(34, max(19, a)), 0.95)


# --------------------------------------------------------------- loading
def load():
    cx = sqlite3.connect(DB)
    players = {}
    for row in cx.execute("""
        SELECT id, code, web_name, full_name, team, pos, price, sel_pct,
               status, news, chance, joined, birth_date, pens, corners, fk
        FROM player"""):
        (pid, code, name, full, team, pos, price, sel, status, news, chance,
         joined, dob, pens, corners, fk) = row
        players[pid] = dict(id=pid, code=code, name=name, full_name=full,
                            team=team, pos=pos, price=price, sel_pct=sel,
                            status=status, news=news or '', chance=chance,
                            joined=joined or '', dob=dob, pens=pens,
                            corners=corners, fk=fk, hist=[])
    by_code = defaultdict(list)
    for row in cx.execute("""
        SELECT code, season, minutes, starts, points, goals, assists, xg, xa,
               xgc, defcon, clean_sheets, bonus, saves, yellow, bps
        FROM season_stat WHERE season IN ('2022/23','2023/24','2024/25','2025/26')"""):
        (code, season, mins, starts, pts, g, a, xg, xa, xgc, dc, cs, bonus,
         saves, yellow, bps) = row
        if not mins:
            continue
        p90 = mins / 90.0
        by_code[code].append(dict(
            season=season, mins=mins, starts=starts or 0, pts=pts,
            pts90=pts / p90, xg90=(xg or 0) / p90, xa90=(xa or 0) / p90,
            dc90=(dc or 0) / p90, bonus90=(bonus or 0) / p90,
            saves90=(saves or 0) / p90, yellow90=(yellow or 0) / p90,
            g=g, a=a, cs=cs, defcon_raw=dc))
    for p in players.values():
        p['hist'] = sorted(by_code.get(p['code'], []), key=lambda h: h['season'])
    cx.close()
    return players


def positional_priors(players):
    """Minutes-weighted positional means -- the target of the shrinkage.

    Metrics that did not exist for the whole panel must only average the seasons
    where they were actually recorded. Defensive contributions arrived in
    2024/25, so the two earlier seasons carry a literal zero for every player.
    Averaging those in halved the prior — 4.55 against a true 7.55 for defenders
    and 5.01 against 8.50 for midfielders — and every player's DefCon estimate
    was then shrunk towards that phantom value. shrink() already excluded those
    rows; this did not, and the two have to agree.
    """
    acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for p in players.values():
        for h in p['hist']:
            if h['mins'] < 450:
                continue
            for m in ('xg90', 'xa90', 'dc90', 'bonus90', 'saves90', 'yellow90'):
                if h['season'] < METRIC_FIRST_SEASON.get(m, '0000/00'):
                    continue
                acc[p['pos']][m][0] += h[m] * h['mins']
                acc[p['pos']][m][1] += h['mins']
    return {pos: {m: (v[0] / v[1] if v[1] else 0.0) for m, v in d.items()}
            for pos, d in acc.items()}


PRICE_MEDIAN = {}


def load_overlay():
    """Research the data cannot contain: confirmed line-up decisions, role
    changes, squad-depth reality. Reuses the v1 overlay, which is keyed by FPL
    element id and still current. Price-rank ordering cannot resolve two keepers
    on the same price -- only a press conference can."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('ov', ROOT / 'overlay.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.OVERLAY, mod.PRESEASON_FORM


OVERLAY, PRESEASON_FORM = load_overlay()


def shrink(p, metric, priors):
    """Empirical-Bayes estimate of a player's true rate for one metric.

    Blends his own (recency-weighted) history with the positional prior. The
    weight on his own number is n/(n+k), where n is his effective sample in
    full-season units and k is set by the measured stability: a stable metric
    needs little evidence to be believed, an unstable one needs a lot.
    """
    prior = priors.get(p['pos'], {}).get(metric, 0.0)
    stab = STABILITY.get(metric, 0.5)
    if metric == 'bonus90' and p['pos'] == 'DEF':
        stab = STABILITY_DEF_BONUS
    # DefCon only exists from 2024/25, so older seasons carry no information
    first = METRIC_FIRST_SEASON.get(metric, '0000/00')
    seasons = [h for h in p['hist'] if h['mins'] >= 200 and h['season'] >= first]
    if not seasons:
        # The prior is the average of ESTABLISHED players at this position, so
        # handing it whole to someone with no Premier League record over-rates
        # unknown teenagers. Discount it, and lean on price, which encodes the
        # game-makers' own expectation.
        price_ratio = p['price'] / max(4.0, PRICE_MEDIAN.get(p['pos'], 5.5))
        return prior * 0.62 * min(1.6, max(0.55, price_ratio)), 0.0
    num = den = 0.0
    for h in seasons:
        w = SEASON_WEIGHT[h['season']] * h['mins']
        num += h[metric] * w
        den += w
    own = num / den
    n_eff = den / FULL_SEASON_MINS
    k = max(0.15, (1.0 - stab) / max(stab, 0.05))
    w_own = n_eff / (n_eff + k)
    return w_own * own + (1 - w_own) * prior, w_own


# --------------------------------------------------------------- minutes
def minutes_model(p, players):
    """Expected minutes per appearance and probability of starting.

    Start rate measured 0.46 stability year to year, so the observed rate is
    shrunk towards the club/position pecking order — the same correction v1
    arrived at by hand, now with a number behind it.
    """
    hist = [h for h in p['hist'] if h['mins'] >= 200]
    if hist:
        num = den = 0.0
        mps_num = mps_den = 0.0
        for h in hist:
            w = SEASON_WEIGHT[h['season']]
            if h['starts']:
                num += (h['starts'] / 38.0) * w
                den += w
                mps_num += min(92.0, h['mins'] / h['starts']) * w * h['mins']
                mps_den += w * h['mins']
        observed = num / den if den else 0.4
        mps = mps_num / mps_den if mps_den else 80.0
    else:
        observed, mps = None, 82.0

    peers = sorted((q['price'] for q in players.values()
                    if q['team'] == p['team'] and q['pos'] == p['pos']), reverse=True)
    rank = peers.index(p['price']) if p['price'] in peers else len(peers) - 1
    table = {'GKP': [0.92, 0.09, 0.03, 0.02],
             'DEF': [0.85, 0.80, 0.73, 0.63, 0.46, 0.29, 0.16, 0.08],
             'MID': [0.85, 0.78, 0.68, 0.56, 0.40, 0.25, 0.14, 0.07],
             'FWD': [0.82, 0.52, 0.28, 0.14, 0.07]}[p['pos']]
    rank_rate = table[min(rank, len(table) - 1)]

    if observed is None:
        start_rate = rank_rate * 0.9
    else:
        w = min(1.0, observed)          # more starts seen = more trust
        # A start rate earned at a DIFFERENT club says little about the pecking
        # order at the new one. Meslier started most of last season for Leeds;
        # at Arsenal he is behind Raya. Cap the trust for summer arrivals so the
        # new club's ordering dominates.
        if p['joined'] >= '2026-05-01':
            w = min(w, 0.30)
        # Goalkeeping is winner-take-all: one keeper plays every minute and the
        # rest play none. However many games a reserve started elsewhere, the
        # pecking order at his current club decides it.
        if p['pos'] == 'GKP' and rank >= 1:
            w = min(w, 0.15)
        start_rate = w * observed + (1 - w) * rank_rate

    ov = OVERLAY.get(p['id'], {})
    if 'mins' in ov:
        # the overlay states expected minutes per gameweek directly
        start_rate = min(0.97, ov['mins'] / max(mps, 1.0))

    # availability
    st, ch = p['status'], p['chance']
    if st == 'u':
        start_rate = 0.0
    elif st == 's':
        start_rate *= 0.30
    elif st == 'i':
        start_rate *= 0.40 if 'expected back' in p['news'].lower() else 0.06
    elif st == 'd' and ch is not None:
        start_rate *= ch / 100.0
    return max(0.0, min(0.97, start_rate)), mps


def poisson_at_least(mean, k):
    if mean <= 0:
        return 0.0
    term = math.exp(-mean)
    cum = term
    for i in range(1, k):
        term *= mean / i
        cum += term
    return max(0.0, min(1.0, 1.0 - cum))


def expected_floor_div(mean, n, cap=20):
    """E[floor(X / n)] for X ~ Poisson(mean).

    FPL's scoring uses floors, not rates: a defender loses 1 point per TWO goals
    conceded, a keeper gains 1 per THREE saves. Charging `mean / n` instead is
    wrong in a way that does not average out — the floor discards the remainder
    every match, so `mean / n` always overstates the count. At a typical 1.4
    expected goals conceded the true penalty is 0.47 a match, not 0.70, so every
    defender and goalkeeper was being over-charged by about a quarter of a point
    a game, which is most of a defender's margin.
    """
    if mean <= 0:
        return 0.0
    term = math.exp(-mean)
    total = 0.0
    for k in range(cap + 1):
        if k:
            term *= mean / k
        total += (k // n) * term
    return total


def defcon_hit_prob(mean, k, evidence):
    """P(a player clears his DefCon threshold in a match).

    NOT `poisson_at_least(shrunk_mean, k)`. That substitutes a point estimate
    into a sharply convex function and badly understates the answer — Jensen's
    inequality. Shrinking Gabriel's rate from 9.07 to 6.96 (a 23% cut) collapsed
    his hit probability from 0.41 to 0.16, a 61% cut, purely as an artefact.

    Two things genuinely spread the count around its mean, and both fatten the
    upper tail:

      * match-to-match variation — defensive actions depend on game state,
        opponent and how much of the ball the side has, so counts are
        over-dispersed relative to Poisson;
      * uncertainty in the player's own true rate, which is exactly what the
        shrinkage is expressing.

    Both are handled by a gamma-Poisson mixture: the true per-match rate is
    Gamma-distributed about the shrunk mean, so the count is negative binomial.
    `evidence` (0-1, the empirical-Bayes weight on the player's own record) sets
    the dispersion — a well-evidenced player gets a tight distribution, a
    thinly-evidenced one a wide one.
    """
    if mean <= 0:
        return 0.0
    r = 4.0 + 11.0 * max(0.0, min(1.0, evidence))   # 4 (uncertain) .. 15 (solid)
    p = r / (r + mean)
    # P(X = 0) = p^r, then the standard negative-binomial recurrence
    term = p ** r
    cum = term
    for i in range(1, k):
        term *= (r + i - 1) / i * (1 - p)
        cum += term
    return max(0.0, min(1.0, 1.0 - cum))


# ------------------------------------------------------------ projection
def project(players, view, priors):
    # median price per position, used to temper the prior for unknown players
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        prices = sorted(q['price'] for q in players.values() if q['pos'] == pos)
        PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5

    out = []
    for p in players.values():
        pos = p['pos']
        start_rate, mps = minutes_model(p, players)
        frac = mps / 90.0
        # The cameo term must be conditional on the player being available at
        # all. Applied unconditionally it gave players who have LEFT THE CLUB a
        # 20% chance of appearing, so Harrison (New England Revolution), Uche
        # (returned to Getafe) and Henderson (departed as a free agent) all
        # projected around 2.5 points over the horizon instead of zero.
        available = p['status'] != 'u'
        p_play = (start_rate + (1 - start_rate) * 0.20) if available else 0.0
        if not available:
            start_rate = 0.0

        xg90, w_xg = shrink(p, 'xg90', priors)
        xa90, _ = shrink(p, 'xa90', priors)
        dc90, w_dc = shrink(p, 'dc90', priors)
        bonus90, _ = shrink(p, 'bonus90', priors)
        saves90, _ = shrink(p, 'saves90', priors)
        yellow90, _ = shrink(p, 'yellow90', priors)

        af = age_factor(p['dob'])
        xg90 *= af
        xa90 *= af
        ov = OVERLAY.get(p['id'], {})
        rate_mult = ov.get('rate_mult', 1.0)
        xg90 *= rate_mult
        xa90 *= rate_mult

        fixtures = view['view'].get(p['team'], {})
        by_gw, total = [], 0.0
        for gw in range(1, HORIZON + 1):
            fx = fixtures.get(str(gw)) or []
            if not fx:
                by_gw.append(0.0)
                continue
            pts = 0.0
            for f in fx:                       # handles double gameweeks
                # attacking, scaled by how many goals this team is expected to
                # score in THIS fixture relative to a league-average match
                vol = f['xg'] / 1.45
                pts += (xg90 * frac * vol * GOAL_PTS[pos]
                        + xa90 * frac * vol * 3.0) * p_play
                # clean sheet: straight from the fitted scoreline distribution
                if CS_PTS[pos]:
                    pts += CS_PTS[pos] * f['cs'] * start_rate
                if pos in ('GKP', 'DEF'):
                    pts -= expected_floor_div(f['xgc'], 2) * start_rate
                if pos == 'GKP':
                    pts += expected_floor_div(saves90 * frac, 3) * start_rate
                thr = DC_THRESHOLD[pos]
                if thr and dc90 > 0:
                    pts += 2.0 * defcon_hit_prob(dc90 * frac, thr, w_dc) * p_play
                pts += start_rate * 2.0 + (p_play - start_rate) * 1.0
                pts += bonus90 * frac * p_play * 0.85
                pts -= yellow90 * frac * p_play
            by_gw.append(round(max(0.0, pts), 3))
            total += by_gw[-1]

        out.append(dict(
            id=p['id'], name=p['name'], full_name=p['full_name'], team=p['team'],
            pos=pos, price=p['price'], sel_pct=p['sel_pct'], status=p['status'],
            news=p['news'], joined=p['joined'], pens=p['pens'],
            corners=p['corners'], fk=p['fk'],
            proj_by_gw=by_gw, proj_6gw=round(total, 2),
            proj_gw=round(total / HORIZON, 3),
            value=round(total / p['price'], 4) if p['price'] else 0,
            start_rate=round(start_rate, 3), mins_proj=round(start_rate * mps),
            xg90=round(xg90, 4), xa90=round(xa90, 4), dc90=round(dc90, 3),
            evidence=round(w_xg, 2),
            seasons=len([h for h in p['hist'] if h['mins'] >= 450]),
            note=OVERLAY.get(p['id'], {}).get('note', '')
                 or PRESEASON_FORM.get(p['id'], ''),
            pts_last=next((h['pts'] for h in p['hist'] if h['season'] == '2025/26'), 0),
        ))
    calibrate(out, players)
    out.sort(key=lambda r: -r['proj_6gw'])
    return out


def calibrate(rows, players):
    """Remove the positional level bias introduced by shrinkage.

    Shrinking every player towards a positional mean that includes fringe
    squad members drags regular starters down, and it does so unevenly: raw v2
    lands at 1.03x for keepers but only 0.78x for midfielders against what
    comparable players actually scored last season. Uncorrected, that makes a
    goalkeeper look like a captaincy pick.

    The fix is a single multiplier per position, fitted so that established
    players (2,000+ minutes last season) project in line with what they actually
    delivered. It rescales levels only — the within-position ordering, which the
    backtest showed is v2's real strength, is untouched.
    """
    actual = {}
    for p in players.values():
        h = next((x for x in p['hist'] if x['season'] == '2025/26'), None)
        if h and h['mins'] >= 2000:
            actual[p['id']] = h['pts'] / 38.0

    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        proj, act = [], []
        for r in rows:
            if r['pos'] == pos and r['id'] in actual:
                proj.append(r['proj_gw']); act.append(actual[r['id']])
        if len(proj) < 6:
            continue
        ratio = (sum(proj) / len(proj)) / (sum(act) / len(act))
        if ratio <= 0:
            continue
        k = 1.0 / ratio
        k = max(0.7, min(1.45, k))          # never rescale by more than ~45%
        for r in rows:
            if r['pos'] != pos:
                continue
            r['proj_by_gw'] = [round(v * k, 3) for v in r['proj_by_gw']]
            r['proj_6gw'] = round(sum(r['proj_by_gw']), 2)
            r['proj_gw'] = round(r['proj_6gw'] / HORIZON, 3)
            r['value'] = round(r['proj_6gw'] / r['price'], 4) if r['price'] else 0
        print(f'  calibration {pos}: raw was {ratio:.2f}x actual, scaled by {k:.3f}')


if __name__ == '__main__':
    players = load()
    view = json.loads(SEASON_VIEW.read_text())
    priors = positional_priors(players)

    print('Positional priors (minutes-weighted per-90 means)')
    print(f"{'pos':<5}{'xG90':>8}{'xA90':>8}{'DefCon90':>10}{'bonus90':>9}")
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        d = priors.get(pos, {})
        print(f"{pos:<5}{d.get('xg90',0):>8.3f}{d.get('xa90',0):>8.3f}"
              f"{d.get('dc90',0):>10.2f}{d.get('bonus90',0):>9.3f}")

    rows = project(players, view, priors)
    json.dump({'players': rows, 'horizon': HORIZON}, open(OUT, 'w'))
    print(f'\nprojected {len(rows)} players -> {OUT}\n')

    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        print(f'--- top 10 {pos} (v2) ---')
        for r in [x for x in rows if x['pos'] == pos][:10]:
            print(f"  {r['proj_6gw']:>6.1f}  £{r['price']:>4}m  {r['name']:<16}"
                  f"{r['team']:<5} start {r['start_rate']*100:>3.0f}%  "
                  f"val {r['value']:.2f}  own {r['sel_pct']:>4.1f}%  "
                  f"{r['seasons']}sn")
        print()
