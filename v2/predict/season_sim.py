"""Simulate the whole 2026/27 Premier League season.

The team layer is v2's fitted Dixon-Coles model (v2/season_view.json). The one
thing that model does not carry is uncertainty about ITSELF: it hands over a
single attack and defence number per club as if they were known. They are not.
Three separate sources of doubt are measured from the project's own data and
added on top, redrawn once per simulated season:

  * genuine year-to-year drift in a club's true strength (atk sd 0.111,
    dfn sd 0.099, measured over 51 consecutive club-seasons after stripping
    single-season estimation noise);
  * estimation error in the production fit itself, bootstrapped per club
    (mean atk sd 0.093, dfn sd 0.094 -- much larger for Sunderland and Leeds,
    who have one season of top-flight record);
  * for Coventry and Hull, who have no Premier League record at all, the
    measured spread of the nine promoted club-seasons in the database around
    the promoted prior (atk sd 0.158, dfn sd 0.119). Sunderland came up last
    year and defended better than the league average; Southampton came up and
    were 0.70 below on attack. That range is real and must be in the sim.

Without this the sim answers "how do the fitted ratings play out", which
overstates every favourite. With it, it answers the question actually asked.
"""
from pathlib import Path as _P
import os as _os, sys as _sys
_HERE = _P(__file__).resolve().parent
OUT = _HERE / '_out'
OUT.mkdir(exist_ok=True)
ROOTDIR = _HERE.parents[1]
_os.chdir(ROOTDIR)
_sys.path.insert(0, str(_HERE))
_sys.path.insert(0, str(_HERE.parent))
import json
import sqlite3
import sys
import numpy as np
from pathlib import Path

ROOT = Path('.')
SV = json.load(open('v2/season_view.json'))
SE = json.load(open(OUT / 'rating_se.json'))

# Measured one-year drift (yoy.py). But the production fit's evidence is
# centred on 26 Jun 2025 at a 280-day half-life, and the season being predicted
# is centred on 10 Jan 2027 -- a 1.54-year horizon, not one year. Strength
# drifts like a random walk, so the one-year figure scales by sqrt(1.54).
DRIFT_HORIZON = 1.54
DRIFT_A, DRIFT_D = 0.111 * DRIFT_HORIZON ** 0.5, 0.099 * DRIFT_HORIZON ** 0.5
# Residual calibration. Even after the above, the simulated realised table is
# less spread than the four real ones in the database (sd 15.9 vs 17.7): an
# independent-match model cannot produce a side that gels into a 90-point
# machine or one that collapses to 16. Fitted to close that gap, and flagged
# as fitted -- it is the same kind of applied calibration v2 already uses on
# player levels, not something derived.
RESIDUAL = 1.20
PROMO_A, PROMO_D = 0.158, 0.119          # measured, promoted.py
NEW_MANAGER = {'BOU', 'CHE', 'CRY', 'FUL', 'IPS', 'LIV', 'MCI', 'NEW', 'NFO', 'TOT'}
MANAGER_INFLATE = 1.40                   # judgement: a new manager widens drift
NO_RECORD = {'COV', 'HUL'}
IPS_PRIOR_W = 0.6                        # season_view blends Ipswich 60/40

TEAMS = sorted(SV['atk'])
IX = {t: i for i, t in enumerate(TEAMS)}
NT = len(TEAMS)
ATK = np.array([SV['atk'][t] for t in TEAMS])
DFN = np.array([SV['dfn'][t] for t in TEAMS])
HA, RHO = SV['home_adv'], SV['rho']

