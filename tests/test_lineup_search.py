import random

import pytest

from research.lineup_oracle import enumerate_lineups
from tests import test_squad_evaluator as fixtures
from v2.lineup_search import search


@pytest.mark.parametrize('seed', [1, 9, 18, 42, 71])
def test_vectorized_search_matches_every_scalar_legal_lineup(seed):
    fixture = fixtures.SquadRuleTests()
    fixture.setUp()
    rng = random.Random(seed)
    for player in fixture.squad:
        play = rng.choice([0., .1, .5, .9, 1.])
        player['play_by_gw'] = [play]
        player['proj_by_gw'] = [play * rng.uniform(-1, 10)]
    oracle = max(score for score, _ in enumerate_lineups(fixture.squad, 1))
    actual, lineup = search(fixture.squad, 1)
    assert actual == pytest.approx(oracle, abs=1e-9)
    assert len(lineup.xi) == 11
    assert len({p['id'] for p in lineup.xi + lineup.bench}) == 15


def test_reserve_keeper_covers_a_higher_conditional_value_starter():
    fixture = fixtures.SquadRuleTests()
    fixture.setUp()
    fixture.squad[0].update(proj_by_gw=[4.], play_by_gw=[.5])
    fixture.squad[1].update(proj_by_gw=[5.], play_by_gw=[1.])
    score, lineup = search(fixture.squad, 1)
    assert score == 83.5
    assert lineup.xi[0]['id'] == 'g1'
