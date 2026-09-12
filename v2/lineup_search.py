"""Exhaustive XI/bench search using vectorised independent-appearance states.

Optimises selection conditional on supplied forecasts and independent player
appearances. It does not establish predictive calibration.
"""
from functools import lru_cache
from itertools import combinations, permutations, product

import numpy as np

POSITIONS = ('DEF', 'MID', 'FWD')
MINIMUM = (3, 2, 1)
MAXIMUM = (5, 5, 3)
LABELS = (0,) * 5 + (1,) * 5 + (2,) * 3
MISSING = tuple(product(range(6), range(6), range(4)))


def _replace(missing, counts, sub):
    for absent in (sub, *(p for p in range(3) if p != sub)):
        if missing[absent] == 0:
            continue
        trial = list(counts)
        trial[absent] -= 1
        trial[sub] += 1
        if all(MINIMUM[p] <= trial[p] <= MAXIMUM[p] for p in range(3)):
            after = list(missing)
            after[absent] -= 1
            return tuple(after), tuple(trial)
    return None


@lru_cache(maxsize=27)
def _activation_masks(bench_positions):
    """Seven conditional activation masks: 1 + 2 + 4 prefix-appearance states."""
    counts = tuple(LABELS.count(p) - bench_positions.count(p) for p in range(3))
    masks = []
    for index, current in enumerate(bench_positions):
        for prefix in product((False, True), repeat=index):
            mask = []
            for missing in MISSING:
                if any(m > c for m, c in zip(missing, counts)):
                    mask.append(False)
                    continue
                state = missing, counts
                for previous, played in zip(bench_positions, prefix):
                    if played:
                        state = _replace(*state, previous) or state
                mask.append(_replace(*state, current) is not None)
            masks.append(mask)
    return np.array(masks, dtype=np.int8)


@lru_cache(maxsize=1)
def _template():
    starters, benches, parents, masks = [], [], [], []
    for xi in combinations(range(13), 10):
        counts = tuple(sum(LABELS[i] == p for i in xi) for p in range(3))
        if not all(MINIMUM[p] <= counts[p] <= MAXIMUM[p] for p in range(3)):
            continue
        parent = len(starters)
        starters.append(xi)
        for bench in permutations(i for i in range(13) if i not in xi):
            benches.append(bench)
            parents.append(parent)
            masks.append(_activation_masks(tuple(LABELS[i] for i in bench)))
    selected = np.zeros((len(starters), 13), dtype=bool)
    for index, xi in enumerate(starters):
        selected[index, list(xi)] = True
    return (np.array(starters), np.array(benches), np.array(parents),
            np.array(masks), selected)


def _missing_mass(play, selected):
    distributions = []
    offset = 0
    for size in (5, 5, 3):
        distribution = np.zeros((len(selected), size + 1))
        distribution[:, 0] = 1
        for index in range(offset, offset + size):
            q = (1 - play[index]) * selected[:, index]
            previous = distribution.copy()
            distribution *= (1 - q[:, None])
            distribution[:, 1:] += previous[:, :-1] * q[:, None]
        distributions.append(distribution)
        offset += size
    return (distributions[0][:, :, None, None]
            * distributions[1][:, None, :, None]
            * distributions[2][:, None, None, :]).reshape(len(selected), -1)


@lru_cache(maxsize=512)
def _autosub_coefficients(probabilities):
    """Reusable activation probabilities; independent of prices, IDs and points."""
    play = np.array(probabilities)
    xi, benches, parent, masks, selected = _template()
    mass = _missing_mass(play, selected)
    activation = np.einsum('ij,ikj->ik', mass[parent], masks)
    p0, p1 = play[benches[:, 0]], play[benches[:, 1]]
    probabilities = np.column_stack((np.ones(len(benches)), 1 - p0, p0,
        (1 - p0) * (1 - p1), (1 - p0) * p1, p0 * (1 - p1), p0 * p1))
    weighted = activation * probabilities
    coefficients = np.column_stack((weighted[:, 0], weighted[:, 1:3].sum(1),
                                     weighted[:, 3:7].sum(1)))
    coefficients.flags.writeable = False
    return coefficients


