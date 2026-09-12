"""Paired squad simulations, conditional on the forecast and its assumptions.

Shared players have identical outcomes in both squads; opponents share match
goals and all fixtures are scored separately before gameweek aggregation.
Goals currently use independent Poisson marginals from season-view xG, without
the fitted Dixon-Coles low-score dependence. Starts are independent across
players and fixtures; attacking events are not yet conserved across a club.

Each player's expected score is reconciled separately in each gameweek using
the sampling equations' analytic mean and a per-appearance residual. These
fractional adjustments and approximate bonus/cards/minutes make this a model
of score variation, not an exact match-event simulator. It cannot independently
validate the projections it is calibrated to. At 4,000 draws, Monte Carlo error
alone is at most about 0.8 percentage points; model uncertainty is additional.

Ties count as half wins. The autosub engine follows bench order and legal
formation constraints. Manager identity, red cards and own goals are omitted.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

try:
    from .squad_evaluator import pick_lineup
except ImportError:
    from squad_evaluator import pick_lineup

ROOT = Path(__file__).resolve().parent.parent
PROJ_PATH = ROOT / 'v2' / 'projections_v2.json'
VIEW_PATH = ROOT / 'v2' / 'season_view.json'

POS_ORDER = ('GKP', 'DEF', 'MID', 'FWD')
POS_IX = {pos: i for i, pos in enumerate(POS_ORDER)}
GOAL_PTS = {'GKP': 6, 'DEF': 6, 'MID': 5, 'FWD': 4}
CS_PTS = {'GKP': 4, 'DEF': 4, 'MID': 1, 'FWD': 0}
XI_MIN = {'GKP': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
SQUAD_SIZE = 15
SQUAD_COUNTS = {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
MAX_PER_CLUB = 3
DEFAULT_SEED = 20260902


# ------------------------------------------------------------------ inputs
def load_players(path=PROJ_PATH):
    """id -> player dict from v2/projections_v2.json."""
    data = json.loads(Path(path).read_text())
    return {p['id']: p for p in data['players']}


def load_fixture_xg(path=VIEW_PATH):
    """club -> {gw: [(xg_for, xg_against, opponent_or_None), ...]}.

    Built from the season-view fixture list; a gameweek with two fixtures
    simply yields two entries.
    """
    view = json.loads(Path(path).read_text())['view']
    out = {}
    for club, by_gw in view.items():
        out[club] = {}
        for gw, fixtures in by_gw.items():
            out[club][int(gw)] = [
                (float(f['xg']), float(f['xgc']), f.get('opp'))
                for f in fixtures
            ]
    return out


def _fixtures_at(fixture_xg, club, gw):
    """Normalised fixture rows for one club-gameweek.

    Accepts the loader's (xg, xgc, opp) tuples, plain (xg, xgc) pairs, or a
    dict/sequence of either -- whatever shape the caller found convenient.
    """
    rows = (fixture_xg.get(club) or {}).get(gw)
    if rows is None:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    elif (isinstance(rows, (tuple, list)) and rows
          and not isinstance(rows[0], (tuple, list, dict))):
        rows = [rows]          # a bare (xg, xgc) row, not a list of them
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append((float(row['xg']), float(row['xgc']), row.get('opp')))
        elif len(row) >= 3:
            out.append((float(row[0]), float(row[1]), row[2]))
        else:
            out.append((float(row[0]), float(row[1]), None))
    return out


def _series_value(player, key, gw, scalar, default):
    """Per-gameweek value from a GW1-indexed list, falling back to a scalar."""
    values = player.get(key) or []
    if gw - 1 < len(values) and values[gw - 1] is not None:
        return float(values[gw - 1])
    if scalar is not None:
        return float(scalar)
    return default


def _player_params(player, start_gw, horizon):
    """Simulator parameters for one player over [start_gw, horizon].

    ``mins_by_gw`` is an unconditional expectation in the projection file.  It
    must not be used as minutes conditional on starting: doing that discounts
    playing time twice for rotation risks.  The availability rows carry the
    conditional start/cameo minutes; infer them from the expectation only when
    those rows are absent (as in small synthetic callers).
    """
    pos = str(player.get('pos', '')).upper()
    if pos not in POS_IX:
        raise ValueError(
            f"player {player.get('id', '?')} ({player.get('name', '?')}) has "
            f"invalid position {player.get('pos')!r}; expected one of {POS_ORDER}")
    gws = list(range(start_gw, horizon + 1))
    p_start = [float(np.clip(_series_value(
        player, 'start_by_gw', gw, player.get('start_rate'), 1.0), 0.0, 1.0))
        for gw in gws]
    p_play = [float(np.clip(_series_value(
        player, 'play_by_gw', gw, player.get('play_rate'), p_start[wi]),
        p_start[wi], 1.0)) for wi, gw in enumerate(gws)]
    expected_mins = [_series_value(
        player, 'mins_by_gw', gw, player.get('mins_proj'), 80.0) for gw in gws]
    availability = player.get('availability_by_gw') or []
    start_mins, cameo_mins = [], []
    for wi, gw in enumerate(gws):
        row = availability[gw - 1] if gw - 1 < len(availability) else None
        row = row if isinstance(row, dict) else {}
        if row.get('p_start') is not None and row.get('p_play') is not None:
            p_start[wi] = float(row['p_start'])
            p_play[wi] = float(row['p_play'])
            expected_mins[wi] /= max(int(row.get('fixtures', 1)), 1)
            if not 0 <= p_start[wi] <= p_play[wi] <= 1:
                raise ValueError(f"Player {player['id']}: invalid per-fixture availability")
        cameo = float(np.clip(row.get('cameo_minutes', 25.0), 1.0, 59.0))
        if row.get('start_minutes') is not None:
            start = float(row['start_minutes'])
        elif p_start[wi] > 0.0:
            cameo_prob = p_play[wi] - p_start[wi]
            start = (expected_mins[wi] - cameo_prob * cameo) / p_start[wi]
        else:
            start = expected_mins[wi]
        start_mins.append(float(np.clip(start, 1.0, 90.0)))
        cameo_mins.append(cameo)
    proj_series = player.get('proj_by_gw') or []
    proj = [float(proj_series[gw - 1])
            if gw - 1 < len(proj_series) and proj_series[gw - 1] is not None
            else None for gw in gws]
    return {
        'id': player['id'],
        'name': player.get('name', str(player['id'])),
        'pos': pos,
        'pos_i': POS_IX[pos],
        'team': player.get('team') or '',
        'p_start': p_start,
        'p_play': p_play,
        'mins': expected_mins,
        'start_mins': start_mins,
        'cameo_mins': cameo_mins,
        'proj': proj,
        'xg90': float(player.get('xg90') or 0.0),
        'xa90': float(player.get('xa90') or 0.0),
        'saves90': float(player.get('saves90') or 0.0),
        'bonus90': float(player.get('bonus90') or 0.0),
        'yellow90': float(player.get('yellow90') or 0.0),
        'dc90': float(player.get('dc90') or 0.0),
        'k_att': 1.0,
        'add': 0.0,
    }


# ------------------------------------------------------------- team layer
def _team_draws(teams, fixture_xg, start_gw, horizon, n_sims, rng):
    """Draw goals for/against per club per gameweek: gf, ga, lam, n_fixtures.

    One Poisson draw per MATCH, taken from whichever club's view comes first
    alphabetically, so the home side's goals are exactly the away side's goals
    conceded. Contradictory mirrored rates are rejected.
    Clubs outside the pool are never drawn; a club with no fixture in a
    gameweek leaves everyone at zero minutes that week.
    """
    window = horizon - start_gw + 1
    clubs = sorted(teams)
    slots = max([1] + [len(_fixtures_at(fixture_xg, c, gw)) for c in clubs
                       for gw in range(start_gw, horizon + 1)])
    gf = {c: np.zeros((n_sims, window, slots), np.int16) for c in clubs}
    ga = {c: np.zeros((n_sims, window, slots), np.int16) for c in clubs}
    lam = {c: np.zeros((window, slots)) for c in clubs}
    n_fx = {c: np.zeros(window, int) for c in clubs}    # fixtures per gw
    fx_rows = {c: [] for c in clubs}                    # (wi, xg, xgc) per club
    drawn = {}
    for c in clubs:
        for wi, gw in enumerate(range(start_gw, horizon + 1)):
            occurrences = {}
            for fi, (xg, xgc, opp) in enumerate(_fixtures_at(fixture_xg, c, gw)):
                if not all(math.isfinite(x) and x >= 0 for x in (xg, xgc)):
                    raise ValueError('Fixture goal expectations must be finite and non-negative')
                lam[c][wi, fi] = xg
                n_fx[c][wi] += 1
                fx_rows[c].append((wi, xg, xgc))
                occurrence = occurrences.get(opp, 0)
                occurrences[opp] = occurrence + 1
                key = (gw, frozenset((c, opp)), occurrence) if opp else None
                if key is not None and key in drawn:
                    _, against, goals_for, previous_xg, previous_xgc = drawn[key]
                    if not (math.isclose(xg, previous_xgc, abs_tol=1e-6)
                            and math.isclose(xgc, previous_xg, abs_tol=1e-6)):
                        raise ValueError(f'Contradictory mirrored fixture rates: {c}/{opp}, GW{gw}')
                    goals_agt = against
                else:
                    goals_for = rng.poisson(xg, n_sims)
                    goals_agt = rng.poisson(xgc, n_sims)
                    if key is not None:
                        drawn[key] = (c, goals_for, goals_agt, xg, xgc)
                gf[c][:, wi, fi] = goals_for
                ga[c][:, wi, fi] = goals_agt
    return gf, ga, lam, n_fx, fx_rows


# -------------------------------------------------------------- physics
def _simulate_fixture_player(par, gf, ga, lam, n_fx, rng):
    """Return point and appearance draws with shape ``(n_sims, window)``.

    A single uniform draw couples start and appearance, then conditional
    start/cameo minutes determine appearance and clean-sheet eligibility.
    Attacking rates scale with realised club goals, preserving the important
    within-club correlation while retaining each player's own xG/xA rate.
    """
    n_sims, window = gf[par['team']].shape if par['team'] in gf else (0, 0)
    pts = np.zeros((n_sims, window), np.float32)
    played = np.zeros((n_sims, window), bool)
    team = par['team']
    if team not in gf:
        return pts, played
    pos = par['pos']
    goal_pts, cs_pts = GOAL_PTS[pos], CS_PTS[pos]
    for wi in range(window):
        if n_fx[team][wi] == 0:
            continue
        appearance_draw = rng.random(n_sims)
        starts = appearance_draw < par['p_start'][wi]
        appears = appearance_draw < par['p_play'][wi]
        cameos = appears & ~starts
        mins = (starts * par['start_mins'][wi]
                + cameos * par['cameo_mins'][wi])
        frac = mins / 90.0
        pts[:, wi] += appears
        pts[:, wi] += starts & (mins >= 60.0)

        team_goals = gf[team][:, wi]
        expected = max(float(lam[team][wi]), 0.3)
        boost = np.where(team_goals > 0, team_goals / expected, 0.0)
        k = par['k_att'][wi] if isinstance(par['k_att'], (list, np.ndarray)) else par['k_att']
        goals = rng.poisson(np.clip(
            par['xg90'] * k * frac * boost, 0.0, 6.0))
        assists = rng.poisson(np.clip(
            par['xa90'] * k * frac * boost, 0.0, 6.0))
        pts[:, wi] += goals * goal_pts + assists * 3.0

        cs = (ga[team][:, wi] == 0) & starts & (mins >= 60.0)
        pts[:, wi] += cs * cs_pts
        if pos in ('GKP', 'DEF'):
            # Without event timestamps, starters are conservatively exposed to
            # the full match; short cameos are not charged goals conceded.
            pts[:, wi] -= np.floor(ga[team][:, wi] / 2.0) * starts
        if pos == 'GKP':
            saves = rng.poisson(
                np.clip(par['saves90'] * frac, 0.0, 12.0))
            pts[:, wi] += np.floor(saves / 3.0)
        if pos != 'GKP' and par['dc90'] > 0:
            m = np.clip(par['dc90'] * frac, 0.0, 30.0)
            r = 12.0
            draws = rng.negative_binomial(r, r / (r + m))
            pts[:, wi] += (
                (draws >= (10 if pos == 'DEF' else 12)) & appears) * 2.0

        good = (goals + assists) > 0
        if pos in ('GKP', 'DEF'):
            good |= cs
        bp = rng.random(n_sims) < np.clip(
            par['bonus90'] * k * frac * 0.55, 0.0, 0.9)
        mag = rng.integers(1, 4, n_sims)
        pts[:, wi] += (good & bp & appears) * mag
        pts[:, wi] -= (
            rng.random(n_sims)
            < np.clip(par['yellow90'] * frac, 0.0, 0.6)) & appears
        played[:, wi] = appears
    return pts, played


def _simulate_player(par, gf, ga, lam, n_fx, rng):
    """Score fixtures individually; aggregate points and any appearance by GW."""
    shape = gf[par['team']].shape
    if len(shape) == 2:
        if any(np.max(n) > 1 for n in n_fx.values()):
            raise ValueError('Double gameweeks require separate fixture goal draws')
        pts, played = _simulate_fixture_player(par, gf, ga, lam, n_fx, rng)
    else:
        pts = np.zeros(shape[:2], np.float64)
        played = np.zeros(shape[:2], bool)
        for fi in range(shape[2]):
            this_pts, this_played = _simulate_fixture_player(
                par, {c: v[:, :, fi] for c, v in gf.items()},
                {c: v[:, :, fi] for c, v in ga.items()},
                {c: v[:, fi] for c, v in lam.items()},
                {c: (v > fi).astype(int) for c, v in n_fx.items()}, rng)
            pts += this_pts
            played |= this_played
    return pts + played * np.asarray(par['add']), played


def _floor_poisson(mean, divisor):
    from scipy.stats import poisson
    values = np.arange(int(poisson.ppf(1 - 1e-12, mean)) + 1)
    return float(np.dot(values // divisor, poisson.pmf(values, mean)))


def _raw_fixture_mean(par, wi, xg, xgc, k):
    """Analytic mean of the implemented single-fixture approximation.

    Includes Poisson clipping, discrete saves/conceded floors, conditional
    bonus gating and the negative-binomial DefCon threshold. This reconciles
    the actual sampling equations, without fitting noise from a short pilot.
    """
    from scipy.stats import poisson, nbinom
    team_goals = np.arange(int(poisson.ppf(1 - 1e-12, xg)) + 1)
    mass = poisson.pmf(team_goals, xg)
    pos = par['pos']
    expected = 0.
    for probability, minutes, starts in (
        (par['p_start'][wi], par['start_mins'][wi], True),
        (par['p_play'][wi] - par['p_start'][wi], par['cameo_mins'][wi], False),
    ):
        if probability <= 0:
            continue
        frac = minutes / 90.
        boost = team_goals / max(xg, .3)
        goals = np.clip(par['xg90'] * k * frac * boost, 0, 6)
        assists = np.clip(par['xa90'] * k * frac * boost, 0, 6)
        mean = 1. + (starts and minutes >= 60)
        mean += float(np.dot(mass, GOAL_PTS[pos] * goals + 3 * assists))
        cs = math.exp(-xgc) if starts and minutes >= 60 else 0.
        mean += cs * CS_PTS[pos]
        if pos in ('GKP', 'DEF') and starts:
            mean -= _floor_poisson(xgc, 2)
        if pos == 'GKP':
            mean += _floor_poisson(float(np.clip(par['saves90'] * frac, 0, 12)), 3)
        elif par['dc90'] > 0:
            m = float(np.clip(par['dc90'] * frac, 0, 30))
            mean += 2 * nbinom.sf((10 if pos == 'DEF' else 12) - 1, 12., 12. / (12. + m))
        good = float(np.dot(mass, 1 - np.exp(-goals - assists)))
        if pos in ('GKP', 'DEF'):
            good += (1 - good) * cs
        mean += 2 * good * float(np.clip(par['bonus90'] * k * frac * .55, 0, .9))
        mean -= float(np.clip(par['yellow90'] * frac, 0, .6))
        expected += probability * mean
    return float(expected)


def _calibrate_pool(pool, fx_rows, gf, ga, lam, n_fx, rng):
    """Reconcile each player's gameweek mean analytically, with no pilot noise."""
    _, window = next(iter(gf.values())).shape[:2]
    for pid in sorted(pool):
        par = pool[pid]
        par['k_att'], par['add'], par['p_play_gw'] = [1.] * window, [0.] * window, [0.] * window
        for wi, target in enumerate(par['proj']):
            rows = [(xg, xgc) for index, xg, xgc in fx_rows.get(par['team'], []) if index == wi]
            p_any = 1 - (1 - par['p_play'][wi]) ** len(rows)
            par['p_play_gw'][wi] = p_any
            if target is None:
                continue
            if p_any == 0:
                if abs(target) > 1e-8:
                    raise ValueError(f'Player {pid}, week {wi}: nonzero projection without an appearance route')
                continue
            raw = lambda k: sum(_raw_fixture_mean(par, wi, xg, xgc, k) for xg, xgc in rows)
            steady, unit = raw(0.), raw(1.)
            k = float(np.clip((target - steady) / (unit - steady), .25, 4.)) if unit > steady + 1e-8 else 1.
            par['k_att'][wi] = k
            par['add'][wi] = (target - raw(k)) / p_any


