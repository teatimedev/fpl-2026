import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
from weekly import price_watch


def boot(total=None, selected='10.0'):
    return {'total_players': total, 'elements': [
        {'id': 1, 'transfers_in_event': 100, 'transfers_out_event': 0,
         'selected_by_percent': selected},
        {'id': 2, 'transfers_in_event': 0, 'transfers_out_event': 200,
         'selected_by_percent': selected},
    ]}


def test_missing_or_rounded_zero_ownership_cannot_create_infinite_pressure():
    players = {1: {'id': 1}, 2: {'id': 2}}
    for source in (boot(), boot(0), boot(float('nan')), boot(1000, '0.0')):
        rises, falls = price_watch(source, players, set())
        assert rises[0]['p']['id'] == 1 and falls[0]['p']['id'] == 2
        assert rises[0]['pressure'] is None and falls[0]['pressure'] is None


def test_actual_owner_count_is_used_and_rises_never_include_sellers():
    rises, falls = price_watch(boot(10000), {1: {'id': 1}, 2: {'id': 2}}, set())
    assert len(rises) == len(falls) == 1
    assert rises[0]['pressure'] == .1
    assert falls[0]['pressure'] == -.2