def sigmas():
    sa, sd = np.zeros(NT), np.zeros(NT)
    for t, i in IX.items():
        if t in NO_RECORD:
            sa[i], sd[i] = PROMO_A * RESIDUAL, PROMO_D * RESIDUAL
        else:
            da, dd = DRIFT_A, DRIFT_D
            if t in NEW_MANAGER:
                da, dd = da * MANAGER_INFLATE, dd * MANAGER_INFLATE
            ea = SE['atk_se'].get(t, 0.13)
            ed = SE['dfn_se'].get(t, 0.13)
            sa[i] = np.hypot(da, ea) * RESIDUAL
            sd[i] = np.hypot(dd, ed) * RESIDUAL
            if t == 'IPS':      # part promoted prior, part own thin record
                sa[i] = np.hypot(sa[i] * (1 - IPS_PRIOR_W), PROMO_A * IPS_PRIOR_W)
                sd[i] = np.hypot(sd[i] * (1 - IPS_PRIOR_W), PROMO_D * IPS_PRIOR_W)
    return sa, sd

def fixtures():
    cx = sqlite3.connect('v2/fpl.db')
    short = {r[0]: r[1] for r in cx.execute('SELECT id, short FROM team')}
    rows = cx.execute('SELECT team_h, team_a FROM fixture '
                      'WHERE event IS NOT NULL ORDER BY event, id').fetchall()
    cx.close()
    h = np.array([IX[short[a]] for a, _ in rows])
    a = np.array([IX[short[b]] for _, b in rows])
    return h, a

def sample_dc(lam, mu, rho, rng):
    """Exact Dixon-Coles draws by rejection on the tau correction.

    tau only reweights the four low-score cells, so independent Poisson draws
    accepted with probability tau/max(tau) sample the joint distribution
    exactly. Acceptance runs about 80% at rho = -0.10.
    """
    M = 1.0 + abs(rho) * np.maximum(lam * mu, 1.0).max()
    h = rng.poisson(lam)
    a = rng.poisson(mu)
    todo = np.ones(h.shape, bool)
    for _ in range(60):
        t = np.ones(h.shape)
        m = (h == 0) & (a == 0); t[m] = 1 - lam[m] * mu[m] * rho
        m = (h == 0) & (a == 1); t[m] = 1 + lam[m] * rho
        m = (h == 1) & (a == 0); t[m] = 1 + mu[m] * rho
        m = (h == 1) & (a == 1); t[m] = 1 - rho
        acc = rng.random(h.shape) < np.clip(t, 0, None) / M
        todo = todo & ~acc
        if not todo.any():
            break
        h[todo] = rng.poisson(lam[todo])
        a[todo] = rng.poisson(mu[todo])
    return h, a

def run(n_sims=60000, batch=4000, seed=20260820, param_noise=True):
    rng = np.random.default_rng(seed)
    hi, ai = fixtures()
    NF = len(hi)
    sa, sd = sigmas()
    Mh = np.zeros((NF, NT)); Mh[np.arange(NF), hi] = 1
    Ma = np.zeros((NF, NT)); Ma[np.arange(NF), ai] = 1

    pts = np.zeros((n_sims, NT), np.int16)
    gf = np.zeros((n_sims, NT), np.int16)
    ga = np.zeros((n_sims, NT), np.int16)
    cs = np.zeros((n_sims, NT), np.int16)     # clean sheets, for the player layer
    # the club's DRAWN attack strength relative to its point estimate. This is
    # what a teammate's output should correlate with -- not the club's realised
    # goal count, which already contains that same player's own Poisson noise.
    tvol = np.ones((n_sims, NT), np.float32)
    done = 0
    while done < n_sims:
        b = min(batch, n_sims - done)
        if param_noise:
            A = ATK + rng.normal(0, sa, (b, NT))
            D = DFN + rng.normal(0, sd, (b, NT))
        else:
            A = np.tile(ATK, (b, 1)); D = np.tile(DFN, (b, 1))
        tvol[done:done + b] = np.exp(A - ATK).astype(np.float32)
        lam = np.exp(A[:, hi] - D[:, ai] + HA)
        mu = np.exp(A[:, ai] - D[:, hi])
        np.clip(lam, 1e-4, 12, out=lam); np.clip(mu, 1e-4, 12, out=mu)
        h, a = sample_dc(lam, mu, RHO, rng)
        hp = (3 * (h > a) + (h == a)).astype(np.float32)
        ap = (3 * (a > h) + (h == a)).astype(np.float32)
        pts[done:done + b] = (hp @ Mh + ap @ Ma).astype(np.int16)
        gf[done:done + b] = (h.astype(np.float32) @ Mh
                             + a.astype(np.float32) @ Ma).astype(np.int16)
        ga[done:done + b] = (a.astype(np.float32) @ Mh
                             + h.astype(np.float32) @ Ma).astype(np.int16)
        cs[done:done + b] = ((a == 0).astype(np.float32) @ Mh
                             + (h == 0).astype(np.float32) @ Ma).astype(np.int16)
        done += b
    return pts, gf, ga, cs, tvol