# ------------------------------------------------------------- squad rules
def _validate_squad(ids, players_by_id, label):
    """Reject anything that is not a legal 15-player FPL squad."""
    if len(set(ids)) != len(ids):
        raise ValueError(f"{label}: duplicate player ids in squad")
    unknown = [i for i in ids if i not in players_by_id]
    if unknown:
        raise ValueError(f"{label}: ids not in players_by_id: {unknown}")
    if len(ids) != SQUAD_SIZE:
        raise ValueError(
            f"{label}: expected exactly {SQUAD_SIZE} players for a legal FPL "
            f"squad, got {len(ids)}")

    counts = {pos: 0 for pos in POS_ORDER}
    clubs = {}
    for pid in ids:
        player = players_by_id[pid]
        pos = str(player.get('pos', '')).upper()
        if pos not in counts:
            raise ValueError(
                f"{label}: player {pid} has invalid position "
                f"{player.get('pos')!r}; expected one of {POS_ORDER}")
        team = str(player.get('team') or '').strip()
        if not team:
            raise ValueError(f"{label}: player {pid} has no club/team")
        counts[pos] += 1
        clubs[team] = clubs.get(team, 0) + 1
    if counts != SQUAD_COUNTS:
        got = ", ".join(f"{counts[pos]} {pos}" for pos in POS_ORDER)
        need = ", ".join(f"{SQUAD_COUNTS[pos]} {pos}" for pos in POS_ORDER)
        raise ValueError(
            f"{label}: illegal position counts ({got}); expected {need}")
    over = sorted((club, count) for club, count in clubs.items()
                  if count > MAX_PER_CLUB)
    if over:
        detail = ", ".join(f"{club} {count}" for club, count in over)
        raise ValueError(
            f"{label}: at most {MAX_PER_CLUB} players per club; got {detail}")


