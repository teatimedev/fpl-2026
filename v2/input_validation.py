"""Reject incomplete account and forecast inputs before publishing advice."""
import math


def validate_public_picks(payload, expected_gw=None):
    picks = payload.get('picks') or []
    ids = [p.get('element') for p in picks]
    positions = [p.get('position') for p in picks]
    bank = (payload.get('entry_history') or {}).get('bank')
    if (len(picks) != 15 or len(set(ids)) != 15
            or any(type(i) is not int or i <= 0 for i in ids)
            or sorted(positions, key=lambda p: -1 if p is None else p) != list(range(1, 16))):
        raise ValueError('Public picks must contain 15 unique players and positions 1–15')
    if not isinstance(bank, (int, float)) or not math.isfinite(bank) or bank < 0:
        raise ValueError('Public picks have no valid bank balance')
    for flag in ('is_captain', 'is_vice_captain'):
        if sum(bool(p.get(flag)) for p in picks) != 1:
            raise ValueError(f'Public picks have no unique {flag}')
    armbands = [next(p for p in picks if p.get(flag)) for flag in ('is_captain', 'is_vice_captain')]
    if armbands[0]['element'] == armbands[1]['element'] or any(p['position'] > 11 for p in armbands):
        raise ValueError('Captain and vice must be distinct starting players')
    if expected_gw is not None and payload.get('entry_history', {}).get('event') != expected_gw:
        raise ValueError('Public picks belong to a different gameweek')


def validate_history(history, previous_gw):
    rows = history.get('current')
    if not isinstance(rows, list) or not isinstance(history.get('chips'), list):
        raise ValueError('FPL transfer history is incomplete')
    events = [row.get('event') for row in rows]
    if any(type(event) is not int or not 1 <= event <= 38 for event in events):
        raise ValueError('FPL transfer history has invalid gameweeks')
    if (not events or len(set(events)) != len(events)
            or previous_gw not in events):
        raise ValueError('FPL transfer history has not reached the previous deadline')
    if any(type(row.get('event_transfers')) is not int
           or row['event_transfers'] < 0 for row in rows):
        raise ValueError('FPL transfer history has missing transfer counts')
    if any(c.get('name') not in {'wildcard', 'freehit', 'bboost', '3xc'}
           or type(c.get('event')) is not int or not 1 <= c['event'] <= 38
           for c in history['chips']):
        raise ValueError('FPL chip history is incomplete or invalid')
    if len({c['event'] for c in history['chips']}) != len(history['chips']):
        raise ValueError('FPL chip history contains more than one chip in a gameweek')
    if set(range(min(events), previous_gw + 1)) - set(events):
        raise ValueError('FPL transfer history has missing gameweeks')


def validate_forecast_inputs(players, bootstrap):
    """No fresh prices/status may be mixed into an older player forecast."""
    elements = {e['id']: e for e in bootstrap['elements']}
    changes = []
    for pid, player in players.items():
        e = elements.get(pid)
        if e is None:
            changes.append(f'{player["name"]}: missing from FPL')
            continue
        if (abs(float(player['price']) - e['now_cost'] / 10) > 1e-6
                or player['status'] != e['status']
                or (player.get('news') or '') != (e.get('news') or '')
                or player.get('chance') != e.get('chance_of_playing_next_round')):
            changes.append(player['name'])
    if changes:
        raise ValueError('Forecast inputs changed; rebuild before recommending: '
                         + ', '.join(changes[:8]))
