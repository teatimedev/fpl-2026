from unittest.mock import patch

import pytest

from tests import test_squad_evaluator as fixtures
from v2 import chips


def squad_fixture(last_gw=1):
    fixture = fixtures.SquadRuleTests()
    fixture.setUp()
    for i, p in enumerate(fixture.squad):
        p.update(team=f'C{i // 3}', price=5., by_gw=p['proj_by_gw'] * last_gw,
                 proj_by_gw=p['proj_by_gw'] * last_gw, play_by_gw=[1.] * last_gw)
    return fixture.squad


def test_bench_boost_subtracts_cover_that_would_already_score_without_the_chip():
    squad = squad_fixture()
    squad[0].update(proj_by_gw=[4.], by_gw=[4.], play_by_gw=[.5])
    squad[1].update(proj_by_gw=[5.], by_gw=[5.])
    ordinary, boost, _ = chips.your_week(squad, 1)
    assert ordinary == 83.5
    assert boost == 5.5  # All 15 plus captain = 89; ordinary already gets 2.5 cover.


def test_triple_captain_includes_the_vice_fallback():
    squad = squad_fixture()
    for p in squad:
        p.update(proj_by_gw=[1.], by_gw=[1.])
    squad[7].update(proj_by_gw=[5.], by_gw=[5.], play_by_gw=[.5])
    squad[8].update(proj_by_gw=[6.], by_gw=[6.])
    gain, captain = chips.triple_captain_gain(squad, 1)
    assert gain == 8.  # 5 + .5*6, not the highest individual mean of 6.
    assert captain['id'] == squad[7]['id']


def test_triple_captain_selection_matches_scalar_enumeration():
    import random
    from research.lineup_oracle import enumerate_lineups
    from v2.lineup_search import search
    from v2.squad_evaluator import captain_options
    squad = squad_fixture()
    rng = random.Random(914)
    for p in squad:
        play = rng.uniform(.1, 1)
        value = play * rng.uniform(-1, 10)
        p.update(proj_by_gw=[value], by_gw=[value], play_by_gw=[play])
    oracle = max(score + captain_options(lineup.xi, 1)[0]['bonus']
                 for score, lineup in enumerate_lineups(squad, 1))
    assert search(squad, 1, captain_copies=2)[0] == pytest.approx(oracle, abs=1e-9)


def test_free_hit_keeps_owned_price_gains_but_cannot_spend_them_on_a_replacement():
    squad = squad_fixture()
    squad[0]['price'] = 6.
    prices = {p['id']: 5. for p in squad}
    prices[squad[0]['id']] = 4.
    incoming = dict(squad[0], id='new', team='NEW', price=8., by_gw=[30.], proj_by_gw=[30.])
    players = {p['id']: p for p in [*squad, incoming]}
    for bank in (0., 2.):
        total, xi, _ = chips.best_possible_week(players, 1, sum(prices.values()) + bank,
                                              owned=list(prices), sell_prices=prices)
        assert total > 0
        assert incoming['id'] not in {p['id'] for p in xi}
    _, xi, _ = chips.best_possible_week(players, 1, sum(prices.values()) + 4.,
                                      owned=list(prices), sell_prices=prices)
    assert incoming['id'] in {p['id'] for p in xi}


def test_an_infeasible_free_hit_does_not_publish_a_solver_score():
    players = {p['id']: p for p in squad_fixture()}
    with pytest.raises(ValueError, match='did not complete'):
        chips.best_possible_week(players, 1, 1.)


def evaluate_fixture(gw, last_gw, windows, used=None, **kwargs):
    squad = squad_fixture(last_gw)
    players = {p['id']: p for p in squad}
    with patch.object(chips, 'doubles_and_blanks', return_value=({}, {})), \
            patch.object(chips, 'best_possible_week', return_value=(100., [], None)):
        return chips.evaluate(players, list(players), 0., gw, last_gw, windows, used or {},
                              sell_prices={p['id']: p['price'] for p in squad}, **kwargs)


