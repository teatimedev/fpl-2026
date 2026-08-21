"""One source of truth for FPL lineup, autosub and squad-value rules."""
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


SQUAD_SHAPE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}
POS_ORDER = ("GKP", "DEF", "MID", "FWD")


@dataclass(frozen=True)
class Lineup:
    xi: list
    bench: list
    captain: object | None
    vice: object | None


@dataclass(frozen=True)
class AutosubResult:
    scoring_ids: tuple
    substitutions: tuple
    unreplaced_ids: tuple


@dataclass(frozen=True)
class WeekEvaluation:
    gw: int
    xi_points: float
    captain_points: float
    autosub_points: float
    total: float
    lineup: Lineup


@dataclass(frozen=True)
class SquadEvaluation:
    start_gw: int
    horizon: int
    xi_points: float
    captain_points: float
    autosub_points: float
    total: float
    weeks: tuple

    @property
    def xi_captain_points(self):
        return self.xi_points + self.captain_points


def gw_points(player, gw):
    values = player.get("proj_by_gw") or player.get("by_gw") or []
    return float(values[gw - 1]) if 0 <= gw - 1 < len(values) else 0.0


def play_probability(player, gw):
    values = player.get("play_by_gw") or []
    if 0 <= gw - 1 < len(values):
        return max(0.0, min(1.0, float(values[gw - 1])))
    if player.get("status") == "u":
        return 0.0
    starts = player.get("start_by_gw") or []
    start = (float(starts[gw - 1]) if 0 <= gw - 1 < len(starts)
             else float(player.get("start_rate", 1.0)))
    return max(0.0, min(1.0, start + (1.0 - start) * 0.20))


def pick_lineup(squad: Sequence[Mapping], gw: int,
                points: Callable[[Mapping, int], float] = gw_points):
    """Pick the highest-projected legal XI and ordered bench for one GW."""
    key = lambda p: points(p, gw)
    by_pos = {pos: [] for pos in POS_ORDER}
    for player in squad:
        by_pos.setdefault(player["pos"], []).append(player)
    for players in by_pos.values():
        players.sort(key=key, reverse=True)

    xi = []
    used = {pos: 0 for pos in POS_ORDER}
    for pos in POS_ORDER:
        for player in by_pos.get(pos, [])[:XI_MIN[pos]]:
            xi.append(player)
            used[pos] += 1

    selected = {p["id"] for p in xi}
    rest = sorted((p for p in squad if p["id"] not in selected),
                  key=key, reverse=True)
    for player in rest:
        if len(xi) >= 11:
            break
        if used[player["pos"]] < XI_MAX[player["pos"]]:
            xi.append(player)
            selected.add(player["id"])
            used[player["pos"]] += 1

    bench = [p for p in squad if p["id"] not in selected]
    # FPL displays the reserve goalkeeper separately, followed by outfield
    # substitutes in the order they would enter.
    bench.sort(key=lambda p: (p["pos"] != "GKP", -key(p)))
    ranked = sorted(xi, key=key, reverse=True)
    return Lineup(
        xi=xi,
        bench=bench,
        captain=ranked[0] if ranked else None,
        vice=ranked[1] if len(ranked) > 1 else None,
    )


def _legal_counts(counts):
    return all(XI_MIN[pos] <= counts.get(pos, 0) <= XI_MAX[pos]
               for pos in POS_ORDER)


