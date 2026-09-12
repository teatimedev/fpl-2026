import copy
from urllib.error import HTTPError

import pytest
from v2.input_validation import validate_public_picks, validate_history, validate_forecast_inputs
from v2 import weekly


def picks():
    return {'picks': [dict(element=i, position=i, is_captain=i == 1,
                          is_vice_captain=i == 2) for i in range(1, 16)],
            'entry_history': {'bank': 0}}


def test_missing_bank_duplicate_players_and_duplicate_positions_fail():
    validate_public_picks(picks())
    for mutate in (lambda p: p.pop('entry_history'),
                   lambda p: p['picks'][1].update(element=1),
                   lambda p: p['picks'][1].update(position=1)):
        payload = picks()
        mutate(payload)
        with pytest.raises(ValueError):
            validate_public_picks(payload)


def test_weekly_outage_cannot_fall_back_to_an_old_squad_or_assume_one_ft(monkeypatch):
    calls = []
    def fail(path):
        calls.append(path)
        raise HTTPError(path, 403, 'Unavailable', {}, None)
    monkeypatch.setattr(weekly, 'api', fail)
    with pytest.raises(HTTPError):
        weekly.load_squad(123, {}, 4)
    assert len(calls) == 1
    monkeypatch.setattr(weekly, 'api', lambda path: picks() if 'picks' in path else fail(path))
    with pytest.raises(HTTPError):
        weekly.load_squad(123, {}, 4)


def test_incomplete_history_does_not_invent_transfer_carry():
    history = {'current': [dict(event=i, event_transfers=0) for i in [1, 2, 3]], 'chips': []}
    validate_history(history, 3)
    for events in [[1, 2], [1, 3], [1, 2, 3, 3]]:
        with pytest.raises(ValueError):
            validate_history({'current': [dict(event=i, event_transfers=0) for i in events], 'chips': []}, 3)


def test_live_chance_and_prices_must_match_forecast():
    player = dict(name='Player', price=5, status='d', chance=75, news='Fitness test')
    element = dict(id=1, now_cost=50, status='d', chance_of_playing_next_round=75, news='Fitness test')
    validate_forecast_inputs({1: player}, {'elements': [element]})
    for change in [dict(now_cost=51), dict(chance_of_playing_next_round=25), dict(news='Out')]:
        updated = copy.deepcopy(element)
        updated.update(change)
        with pytest.raises(ValueError, match='rebuild'):
            validate_forecast_inputs({1: player}, {'elements': [updated]})