def _pick_lineup(par_list, wi, gw):
    """Use the same exhaustive selection as the deterministic squad evaluator."""
    squad = [dict(id=p['id'], pos=p['pos'], proj_by_gw=[p['proj'][wi]],
                  play_by_gw=[(p.get('p_play_gw') or p['p_play'])[wi]])
             for p in par_list]
    best = pick_lineup(squad, 1)
    if len(best.xi) != 11:
        raise ValueError(f'cannot field a legal XI in GW{gw}')
    indices = {par['id']: ix for ix, par in enumerate(par_list)}
    return {'xi': [indices[p['id']] for p in best.xi],
            'bench': [indices[p['id']] for p in best.bench],
            'captain': indices[best.captain['id']],
            'vice': indices[best.vice['id']] if best.vice else None}


def _squad_gw_points(par_list, lineup, pts_by_pid, played_by_pid, wi):
    """One gameweek of squad points: XI + auto-subs + doubled captain.

    Vectorised autosubs with per-simulation formation counts: the reserve
    keeper swaps in for a failed starter unconditionally; each outfield bench
    player, in bench order, replaces the first non-playing starter whose
    position keeps the nominal formation legal (like-for-like preferred).
    """
    n_sims = pts_by_pid[par_list[0]['id']].shape[0]
    xi, bench = lineup['xi'], lineup['bench']
    total = np.zeros(n_sims, np.float32)
    for ix in xi:
        total += pts_by_pid[par_list[ix]['id']][:, wi]

    # keeper auto-sub: reserve keeper in, failed starter out
    xi_gkp = next((ix for ix in xi if par_list[ix]['pos'] == 'GKP'), None)
    bench_gkp = next((ix for ix in bench if par_list[ix]['pos'] == 'GKP'), None)
    if xi_gkp is not None and bench_gkp is not None:
        fire = (~played_by_pid[par_list[xi_gkp]['id']][:, wi]
                & played_by_pid[par_list[bench_gkp]['id']][:, wi])
        total += fire * pts_by_pid[par_list[bench_gkp]['id']][:, wi]

    # outfield auto-subs, bench order, formation legality per simulation
    counts = np.zeros((n_sims, 4), np.int16)
    for ix in xi:
        counts[:, par_list[ix]['pos_i']] += 1
    xi_out = [ix for ix in xi if par_list[ix]['pos'] != 'GKP']
    subbed_out = {ix: np.zeros(n_sims, bool) for ix in xi_out}
    for sub in (ix for ix in bench if par_list[ix]['pos'] != 'GKP'):
        played_sub = played_by_pid[par_list[sub]['id']][:, wi]
        taken = np.zeros(n_sims, bool)
        order = sorted(xi_out, key=lambda ix: (par_list[ix]['pos'] !=
                                               par_list[sub]['pos'], ix))
        for ix in order:
            p, q = par_list[ix]['pos'], par_list[sub]['pos']
            legal = ((p == q)
                     | ((counts[:, POS_IX[p]] > XI_MIN[p])
                        & (counts[:, POS_IX[q]] < XI_MAX[q])))
            fire = ((~played_by_pid[par_list[ix]['id']][:, wi])
                    & ~subbed_out[ix] & played_sub & ~taken & legal)
            counts[fire, POS_IX[p]] -= 1
            counts[fire, POS_IX[q]] += 1
            subbed_out[ix] |= fire
            total += fire * pts_by_pid[par_list[sub]['id']][:, wi]
            taken |= fire

    cap = lineup['captain']
    cap_pts = pts_by_pid[par_list[cap]['id']][:, wi]
    cap_played = played_by_pid[par_list[cap]['id']][:, wi]
    if lineup['vice'] is not None:
        vice = lineup['vice']
        total += np.where(
            cap_played, cap_pts,
            np.where(played_by_pid[par_list[vice]['id']][:, wi],
                     pts_by_pid[par_list[vice]['id']][:, wi], 0.0))
    else:
        total += np.where(cap_played, cap_pts, 0.0)
    return total