def apply_autosubs(xi: Sequence[Mapping], bench: Sequence[Mapping],
                   played: Mapping):
    """Apply FPL's position, bench-order and one-use autosub rules."""
    scoring = [p["id"] for p in xi if played.get(p["id"], False)]
    substitutions = []

    starting_keeper = next((p for p in xi if p["pos"] == "GKP"), None)
    bench_keeper = next((p for p in bench if p["pos"] == "GKP"), None)
    if (starting_keeper and not played.get(starting_keeper["id"], False)
            and bench_keeper and played.get(bench_keeper["id"], False)):
        scoring.append(bench_keeper["id"])
        substitutions.append((starting_keeper["id"], bench_keeper["id"]))

    missing = [p for p in xi
               if p["pos"] != "GKP" and not played.get(p["id"], False)]
    counts = {pos: sum(p["pos"] == pos for p in xi) for pos in POS_ORDER}
    for substitute in (p for p in bench if p["pos"] != "GKP"):
        if not played.get(substitute["id"], False):
            continue
        chosen = None
        # Prefer a like-for-like replacement.  Otherwise replace the first
        # missing starter whose removal still leaves a legal nominal formation.
        candidates = sorted(missing, key=lambda p: p["pos"] != substitute["pos"])
        for absent in candidates:
            trial = dict(counts)
            trial[absent["pos"]] -= 1
            trial[substitute["pos"]] += 1
            if _legal_counts(trial):
                chosen = absent
                counts = trial
                break
        if chosen is None:
            continue
        missing.remove(chosen)
        scoring.append(substitute["id"])
        substitutions.append((chosen["id"], substitute["id"]))

    return AutosubResult(
        scoring_ids=tuple(scoring),
        substitutions=tuple(substitutions),
        unreplaced_ids=tuple(p["id"] for p in missing),
    )


def captain_replacement(captain_id, vice_id, played: Mapping):
    """Return the player whose points are doubled, or None."""
    if captain_id is not None and played.get(captain_id, False):
        return captain_id
    if vice_id is not None and played.get(vice_id, False):
        return vice_id
    return None


def _poisson_binomial(dnp_probabilities):
    """Probability mass for 0..N independent non-appearances."""
    dist = [1.0]
    for q in dnp_probabilities:
        nxt = [0.0] * (len(dist) + 1)
        for k, value in enumerate(dist):
            nxt[k] += value * (1.0 - q)
            nxt[k + 1] += value * q
        dist = nxt
    return dist


def _expected_outfield_autosubs(lineup, gw):
    """Expected outfield autosubs with formation and bench failures exact."""
    positions = ("DEF", "MID", "FWD")
    pos_index = {pos: i for i, pos in enumerate(positions)}
    outfield_xi = [p for p in lineup.xi if p["pos"] != "GKP"]
    original_counts = tuple(sum(p["pos"] == pos for p in outfield_xi)
                            for pos in positions)

    # State is (missing starters by position, current nominal formation).
    states = {((0, 0, 0), original_counts): 1.0}
    for starter in outfield_xi:
        q = 1.0 - play_probability(starter, gw)
        idx = pos_index[starter["pos"]]
        nxt = {}
        for (missing, counts), probability in states.items():
            nxt[(missing, counts)] = nxt.get((missing, counts), 0.0) + probability * (1.0 - q)
            absent = list(missing)
            absent[idx] += 1
            key = (tuple(absent), counts)
            nxt[key] = nxt.get(key, 0.0) + probability * q
        states = nxt

    def replacement(state, bench_pos):
        missing, counts = state
        # Like-for-like first, then preserve the XI's positional ordering.
        order = [bench_pos] + [p["pos"] for p in outfield_xi if p["pos"] != bench_pos]
        seen = set()
        for absent_pos in order:
            if absent_pos in seen:
                continue
            seen.add(absent_pos)
            absent_idx = pos_index[absent_pos]
            if missing[absent_idx] <= 0:
                continue
            trial = list(counts)
            trial[absent_idx] -= 1
            trial[pos_index[bench_pos]] += 1
            full = {"GKP": 1, **dict(zip(positions, trial))}
            if _legal_counts(full):
                return absent_idx, tuple(trial)
        return None

    expected = 0.0
    for substitute in (p for p in lineup.bench if p["pos"] != "GKP"):
        activation = sum(probability for state, probability in states.items()
                         if replacement(state, substitute["pos"]) is not None)
        expected += activation * gw_points(substitute, gw)

        p_available = play_probability(substitute, gw)
        nxt = {}
        for state, probability in states.items():
            nxt[state] = nxt.get(state, 0.0) + probability * (1.0 - p_available)
            change = replacement(state, substitute["pos"])
            if change is None:
                nxt[state] = nxt.get(state, 0.0) + probability * p_available
                continue
            absent_idx, counts = change
            missing = list(state[0])
            missing[absent_idx] -= 1
            key = (tuple(missing), counts)
            nxt[key] = nxt.get(key, 0.0) + probability * p_available
        states = nxt
    return expected


