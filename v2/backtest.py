"""
Does any of this actually work?

Complexity is not evidence. This holds out a season, builds each method using
only what was knowable beforehand, and scores the predictions against what
actually happened.

Target: a player's points per 90 in the held-out season.
Tested on 2024/25 (trained on 2022/23-2023/24) and 2025/26 (trained on
2022/23-2024/25), for every player with at least 900 minutes in both the
training window and the target season.

Methods compared:

  naive_last     last season's points per 90, carried forward unchanged
  naive_price    a fit of points per 90 on price alone -- what the game-makers
                 themselves think, and a surprisingly hard baseline
  positional     the positional average; the "know nothing" floor
  v1_style       recency-weighted own history, the shrinkage v1 used
  v2_shrunk      empirical-Bayes shrinkage with the measured per-metric
                 stability, plus the aging curve

Reported as mean absolute error, root mean squared error, and Spearman rank
correlation. Rank correlation matters most: FPL is a choosing problem, not an
estimating one -- getting the order right is worth more than getting the level
right.
"""
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26']
SEASON_WEIGHT = {'2022/23': 0.30, '2023/24': 0.50, '2024/25': 0.75, '2025/26': 1.0}
MIN_MINS = 900
FULL_SEASON_MINS = 2200.0
STABILITY = {'pts90': 0.65, 'xgi90': 0.91}
AGE_CURVE = {19: 0.94, 20: 0.97, 21: 0.96, 22: 0.99, 23: 0.99, 24: 1.00,
             25: 1.00, 26: 0.97, 27: 0.99, 28: 0.98, 29: 0.95, 30: 0.95,
             31: 0.92, 32: 0.94, 33: 0.95, 34: 0.96}


def load():
    cx = sqlite3.connect(DB)
    rows = cx.execute("""
        SELECT s.code, s.season, s.minutes, s.starts, s.points, s.goals, s.assists,
               s.xg, s.xa, s.defcon, s.bonus, s.clean_sheets,
               p.pos, p.birth_date, s.start_cost, p.web_name
        FROM season_stat s JOIN player p ON p.code = s.code
        WHERE s.season IN ('2022/23','2023/24','2024/25','2025/26')""").fetchall()
    cx.close()
    panel = defaultdict(dict)
    for (code, season, mins, starts, pts, g, a, xg, xa, dc, bonus, cs, pos,
         dob, start_cost, name) in rows:
        if not mins:
            continue
        # PRICE MUST BE THE START-OF-SEASON PRICE, NOT TODAY'S.
        # Using the 2026/27 price to predict 2025/26 leaks the answer: that
        # price was set in the summer of 2026, after the season it is being
        # asked to predict. With the leak in place `naive_price` looked like the
        # best method in the whole test; it is not.
        price = (start_cost or 50) / 10.0
        p90 = mins / 90.0
        panel[code][season] = dict(
            code=code, season=season, mins=mins, starts=starts or 0, pos=pos,
            dob=dob, price=price, name=name, pts=pts,
            pts90=pts / p90,
            xgi90=((xg or 0) + (xa or 0)) / p90,
            dc90=(dc or 0) / p90, bonus90=(bonus or 0) / p90,
            cs90=(cs or 0) / p90)
    return panel


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d else float('nan')


def age_factor(dob, year):
    if not dob:
        return 1.0
    return AGE_CURVE.get(min(34, max(19, year - int(dob[:4]))), 0.95)


def build_cases(panel, target_season):
    """Everything knowable before `target_season`, paired with the outcome."""
    ti = SEASONS.index(target_season)
    train_seasons = SEASONS[:ti]
    cases = []
    for code, by in panel.items():
        tgt = by.get(target_season)
        if not tgt or tgt['mins'] < MIN_MINS:
            continue
        hist = [by[s] for s in train_seasons if s in by and by[s]['mins'] >= 200]
        if not hist:
            continue
        if sum(h['mins'] for h in hist) < MIN_MINS:
            continue
        cases.append(dict(code=code, pos=tgt['pos'], name=tgt['name'],
                          dob=tgt['dob'], price=tgt['price'],
                          hist=hist, actual=tgt['pts90'],
                          target_year=int(target_season[:4])))
    return cases


def positional_prior(cases, metric='pts90'):
    acc = defaultdict(lambda: [0.0, 0.0])
    for c in cases:
        for h in c['hist']:
            acc[c['pos']][0] += h[metric] * h['mins']
            acc[c['pos']][1] += h['mins']
    return {p: (v[0] / v[1] if v[1] else 0.0) for p, v in acc.items()}


# ------------------------------------------------------------- methods
def m_naive_last(c, priors, pricefit):
    return c['hist'][-1]['pts90']


def m_positional(c, priors, pricefit):
    return priors.get(c['pos'], 4.0)


