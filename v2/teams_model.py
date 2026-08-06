"""
Dixon-Coles team strength model.

Replaces FPL's 2-5 "fixture difficulty rating" -- which is a hand-assigned
heuristic -- with attack and defence ratings estimated by maximum likelihood
from four seasons of actual results.

The model (Dixon & Coles 1997):

    home goals ~ Poisson(lambda),  lambda = exp(atk_home - def_away + home_adv)
    away goals ~ Poisson(mu),      mu     = exp(atk_away - def_home)

with two refinements that matter:

  * a low-score dependence correction `tau`, because independent Poisson
    underestimates 0-0 and 1-1 and overestimates 1-0 and 0-1 -- and those are
    exactly the scorelines that decide clean sheets, which is most of a
    defender's FPL value;
  * exponential time decay, so a result from 2022 counts less than one from
    2026. The half-life is tuned by out-of-sample log-loss rather than guessed.

What this buys over FDR: a full scoreline distribution for every fixture, so
clean-sheet probability, expected goals conceded and expected goals scored all
come out calibrated instead of being read off a 2-5 scale.

Validation is against Pinnacle's closing odds, which is the honest benchmark --
the market is very hard to beat, and a model that cannot get close to it should
not be trusted to price a defender.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
OUT = ROOT / 'v2' / 'team_ratings.json'
MAXG = 10          # scoreline grid, 0..MAXG goals


# ------------------------------------------------------------------- data
def load_matches():
    cx = sqlite3.connect(DB)
    rows = cx.execute(
        'SELECT season, date, home, away, hg, ag, odds_h, odds_d, odds_a '
        'FROM match WHERE hg IS NOT NULL').fetchall()
    cx.close()
    out = []
    for season, date, h, a, hg, ag, oh, od, oa in rows:
        try:
            d = datetime.strptime(date, '%d/%m/%Y')
        except ValueError:
            d = datetime.strptime(date, '%d/%m/%y')
        out.append(dict(season=season, date=d, home=h, away=a, hg=hg, ag=ag,
                        oh=oh, od=od, oa=oa))
    out.sort(key=lambda r: r['date'])
    return out


# -------------------------------------------------------------- the model
def tau(hg, ag, lam, mu, rho):
    """Dixon-Coles low-score correction.

    Independent Poisson gets the 0-0/1-0/0-1/1-1 cell probabilities wrong;
    this reweights exactly those four cells and leaves the rest untouched.
    """
    t = np.ones_like(lam, dtype=float)
    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)
    t[m00] = 1.0 - lam[m00] * mu[m00] * rho
    t[m01] = 1.0 + lam[m01] * rho
    t[m10] = 1.0 + mu[m10] * rho
    t[m11] = 1.0 - rho
    return np.clip(t, 1e-9, None)


def negloglik(params, hi, ai, hg, ag, w, n_teams):
    atk = params[:n_teams]
    dfn = params[n_teams:2 * n_teams]
    home_adv, rho = params[-2], params[-1]
    lam = np.exp(atk[hi] - dfn[ai] + home_adv)
    mu = np.exp(atk[ai] - dfn[hi])
    lam = np.clip(lam, 1e-6, 12)
    mu = np.clip(mu, 1e-6, 12)
    ll = (hg * np.log(lam) - lam + ag * np.log(mu) - mu
          + np.log(tau(hg, ag, lam, mu, rho)))
    # identifiability: attack ratings sum to zero
    penalty = 1000.0 * (atk.mean() ** 2)
    return -np.sum(w * ll) + penalty


def fit(matches, half_life_days=340.0, ref_date=None):
    teams = sorted({m['home'] for m in matches} | {m['away'] for m in matches})
    idx = {t: k for k, t in enumerate(teams)}
    n = len(teams)

    hi = np.array([idx[m['home']] for m in matches])
    ai = np.array([idx[m['away']] for m in matches])
    hg = np.array([m['hg'] for m in matches], dtype=float)
    ag = np.array([m['ag'] for m in matches], dtype=float)

    ref = ref_date or max(m['date'] for m in matches)
    age = np.array([(ref - m['date']).days for m in matches], dtype=float)
    w = np.exp(-np.log(2) * age / half_life_days)

    x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05]])
    bounds = [(-3, 3)] * n + [(-3, 3)] * n + [(-1, 1), (-0.2, 0.2)]
    res = minimize(negloglik, x0, args=(hi, ai, hg, ag, w, n),
                   method='L-BFGS-B', bounds=bounds,
                   options={'maxiter': 3000, 'ftol': 1e-10})
    # Only ONE direction is unidentifiable: adding a constant to every attack
    # rating and the same constant to every defence rating leaves every lambda
    # unchanged. Pinning the attack mean to zero (the penalty above) fixes it.
    #
    # The defence mean must then be left alone. It is not a free parameter — it
    # carries the overall goal level of the league, jointly with home advantage.
    # Re-centring it post-fit multiplies EVERY expected-goal value by
    # exp(mean(dfn)): with a fitted mean of -0.238 that scaled all goals by 0.79,
    # dropping the model from 2.91 goals a match (actual: 2.95) to 2.29, and
    # inflating clean-sheet probability by 32-38%. The 1X2 validation did not
    # catch it because scaling both sides' goals together barely moves
    # win/draw/loss, while it wrecks clean sheets — which is most of what a
    # defender is worth in FPL.
    atk = res.x[:n] - res.x[:n].mean()
    dfn = res.x[n:2 * n]
    return dict(teams=teams, atk=dict(zip(teams, atk)), dfn=dict(zip(teams, dfn)),
                home_adv=float(res.x[-2]), rho=float(res.x[-1]),
                nll=float(res.fun), half_life=half_life_days)


# ---------------------------------------------------------- match outcomes
def score_matrix(model, home, away, atk_over=None, dfn_over=None):
    atk = atk_over or model['atk']
    dfn = dfn_over or model['dfn']
    lam = np.exp(atk[home] - dfn[away] + model['home_adv'])
    mu = np.exp(atk[away] - dfn[home])
    k = np.arange(MAXG + 1)
    from scipy.stats import poisson
    ph = poisson.pmf(k, lam)
    pa = poisson.pmf(k, mu)
    M = np.outer(ph, pa)
    rho = model['rho']
    M[0, 0] *= 1 - lam * mu * rho
    M[0, 1] *= 1 + lam * rho
    M[1, 0] *= 1 + mu * rho
    M[1, 1] *= 1 - rho
    return M / M.sum(), float(lam), float(mu)


def outcome_probs(M):
    ph = float(np.tril(M, -1).sum())
    pd_ = float(np.trace(M))
    pa = float(np.triu(M, 1).sum())
    return ph, pd_, pa


def team_view(model, home, away):
    """Everything the player model needs from one fixture, for both sides."""
    M, lam, mu = score_matrix(model, home, away)
    ph, pd_, pa = outcome_probs(M)
    return {
        home: dict(opp=away, home=True, xg=lam, xgc=mu,
                   cs=float(M[:, 0].sum()), win=ph, draw=pd_),
        away: dict(opp=home, home=False, xg=mu, xgc=lam,
                   cs=float(M[0, :].sum()), win=pa, draw=pd_),
    }


# ------------------------------------------------------------- validation
def devig(oh, od, oa):
    if not (oh and od and oa):
        return None
    p = np.array([1 / oh, 1 / od, 1 / oa])
    return p / p.sum()


def logloss(probs, outcome_idx):
    p = np.clip(probs, 1e-9, 1)
    return -np.log(p[outcome_idx])


def walk_forward(matches, half_life, min_train=600, step=40):
    """Rolling-origin validation: fit on the past, predict the next block, never
    look ahead. Reports the model against the bookmaker on identical matches."""
    m_ll, b_ll, m_br, b_br, n = [], [], [], [], 0
    i = min_train
    while i < len(matches):
        train = matches[:i]
        test = matches[i:i + step]
        try:
            mdl = fit(train, half_life_days=half_life,
                      ref_date=train[-1]['date'])
        except Exception:
            i += step
            continue
        known = set(mdl['teams'])
        for m in test:
            if m['home'] not in known or m['away'] not in known:
                continue
            mk = devig(m['oh'], m['od'], m['oa'])
            if mk is None:
                continue
            M, _, _ = score_matrix(mdl, m['home'], m['away'])
            pm = np.array(outcome_probs(M))
            oi = 0 if m['hg'] > m['ag'] else (1 if m['hg'] == m['ag'] else 2)
            m_ll.append(logloss(pm, oi))
            b_ll.append(logloss(mk, oi))
            act = np.zeros(3); act[oi] = 1
            m_br.append(float(((pm - act) ** 2).sum()))
            b_br.append(float(((mk - act) ** 2).sum()))
            n += 1
        i += step
    return dict(n=n, model_ll=float(np.mean(m_ll)), book_ll=float(np.mean(b_ll)),
                model_brier=float(np.mean(m_br)), book_brier=float(np.mean(b_br)))


# ------------------------------------------------- promoted-team handling
def promoted_prior(matches):
    """What rating should a newly promoted club get before it has played?

    Estimated from history: find clubs that appear in a season having not played
    the previous one, and average their first-season attack and defence.

    Returned as OFFSETS FROM THAT SEASON'S LEAGUE MEAN, not as absolute ratings.
    Defence ratings are no longer centred on zero (see fit()), so their level
    differs between fits; only the offset transfers.
    """
    by_season = {}
    for m in matches:
        by_season.setdefault(m['season'], set()).update([m['home'], m['away']])
    seasons = sorted(by_season)
    atks, dfns = [], []
    for k in range(1, len(seasons)):
        newcomers = by_season[seasons[k]] - by_season[seasons[k - 1]]
        if not newcomers:
            continue
        sub = [m for m in matches if m['season'] == seasons[k]]
        mdl = fit(sub, half_life_days=10_000)
        mean_a = np.mean(list(mdl['atk'].values()))
        mean_d = np.mean(list(mdl['dfn'].values()))
        for t in newcomers:
            if t in mdl['atk']:
                atks.append(mdl['atk'][t] - mean_a)
                dfns.append(mdl['dfn'][t] - mean_d)
    if not atks:
        return -0.25, -0.25
    return float(np.mean(atks)), float(np.mean(dfns))


DEFAULT_HALF_LIFE = 280.0


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--tune', action='store_true',
                    help='re-run the half-life sweep (slow: many walk-forward '
                         'refits, several minutes). Only worth doing when a lot '
                         'of new results have accumulated.')
    args = ap.parse_args()

    matches = load_matches()
    print(f'{len(matches)} matches, {matches[0]["date"].date()} to '
          f'{matches[-1]["date"].date()}')

    if args.tune:
        print('\nTuning the time-decay half-life by out-of-sample log-loss')
        best = None
        for hl in (120, 200, 280, 365, 500, 700, 1200):
            v = walk_forward(matches, hl)
            flag = ''
            if best is None or v['model_ll'] < best[1]['model_ll']:
                best = (hl, v); flag = '  <-- best'
            print(f'  half-life {hl:>5}d   model log-loss {v["model_ll"]:.4f}   '
                  f'bookmaker {v["book_ll"]:.4f}   n={v["n"]}{flag}')
        hl, v = best
        print(f'\nChosen half-life: {hl} days')
        print(f'  model      log-loss {v["model_ll"]:.4f}   Brier {v["model_brier"]:.4f}')
        print(f'  bookmaker  log-loss {v["book_ll"]:.4f}   Brier {v["book_brier"]:.4f}')
        gap = v['model_ll'] - v['book_ll']
        print(f'  gap to market: {gap:+.4f} log-loss '
              f'({"model is behind the market, as expected" if gap > 0 else "model beats the closing line"})')
    else:
        # Reuse the previously tuned value. The sweep costs minutes and the
        # answer barely moves week to week, so a scheduled refresh should not
        # pay for it every time.
        prev = json.loads(OUT.read_text()) if OUT.exists() else {}
        hl = prev.get('half_life', DEFAULT_HALF_LIFE)
        v = prev.get('validation', {})
        print(f'\nUsing stored half-life: {hl:.0f} days  (pass --tune to re-tune)')

    model = fit(matches, half_life_days=hl)

    # Level check. A model can score well on 1X2 while being badly wrong about
    # how many goals are scored, because scaling both sides together barely
    # moves win/draw/loss. Clean sheets are exactly what that breaks, and clean
    # sheets are most of a defender's FPL value — so check the level explicitly.
    recent = [m for m in matches if m['season'] == matches[-1]['season']]
    act_g = np.mean([m['hg'] + m['ag'] for m in recent])
    act_csh = np.mean([m['ag'] == 0 for m in recent])
    act_csa = np.mean([m['hg'] == 0 for m in recent])
    pg, pcsh, pcsa = [], [], []
    for m in recent:
        if m['home'] not in model['atk'] or m['away'] not in model['atk']:
            continue
        M, lam, mu = score_matrix(model, m['home'], m['away'])
        pg.append(lam + mu)
        pcsh.append(M[:, 0].sum())
        pcsa.append(M[0, :].sum())
    print(f'\nLevel check on {matches[-1]["season"]}:')
    print(f'{"":22}{"actual":>9}{"model":>9}{"ratio":>8}')
    for lbl, a, p in (('goals per match', act_g, np.mean(pg)),
                      ('home clean sheets', act_csh, np.mean(pcsh)),
                      ('away clean sheets', act_csa, np.mean(pcsa))):
        r = p / a if a else float('nan')
        warn = '   <-- OFF' if abs(r - 1) > 0.10 else ''
        print(f'{lbl:<22}{a:>9.3f}{p:>9.3f}{r:>8.2f}{warn}')

    pa, pdf = promoted_prior(matches)
    model['promoted_prior'] = {'atk': pa, 'dfn': pdf}
    model['validation'] = v

    print(f'\nHome advantage: {model["home_adv"]:+.3f} log-goals '
          f'(x{np.exp(model["home_adv"]):.3f})   low-score rho {model["rho"]:+.3f}')
    print(f'Promoted-club prior: attack {pa:+.3f}, defence {pdf:+.3f}\n')

    print(f"{'team':<6}{'attack':>9}{'defence':>9}   (higher attack = scores more, "
          f"higher defence = concedes less)")
    for t in sorted(model['teams'], key=lambda t: -(model['atk'][t] + model['dfn'][t])):
        print(f"{t:<6}{model['atk'][t]:>+9.3f}{model['dfn'][t]:>+9.3f}")

    OUT.write_text(json.dumps(model, indent=1))
    print(f'\nwrote {OUT}')