def evaluate_week(squad: Sequence[Mapping], gw: int):
    """Expected XI, captain and risk-sensitive autosub points for one GW.

    Projections are unconditional, so a bench player's expected points are
    multiplied by the chance that a slot is needed—not by another arbitrary
    fixed bench discount.  Formation is exact in ``apply_autosubs``; this
    expectation enumerates the independent DNP states and applies bench order
    and formation legality, while remaining fast enough for the transfer engine.
    """
    lineup = pick_lineup(squad, gw)
    xi_points = sum(gw_points(p, gw) for p in lineup.xi)
    captain_points = 0.0
    if lineup.captain:
        captain_points = gw_points(lineup.captain, gw)
        if lineup.vice:
            captain_points += (
                (1.0 - play_probability(lineup.captain, gw))
                * gw_points(lineup.vice, gw)
            )

    autosub_points = 0.0
    starting_keeper = next((p for p in lineup.xi if p["pos"] == "GKP"), None)
    bench_keeper = next((p for p in lineup.bench if p["pos"] == "GKP"), None)
    if starting_keeper and bench_keeper:
        autosub_points += (
            (1.0 - play_probability(starting_keeper, gw))
            * gw_points(bench_keeper, gw)
        )

    autosub_points += _expected_outfield_autosubs(lineup, gw)

    total = xi_points + captain_points + autosub_points
    return WeekEvaluation(
        gw=gw,
        xi_points=xi_points,
        captain_points=captain_points,
        autosub_points=autosub_points,
        total=total,
        lineup=lineup,
    )


def evaluate_squad(squad: Sequence[Mapping], start_gw: int, horizon: int):
    weeks = tuple(evaluate_week(squad, gw) for gw in range(start_gw, horizon + 1))
    return SquadEvaluation(
        start_gw=start_gw,
        horizon=horizon,
        xi_points=sum(w.xi_points for w in weeks),
        captain_points=sum(w.captain_points for w in weeks),
        autosub_points=sum(w.autosub_points for w in weeks),
        total=sum(w.total for w in weeks),
        weeks=weeks,
    )


def deadline_unavailable(squad: Sequence[Mapping], gw: int, threshold=0.05):
    """Players with effectively no route to minutes at the next deadline."""
    return [p for p in squad if p.get("status") == "u"
            or play_probability(p, gw) < threshold]


def modelled_bench_weights(players: Sequence[Mapping], gw: int):
    """Data-derived linear bench weights for MILP consumers.

    A solver cannot multiply a selected starter's DNP probability by a selected
    substitute.  Use the likely XI's actual DNP distribution as the linear
    approximation: reserve-GK weight is its starter's DNP risk; each outfield
    reserve slot gets the corresponding probability of at least 1/2/3 absences.
    """
    likely = pick_lineup(players, gw)
    keeper = next((p for p in likely.xi if p["pos"] == "GKP"), None)
    gkp = 1.0 - play_probability(keeper, gw) if keeper else 0.0
    outfield = [p for p in likely.xi if p["pos"] != "GKP"]
    dist = _poisson_binomial([1.0 - play_probability(p, gw) for p in outfield])
    tails = [sum(dist[k:]) for k in (1, 2, 3)]
    return {"GKP": gkp, "outfield": sum(tails) / 3.0 if tails else 0.0}
