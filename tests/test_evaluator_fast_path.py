"""The fast evaluate_week path must equal the explicit autosub enumeration."""
import random

import pytest

from tests import test_squad_evaluator as fixtures
from v2 import squad_evaluator as se


def explicit_week(squad, gw):
    """The pre-2026-09-23 computation: search lineup, then enumerate states."""
    lineup = se.pick_lineup(squad, gw)
    xi = sum(se.gw_points(p, gw) for p in lineup.xi)
    cap = se.gw_points(lineup.captain, gw) + (
        (1 - se.play_probability(lineup.captain, gw)) * se.gw_points(lineup.vice, gw))
    keeper = next(p for p in lineup.xi if p['pos'] == 'GKP')
    reserve = next(p for p in lineup.bench if p['pos'] == 'GKP')
    auto = (1 - se.play_probability(keeper, gw)) * se.gw_points(reserve, gw)
    auto += se._expected_outfield_autosubs(lineup, gw)
    return xi, cap, auto


@pytest.mark.parametrize('seed', range(12))
def test_fast_week_total_equals_explicit_enumeration(seed):
    fixture = fixtures.SquadRuleTests()
    fixture.setUp()
    rng = random.Random(seed)
    for player in fixture.squad:
        play = rng.choice([0., .05, .3, .6, .9, 1.])
        player['play_by_gw'] = [play]
        player['proj_by_gw'] = [play * rng.uniform(0, 9)]
    week = se.evaluate_week(fixture.squad, 1)
    xi, cap, auto = explicit_week(fixture.squad, 1)
    assert week.xi_points == pytest.approx(xi, abs=1e-9)
    assert week.captain_points == pytest.approx(cap, abs=1e-9)
    assert week.autosub_points == pytest.approx(auto, abs=1e-9)
    assert week.total == pytest.approx(xi + cap + auto, abs=1e-9)
