"""
Threshold sweep: what HOLD_THRESHOLD would have been optimal under noise.

HOLD_THRESHOLD = 2.0 in v2/weekly.py:83 (and the 2.0-per-move churn bar in
worth_rebuilding, weekly.py:90) is hand-set: "a free transfer worth less
than this over the window is usually better banked". This harness replaces
that guess with a synthetic sensitivity check. It is a DECISION-RULE EXPERIMENT, not a
pipeline replay: no squads, players, fixtures or prices are simulated, only
the act-or-bank decision itself, so its numbers rank thresholds against
each other and must not be read as forecasts of actual points.

The experiment. Each of T weeks (default 38) offers one candidate transfer
with true gain g ~ 0.70*N(0.5, 1.5) + 0.20*N(4, 2) + 0.10*N(9, 3), floored
at -2, capped at 12 (measured: mean 2.05, sd 3.12). The decision-maker
sees g~ = g + N(0, sigma) and follows a single rule: act iff the NET
observed gain clears the threshold -- g~ when a free transfer is in hand,
g~ - 4 when the act would be a hit. Banking is modelled exactly as FPL
does (weekly.py MAX_FT = 5): every ordinary week adds one FT after transfers
are spent, capped at five. With only one candidate per week and FT0 >= 1,
this experiment NEVER needs a hit. It therefore cannot measure the option
value of saving transfers for multi-transfer packages or injury shocks.

The threshold applies to net gain after any real hit. With the default
initial bank and at most one candidate per week there are no hits; tests
also cover an explicitly empty initial bank, when the first move can cost 4.

Synthetic noise sensitivity. --sigma auto (the default) solves numerically, by
bisection on common random numbers (n=400k), for the sigma whose
observed-vs-true Spearman correlation on this mixture hits 0.46 -- the
previously reported player rank correlation. That lands at sigma=5.16
(achieved rho 0.460). Player rank error does not identify transfer-gain
error: this mapping is hypothetical, not an empirical calibration.

Audit correction, 6 September 2026: the previous implementation omitted
the following week's FT after acting, creating fictitious hits. Its claim
that a 2-point threshold was validated is withdrawn. At zero noise, zero
is optimal in this one-candidate toy. Production threshold changes require
historical decision replay with real squads, multiple moves and forecast
errors; this experiment alone cannot justify changing the live policy.

What this does NOT capture: price moves between weeks (a rising target
changes the gain distribution week to week), injury/news shocks that force
transfers regardless of any threshold, planner re-solve effects (acting
changes the squad and therefore next week's candidate set; here
opportunities are i.i.d. draws), the collapse of a 15-man squad decision
into one candidate per week, and the mixture itself, which is stylized
rather than fitted to logged transfer EVs. Player-forecast rank noise is
mapped 1:1 onto transfer-EV noise, which is an approximation.
"""
import argparse

import numpy as np

# True-gain mixture: (weight, mean, sd) per branch.
MIXTURE = ((0.70, 0.5, 1.5),   # small upgrades
           (0.20, 4.0, 2.0),   # real fixes
           (0.10, 9.0, 3.0))   # big fixes
GAIN_FLOOR, GAIN_CAP = -2.0, 12.0
BANK_CAP = 5        # weekly.py MAX_FT: banked free transfers cap
HIT_COST = 4.0      # points paid for an extra transfer
FT0 = 1             # a season starts with one free transfer
SPEARMAN_TARGET = 0.46   # the model's measured rank correlation
CALIB_N = 400_000
CALIB_SEED = 11
THRESHOLDS = np.round(np.arange(0.0, 6.0 + 1e-9, 0.25), 2)
AUDITED_HOLD = 2.0  # weekly.py HOLD_THRESHOLD, the number under test


def draw_gains(rng, shape):
    """Sample true transfer gains from the mixture, floored/capped."""
    u = rng.random(shape)
    w1, w2 = MIXTURE[0][0], MIXTURE[0][0] + MIXTURE[1][0]
    mu = np.where(u < w1, MIXTURE[0][1], np.where(u < w2, MIXTURE[1][1], MIXTURE[2][1]))
    sd = np.where(u < w1, MIXTURE[0][2], np.where(u < w2, MIXTURE[1][2], MIXTURE[2][2]))
    return np.clip(rng.normal(mu, sd), GAIN_FLOOR, GAIN_CAP)


