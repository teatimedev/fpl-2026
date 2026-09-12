"""Empirical playing-time distributions; experimental, not live forecasts.

The caller supplies only prior evidence and a separately fitted positional
prior. Outcomes distinguish starts/cameos and the 60-minute scoring boundary.
No filesystem, clock, account, or present-day player metadata is read here.
"""
import math

POSITIONS = ('GKP', 'DEF', 'MID', 'FWD')


def fit_priors(rows):
    priors = {}
    for pos in POSITIONS:
        subset = [r for r in rows if r['pos'] == pos and r['starts'] is not None]
        starters = [r['minutes'] for r in subset if r['starts']]
        nonstarts = [r['minutes'] for r in subset if not r['starts']]
        cameos = [m for m in nonstarts if m > 0]
        if not starters or not nonstarts:
            raise ValueError(f'Insufficient recorded training outcomes for {pos}')
        priors[pos] = {
            'p_cameo': (len(cameos) + .5) / (len(nonstarts) + 1),
            'p60_start': (sum(m >= 60 for m in starters) + .5) / (len(starters) + 1),
            'p60_cameo': (sum(m >= 60 for m in cameos) + .5) / (len(cameos) + 1),
            'start_minutes': sum(starters) / len(starters),
            'cameo_minutes': sum(cameos) / len(cameos) if cameos else 25.,
            'n_starts': len(starters), 'n_nonstarts': len(nonstarts),
            'n_cameos': len(cameos),
        }
    return priors


def estimate(prior, evidence=(), strength=8., half_life=12.):
    """Shrink weighted observations towards a frozen prior.

    evidence: (fixtures_ago, started, minutes), with unknown labels omitted.
    Ages count all earlier fixtures, including those with unavailable labels.
    """
    if strength <= 0 or half_life <= 0:
        raise ValueError('Evidence strength and half-life must be positive')
    weighted = [(0.5 ** (age / half_life), bool(start), minutes)
                for age, start, minutes in evidence if start is not None]
    starts = [(w, m) for w, started, m in weighted if started]
    nonstarts = [(w, m) for w, started, m in weighted if not started]
    cameos = [(w, m) for w, m in nonstarts if m > 0]

    def blend(rows, baseline, transform):
        return (strength * baseline + sum(w * transform(m) for w, m in rows)) / (
            strength + sum(w for w, _ in rows))

    return {
        'p_cameo': blend(nonstarts, prior['p_cameo'], lambda m: m > 0),
        'p60_start': blend(starts, prior['p60_start'], lambda m: m >= 60),
        'p60_cameo': blend(cameos, prior['p60_cameo'], lambda m: m >= 60),
        'start_minutes': blend(starts, prior['start_minutes'], float),
        'cameo_minutes': blend(cameos, prior['cameo_minutes'], float),
    }


def scoring_probabilities(model, p_start):
    """Coherent P(appearance), P60 and expected minutes under the mixture."""
    if not math.isfinite(p_start) or not 0 <= p_start <= 1:
        raise ValueError('Starting probability must be finite and within [0,1]')
    cameo = (1 - p_start) * model['p_cameo']
    return dict(p_play=p_start + cameo,
                p60=p_start * model['p60_start'] + cameo * model['p60_cameo'],
                minutes=p_start * model['start_minutes'] + cameo * model['cameo_minutes'])
