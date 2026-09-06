"""Deadline-frozen one-week policy comparisons. No future refits or hindsight picks."""
from datetime import datetime

try:
    from .squad_evaluator import pick_lineup, apply_autosubs, captain_replacement
except ImportError:
    from squad_evaluator import pick_lineup, apply_autosubs, captain_replacement


def freeze(players, squad, gw, elements, review=None, ft=1, plan=None):
    def policy(label, selected, points=None, hit=0):
        lineup = pick_lineup(selected, gw, **({'points': points} if points else {}))
        return dict(label=label, squad=[p['id'] for p in selected],
                    xi=[p['id'] for p in lineup.xi], bench=[p['id'] for p in lineup.bench],
                    captain=lineup.captain['id'], vice=lineup.vice['id'], hit=hit)
    rows = [policy('model_hold', squad)]
    # Do not silently replace a missing official benchmark with our own model.
    if all(elements.get(p['id'], {}).get('ep_next') not in (None, '') for p in squad):
        rows.append(policy('official_ep_next_hold', squad,
                           lambda p, _: float(elements[p['id']]['ep_next'])))
    options = [r for r in (review or {}).get('players', []) if r.get('replacement') is not None]
    if options:
        top = max(options, key=lambda r: r['net'])
        changed = [players[top['replacement']] if p['id'] == top['player_id'] else p for p in squad]
        rows.append(policy('best_single_static_window', changed, hit=4 * max(0, 1-ft)))
        for name in ('Thiago', 'Kluivert'):
            case = next((r for r in options if players[r['player_id']]['name'] == name), None)
            if case:
                changed = [players[case['replacement']] if p['id'] == case['player_id'] else p for p in squad]
                rows.append(policy('case_' + name.lower() + '_single', changed, hit=4 * max(0, 1-ft)))
    if plan and plan.get('weeks'):
        week = plan['weeks'][0]
        ids = {p['id'] for p in squad} - set(week['out']) | set(week['in_'])
        moves = len(week['in_'])
        margin = plan.get('diff_unrounded', plan['diff'])
        if len(ids) == 15 and all(i in players for i in ids):
            for buffer in (0, .1, .25, .5, 1, 2):
                act = moves > 0 and margin > 0 and margin >= buffer*moves
                rows.append(policy(f'planner_buffer_{buffer:g}', [players[i] for i in sorted(ids)] if act else squad,
                                   hit=4*week['hits'] if act else 0))
    return rows


def grade_policies(snapshot, actual):
    deadline = snapshot.get('deadline')
    generated = snapshot.get('generated')
    if not deadline or not generated or datetime.fromisoformat(generated.replace('Z', '+00:00')) \
            >= datetime.fromisoformat(deadline.replace('Z', '+00:00')):
        return []
    players = {p['id']: p for p in snapshot['players']}
    points = {int(pid): row[0] for pid, row in actual['points'].items()}
    played = {int(pid): row[1] > 0 for pid, row in actual['points'].items()}
    results = []
    for policy in snapshot.get('policy_benchmarks', []):
        if any(pid not in points or pid not in players for pid in policy['squad']):
            continue
        scoring = apply_autosubs([players[i] for i in policy['xi']],
                                [players[i] for i in policy['bench']], played)
        captain = captain_replacement(policy['captain'], policy['vice'], played)
        score = sum(points[i] for i in scoring.scoring_ids) + points.get(captain, 0) - policy['hit']
        results.append(dict(policy=policy['label'], points=score, hit=policy['hit']))
    return results
