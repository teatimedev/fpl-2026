"""Shadow P(60+ minutes), kept separate from starting probability.

An empirical positional prior plus four prior starts is an explicit experiment,
not a calibrated production rule. Only completed fixtures may enter evidence.
"""
from collections import defaultdict


def fit(evidence, positions, strength=4.0):
    counts = defaultdict(lambda: [0, 0])
    for pid, rows in evidence.items():
        for _, started, mins in rows:
            if started:
                counts[positions[pid]][0] += int(mins >= 60)
                counts[positions[pid]][1] += 1
    out = {}
    for pid, rows in evidence.items():
        starters = [mins for _, started, mins in rows if started]
        yes, total = counts[positions[pid]]
        own_yes = sum(m >= 60 for m in starters)
        # Leave this player's own observations out of the positional prior.
        prior = (yes - own_yes + 1) / (total - len(starters) + 2)
        conditional = (own_yes + strength * prior) / (len(starters) + strength)
        cameos = [mins for _, started, mins in rows if not started and mins > 0]
        out[pid] = dict(conditional_start=conditional,
                       conditional_cameo=sum(m >= 60 for m in cameos) / len(cameos) if cameos else 0.0,
                       n_starts=len(starters), strength=strength)
    return out


def probability(fit_row, p_start, p_cameo_unconditional):
    return max(0, min(p_start + p_cameo_unconditional,
                     p_start * fit_row['conditional_start']
                     + p_cameo_unconditional * fit_row['conditional_cameo']))