# ------------------------------------------------------------------ core
def compare(squad_a_ids, squad_b_ids, players_by_id, fixture_xg,
            start_gw, horizon, n_sims=4000, seed=DEFAULT_SEED,
            hit_points_a=0, hit_points_b=0):
    """Monte-Carlo squad A vs squad B over [start_gw, horizon].

    Returns {'p_b_wins', 'mean_delta', 'p_delta_gt_2', 'p_delta_lt_minus_2'}
    where delta = squad B window total - squad A window total per simulation
    and p_b_wins counts ties as half wins. Hit costs are deducted from each
    squad's total in every draw, including the win probability.
    """
    squad_a_ids = list(squad_a_ids)
    squad_b_ids = list(squad_b_ids)
    _validate_squad(squad_a_ids, players_by_id, 'squad A')
    _validate_squad(squad_b_ids, players_by_id, 'squad B')
    if horizon < start_gw:
        raise ValueError(f"horizon {horizon} before start_gw {start_gw}")
    if n_sims < 1:
        raise ValueError(f"n_sims must be >= 1, got {n_sims}")
    if any(not math.isfinite(h) or h < 0 for h in (hit_points_a, hit_points_b)):
        raise ValueError('hit points must be finite and non-negative')

    pool = sorted(set(squad_a_ids) | set(squad_b_ids))
    pars = {pid: _player_params(players_by_id[pid], start_gw, horizon)
            for pid in pool}
    rng = np.random.default_rng(seed)
    gf, ga, lam, n_fx, fx_rows = _team_draws(
        {par['team'] for par in pars.values()}, fixture_xg, start_gw,
        horizon, n_sims, rng)
    _calibrate_pool(pars, fx_rows, gf, ga, lam, n_fx, rng)

    pts_by_pid, played_by_pid = {}, {}
    for pid in pool:
        pts_by_pid[pid], played_by_pid[pid] = _simulate_player(
            pars[pid], gf, ga, lam, n_fx, rng)

    def window_total(ids):
        par_list = [pars[pid] for pid in ids]
        total = np.zeros(n_sims, np.float32)
        for wi, gw in enumerate(range(start_gw, horizon + 1)):
            lineup = _pick_lineup(par_list, wi, gw)
            total += _squad_gw_points(par_list, lineup, pts_by_pid,
                                      played_by_pid, wi)
        return total

    delta = window_total(squad_b_ids).astype(np.float64) \
        - window_total(squad_a_ids).astype(np.float64)
    delta += hit_points_a - hit_points_b
    return {
        'p_b_wins': float((delta > 0).mean() + 0.5 * (delta == 0).mean()),
        'mean_delta': float(delta.mean()),
        'p_delta_gt_2': float((delta > 2).mean()),
        'p_delta_lt_minus_2': float((delta < -2).mean()),
    }


