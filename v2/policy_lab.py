"""Controlled policy and forced-transfer comparisons on one frozen forecast.

Run python -m v2.policy_lab after weekly.py. This is a shadow experiment;
neither sentiment scenarios nor the preferred buffer become production policy.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v2'))
from planner import plan
from v2.decision_state import atomic_json, forecast_id
from v2.transfer_review import stress_player


def score(path):
    return path.get('total_unrounded', path['total'])


def compare_buffers(margin, moves):
    return [dict(buffer=b, required=round(b*moves, 3),
                 decision='act' if moves > 0 and margin > 0 and margin >= b*moves else 'hold')
            for b in (0, .1, .25, .5, 1, 2)]


def stress_existing():
    """Re-optimise BOTH act and wait in each hypothetical football world."""
    output = ROOT / 'data/policy_lab.json'
    result = json.loads(output.read_text())
    projection = ROOT / 'v2/projections_v2.json'
    if result['forecast_id'] != forecast_id(projection):
        raise ValueError('Rebuild the baseline experiment for this forecast')
    now = datetime.now(timezone.utc)
    if now >= datetime.fromisoformat(result['deadline'].replace('Z', '+00:00')):
        raise ValueError('Experiment deadline has passed')
    players = {p['id']: p for p in json.loads(projection.read_text())['players']}
    view = json.loads((ROOT / 'v2/season_view.json').read_text())['view']
    account = result['account']; owned = account['ids']
    kw = dict(bank=account['bank'], ft=account['ft'], gw=result['gw'], horizon=result['horizon'],
              sell_prices={int(k):v for k,v in account['sell_prices'].items()}, time_limit=15)
    for case in result['cases']:
        scenarios = []
        for target_drop in (0, .2):
            world = dict(players)
            old, new = case['outgoing'], case['incoming']
            world[old] = stress_player(players[old], view, result['gw'], result['horizon'], .2)
            world[new] = stress_player(players[new], view, result['gw'], result['horizon'], target_drop)
            hold = plan(world, owned, **kw, freeze_this_week=True)
            act = plan(world, owned, **kw, first_week_squad=[new if i == old else i for i in owned])
            row = dict(outgoing_drop=.2, incoming_drop=target_drop, status='solved' if hold and act else 'unavailable')
            if hold and act:
                row.update(act_vs_wait=round(score(act)-score(hold), 4), hold_solver=hold['solver'], act_solver=act['solver'])
            scenarios.append(row)
            print(players[old]['name'], row, flush=True)
        case['replanned_scenarios'] = scenarios
    result['generated'] = now.isoformat()
    atomic_json(output, result)
    atomic_json(ROOT / 'data/history/policy_lab' / (now.strftime('%Y%m%dT%H%M%S%fZ')+'.json'), result)


def run():
    weekly = json.loads((ROOT / 'data/weekly.json').read_text())
    path = ROOT / 'v2/projections_v2.json'
    raw = json.loads(path.read_text())
    identity = forecast_id(path)
    if identity != weekly.get('forecast_id') or raw['start_gw'] != weekly['gw']:
        raise ValueError('Weekly account and forecast must match before the experiment')
    now = datetime.now(timezone.utc)
    if now >= datetime.fromisoformat(weekly['deadline'].replace('Z', '+00:00')):
        raise ValueError('Cannot freeze a policy experiment after the deadline')
    players = {p['id']: p for p in raw['players']}
    state = weekly['squad']
    owned = state['ids']
    if state.get('selling_prices_unknown') or any(str(i) not in state.get('sell_prices', {}) for i in owned):
        raise ValueError('Resolve unknown selling prices before comparing policies')
    sells = {int(i): v for i, v in state['sell_prices'].items()}
    kw = dict(bank=state['bank'], ft=state['ft'], gw=weekly['gw'], horizon=weekly['horizon'],
              sell_prices=sells, time_limit=30)
    def solve(label, **extra):
        result = plan(players, owned, **{**kw, **extra})
        if result is None: raise ValueError(f'No feasible solution for {label}')
        print(label, round(score(result), 3), result['solver'], flush=True)
        return result
    hold = solve('hold_now', freeze_this_week=True)
    act = solve('flexible_now')
    margin = score(act) - score(hold)
    moves = len(act['weeks'][0]['in'])
    extra = solve('hold_with_one_extra_ft', freeze_this_week=True, ft=min(5, state['ft']+1)) if state['ft'] < 5 else hold
    cases = []
    for name in ('Thiago', 'Kluivert'):
        outgoing = next((p['id'] for p in players.values()
                         if p['name'] == name and p['id'] in owned), None)
        if outgoing is None:
            continue
        row = next(r for r in weekly['transfer_review']['players'] if r['player_id'] == outgoing)
        target = row['replacement']
        if target is None: continue
        chosen = [target if i == outgoing else i for i in owned]
        forced = solve(name + '_single_now', first_week_squad=chosen)
        cases.append(dict(outgoing=outgoing, incoming=target,
                          static_window_gain=row['net'], next_gw_gain=row['now_net'],
                          act_vs_wait=round(score(forced)-score(hold), 4),
                          plan=forced))
    result = dict(version=1, generated=now.isoformat(), deadline=weekly['deadline'],
                  gw=weekly['gw'], horizon=weekly['horizon'], forecast_id=identity,
                  account=dict(ids=owned, bank=state['bank'], ft=state['ft'], sell_prices=sells),
                  act_vs_wait=round(margin, 4), moves=moves,
                  extra_ft_value=round(score(extra)-score(hold), 4),
                  thresholds=compare_buffers(margin, moves), cases=cases,
                  plans=dict(hold=hold, act=act, extra_ft=extra),
                  status='shadow',
                  note='Act versus wait already includes transfer banking and hit costs. A further per-move buffer is an unvalidated uncertainty allowance, not another free-transfer cost.',
                  limitation='Static prices and forecasts; linear bench approximation selects paths, then the same legal XI/captain/autosub evaluator scores each. Solver bounds apply to that approximation. Differences are not realised gains or confidence intervals.')
    atomic_json(ROOT / 'data/policy_lab.json', result)
    atomic_json(ROOT / 'data/history/policy_lab' / (now.strftime('%Y%m%dT%H%M%S%fZ')+'.json'), result)
    return result


if __name__ == '__main__':
    stress_existing() if '--stress' in sys.argv else run()
