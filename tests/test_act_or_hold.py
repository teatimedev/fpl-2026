"""Phase 4: the sampled act-versus-hold decision (v2/act_or_hold.py)."""
import random
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1] / 'v2'
sys.path.insert(0, str(V2))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import act_or_hold as A  # noqa: E402
from planner import production_valuation  # noqa: E402
from test_planner_terminal import full_tail, squad  # noqa: E402


class FakeModel:
    """Stands in for PathModel: objective = base + effect[action] + noise[world]."""

    def __init__(self, owned, effects, noise_sd=1.0):
        self.ids = list(owned) + [100, 101, 102]
        self.P = {i: dict(id=i, pos='MID', proj_by_gw=[1.0] * 3) for i in self.ids}
        self.GW, self.gw, self.TAIL = [1, 2, 3], 1, []
        self.effects, self.noise_sd, self.calls = effects, noise_sd, 0

    def tail_source(self, players=None):
        return {}

    def solve(self, first_week_squad=None, players=None, **_):
        self.calls += 1
        world = players[self.ids[0]]['proj_by_gw'][1]      # a per-world draw
        if first_week_squad is None:
            key = max(self.effects, key=self.effects.get)
            squad = list(key)
        else:
            key, squad = frozenset(first_week_squad), list(first_week_squad)
        effect, spread = self.effects.get(key, (0.0, 0.0)) if isinstance(
            self.effects.get(key), tuple) else (self.effects.get(key, 0.0), 0.0)
        return dict(objective=500 + effect + spread * (world - 1.0) * 10,
                    weeks=[dict(squad=squad)])


def action(owned, out, new):
    return [i for i in owned if i != out] + [new]


OWNED = list(range(1, 16))


def test_a_clear_gain_is_taken_and_hold_is_always_evaluated():
    good = action(OWNED, 15, 100)
    model = FakeModel(OWNED, {frozenset(OWNED): 0.0, frozenset(good): 3.0})
    res = A.decide(model, OWNED, [('single', good)], samples=8, discovery=0, seed=1,
                   max_actions=3, budget=1e9)
    assert res['chosen']['in_'] == [100]
    assert res['chosen']['out'] == [15]
    assert abs(res['chosen']['gain'] - 3.0) < 1e-9
    assert res['samples'] == 8
    assert {r['label'] for r in res['rows']} == {'hold', 'single'}


def test_one_standard_error_rule_prefers_fewer_moves_within_noise():
    # +0.2 on average but +/- several points world to world: not resolvable.
    noisy = action(OWNED, 15, 100)
    model = FakeModel(OWNED, {frozenset(OWNED): 0.0, frozenset(noisy): (0.2, 3.0)})
    res = A.decide(model, OWNED, [('single', noisy)], samples=12, discovery=0, seed=4,
                   budget=1e9)
    assert res['chosen']['in_'] == []
    single = next(r for r in res['rows'] if r['in_'])
    assert single['se'] > 0


def test_decision_is_reproducible_from_its_seed():
    move = action(OWNED, 15, 100)
    runs = [A.decide(FakeModel(OWNED, {frozenset(move): (0.5, 1.0)}), OWNED,
                     [('single', move)], samples=6, discovery=0, seed=11, budget=1e9)
            for _ in range(2)]
    assert [r['gain'] for r in runs[0]['rows']] == [r['gain'] for r in runs[1]['rows']]


def test_sampler_stops_at_its_time_budget_after_the_minimum_worlds():
    move = action(OWNED, 15, 100)
    ticks = iter(range(0, 10_000, 100))
    res = A.decide(FakeModel(OWNED, {frozenset(move): 1.0}), OWNED, [('single', move)],
                   samples=50, discovery=0, seed=2, budget=50, clock=lambda: next(ticks))
    assert res['samples'] == A.MIN_SAMPLES


def test_perturbation_keeps_this_week_and_dead_players_fixed():
    players, owned = squad()
    players[3]['proj_by_gw'] = [0.0] * 12          # owned, no route to points
    model = A.build_model(players, owned, 0.0, 1, 2, 4, None,
                          production_valuation(tail=full_tail(players)), pool_size=30)
    world = A.perturb(model, random.Random(5))
    assert all(world[i]['proj_by_gw'][1] == players[i]['proj_by_gw'][1] for i in model.ids)
    assert world[3]['proj_by_gw'][2:4] == [0.0, 0.0]
    moved = [world[i]['proj_by_gw'][2] for i in owned if i != 3]
    assert len(set(moved)) > 1 and min(moved) >= 0
    assert '_tail' in world[1]


def test_at_five_free_transfers_holding_wastes_one_so_a_small_gain_is_taken():
    # A modest upgrade that is not worth a banked transfer at 1 FT is worth
    # making at 5, where holding forfeits the transfer. The old 2-point buffer
    # overrode exactly this (weekly.py, GW5 hold at the cap).
    players, owned = squad(extra=[(99, 'MID', [4.05] * 12, 5.0)])
    tail = full_tail(players)
    candidates = [('single', action(owned, 12, 99))]
    kw = dict(samples=6, discovery=0, max_actions=2, budget=1e9, persistent_sd=0.05, week_sd=0.02)
    at_one, _ = A.run(players, owned, 0.0, 1, 2, 4, None,
                      production_valuation(tail=tail), candidates, seed=3, pool_size=30, **kw)
    at_five, nominal = A.run(players, owned, 0.0, 5, 2, 4, None,
                             production_valuation(tail=tail), candidates, seed=3, pool_size=30, **kw)
    assert at_one['chosen']['in_'] == []
    assert at_five['chosen']['in_'] == [99]
    assert A.nominal_gain(nominal, owned, candidates[0][1]) > 0


def test_public_rows_are_json_ready():
    move = action(OWNED, 15, 100)
    res = A.decide(FakeModel(OWNED, {frozenset(move): 2.0}), OWNED, [('single', move)],
                   samples=4, discovery=0, seed=1, budget=1e9)
    rows = A.public_rows(res)
    hold = next(r for r in rows if r['hold'])
    single = next(r for r in rows if not r['hold'])
    assert hold['p_beats_hold'] is None and single['p_beats_hold'] == 1.0
    assert single['qualifies'] and not hold['qualifies']
    assert 'key' not in single and 'squad' not in single
