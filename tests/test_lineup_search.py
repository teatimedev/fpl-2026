import random
import json
from pathlib import Path

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


def test_simulator_and_browser_fixtures_use_the_same_selected_lineup():
    from v2.decision_sim import _pick_lineup
    from v2.squad_evaluator import evaluate_week
    path = Path(__file__).resolve().parents[1] / 'app/tests/fixtures/lineup-parity.json'
    for case in json.loads(path.read_text())['cases']:
        squad, expected = case['squad'], case['expected']
        value = evaluate_week(squad, 1)
        assert value.total == pytest.approx(expected['score'], abs=1e-9)
        pars = [dict(id=p['id'], pos=p['pos'], proj=p['proj_by_gw'],
                     p_play=[0.], p_play_gw=p['play_by_gw']) for p in squad]
        lineup = _pick_lineup(pars, 0, 4)
        for key in ['xi', 'bench']:
            assert [squad[i]['id'] for i in lineup[key]] == expected[key]
        for key in ['captain', 'vice']:
            assert squad[lineup[key]]['id'] == expected[key]


@pytest.mark.parametrize('field,value', [('proj_by_gw', float('nan')),
                                       ('play_by_gw', float('inf'))])
def test_invalid_keeper_values_do_not_become_valid_lineups(field, value):
    fixture = fixtures.SquadRuleTests()
    fixture.setUp()
    fixture.squad[0][field] = [value]
    with pytest.raises(ValueError, match='finite'):
        search(fixture.squad, 1)
