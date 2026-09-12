import sqlite3

import pytest

from v2 import weekly
from v2.decision_state import public_selling_prices
from tests.test_input_validation import picks


def history(first, last, transfers=None, chips=None):
    return dict(current=[dict(event=g, event_transfers=(transfers or {}).get(g, 0))
                         for g in range(first, last + 1)], chips=chips or [])


@pytest.mark.parametrize('first,last,transfers,chips,expected', [
    (1, 1, {}, [], 1), (1, 3, {}, [], 3), (10, 10, {10: 12}, [], 1),
    (10, 11, {}, [], 2), (10, 12, {11: 1}, [], 2),
    (1, 8, {}, [], 5),
    (10, 12, {12: 12}, [dict(name='wildcard', event=12)], 2),
    (10, 12, {12: 12}, [dict(name='freehit', event=12)], 2),
])
def test_transfer_counts_start_at_the_entrys_first_deadline(first, last, transfers, chips, expected):
    assert weekly.infer_free_transfers(history(first, last, transfers, chips), last + 1) == expected


def test_weekly_uses_the_restored_squad_after_a_free_hit(monkeypatch):
    permanent, temporary = picks(), picks()
    permanent['entry_history']['bank'] = 3
    permanent['entry_history']['event'] = 3
    temporary['entry_history']['event'] = 4
    for p in temporary['picks']:
        p['element'] += 100
    h = history(1, 4, {4: 12}, [dict(name='freehit', event=4)])
    def api(path):
        if path.endswith('history/'): return h
        return temporary if '/event/4/' in path else permanent
    monkeypatch.setattr(weekly, 'api', api)
    state = weekly.load_squad(123, {}, 5)
    assert state['ids'] == list(range(1, 16))
    assert state['picks_gw'] == 3
    assert state['bank'] == .3
    assert state['ft'] == 3


def test_late_joiners_initial_prices_are_unknown_instead_of_assumed_gw1(tmp_path):
    path = tmp_path / 'prices.db'
    with sqlite3.connect(path) as cx:
        cx.execute('CREATE TABLE gw_stat(code, season, round, price, kickoff)')
        cx.execute("INSERT INTO gw_stat VALUES (101,'2026/27',1,55,'2026-08-01')")
    elements = {1: dict(code=101, now_cost=58)}
    prices, unknown = public_selling_prices([1], elements, [], history(1, 3), path)
    assert prices == {1: 5.6} and unknown == []
    prices, unknown = public_selling_prices([1], elements, [], history(3, 3), path)
    assert prices == {} and unknown == [1]
    transfers = [dict(time='2026-09-01', event=3, element_out=2, element_in=1, element_in_cost=57)]
    prices, unknown = public_selling_prices([1], elements, transfers, history(3, 3), path)
    assert prices == {1: 5.7} and unknown == []


def test_a_missing_recorded_purchase_price_cannot_fall_back_to_gw1(tmp_path):
    path = tmp_path / 'prices.db'
    with sqlite3.connect(path) as cx:
        cx.execute('CREATE TABLE gw_stat(code, season, round, price, kickoff)')
        cx.execute("INSERT INTO gw_stat VALUES (101,'2026/27',1,55,'2026-08-01')")
    transfers = [dict(time='2026-09-01', event=3, element_out=2, element_in=1, element_in_cost=None)]
    prices, unknown = public_selling_prices([1], {1: dict(code=101, now_cost=58)}, transfers, history(1, 3), path)
    assert prices == {} and unknown == [1]
