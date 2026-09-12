"""Freeze cross-language selection cases, checked against scalar enumeration.

Run from the repository root; writes only the explicit app test fixture.
The reference enumerates all 3,300 legal XI/bench choices per case.
"""
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.lineup_oracle import enumerate_lineups
from tests import test_squad_evaluator as fixtures
from v2.lineup_search import search


def generate():
    cases = []
    for seed in [None, 1, 9, 18, 42, 71, 'keeper', 'blank']:
        fixture = fixtures.SquadRuleTests()
        fixture.setUp()
        squad = fixture.squad
        if isinstance(seed, int):
            rng = random.Random(seed)
            for p in squad:
                play = rng.choice([0., .1, .5, .9, 1.])
                p.update(play_by_gw=[play], proj_by_gw=[play * rng.uniform(-1, 10)])
        elif seed == 'keeper':
            squad[0].update(proj_by_gw=[4.], play_by_gw=[.5])
            squad[1].update(proj_by_gw=[5.], play_by_gw=[1.])
        elif seed == 'blank':
            for p in squad:
                p.update(proj_by_gw=[0.], play_by_gw=[0.])
        score, lineup = search(squad, 1)
        oracle = max(value for value, _ in enumerate_lineups(squad, 1))
        assert abs(score - oracle) < 1e-9
        cases.append(dict(case=str(seed), squad=squad, expected=dict(
            score=oracle, xi=[p['id'] for p in lineup.xi],
            bench=[p['id'] for p in lineup.bench], captain=lineup.captain['id'],
            vice=lineup.vice['id'])))
    target = ROOT / 'app/tests/fixtures/lineup-parity.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(basis='Scalar exhaustive independent-appearance expectation',
                                    cases=cases), indent=2) + '\n')


if __name__ == '__main__':
    generate()