def _avg_ranks(x):
    """Average ranks with ties shared, vectorised (no scipy dependency)."""
    x = np.asarray(x, float)
    order = np.argsort(x, kind='mergesort')
    sx = x[order]
    new_group = np.concatenate(([True], sx[1:] != sx[:-1]))
    gid = np.cumsum(new_group) - 1
    cnt = np.bincount(gid)
    last_rank = np.cumsum(cnt)              # 1-based rank of each group's last member
    group_avg = last_rank - (cnt - 1) / 2.0  # mean of its consecutive ranks
    r = np.empty(x.size)
    r[order] = group_avg[gid]
    return r


def spearman(x, y):
    return float(np.corrcoef(_avg_ranks(x), _avg_ranks(y))[0, 1])


def derive_sigma(target=SPEARMAN_TARGET, n=CALIB_N, seed=CALIB_SEED, tol=1e-4):
    """Solve numerically for sigma s.t. Spearman(g, g + sigma*z) ~= target.

    Bisection on common random numbers: one fixed (g, z) sample, so the
    achieved correlation is monotone in sigma and the result is exactly
    reproducible for a given seed.
    """
    rng = np.random.default_rng(seed)
    g = draw_gains(rng, n)
    z = rng.standard_normal(n)
    lo, hi = 0.05, 25.0
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if spearman(g, g + mid * z) > target:
            lo = mid
        else:
            hi = mid
    sigma = 0.5 * (lo + hi)
    return sigma, spearman(g, g + sigma * z)


def play_season(g_true, g_obs, threshold, ft0=FT0):
    """One season of the policy, scalar reference implementation.

    Returns (total, acts, fts): realised points, acted week indices, and
    the free-transfer count at every week boundary (fts[0] = start).
    """
    ft = float(ft0)
    total = 0.0
    acts = []
    fts = [ft]
    for t in range(len(g_true)):
        hit = ft < 1.0
        if g_obs[t] - (HIT_COST if hit else 0.0) >= threshold:
            total += g_true[t] - (HIT_COST if hit else 0.0)
            acts.append(t)
            ft = max(0.0, ft - 1.0)
        ft = min(BANK_CAP, ft + 1.0)
        fts.append(ft)
    return total, acts, fts


def simulate_totals(g, z, sigma, thresholds=None, ft0=FT0, cap=BANK_CAP):
    """Vectorised play_season over many seasons; common random numbers.

    g, z: (seasons, weeks) arrays of true gains and standard-normal noise;
    the observation is g + sigma*z, so sweeping sigma or the threshold on
    the same draws makes comparisons paired. Row k holds every season's
    total under thresholds[k]. Bit-identical to looping play_season
    (tests/test_threshold_sweep.py checks it).
    """
    if thresholds is None:
        thresholds = THRESHOLDS
    obs = g + sigma * z
    seasons, weeks = g.shape
    out = np.empty((len(thresholds), seasons))
    for k, tau in enumerate(thresholds):
        ft = np.full(seasons, float(ft0))
        total = np.zeros(seasons)
        for t in range(weeks):
            hit = ft < 1.0
            act = (obs[:, t] - np.where(hit, HIT_COST, 0.0)) >= tau
            total += np.where(act, g[:, t] - np.where(hit, HIT_COST, 0.0), 0.0)
            ft = np.minimum(cap, np.maximum(ft - act, 0.0) + 1.0)
        out[k] = total
    return out


def plateau(means, thresholds, max_drop=1.0):
    """Contiguous thresholds within max_drop points of the best mean."""
    best = int(np.argmax(means))
    lo = best
    while lo - 1 >= 0 and means[best] - means[lo - 1] <= max_drop:
        lo -= 1
    hi = best
    while hi + 1 < len(means) and means[best] - means[hi + 1] <= max_drop:
        hi += 1
    return lo, hi


def _fmt_row(tau, mean, p10, std):
    return f"  {tau:4.2f}  {mean:6.2f}  {p10:6.2f}  {std:6.2f}"


