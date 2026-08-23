"""Who finishes the season top? Not the same question as who projects highest.

A projection is a mean. The leading FPL scorer is the MAXIMUM of a few hundred
correlated random variables, and the player who wins it is usually the one with
the fattest right tail, not the highest mean. Haaland's points are made of
goals -- Poisson, high variance. Bruno Fernandes' are made of appearances and
defensive contributions -- nearly deterministic once he is on the pitch. Their
means sit two points apart. Their chances of topping the table do not.

Variance, each piece measured rather than assumed:

  * MINUTES. A player who started 28+ games last season averages 26.8 the next,
    sd 9.7 -- three and a half times what a coin-flip model gives, with a 21%
    chance of fewer than 20 starts. Sampled non-parametrically from that
    empirical distribution, so the injury tail keeps its real shape rather than
    a Gaussian one.
  * FORM. Realised xGI/90 moves year to year with log sd 0.38, of which 0.157
    is the part v2's shrinkage cannot predict (measured stability 0.91).
  * THE CLUB. Taken from the league simulation's DRAWN attack and defence
    ratings, not its realised goal counts. Using realised goals would double
    count: for a striker who scores a third of his side's goals, "City scored
    more than expected" and "Haaland scored more than expected" are close to
    the same event, and multiplying the two inflates his spread by half again.
    Clean sheets and goals conceded do come from realised results, because for
    a defender those genuinely are the team's outcome and not his own.

The whole thing is then calibrated against what actually happens: over the four
seasons in the database the top FPL scorer averages 248 points, four or five
players clear 200, the Golden Boot averages 28 goals and two or three players
reach 20. A simulation that does not reproduce those four numbers is not
describing this sport, and the first version did not -- it had the leader at
321 points and nine players past 20 goals.
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
import json, sys, math
import numpy as np
import sqlite3
import season_sim as S

GOAL_PTS = {'GKP': 6, 'DEF': 6, 'MID': 5, 'FWD': 4}
FORM_SD = 0.157
CAMEO_FRAC = 0.28          # a substitute plays ~25 of 90 minutes, not a full match

# Reality, from v2/fpl.db (2022/23 - 2025/26)
TARGET = dict(max_pts=247.8, n200=4.25, max_g=28.25, n20=2.5)

def load_players(noise_scale=1.0, cameo_fix=True):
    comp = json.load(open(OUT / 'components.json'))
    comp = [r for r in comp if r['total'] > 45 and r['status'] != 'u']
    for r in comp:
        # v2 credits a cameo with a FULL start's per-90 exposure: p_play scales
        # the attacking, DefCon and bonus terms but the minutes fraction stays
        # at minutes-per-START. A 25-minute substitute is therefore given a
        # starter's chance of hitting 10 defensive actions. Rescale the cameo
        # slice of those three terms to real substitute minutes.
        if not cameo_fix:
            r['f'] = 1.0
            continue
        pp, sr = r['p_play'], r['start_rate']
        r['f'] = (sr + (pp - sr) * CAMEO_FRAC) / pp if pp > 0 else 0.0
    return comp

def run(comp, noise_scale=1.0, L=1.0, Lg=1.0, n_sims=40000, batch=2500,
        seed=4242, verbose=True):
    """L and Lg are level calibrations, fitted in --tune.

    v2's season projections are too generous in the 150-200 band: 17 players
    project past 170 and 38 past 150, against 7-13 and 16-28 in the four real
    seasons -- and a table of MEANS should be narrower than a realised one, not
    wider. The cause is visible in calibrate(): the per-position multiplier is
    fitted on players who logged 2,000+ minutes last season, a survivor cohort,
    and then applied across a full 38 gameweeks to everyone. The very top of the
    projection is unaffected (5 players past 200, against 4-5 in reality), which
    is why the ORDERING -- the part v2's backtest actually validated -- is left
    alone here and only the level is rescaled.
    """
    NP_ = len(comp)
    # residual around a SHRUNK minutes prediction, sd 0.274 of a season and
    # left-skewed, sampled non-parametrically (minutes_resid.py). Additive, not
    # multiplicative: a player predicted at 0.85 cannot start 120% of the games.
    EMP = np.load(OUT / 'min_resid.npy')

    TEAM = [r['team'] for r in comp]
    ti = np.array([S.IX[t] for t in TEAM])
    f = np.array([r['f'] for r in comp])
    adj = np.array([r.get('adj', 1.0) for r in comp])
    eg = np.array([r['eg'] * r['k'] for r in comp]) * adj * f
    ea = np.array([r['ea'] * r['k'] for r in comp]) * adj * f
    gp = np.array([GOAL_PTS[r['pos']] for r in comp], float)
    p_cs = np.array([r['pts_cs'] for r in comp])
    p_dc = np.array([r['pts_defcon'] for r in comp]) * f
    p_app = np.array([r['pts_appear'] for r in comp])
    p_bon = np.array([r['pts_bonus'] for r in comp]) * f
    p_sav = np.array([r['pts_saves'] for r in comp])
    p_neg = np.array([r['pts_neg'] for r in comp])
    sr0 = np.array([r['start_rate'] for r in comp])
    eg = eg * Lg; ea = ea * Lg
    for arr in ('p_cs', 'p_dc', 'p_app', 'p_bon', 'p_sav', 'p_neg'):
        pass
    p_cs, p_dc, p_app = p_cs * L, p_dc * L, p_app * L
    p_bon, p_sav, p_neg = p_bon * L, p_sav * L, p_neg * L
    base = (eg * gp + ea * 3 + p_cs + p_dc + p_app + p_bon + p_sav - p_neg)

    Lpts, Lgf, Lga, Lcs, Ltv = S.run(n_sims, batch=batch)
    E_ga = Lga.mean(0); E_cs = np.maximum(Lcs.mean(0), 0.5)

    rng = np.random.default_rng(seed)
    acc = dict(win=np.zeros(NP_), top3=np.zeros(NP_), top10=np.zeros(NP_),
               gwin=np.zeros(NP_), gtop3=np.zeros(NP_),
               sp=np.zeros(NP_), sp2=np.zeros(NP_),
               sg=np.zeros(NP_), sg2=np.zeros(NP_),
               p200=np.zeros(NP_), g20=np.zeros(NP_), g25=np.zeros(NP_))
    wp, wg, n200, n20 = [], [], [], []
    done = 0
    while done < n_sims:
        b = min(batch, n_sims - done); sl = slice(done, done + b)
        r = rng.choice(EMP, size=(b, NP_)) * noise_scale
        srr = np.clip(sr0[None, :] + r, 0, 1) / np.maximum(sr0[None, :], 1e-6)
        form = np.exp(rng.normal(0, FORM_SD * noise_scale, (b, NP_)))
        tv = Ltv[sl][:, ti]
        tcs = Lcs[sl][:, ti] / E_cs[ti]
        tga = Lga[sl][:, ti] / E_ga[ti]

        G = rng.poisson(np.clip(eg[None, :] * srr * form * tv, 0, None))
        A = rng.poisson(np.clip(ea[None, :] * srr * form * tv, 0, None))
        ret_exp = np.maximum((eg + ea)[None, :], 0.5)
        bon = p_bon[None, :] * srr * (0.45 + 0.55 * np.clip((G + A) / ret_exp, 0, 4))
        P = (G * gp[None, :] + A * 3.0
             + p_cs[None, :] * srr * tcs
             + p_dc[None, :] * srr * np.exp(rng.normal(0, 0.16 * noise_scale, (b, NP_)))
             + p_app[None, :] * srr
             + bon + p_sav[None, :] * srr
             - p_neg[None, :] * srr * (0.35 + 0.65 * tga))

        o = np.argmax(P, axis=1)
        acc['win'] += np.bincount(o, minlength=NP_)
        t3 = np.argpartition(-P, 3, axis=1)[:, :3]
        acc['top3'] += np.bincount(t3.ravel(), minlength=NP_)
        t10 = np.argpartition(-P, 10, axis=1)[:, :10]
        acc['top10'] += np.bincount(t10.ravel(), minlength=NP_)
        wp.append(P[np.arange(b), o]); n200.append((P >= 200).sum(1))
        Gj = G + rng.random(G.shape) * 0.5
        og = np.argmax(Gj, axis=1)
        acc['gwin'] += np.bincount(og, minlength=NP_)
        g3 = np.argpartition(-Gj, 3, axis=1)[:, :3]
        acc['gtop3'] += np.bincount(g3.ravel(), minlength=NP_)
        wg.append(G[np.arange(b), og]); n20.append((G >= 20).sum(1))
        acc['sp'] += P.sum(0); acc['sp2'] += (P**2).sum(0)
        acc['sg'] += G.sum(0); acc['sg2'] += (G**2).sum(0)
        acc['p200'] += (P >= 200).sum(0); acc['g20'] += (G >= 20).sum(0)
        acc['g25'] += (G >= 25).sum(0)
        done += b
        if verbose: print(f'  {done}/{n_sims}', end='\r')
    if verbose: print(' ' * 30, end='\r')
    wp = np.concatenate(wp); wg = np.concatenate(wg)
    stats = dict(max_pts=wp.mean(), n200=np.concatenate(n200).mean(),
                 max_g=wg.mean(), n20=np.concatenate(n20).mean(),
                 wp=wp, wg=wg)
    return acc, stats, base, n_sims

if __name__ == '__main__':
    comp = load_players()
    print(f'{len(comp)} players in the race\n')
    if '--tune' in sys.argv:
        print(f"{'noise':>6}{'L':>6}{'Lg':>6}{'maxPts':>9}{'n200':>7}"
              f"{'maxG':>7}{'n20':>7}{'err':>8}")
        print(f"{'TARGET':>18}{TARGET['max_pts']:>9.0f}{TARGET['n200']:>7.2f}"
              f"{TARGET['max_g']:>7.1f}{TARGET['n20']:>7.2f}")
        best = None
        for k in (0.7, 0.85, 1.0):
            for L in (0.80, 0.86, 0.92):
                for Lg in (0.72, 0.80, 0.88):
                    _, st, _, _ = run(comp, k, L, Lg, n_sims=8000, verbose=False)
                    err = sum(((st[m] - TARGET[m]) / TARGET[m]) ** 2
                              for m in TARGET) ** .5
                    print(f'{k:>6.2f}{L:>6.2f}{Lg:>6.2f}{st["max_pts"]:>9.0f}'
                          f'{st["n200"]:>7.2f}{st["max_g"]:>7.1f}'
                          f'{st["n20"]:>7.2f}{err:>8.3f}')
                    if best is None or err < best[0]:
                        best = (err, k, L, Lg)
        print(f'\nbest: noise {best[1]:.2f}, level {best[2]:.2f}, '
              f'goal level {best[3]:.2f}  (rms {best[0]:.3f})')
        sys.exit()

    K, L, Lg = (float(x) for x in (sys.argv[1:4] if len(sys.argv) > 3
                                   else (1.0, 1.0, 1.0)))
    acc, st, base, N = run(comp, K, L, Lg)
    print(f"top FPL scorer: {st['max_pts']:.0f} pts "
          f"(5-95%: {np.percentile(st['wp'],5):.0f}-{np.percentile(st['wp'],95):.0f})"
          f"   real four-season mean 248")
    print(f"players over 200: {st['n200']:.1f}   real 4.25")
    print(f"Golden Boot: {st['max_g']:.1f} goals "
          f"(5-95%: {np.percentile(st['wg'],5):.0f}-{np.percentile(st['wg'],95):.0f})"
          f"   real four-season mean 28")
    print(f"players over 20 goals: {st['n20']:.1f}   real 2.5\n")
    res = [dict(name=r['name'], team=r['team'], pos=r['pos'], price=r['price'],
                sel=r['sel'], proj=base[i],
                mean=acc['sp'][i] / N,
                sd=math.sqrt(max(acc['sp2'][i] / N - (acc['sp'][i] / N) ** 2, 0)),
                win=acc['win'][i] / N, top3=acc['top3'][i] / N,
                top10=acc['top10'][i] / N, goals=acc['sg'][i] / N,
                gsd=math.sqrt(max(acc['sg2'][i] / N - (acc['sg'][i] / N) ** 2, 0)),
                gwin=acc['gwin'][i] / N, gtop3=acc['gtop3'][i] / N,
                p200=acc['p200'][i] / N, g20=acc['g20'][i] / N, g25=acc['g25'][i] / N)
           for i, r in enumerate(comp)]
    json.dump(res, open(OUT / 'players.json', 'w'))

    print('=== BEST PLAYER — most FPL points ===')
    print(f"{'':<3}{'player':<15}{'tm':<5}{'pos':<4}{'£':>5}{'own%':>6}"
          f"{'mean':>6}{'±':>5}{'WIN':>7}{'top3':>7}{'top10':>7}{'200+':>6}")
    for k, r in enumerate(sorted(res, key=lambda r: -r['win'])[:20], 1):
        print(f"{k:<3}{r['name']:<15}{r['team']:<5}{r['pos']:<4}{r['price']:>5.1f}"
              f"{r['sel']:>6.1f}{r['mean']:>6.0f}{r['sd']:>5.0f}{r['win']*100:>6.1f}%"
              f"{r['top3']*100:>6.1f}%{r['top10']*100:>6.1f}%{r['p200']*100:>5.0f}%")
    print('\n=== TOP SCORER — most goals ===')
    print(f"{'':<3}{'player':<15}{'tm':<5}{'pos':<4}{'£':>5}"
          f"{'goals':>7}{'±':>5}{'WIN':>7}{'top3':>7}{'20+':>6}{'25+':>6}")
    for k, r in enumerate(sorted(res, key=lambda r: -r['gwin'])[:20], 1):
        print(f"{k:<3}{r['name']:<15}{r['team']:<5}{r['pos']:<4}{r['price']:>5.1f}"
              f"{r['goals']:>7.1f}{r['gsd']:>5.1f}{r['gwin']*100:>6.1f}%"
              f"{r['gtop3']*100:>6.1f}%{r['g20']*100:>5.0f}%{r['g25']*100:>5.0f}%")