def test_only_one_chip_can_be_recommended_even_at_the_expiry_deadline():
    result = evaluate_fixture(19, 19, {key: [(1, 19)] for key in chips.NAMES}, wc_now=50.)
    assert sum(c['play'] for c in result['chips'].values()) == 1
    assert result['chips']['wildcard']['play']


def test_second_free_hit_cannot_follow_the_first_in_consecutive_gameweeks():
    result = evaluate_fixture(20, 21, {'freehit': [(2, 19), (20, 38)]}, used={'freehit': [19]})
    assert [g for g, _ in result['chips']['freehit']['weeks']] == [21]
    assert result['chips']['freehit']['now'] is None


def test_late_entries_cannot_use_transfer_chips_in_their_first_gameweek():
    result = evaluate_fixture(10, 11, {key: [(2, 19)] for key in chips.NAMES}, first_gw=10)
    assert [g for g, _ in result['chips']['freehit']['weeks']] == [11]
    assert result['chips']['wildcard']['play'] is False


def test_missing_chip_calendar_is_not_replaced_with_assumed_old_rules():
    with pytest.raises(ValueError, match='unavailable'):
        chips.chip_windows({'chips': []})


def test_an_entirely_blank_gameweek_is_present_in_the_fixture_outlook():
    doubles, blanks = chips.doubles_and_blanks(
        boot={'teams': [dict(id=1, short_name='A'), dict(id=2, short_name='B')], 'events': [dict(id=1)]},
        fixtures=[])
    assert doubles == {}
    assert blanks == {1: ['A', 'B']}


def test_future_chip_weeks_use_the_planned_squad_not_todays():
    squad = squad_fixture(3)
    players = {p['id']: p for p in squad}
    # The plan swaps the weakest-positioned last player for a strong one by GW3.
    newcomer = dict(squad[-1], id='new', team='NEW', proj_by_gw=[9.] * 3, by_gw=[9.] * 3,
                    play_by_gw=[1.] * 3)
    players['new'] = newcomer
    planned = [p['id'] for p in squad[:-1]] + ['new']
    windows = {key: [(1, 19)] for key in chips.NAMES}
    kw = dict(sell_prices={p['id']: p['price'] for p in squad})
    with patch.object(chips, 'doubles_and_blanks', return_value=({}, {})), \
            patch.object(chips, 'best_possible_week', return_value=(100., [], None)):
        today = chips.evaluate(players, [p['id'] for p in squad], 0., 2, 3, windows, {}, **kw)
        path = chips.evaluate(players, [p['id'] for p in squad], 0., 2, 3, windows, {},
                              path={3: planned}, **kw)
    assert path['gaps']['2'] == today['gaps']['2']      # this week: today's squad
    assert path['gaps']['3'] < today['gaps']['3']       # GW3: the stronger planned squad
    assert 'selected transfer plan' in path['method']


def test_planned_squads_reads_path_weeks():
    path = {'weeks': [{'gw': 6, 'squad': list(range(15))}, {'gw': 7, 'squad': [1, 2]}]}
    assert chips.planned_squads(path, []) == {6: list(range(15))}
    assert chips.planned_squads(None, []) == {}


def test_wildcard_waits_for_a_clearly_better_planned_week():
    windows = {key: [(1, 19)] for key in chips.NAMES}
    later = evaluate_fixture(5, 8, windows, wc_weeks={5: 22., 6: 15., 7: 30.})
    assert later['chips']['wildcard']['play'] is False
    assert later['chips']['wildcard']['best_gw'] == 7
    assert 'GW7' in later['chips']['wildcard']['advice']
    now = evaluate_fixture(5, 8, windows, wc_weeks={5: 29., 6: 15., 7: 30.})
    assert now['chips']['wildcard']['play'] is True
    assert now['chips']['wildcard']['now'] == 29.
