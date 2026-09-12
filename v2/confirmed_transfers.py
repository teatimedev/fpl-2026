"""Deadline-scoped user reports of moves hidden by the public picks API.

This records planning inputs only. It never submits transfers or claims that
the account's current lineup has been read from the authenticated site.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_suffix('.json')


def apply_confirmed_transfers(state, players, gw, path=DEFAULT_PATH):
    path = Path(path)
    if not path.exists():
        return state
    report = json.loads(path.read_text())
    if report.get('entry_id') != state.get('entry_id') or report.get('gw') != gw:
        return state
    expiry = datetime.fromisoformat(report['expires_at'].replace('Z', '+00:00'))
    if expiry.tzinfo is None:
        raise ValueError('Confirmed transfers need an explicit expiry timezone')
    if datetime.now(timezone.utc) >= expiry:
        return state
    if state.get('picks_gw') != report.get('base_picks_gw'):
        raise ValueError('Confirmed transfers do not match the public picks baseline')
    stamp = datetime.fromisoformat(report['confirmed_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc) or stamp >= expiry:
        raise ValueError('Confirmed transfers need a valid past confirmation time')
    out = deepcopy(state)
    bank = round(state['bank'] * 10)
    rows, changes = [], []
    for move in report['transfers']:
        old, new = move['out'], move['in']
        if old not in out['ids'] or new in out['ids'] or new not in players:
            raise ValueError('Confirmed transfer conflicts with the public squad')
        if players[old]['pos'] != players[new]['pos']:
            raise ValueError('Confirmed transfer has different player positions')
        sale, purchase = move['out_cost'], move['in_cost']
        if any(type(c) is not int or c <= 0 for c in (sale, purchase)):
            raise ValueError('Confirmed transfer prices must be positive integer tenths')
        bank += sale - purchase
        out['ids'] = [new if i == old else i for i in out['ids']]
        changes.append(f"{players[old]['name']} → {players[new]['name']}")
        rows.append(dict(element_out=old, element_in=new, element_out_cost=sale,
                         element_in_cost=purchase, event=gw, time=report['confirmed_at']))
    if bank < 0 or any(sum(players[i]['team'] == team for i in out['ids']) > 3
                       for team in {players[i]['team'] for i in out['ids']}):
        raise ValueError('Confirmed transfers produce an invalid budget or club count')
    # These reports currently support ordinary permanent moves only.
    out.update(bank=bank / 10, ft=max(0, state['ft'] - len(rows)), lineup=None,
               confirmed_at=report['confirmed_at'], changes=changes,
               confirmed_transfers=rows,
               public_baseline=dict(ids=state['ids'], bank=state['bank'], ft=state['ft'],
                                    gw=state['picks_gw']),
               account_basis=report['basis'],
               source=state['source'] + f'; user-confirmed GW{gw} transfers')
    return out