def search(squad, gw, captain_copies=1):
    """Return (best expected total, Lineup), examining all 3,300 legal choices."""
    try:
        from .squad_evaluator import Lineup, gw_points, play_probability
    except ImportError:
        from squad_evaluator import Lineup, gw_points, play_probability

    if captain_copies not in (1, 2):
        raise ValueError('Captain copies must be one ordinarily or two for Triple Captain')

    keepers = [p for p in squad if p['pos'] == 'GKP']
    outfield = [p for pos in POSITIONS for p in squad if p['pos'] == pos]
    if (len(keepers) != 2 or tuple(p['pos'] for p in outfield) !=
            tuple(POSITIONS[index] for index in LABELS)
            or len({p['id'] for p in squad}) != 15):
        raise ValueError('Lineup search requires a complete legal positional squad')
    if not all(np.isfinite(gw_points(p, gw)) and np.isfinite(play_probability(p, gw))
               for p in keepers):
        raise ValueError('Lineup inputs must be finite')
    means = np.array([gw_points(p, gw) for p in outfield])
    play = np.array([play_probability(p, gw) for p in outfield])
    if not np.isfinite(means).all() or not np.isfinite(play).all():
        raise ValueError('Lineup inputs must be finite')
    xi, benches, parent, _, _ = _template()
    coefficients = _autosub_coefficients(tuple(play))
    autosubs = (coefficients * means[benches]).sum(1)
    best = None
    for ki, keeper in enumerate(keepers):
        kmean, kp = gw_points(keeper, gw), play_probability(keeper, gw)
        cap_means = np.column_stack((np.full(len(xi), kmean), means[xi]))
        cap_plays = np.column_stack((np.full(len(xi), kp), play[xi]))
        ranked = np.argsort(-cap_means, axis=1, kind='stable')
        top = np.take_along_axis(cap_means, ranked[:, :2], axis=1)
        vice_means = np.broadcast_to(top[:, :1], cap_means.shape).copy()
        vice_means[np.arange(len(xi)), ranked[:, 0]] = top[:, 1]
        bonuses = cap_means + (1 - cap_plays) * vice_means
        cap = np.argmax(np.where(bonuses >= bonuses.max(1)[:, None] - 1e-12,
                                  cap_means, -np.inf), axis=1)
        bonus = bonuses[np.arange(len(xi)), cap] * captain_copies
        reserve = keepers[1 - ki]
        base = means[xi].sum(1) + kmean + (1 - kp) * gw_points(reserve, gw) + bonus
        scores = base[parent] + autosubs
        # Numerically tied totals prefer the stronger XI/captain, then the
        # highest projected bench order. This avoids arbitrary bench churn
        # when, for example, every starter has P(play)=1.
        tied = np.flatnonzero(scores >= scores.max() - 1e-10)
        xi_captain = means[xi].sum(1) + kmean + bonus
        tie_keys = np.column_stack((xi_captain[parent[tied]], means[benches[tied]]))
        order = np.lexsort(tuple(-tie_keys[:, i] for i in reversed(range(4))))
        winner = int(tied[order[0]])
        score = float(scores[winner])
        key = tuple(float(v) for v in (xi_captain[parent[winner]], *means[benches[winner]]))
        if (best is None or score > best[0] + 1e-10
                or (abs(score - best[0]) <= 1e-10 and key > best_key)):
            chosen = int(parent[winner])
            players = [keeper, *[outfield[i] for i in xi[chosen]]]
            ci = int(cap[chosen])
            vi = int(ranked[chosen, 0] if ranked[chosen, 0] != ci else ranked[chosen, 1])
            lineup = Lineup(players, [reserve, *[outfield[i] for i in benches[winner]]],
                            players[ci], players[vi])
            best = score, lineup
            best_key = key
    return best
