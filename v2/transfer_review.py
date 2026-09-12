"""Affordable alternatives and transparent decision boundaries for EVERY holding.

Scenarios vary one mechanism while using the same legal XI/captain/autosub
evaluator. They are sensitivity tests, not probabilities or fitted downgrades.
"""
from copy import deepcopy

try:
    from .squad_evaluator import evaluate_squad
except ImportError:
    from squad_evaluator import evaluate_squad

GOAL_POINTS = {'GKP': 6, 'DEF': 6, 'MID': 5, 'FWD': 4}


def stress_player(player, view, gw, horizon, attack_drop=0.0, start_drop=0.0):
    p = deepcopy(player)
    for week in range(gw, horizon + 1):
        i = week - 1
        if i >= len(p['proj_by_gw']):
            continue
        if attack_drop:
            fixtures = view.get(p['team'], {}).get(str(week), [])
            volume = sum(f['xg'] / 1.45 for f in fixtures)
            minutes = (p.get('mins_by_gw') or [p.get('mins_proj', 0)] * horizon)[i]
            # Exported minutes are a GW total; attacking volume already sums
            # fixtures, so use the per-fixture mean duration here.
            minutes /= max(len(fixtures), 1)
            attack = (p.get('xg90', 0) * GOAL_POINTS[p['pos']] + 3 * p.get('xa90', 0)) \
                * minutes / 90 * volume * p.get('calibration_k', 1)
            p['proj_by_gw'][i] = max(0, p['proj_by_gw'][i] - attack_drop * attack)
        if start_drop:
            # A conservative benching scenario: the lost probability mass
            # becomes non-appearance. All point components/minutes shrink with
            # play probability; squad evaluator then recomputes auto-sub cover.
            old = (p.get('play_by_gw') or [p.get('start_rate', 1)] * horizon)[i]
            start = (p.get('start_by_gw') or [p.get('start_rate', 1)] * horizon)[i]
            new = max(0, old - min(start, start_drop))
            ratio = new / old if old else 0
            p['proj_by_gw'][i] *= ratio
            for key in ('play_by_gw', 'mins_by_gw'):
                if p.get(key) and i < len(p[key]):
                    p[key][i] *= ratio
            if p.get('start_by_gw') and i < len(p['start_by_gw']):
                p['start_by_gw'][i] = max(0, start - start_drop)
    return p


def paired_stress(squad, outgoing, incoming, view, gw, horizon, hit=0):
    """A symmetric grid: test target optimism as well as incumbent pessimism."""
    keep = [p for p in squad if p['id'] != outgoing['id']]
    rows = []
    for old_drop in (0, .1, .2):
        before = evaluate_squad(keep + [stress_player(outgoing, view, gw, horizon, old_drop)], gw, horizon).total
        for new_drop in (0, .1, .2):
            after = evaluate_squad(keep + [stress_player(incoming, view, gw, horizon, new_drop)], gw, horizon).total
            rows.append(dict(outgoing_drop=old_drop, incoming_drop=new_drop, net=round(after-before-hit, 2)))
    return rows


def review(squad, players, bank, ft, gw, horizon, view, sell_prices=None):
    sells = sell_prices or {}
    ids = {p['id'] for p in squad}
    base = evaluate_squad(squad, gw, horizon).total
    base_now = evaluate_squad(squad, gw, gw).total
    hit = 4 * max(0, 1 - ft)
    rows = []
    for outgoing in squad:
        keep = [p for p in squad if p['id'] != outgoing['id']]
        counts = {}
        for p in keep:
            counts[p['team']] = counts.get(p['team'], 0) + 1
        affordable = [p for p in players.values() if p['id'] not in ids
                      and p['pos'] == outgoing['pos'] and p.get('status') != 'u'
                      and counts.get(p['team'], 0) < 3
                      and p['price'] <= bank + sells.get(outgoing['id'], outgoing['price']) + 1e-8]
        scored = [(evaluate_squad(keep + [p], gw, horizon).total, p) for p in affordable]
        if not scored:
            rows.append(dict(player_id=outgoing['id'], replacement=None, scenarios=[])); continue
        total, target = max(scored, key=lambda x: x[0])
        scenarios = []
        for label, attack, start in [('Current model', 0, 0),
                                     ('10% lower attacking output', .1, 0),
                                     ('20% lower attacking output', .2, 0),
                                     ('20 percentage points less chance of playing', 0, .2)]:
            stressed = stress_player(outgoing, view, gw, horizon, attack, start)
            before = evaluate_squad(keep + [stressed], gw, horizon).total
            scenarios.append(dict(label=label, attack_drop=attack, start_drop=start,
                                  net=round(total - before - hit, 2)))
        # Smallest tested attacking-output reduction that beats the fixed hold
        # by more than hit cost. This says nothing about the value of waiting.
        flip = next((s['attack_drop'] for s in scenarios[:3] if s['net'] > 0), None)
        rows.append(dict(player_id=outgoing['id'], replacement=target['id'],
                         net=round(total - base - hit, 2),
                         now_net=round(evaluate_squad(keep + [target], gw, gw).total - base_now - hit, 2),
                         sell_price=sells.get(outgoing['id']), buy_price=target['price'],
                         attack_flip_tested=flip, scenarios=scenarios,
                         paired_scenarios=paired_stress(squad, outgoing, target, view, gw, horizon, hit)))
    rows.sort(key=lambda r: -(r.get('net') if r.get('net') is not None else -1e9))
    return dict(gw=gw, horizon=horizon, players=rows,
                method='Best affordable single replacement per holding; fixed hold, optimal legal XI/captain and auto-sub cover, net of hits.',
                caveat='Scenarios are hypotheses, not measured decline. They hold the incoming player unchanged and do not value future information. The multi-week planner compares acting now with waiting.',
                threshold_status='The legacy 2-point-per-move threshold is a heuristic, not a validated edge.')
