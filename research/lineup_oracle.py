"""Exhaustive lineup reference, independent of production's greedy selection.

Enumerates every legal XI and ordered outfield bench for a supplied squad.
Uses the production expectation equations for scoring, so this checks search
optimality conditional on those equations, not predictive calibration.
"""
from itertools import combinations, permutations
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v2.squad_evaluator import (
    Lineup, XI_MIN, XI_MAX, _expected_outfield_autosubs,
    captain_options, evaluate_week, gw_points, play_probability,
)


def enumerate_lineups(squad, gw):
    keepers = [p for p in squad if p['pos'] == 'GKP']
    outfield = [p for p in squad if p['pos'] != 'GKP']
    for chosen in combinations(outfield, 10):
        counts = {pos: sum(p['pos'] == pos for p in chosen) for pos in ('DEF', 'MID', 'FWD')}
        if not all(XI_MIN[pos] <= counts[pos] <= XI_MAX[pos] for pos in counts):
            continue
        ids = {p['id'] for p in chosen}
        reserves = [p for p in outfield if p['id'] not in ids]
        for keeper in keepers:
            xi = [keeper, *chosen]
            reserve_keeper = next(p for p in keepers if p['id'] != keeper['id'])
            pair = captain_options(xi, gw)[0]
            base = sum(gw_points(p, gw) for p in xi) + pair['bonus']
            base += (1 - play_probability(keeper, gw)) * gw_points(reserve_keeper, gw)
            for bench in permutations(reserves):
                lineup = Lineup(xi, [reserve_keeper, *bench], pair['captain'], pair['vice'])
                yield base + _expected_outfield_autosubs(lineup, gw), lineup


def inspect(squad, gw):
    started = time.perf_counter()
    baseline = evaluate_week(squad, gw)
    scored = list(enumerate_lineups(squad, gw))
    best, lineup = max(scored, key=lambda row: row[0])
    def describe(lineup):
        return dict(xi=[p['name'] for p in lineup.xi], bench=[p['name'] for p in lineup.bench],
                    captain=lineup.captain['name'], vice=lineup.vice['name'])
    return dict(gw=gw, baseline=baseline.total, optimum=best, gain=best - baseline.total,
                candidates=len(scored), elapsed_seconds=time.perf_counter() - started,
                baseline_lineup=describe(baseline.lineup), optimum_lineup=describe(lineup))


if __name__ == '__main__':
    data = json.loads((ROOT / 'v2/projections_v2.json').read_text())
    ids = [1, 250, 8, 445, 229, 471, 304, 12, 427, 398, 70, 545, 411, 165, 441]
    players = {p['id']: p for p in data['players']}
    report = inspect([players[i] for i in ids], data['start_gw'])
    print(json.dumps(report, indent=2))