def table(pts, gf, ga, rng=None):
    """Rank each simulated season: points, then goal difference, then goals
    scored -- the real Premier League tiebreakers, with a coin toss last."""
    rng = rng or np.random.default_rng(1)
    gd = gf.astype(np.int32) - ga.astype(np.int32)
    key = (pts.astype(np.float64) * 1e6 + gd * 1e3 + gf
           + rng.random(pts.shape))
    order = np.argsort(-key, axis=1)
    pos = np.empty_like(order)
    np.put_along_axis(pos, order, np.arange(1, pts.shape[1] + 1)[None, :].repeat(
        pts.shape[0], 0), axis=1)
    return pos

if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60000
    noise = '--no-noise' not in sys.argv
    pts, gf, ga, cs, _ = run(n, param_noise=noise)
    pos = table(pts, gf, ga)

    rows = []
    for t, i in IX.items():
        rows.append(dict(
            team=t, pts=pts[:, i].mean(), pts_sd=pts[:, i].std(),
            p5=np.percentile(pts[:, i], 5), p95=np.percentile(pts[:, i], 95),
            gf=gf[:, i].mean(), ga=ga[:, i].mean(),
            posmean=pos[:, i].mean(),
            title=(pos[:, i] == 1).mean(),
            top4=(pos[:, i] <= 4).mean(),
            top5=(pos[:, i] <= 5).mean(),
            top6=(pos[:, i] <= 6).mean(),
            rel=(pos[:, i] >= 18).mean(),
            bottom=(pos[:, i] == 20).mean(),
        ))
    rows.sort(key=lambda r: -r['pts'])
    print(f'\n2026/27 simulated {n:,} times'
          f'{"" if noise else "  [point-estimate ratings, no parameter noise]"}\n')
    hdr = (f"{'':<4}{'team':<6}{'pts':>6}{'±':>6}{'5-95%':>12}"
           f"{'GF':>6}{'GA':>6}{'pos':>6}{'title':>8}{'top4':>8}{'top6':>8}{'rel':>8}")
    print(hdr); print('-' * len(hdr))
    for k, r in enumerate(rows, 1):
        print(f"{k:<4}{r['team']:<6}{r['pts']:>6.1f}{r['pts_sd']:>6.1f}"
              f"{int(r['p5']):>7}-{int(r['p95']):<4}"
              f"{r['gf']:>6.1f}{r['ga']:>6.1f}{r['posmean']:>6.1f}"
              f"{r['title']*100:>7.1f}%{r['top4']*100:>7.1f}%"
              f"{r['top6']*100:>7.1f}%{r['rel']*100:>7.1f}%")
    json.dump(rows, open(OUT / 'league.json', 'w'), indent=1)

    print('\nsigma used (attack / defence uncertainty per club):')
    sa, sd = sigmas()
    for t in sorted(IX, key=lambda t: -sa[IX[t]]):
        print(f'  {t}  ±{sa[IX[t]]:.3f} atk  ±{sd[IX[t]]:.3f} dfn')