def m_naive_price(c, priors, pricefit):
    a, b = pricefit.get(c['pos'], (4.0, 0.0))
    return a + b * c['price']


def m_v1_style(c, priors, pricefit):
    """Recency-weighted own history, shrunk by minutes -- what v1 did."""
    num = den = 0.0
    for h in c['hist']:
        w = SEASON_WEIGHT[h['season']] * h['mins']
        num += h['pts90'] * w
        den += w
    own = num / den
    total_mins = sum(h['mins'] for h in c['hist'])
    w_own = min(1.0, total_mins / 2400.0)
    return w_own * own + (1 - w_own) * priors.get(c['pos'], 4.0)


def m_v2_shrunk(c, priors, pricefit):
    """Empirical-Bayes weight from the measured stability, plus aging."""
    num = den = 0.0
    for h in c['hist']:
        w = SEASON_WEIGHT[h['season']] * h['mins']
        num += h['pts90'] * w
        den += w
    own = num / den
    n_eff = den / FULL_SEASON_MINS
    stab = STABILITY['pts90']
    k = max(0.15, (1.0 - stab) / max(stab, 0.05))
    w_own = n_eff / (n_eff + k)
    est = w_own * own + (1 - w_own) * priors.get(c['pos'], 4.0)
    # aging, relative to where the player was during the training window
    train_year = int(c['hist'][-1]['season'][:4])
    est *= age_factor(c['dob'], c['target_year']) / age_factor(c['dob'], train_year)
    return est


METHODS = [('naive_last', m_naive_last), ('naive_price', m_naive_price),
           ('positional', m_positional), ('v1_style', m_v1_style),
           ('v2_shrunk', m_v2_shrunk)]


def fit_price(cases):
    """Least-squares points/90 on price, per position, from training data only."""
    out = {}
    byp = defaultdict(list)
    for c in cases:
        num = den = 0.0
        for h in c['hist']:
            num += h['pts90'] * h['mins']; den += h['mins']
        byp[c['pos']].append((c['price'], num / den))
    for pos, pts in byp.items():
        if len(pts) < 6:
            out[pos] = (4.0, 0.0); continue
        x = np.array([p[0] for p in pts]); y = np.array([p[1] for p in pts])
        b = np.cov(x, y, bias=True)[0, 1] / max(np.var(x), 1e-9)
        out[pos] = (float(y.mean() - b * x.mean()), float(b))
    return out


def run(panel, target_season, pos_filter=None):
    cases = build_cases(panel, target_season)
    if pos_filter:
        cases = [c for c in cases if c['pos'] == pos_filter]
    if len(cases) < 25:
        return None
    priors = positional_prior(cases)
    pricefit = fit_price(cases)
    actual = np.array([c['actual'] for c in cases])
    res = {}
    for name, fn in METHODS:
        pred = np.array([fn(c, priors, pricefit) for c in cases])
        res[name] = dict(mae=float(np.mean(np.abs(pred - actual))),
                         rmse=float(np.sqrt(np.mean((pred - actual) ** 2))),
                         rho=spearman(pred, actual))
    return dict(n=len(cases), res=res)


def report(title, out):
    if not out:
        print(f'{title}: too few cases')
        return
    print(f'\n{title}   n={out["n"]}')
    print(f"  {'method':<14}{'MAE':>8}{'RMSE':>8}{'Spearman':>10}")
    best_mae = min(v['mae'] for v in out['res'].values())
    best_rho = max(v['rho'] for v in out['res'].values())
    for name, _ in METHODS:
        v = out['res'][name]
        flag = ''
        if v['mae'] == best_mae:
            flag += '  best MAE'
        if v['rho'] == best_rho:
            flag += '  best rank'
        print(f"  {name:<14}{v['mae']:>8.3f}{v['rmse']:>8.3f}{v['rho']:>10.3f}{flag}")


if __name__ == '__main__':
    panel = load()
    print('=' * 70)
    print('HOLD-OUT BACKTEST — predicting a season never seen during fitting')
    print('=' * 70)

    for target in ('2024/25', '2025/26'):
        report(f'target {target}  (trained on {", ".join(SEASONS[:SEASONS.index(target)])})',
               run(panel, target))

    print('\n' + '=' * 70)
    print('BY POSITION, target 2025/26')
    print('=' * 70)
    for pos in ('DEF', 'MID', 'FWD'):
        report(f'{pos}', run(panel, '2025/26', pos_filter=pos))

    print('\n' + '=' * 70)
    print('READING THIS')
    print('=' * 70)
    print('  Spearman is the number that matters: FPL asks you to rank players,')
    print('  not to estimate their totals. A method that is level-biased but')
    print('  orders players well will still pick the right squad.')
    print('  naive_price is the benchmark to beat — it is what the game-makers')
    print('  already believe, available for free, with no modelling at all.')