def main():
    ap = argparse.ArgumentParser(
        description='Sweep hold thresholds for the weekly act-or-bank decision '
                    'under a rank-calibrated noise model. Decision-rule experiment, '
                    'not a pipeline replay.')
    ap.add_argument('--seasons', type=int, default=2000,
                    help='simulated seasons per threshold (default 2000)')
    ap.add_argument('--gw', type=int, default=38, help='gameweeks per season (default 38)')
    ap.add_argument('--sigma', nargs='+', default=['auto'],
                    help="noise s.d.(s); 'auto' derives the sigma whose observed-vs-true "
                         "Spearman on the gain mixture hits 0.46 (default: auto)")
    ap.add_argument('--seed', type=int, default=13, help='RNG seed for the seasons')
    ap.add_argument('--hold', type=float, default=AUDITED_HOLD,
                    help='threshold to audit against the argmax (default 2.0 = '
                         'weekly.py HOLD_THRESHOLD)')
    args = ap.parse_args()

    auto_sigma, auto_rho = derive_sigma()
    print(f"noise calibration (n={CALIB_N}, seed {CALIB_SEED}): "
          f"Spearman target {SPEARMAN_TARGET} -> sigma={auto_sigma:.2f} "
          f"(achieved {auto_rho:.3f})")
    print(f"  reference points on this mixture: sigma=2.3 -> Spearman "
          f"{spearman(*_calib_pair(2.3)):.3f}, 3.5 -> {spearman(*_calib_pair(3.5)):.3f} "
          f"(synthetic sensitivity, not measured transfer-gain error)")
    print()

    sigmas, labels = [], []
    for tok in args.sigma:
        if tok.lower() == 'auto':
            sigmas.append(auto_sigma)
            labels.append(f"{auto_sigma:.2f} (auto, rho {auto_rho:.3f})")
        else:
            sigmas.append(float(tok))
            labels.append(f"{tok}")

    rng = np.random.default_rng(args.seed)
    g = draw_gains(rng, (args.seasons, args.gw))
    z = rng.standard_normal((args.seasons, args.gw))

    results = []
    for sigma, label in zip(sigmas, labels):
        totals = simulate_totals(g, z, sigma)
        means = totals.mean(axis=1)
        p10 = np.percentile(totals, 10, axis=1)
        std = totals.std(axis=1, ddof=1)
        best = int(np.argmax(means))
        lo, hi = plateau(means, THRESHOLDS)
        hold_k = int(np.argmin(np.abs(THRESHOLDS - args.hold)))
        loss = means[best] - means[hold_k]
        results.append((label, THRESHOLDS[best], means[best]))

        print(f"== sigma={label} | {args.seasons} seasons x {args.gw} GW "
              f"| seed {args.seed} ==")
        print("  tau   mean    p10    std")
        for k in range(len(THRESHOLDS)):
            print(_fmt_row(THRESHOLDS[k], means[k], p10[k], std[k]))
        print(f"  argmax tau={THRESHOLDS[best]:.2f} (mean {means[best]:.2f}); "
              f"tau={THRESHOLDS[hold_k]:.2f} loses {loss:.2f} pts/season "
              f"({100.0 * loss / means[best]:.1f}% of the optimum)")
        print(f"  plateau within 1 pt of max: tau {THRESHOLDS[lo]:.2f} to "
              f"{THRESHOLDS[hi]:.2f}")
        print()

    if len(results) == 1:
        label, tau_b, mean_b = results[0]
        on_plateau = THRESHOLDS[lo] <= args.hold <= THRESHOLDS[hi]
        print(f"conclusion: optimal threshold {tau_b:.2f} "
              f"(mean {mean_b:.2f}/season); HOLD_THRESHOLD={args.hold:.1f} "
              f"loses {loss:.2f} pts/season and "
              f"{'sits' if on_plateau else 'does not sit'} on the within-1-point "
              f"plateau. Decision-rule experiment only: no price moves, "
              f"no injury shocks, no planner re-solve effects.")
    else:
        moves = ", ".join(f"{label.split(' ')[0]}->{tau_b:.2f}" for label, tau_b, _ in results)
        span = max(t for _, t, _ in results) - min(t for _, t, _ in results)
        print(f"conclusion: optimum vs sigma: {moves} — "
              f"{'rises' if span > 0 else 'does not move'} with noise "
              f"(span {span:.2f}); decision-rule experiment only: no price moves, "
              f"no injury shocks, no planner re-solve effects.")


_CALIB_CACHE = {}


def _calib_pair(sigma):
    """The calibration sample (g, g + sigma*z), drawn once and cached."""
    if 'g' not in _CALIB_CACHE:
        rng = np.random.default_rng(CALIB_SEED)
        _CALIB_CACHE['g'] = draw_gains(rng, CALIB_N)
        _CALIB_CACHE['z'] = rng.standard_normal(CALIB_N)
    return _CALIB_CACHE['g'], _CALIB_CACHE['g'] + sigma * _CALIB_CACHE['z']


if __name__ == '__main__':
    main()