# ------------------------------------------------------------------- CLI
def _resolve_token(token, players_by_id):
    """Accept a numeric id or a (case-insensitive, unique) player name."""
    token = token.strip()
    if token.isdigit():
        pid = int(token)
        if pid not in players_by_id:
            raise SystemExit(f"no player with id {pid} in projections")
        return pid
    hits = {pid: p for pid, p in players_by_id.items()
            if p.get('name', '').lower() == token.lower()
            or p.get('full_name', '').lower() == token.lower()}
    if len(hits) != 1:
        raise SystemExit(f"player name {token!r} matched {len(hits)} players; "
                         "use the numeric id")
    return next(iter(hits))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Monte-Carlo two FPL squads over the projection window "
                    "and report P(squad B finishes ahead).")
    ap.add_argument('--a', required=True,
                    help='comma-separated squad A ids (or names)')
    ap.add_argument('--b', required=True,
                    help='comma-separated squad B ids (or names)')
    ap.add_argument('--n-sims', type=int, default=4000)
    ap.add_argument('--seed', type=int, default=DEFAULT_SEED)
    ap.add_argument('--start-gw', type=int, default=None,
                    help='default: start_gw from projections_v2.json')
    ap.add_argument('--horizon', type=int, default=None)
    args = ap.parse_args(argv)

    players_by_id = load_players()
    fixture_xg = load_fixture_xg()
    meta = json.loads(PROJ_PATH.read_text())
    start_gw = args.start_gw or meta.get('start_gw', 1)
    horizon = args.horizon or meta.get('horizon', start_gw + 5)

    squad_a = [_resolve_token(t, players_by_id)
               for t in args.a.split(',') if t.strip()]
    squad_b = [_resolve_token(t, players_by_id)
               for t in args.b.split(',') if t.strip()]
    names = lambda ids: ', '.join(players_by_id[i]['name'] for i in ids)
    changed_a = [players_by_id[i]['name'] for i in squad_a if i not in squad_b]
    changed_b = [players_by_id[i]['name'] for i in squad_b if i not in squad_a]

    t0 = time.perf_counter()
    result = compare(squad_a, squad_b, players_by_id, fixture_xg,
                     start_gw, horizon, n_sims=args.n_sims, seed=args.seed)
    runtime = time.perf_counter() - t0

    print(f"window GW{start_gw}-{horizon} "
          f"({horizon - start_gw + 1} gameweeks), "
          f"n_sims={args.n_sims}, seed={args.seed}")
    print(f"squad A: {names(squad_a)}")
    print(f"squad B: {names(squad_b)}")
    if changed_a or changed_b:
        print(f"change:  {'/'.join(changed_a) or '-'} -> "
              f"{'/'.join(changed_b) or '-'}")
    print(f"p_b_wins          {result['p_b_wins']:.3f}  "
          f"({100 * result['p_b_wins']:.1f}% of simulations)")
    print(f"mean_delta        {result['mean_delta']:+.2f} pts")
    print(f"p_delta_gt_2      {result['p_delta_gt_2']:.3f}")
    print(f"p_delta_lt_minus_2 {result['p_delta_lt_minus_2']:.3f}")
    print(f"runtime_s         {runtime:.2f}")
    return result


if __name__ == '__main__':
    main()
