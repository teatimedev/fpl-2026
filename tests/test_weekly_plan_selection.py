"""Regression cases for approximate planner results driving hold advice."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
from weekly import choose_plan, first_action_candidates


def path(score, n):
    return dict(total=round(score, 1), total_unrounded=score,
                weeks=[{'in': list(range(n)), 'out': list(range(10, 10+n))}])


def test_hold_is_feasible_even_when_unconstrained_proxy_scores_worse():
    hold = path(405.04, 0)
    selected, baseline, rows = choose_plan([('free', path(403.6, 3))], hold)
    assert selected is baseline is hold
    assert rows[0]['gain'] < 0


def test_qualifying_single_survives_a_larger_package_below_its_buffer():
    hold, single, package = path(100, 0), path(102.1, 1), path(103.9, 3)
    chosen, _, rows = choose_plan([('package', package), ('single', single)], hold)
    assert chosen is single
    assert [r['qualifies'] for r in rows] == [False, True]


def test_sub_threshold_rounding_does_not_trigger_a_transfer():
    hold = path(100, 0)
    chosen, _, rows = choose_plan([('single', path(101.96, 1))], hold)
    assert chosen is hold
    assert not rows[0]['qualifies']


def test_better_no_move_path_improves_the_hold_baseline():
    hold, better = path(100, 0), path(101, 0)
    chosen, baseline, _ = choose_plan([('free', better), ('single', path(102.5, 1))], hold)
    assert chosen is baseline is better


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
