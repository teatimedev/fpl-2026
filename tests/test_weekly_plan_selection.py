"""Nominal fallback selection and the honest alternatives table."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
from weekly import annotate_vs_hold, choose_plan, first_action_candidates, vs_hold_lines


def path(score, n, objective=None):
    p = dict(total=round(score, 1), total_unrounded=score,
             weeks=[{'in': list(range(n)), 'out': list(range(10, 10+n))}])
    if objective is not None:
        p['objective'] = objective
    return p


def test_hold_is_feasible_even_when_unconstrained_proxy_scores_worse():
    hold = path(405.04, 0)
    selected, baseline, rows = choose_plan([('free', path(403.6, 3))], hold)
    assert selected is baseline is hold
    assert rows[0]['gain'] < 0


def test_no_per_move_buffer_best_objective_wins():
    hold, single, package = path(100, 0), path(102.1, 1), path(103.9, 3)
    chosen, _, rows = choose_plan([('package', package), ('single', single)], hold)
    assert chosen is package
    assert [r['qualifies'] for r in rows] == [True, False]


def test_the_decision_objective_outranks_window_points():
    # More window points but less value carried past the window loses.
    hold = path(100, 0, objective=150)
    churn = path(101, 1, objective=149)
    chosen, _, _ = choose_plan([('churn', churn)], hold)
    assert chosen is hold


def test_ties_prefer_fewer_moves():
    hold = path(100, 0)
    chosen, _, rows = choose_plan([('single', path(100, 1))], hold)
    assert chosen is hold
    assert not rows[0]['qualifies']


def test_better_no_move_path_improves_the_hold_baseline():
    hold, better = path(100, 0), path(101, 0)
    chosen, baseline, rows = choose_plan([('free', better), ('single', path(100.5, 1))], hold)
    assert chosen is baseline is better
    assert rows[1]['gain'] < 0


def test_failed_candidate_is_visible_without_discarding_other_actions():
    move = path(104, 1)
    chosen, _, rows = choose_plan([('timeout', None), ('single', move)], path(100, 0))
    assert chosen is move
    assert rows[0]['status'] == 'no feasible result'


def test_candidates_include_original_advice_and_deduplicate():
    ids = list(range(1, 16))
    single = dict(out={'id': 8}, in_={'id': 16}, xi_gain=3)
    rows = first_action_candidates(ids, {'singles': [single], 'pairs': []}, [(8, 16)])
    assert len(rows) == 1
    assert 8 not in rows[0][1] and 16 in rows[0][1]


def test_alternatives_are_expressed_against_the_hold_path():
    ids = list(range(1, 16))
    after = frozenset([i for i in ids if i != 8] + [16])
    nominal = {frozenset(ids): 500.0, after: 501.75}
    transfers = dict(singles=[dict(out=8, in_=16, gain=9.7, net=9.7),
                              dict(out=9, in_=17, gain=3.0, net=3.0)],
                     pairs=[dict(out=[8, 9], in_=[16, 17], gain=11.0, net=7.0)])
    sampled = dict(rows=[dict(key=after, gain=1.2)])
    annotate_vs_hold(transfers, ids, nominal, sampled)
    top = transfers['singles'][0]
    assert top['gain'] == 9.7                       # the static diagnostic is unchanged
    assert top['vs_hold_nominal'] == 1.75           # ...but the decision basis is shown
    assert top['vs_hold'] == 1.2
    assert transfers['singles'][1]['vs_hold'] is None
    assert transfers['singles'][1]['vs_hold_nominal'] is None
    assert transfers['pairs'][0]['vs_hold'] is None
    assert 'no further' in transfers['gain_basis']


def test_digest_restates_tested_moves_against_holding():
    players = {i: {'name': f'P{i}'} for i in range(1, 20)}
    transfers = dict(singles=[dict(out=8, in_=16, vs_hold=-1.1, vs_hold_nominal=1.0),
                              dict(out=9, in_=17, vs_hold=None, vs_hold_nominal=0.2),
                              dict(out=10, in_=18, vs_hold=None, vs_hold_nominal=None)],
                     pairs=[])
    line = vs_hold_lines(transfers, players)[0]
    assert 'P8→P16 -1.1' in line
    assert 'P9→P17 +0.2 (unsampled)' in line
    assert 'P10' not in line
    assert vs_hold_lines(dict(singles=[], pairs=[]), players) == []
