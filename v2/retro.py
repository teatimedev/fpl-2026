"""
GW retrospective (P3): why did each player's score differ from the archived
projection — and does the reason carry information for next week?

Runs once per finished gameweek, after player_model.py has refit and before
weekly.py writes the next digest. For every player in the pool it splits the
residual `actual - proj` into pieces that each map to a cause, then assigns
exactly ONE class in a fixed order of precedence, so that a benched player's
zero xG is never read as finishing and an injured player's absence is never
read as selection:

    unavailable > minutes_loss > minutes_watch > minutes_gain > role_change
                > variance > on_model

It never changes a projection or a transfer number. weekly.py renders what it
writes (data/history/gw{n}_retro.json) as a review section, a minutes
warning, table notes and one push line; scorecard.py grades next week's
outcome by this week's class (retro_class), which is the forward validation
of the wording ("check" after one non-start, "sell" after two).

Inputs
  the belief      data/history/gw{n}.json   (weekly.py --snapshot)
  the outcome     data/history/gw{n}_actual.json via scorecard.actuals_for()
                  — per-stat points (`explain`) and raw stats
  the sequence    gw_stat rows (v2/fpl.db, or data/gw_stats.csv)
  the world now   v2/cache/bootstrap.json (this run's fetch)
  the new belief  v2/projections_v2.json (this run's refit)

    python v2/retro.py [--gw N] [--no-fetch]
"""
import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
HISTORY = ROOT / 'data' / 'history'
OUT = ROOT / 'data' / 'retro.json'
PROJ = HERE / 'projections_v2.json'
BOOT_CACHE = HERE / 'cache' / 'bootstrap.json'
DB = HERE / 'fpl.db'
GW_CSV = ROOT / 'data' / 'gw_stats.csv'

GOAL_PTS = {'GKP': 6, 'DEF': 6, 'MID': 5, 'FWD': 4}
CS_PTS = {'GKP': 4, 'DEF': 4, 'MID': 1, 'FWD': 0}
DC_THRESHOLD = {'GKP': None, 'DEF': 10, 'MID': 12, 'FWD': 12}
DEFAULT_CAMEO_MINUTES = 25.0
DEFAULT_CAMEO_PROBABILITY = 0.20

# ------------------------------------------------------------ thresholds
# Every number here is a starting point for backtest_inseason.py --retro to
# move, not a finding (RESEARCH-INSEASON-LEARNING.md §3.2).
LIKELY_START = 0.60          # scorecard's own definition of a likely starter
WATCH_START_LO = 0.35        # 0.35 <= p_start < 0.6 and did not start: watch
GAIN_START = 0.40            # p_start <= 0.4 and started: breakout minutes
FULL_MATCH = 60              # FPL's appearance-points line
RESIDUAL_BAND = 2.0          # ~two thirds of a starter's weekly sd (~3)
CHANCE_BAND = 1.0            # |chance quality| below this = not a chance signal
POOL_MIN_PLAY = 0.30         # snapshot rows in scope: p_play >= this ...
POOL_MIN_PROJ = 0.50         # ... or proj >= this
ROLE_WINDOW_STARTS = 3       # xGI window: the last three starts of 60'+
# PLACEHOLDER: per-match xG standard deviation of a regular attacker, from
# public data (~0.4). The 80% band on a three-start xGI sum is
# +-1.2816 * sd * sqrt(3) ~ +-0.9. Replace with the figure measured on
# gw_stat once P1 has a few weeks of rows (defcon_dispersion.py prints the
# per-match xG dispersion alongside DefCon's).
XGI_MATCH_SD = 0.40
Z80 = 1.2816
HISTORY_LEN = 6

CLASS_ORDER = ('unavailable', 'minutes_loss', 'minutes_watch', 'minutes_gain',
               'role_change', 'variance', 'on_model')


# ------------------------------------------------------------- helpers
def poisson_hit(mean, k, evidence):
    """player_model.defcon_hit_prob, duplicated so this module does not need
    the model's import-time side effects when run in tests."""
    if mean <= 0:
        return 0.0
    r = 4.0 + 11.0 * max(0.0, min(1.0, evidence))
    p = r / (r + mean)
    term = p ** r
    cum = term
    for i in range(1, k):
        term *= (r + i - 1) / i * (1 - p)
        cum += term
    return max(0.0, min(1.0, 1.0 - cum))


def expected_floor_div(mean, n, cap=20):
    if mean <= 0:
        return 0.0
    term = math.exp(-mean)
    total = 0.0
    for k in range(cap + 1):
        if k:
            term *= mean / k
        total += (k // n) * term
    return total


def _f(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# -------------------------------------------------------- expectations
def expected_components(row, fixtures, p_start, p_cameo, start_minutes, cameo_minutes, k):
    """The projection's components for one snapshot row under a start/cameo
    mixture, exactly as player_model.project() builds them. `p_cameo` is the
    UNCONDITIONAL cameo probability. Returns points per component (already
    multiplied by the position's calibration k) plus the expected xG and xA
    behind the attack term (also scaled by k — the model's belief about
    chance volume is the calibrated one, which keeps the decomposition exact
    without a separate calibration bucket)."""
    pos = row['pos']
    xg90, xa90 = _f(row.get('xg90')), _f(row.get('xa90'))
    dc90, bonus90 = _f(row.get('dc90')), _f(row.get('bonus90'))
    saves90, yellow90 = _f(row.get('saves90')), _f(row.get('yellow90'))
    w_dc = _f(row.get('dc_evidence'), _f(row.get('evidence'), 0.5))
    minute_share = (p_start * start_minutes + p_cameo * cameo_minutes) / 90.0
    start_share, cameo_share = start_minutes / 90.0, cameo_minutes / 90.0
    c = dict(attack=0.0, xg=0.0, xa=0.0, cs=0.0, gc=0.0, saves=0.0, defcon=0.0,
             appearance=0.0, bonus=0.0, yellow=0.0)
    for f in fixtures or []:
        vol = _f(f.get('xg'), 1.45) / 1.45
        exg = xg90 * minute_share * vol
        exa = xa90 * minute_share * vol
        c['xg'] += exg
        c['xa'] += exa
        c['attack'] += exg * GOAL_PTS[pos] + exa * 3.0
        if CS_PTS[pos]:
            c['cs'] += CS_PTS[pos] * _f(f.get('cs')) * p_start
        if pos in ('GKP', 'DEF'):
            c['gc'] -= expected_floor_div(_f(f.get('xgc')), 2) * p_start
        if pos == 'GKP':
            c['saves'] += (expected_floor_div(saves90 * start_share, 3) * p_start
                           + expected_floor_div(saves90 * cameo_share, 3) * p_cameo)
        thr = DC_THRESHOLD[pos]
        if thr and dc90 > 0:
            c['defcon'] += 2.0 * (p_start * poisson_hit(dc90 * start_share, thr, w_dc)
                                  + p_cameo * poisson_hit(dc90 * cameo_share, thr, w_dc))
        c['appearance'] += p_start * 2.0 + p_cameo
        c['bonus'] += bonus90 * minute_share * 0.85
        c['yellow'] -= yellow90 * minute_share
    for key in c:
        c[key] *= k
    c['total'] = (c['attack'] + c['cs'] + c['gc'] + c['saves'] + c['defcon']
                  + c['appearance'] + c['bonus'] + c['yellow'])
    return c


def deadline_mixture(row):
    """(p_start, unconditional p_cameo, start_minutes, cameo_minutes) from a
    snapshot row; older snapshots lack the cameo fields and get the defaults."""
    p_start = _f(row.get('p_start'), _f(row.get('start_rate')))
    p_cameo_cond = row.get('p_cameo')
    if p_cameo_cond is None:
        p_cameo_cond = 0.0 if row.get('pos') == 'GKP' else DEFAULT_CAMEO_PROBABILITY
    p_cameo = (1.0 - p_start) * _f(p_cameo_cond)
    start_minutes = row.get('start_minutes')
    cameo_minutes = _f(row.get('cameo_minutes'), DEFAULT_CAMEO_MINUTES)
    if start_minutes is None:
        # back out start minutes from expected minutes when the row predates
        # the field: E[min] = p_start * sm + p_cameo * cm
        em = _f(row.get('expected_minutes'))
        start_minutes = ((em - p_cameo * cameo_minutes) / p_start) if p_start > 0.01 else 0.0
        start_minutes = max(0.0, min(95.0, start_minutes))
    return p_start, p_cameo, _f(start_minutes), cameo_minutes


def actual_mixture(minutes_by_fixture, starts_total):
    """Per-fixture (p_start, p_cameo, start_minutes, cameo_minutes) at what
    actually happened. With one fixture the start flag is authoritative; in a
    double gameweek the per-fixture split falls back to 60 minutes."""
    out = []
    n = len(minutes_by_fixture)
    for mins in minutes_by_fixture:
        if n == 1:
            started = 1 if (starts_total or 0) > 0 else 0
        else:
            started = 1 if mins >= FULL_MATCH else 0
        if started:
            out.append((1.0, 0.0, float(mins), 0.0))
        elif mins > 0:
            out.append((0.0, 1.0, 0.0, float(mins)))
        else:
            out.append((0.0, 0.0, 0.0, 0.0))
    return out


ATTACK_IDS = {'goals_scored', 'assists'}
TEAM_IDS = {'clean_sheets', 'goals_conceded'}


def actual_buckets(stats, explain, pos):
    """Actual points by bucket. `explain` (FPL's per-stat breakdown) is exact;
    without it the buckets are rebuilt from the raw stats with the scoring
    rules, and any mismatch with total_points lands in `other`."""
    b = dict(attack=0.0, team=0.0, defcon=0.0, bonus=0.0, other=0.0)
    if explain:
        for fx in explain:
            for st in fx.get('stats', []):
                ident, pts = st.get('identifier'), _f(st.get('points'))
                if ident in ATTACK_IDS:
                    b['attack'] += pts
                elif ident in TEAM_IDS:
                    b['team'] += pts
                elif ident == 'bonus':
                    b['bonus'] += pts
                elif ident == 'defensive_contribution':
                    b['defcon'] += pts
                else:
                    b['other'] += pts
        return b
    s = stats or {}
    goals, assists = _f(s.get('goals_scored')), _f(s.get('assists'))
    b['attack'] = goals * GOAL_PTS[pos] + assists * 3.0
    cs = _f(s.get('clean_sheets'))
    gc = _f(s.get('goals_conceded'))
    minutes = _f(s.get('minutes'))
    b['team'] = CS_PTS[pos] * cs * (1 if minutes >= FULL_MATCH else 0)
    if pos in ('GKP', 'DEF') and minutes >= FULL_MATCH:
        b['team'] -= gc // 2
    b['bonus'] = _f(s.get('bonus'))
    dc = _f(s.get('defensive_contribution'))
    thr = DC_THRESHOLD[pos]
    b['defcon'] = 2.0 if (thr and dc >= thr) else 0.0
    b['other'] = _f(s.get('total_points')) - sum(b.values())
    return b


def decompose(row, fixtures, stats, explain, k=None):
    """The residual split. Returns (components, proj_recon, actual_total).

    components sum to actual - proj (the snapshot's) by construction; the
    reconstruction gap between the snapshot's rounded proj and the components
    rebuilt from its rounded rates is folded into `other`, and `unexplained`
    (which should be ~0) records anything the buckets missed.
    """
    pos = row['pos']
    k = _f(k if k is not None else row.get('k'), 1.0) or 1.0
    proj = _f(row.get('proj'))
    stats = stats or {}
    # expected at the deadline
    p_start, p_cameo, sm, cm = deadline_mixture(row)
    E = expected_components(row, fixtures, p_start, p_cameo, sm, cm, k)
    # expected at what happened
    minutes_total = int(_f(stats.get('minutes')))
    starts_total = stats.get('starts')
    if explain and len(explain) > 1:
        per_fx = []
        for fx in explain:
            m = next((st.get('value') for st in fx.get('stats', [])
                      if st.get('identifier') == 'minutes'), 0)
            per_fx.append(int(_f(m)))
        if sum(per_fx) != minutes_total:
            per_fx = [minutes_total] + [0] * (len(explain) - 1)
    else:
        per_fx = [minutes_total]
    fixtures = list(fixtures or [])
    if len(fixtures) < len(per_fx):
        filler = fixtures[-1] if fixtures else dict(xg=1.45, xgc=1.45, cs=0.25)
        fixtures = fixtures + [filler] * (len(per_fx) - len(fixtures))
    Ea = dict(attack=0.0, xg=0.0, xa=0.0, cs=0.0, gc=0.0, saves=0.0, defcon=0.0,
              appearance=0.0, bonus=0.0, yellow=0.0, total=0.0)
    for f, mix in zip(fixtures, actual_mixture(per_fx, starts_total)):
        part = expected_components(row, [f], *mix, k)
        for key in Ea:
            Ea[key] += part[key]
    actual = actual_buckets(stats, explain, pos)
    actual_total = _f(stats.get('total_points'), sum(actual.values()))
    xg, xa = _f(stats.get('expected_goals')), _f(stats.get('expected_assists'))
    goal_pts = GOAL_PTS[pos]
    chance = (xg - Ea['xg']) * goal_pts + (xa - Ea['xa']) * 3.0
    finishing = actual['attack'] - (xg * goal_pts + xa * 3.0)
    team = actual['team'] - (Ea['cs'] + Ea['gc'])
    defcon = actual['defcon'] - Ea['defcon']
    bonus = actual['bonus'] - Ea['bonus']
    other_actual = actual['other']
    other_expected = Ea['saves'] + Ea['appearance'] + Ea['yellow']
    recon_gap = E['total'] - proj
    other = other_actual - other_expected + recon_gap
    minutes = Ea['total'] - E['total']
    named = minutes + chance + finishing + team + defcon + bonus + other
    comps = dict(minutes=minutes, chance=chance, finishing=finishing, team=team,
                 defcon=defcon, bonus=bonus, other=other,
                 unexplained=(actual_total - proj) - named,
                 recon_gap=recon_gap)
    return {key: round(v, 3) for key, v in comps.items()}, round(E['total'], 3), actual_total


# ---------------------------------------------------------- classify
def _first(v):
    try:
        return int(v) == 1
    except (TypeError, ValueError):
        return False


def setpiece_changes(row, now):
    """Which first-choice duties changed between the snapshot and now."""
    changes = []
    for key, label in (('pens', 'penalties'), ('corners', 'corners'), ('fk', 'free kicks')):
        before, after = _first(row.get(key)), _first(now.get(key))
        if before != after:
            changes.append(f'now {"first" if after else "not first"} on {label}'
                           + (f' (was {"first" if before else row.get(key) or "unlisted"})'))
    return changes


def xgi_window(gw_rows, row, gw, n=ROLE_WINDOW_STARTS, sd=XGI_MATCH_SD):
    """The last n starts of 60'+ up to and including `gw`: (sum xGI, expected
    sum, band half-width) or None when there are fewer than n such starts.
    Expected uses the snapshot's shrunk rates at the actual minutes and a
    fixture-average volume (the archived per-fixture xG is not kept per past
    match) — noted as an approximation in the plan."""
    starts = [r for r in gw_rows if r.get('round') is not None and int(r['round']) <= gw
              and (r.get('starts') or 0) > 0 and (r.get('mins') or 0) >= FULL_MATCH]
    if len(starts) < n:
        return None
    last = sorted(starts, key=lambda r: (int(r['round']), r.get('fixture_id') or 0))[-n:]
    xgi = sum(_f(r.get('xg')) + _f(r.get('xa')) for r in last)
    rate = _f(row.get('xg90')) + _f(row.get('xa90'))
    expected = sum(rate * (_f(r.get('mins')) / 90.0) for r in last)
    return round(xgi, 2), round(expected, 2), round(Z80 * sd * math.sqrt(n), 2)


def classify(row, stats, now, comps, deadline_iso, gw_rows=None, gw=None):
    """One class per player, in the plan's precedence. Returns
    (class, subtype, tags, note)."""
    stats = stats or {}
    now = now or {}
    minutes = int(_f(stats.get('minutes')))
    starts_total = stats.get('starts')
    started = (starts_total or 0) > 0 if starts_total is not None else minutes >= FULL_MATCH
    p_start = _f(row.get('p_start'), _f(row.get('start_rate')))
    snap_status = row.get('status', 'a')
    overridden_out = (row.get('availability_source') not in (None, '', 'model baseline')
                      and p_start < 0.2)
    now_status = now.get('status', 'a')
    news_added = now.get('news_added') or ''
    flagged_since = bool(news_added and deadline_iso and news_added > deadline_iso
                         and now_status != 'a')
    red = _f(stats.get('red_cards')) > 0
    tags = []
    xg, xa = _f(stats.get('expected_goals')), _f(stats.get('expected_assists'))
    goals, assists = int(_f(stats.get('goals_scored'))), int(_f(stats.get('assists')))
    actual = _f(stats.get('total_points'))
    proj = _f(row.get('proj'))
    resid = actual - proj

    if snap_status != 'a' or overridden_out or now_status in ('i', 's', 'd', 'u') \
            or flagged_since or red:
        why = ('red card' if red else
               f'status {now_status} now' if now_status != 'a' else
               f'status {snap_status} at the deadline' if snap_status != 'a' else
               'override had him out')
        return 'unavailable', None, tags, why

    if p_start >= LIKELY_START and not started:
        sub = 'dnp' if minutes == 0 else 'cameo'
        note = (f'{minutes} minutes, healthy (status a; deadline start estimate '
                f'{p_start * 100:.0f}%)')
        return 'minutes_loss', sub, tags, note

    if started and minutes <= FULL_MATCH:
        return 'minutes_watch', 'hooked', tags, f'started, hooked on {minutes}\''
    if WATCH_START_LO <= p_start < LIKELY_START and not started:
        return 'minutes_watch', 'fringe', tags, (
            f'did not start (deadline start estimate {p_start * 100:.0f}%)')

    if p_start <= GAIN_START and started:
        tags.append('breakout_minutes')
        return 'minutes_gain', None, tags, (
            f'started at a {p_start * 100:.0f}% deadline estimate, {minutes}\'')

    if minutes >= FULL_MATCH:
        changes = setpiece_changes(row, now)
        if changes:
            tags.append('setpiece_change')
            return 'role_change', 'setpiece', tags, '; '.join(changes)
        window = xgi_window(gw_rows or [], row, gw) if gw is not None else None
        if window:
            got, exp, band = window
            if abs(got - exp) > band:
                tags.append('xgi_shift')
                direction = 'above' if got > exp else 'below'
                return 'role_change', 'xgi', tags, (
                    f'last {ROLE_WINDOW_STARTS} starts: {got:.2f} xGI vs {exp:.2f} '
                    f'expected, {direction} the 80% band (+-{band:.2f}) — reassess')

    played_enough = minutes >= FULL_MATCH or (
        row.get('expected_minutes') is not None
        and minutes >= _f(row.get('expected_minutes')) - 15)
    noise = comps['finishing'] + comps['team'] + comps['bonus'] + comps['defcon']
    if played_enough and abs(comps['chance']) < CHANCE_BAND and abs(noise) >= RESIDUAL_BAND:
        returns = f'{goals} goal{"s" if goals != 1 else ""}'
        if assists:
            returns += f', {assists} assist{"s" if assists != 1 else ""}'
        if resid < 0:
            tags.append('blanked_good_xg' if xg + xa >= 0.5 else 'blank')
        else:
            tags.append('hauled_low_xg' if comps['finishing'] >= 3 else 'haul')
        note = (f'{minutes}\', {xg:.2f} xG, {returns}, {actual:.0f} pts '
                f'(proj {proj:.1f}). ')
        biggest = max(('finishing', 'team', 'bonus', 'defcon'), key=lambda c: abs(comps[c]))
        return 'variance', biggest, tags, note + f'{biggest.capitalize()} ({comps[biggest]:+.1f}).'

    if abs(resid) >= RESIDUAL_BAND:
        tags.append('large_residual')
    return 'on_model', None, tags, f'{minutes}\', {actual:.0f} pts (proj {proj:.1f})'


# -------------------------------------------------------------- inputs
def load_snapshot(gw):
    path = HISTORY / f'gw{gw}.json'
    return json.loads(path.read_text()) if path.exists() else None


def load_gw_rows(season='2026/27'):
    """{player code: [row dicts]} from the DB when present, else the CSV."""
    out = {}
    if DB.exists():
        try:
            cx = sqlite3.connect(DB)
            has = cx.execute("SELECT name FROM sqlite_master WHERE name='gw_stat'").fetchone()
            if has:
                for code, rnd, fid, mins, starts, xg, xa in cx.execute(
                        'SELECT code, round, fixture_id, minutes, starts, xg, xa '
                        'FROM gw_stat WHERE season = ?', (season,)):
                    out.setdefault(code, []).append(dict(
                        round=rnd, fixture_id=fid, mins=mins or 0, starts=starts or 0,
                        xg=xg or 0.0, xa=xa or 0.0))
            cx.close()
            if out:
                return out
        except sqlite3.Error:
            out = {}
    if GW_CSV.exists():
        with open(GW_CSV, newline='') as fh:
            for r in csv.DictReader(fh):
                if r.get('season', season) != season:
                    continue
                out.setdefault(int(r['code']), []).append(dict(
                    round=int(_f(r.get('round'))), fixture_id=int(_f(r.get('fixture_id'))),
                    mins=int(_f(r.get('minutes'))), starts=int(_f(r.get('starts'))),
                    xg=_f(r.get('xg')), xa=_f(r.get('xa'))))
    return out


def load_new_projections():
    if not PROJ.exists():
        return {}, None
    d = json.loads(PROJ.read_text())
    return {p['id']: p for p in d['players']}, d.get('calibration') or {}


def load_previous_retro(gw, n=HISTORY_LEN - 1):
    """{player id: [classes of the previous n gameweeks, oldest first]}"""
    out = {}
    for g in range(max(1, gw - n), gw):
        path = HISTORY / f'gw{g}_retro.json'
        if not path.exists():
            continue
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for r in d.get('players', []):
            out.setdefault(r['id'], []).append(
                dict(gw=g, cls=r.get('class'), subtype=r.get('subtype')))
    return out


# ---------------------------------------------------------------- run
def review(gw, snap, actuals, boot_elements, new_proj, calibration, gw_rows, previous):
    """The pass. Returns the gw{n}_retro.json payload."""
    deadline = snap.get('deadline') or ''
    team_cs = snap.get('team_cs') or {}
    stats_all = actuals.get('stats') or {}
    explain_all = actuals.get('explain') or {}
    points_all = actuals.get('points') or {}
    code_of = {int(e['id']): e.get('code') for e in boot_elements.values()}
    has_rates = any(r.get('xg90') is not None for r in snap['players'])
    out = []
    for row in snap['players']:
        pid = row['id']
        if _f(row.get('p_play')) < POOL_MIN_PLAY and _f(row.get('proj')) < POOL_MIN_PROJ:
            continue
        pts_row = points_all.get(str(pid))
        if pts_row is None:
            continue
        stats = dict(stats_all.get(str(pid)) or {})
        if not stats:
            # older actual caches only have (points, minutes, starts)
            stats = dict(total_points=pts_row[0], minutes=pts_row[1],
                         starts=pts_row[2] if len(pts_row) > 2 else None)
        explain = explain_all.get(str(pid))
        now = boot_elements.get(pid) or {}
        now_view = dict(status=now.get('status', 'a'), news=now.get('news', ''),
                        news_added=now.get('news_added'),
                        pens=now.get('penalties_order'),
                        corners=now.get('corners_and_indirect_freekicks_order'),
                        fk=now.get('direct_freekicks_order'))
        row_eff = dict(row)
        rates_source = 'snapshot'
        newp = new_proj.get(pid) or {}
        if not has_rates or row.get('xg90') is None:
            # the snapshot predates the rate fields (GW1): fall back to this
            # run's shrunk rates, which move ~1-3% a week for a regular
            rates_source = 'current'
            for key in ('xg90', 'xa90', 'dc90', 'bonus90', 'saves90', 'yellow90',
                        'evidence', 'dc_evidence'):
                if row_eff.get(key) is None and newp.get(key) is not None:
                    row_eff[key] = newp[key]
            if row_eff.get('k') is None:
                row_eff['k'] = (calibration or {}).get(row['pos'], newp.get('calibration_k', 1.0))
        if 'pens' not in row and newp:
            # only a snapshot that predates the set-piece fields (GW1) takes
            # the fallback: a None in a newer snapshot means "not a taker",
            # and comparing now with now would hide every change of duty
            row_eff['pens'] = newp.get('pens')
            row_eff['corners'] = newp.get('corners')
            row_eff['fk'] = newp.get('fk')
        fixtures = team_cs.get(row['team']) or []
        comps, proj_recon, actual_total = decompose(row_eff, fixtures, stats, explain)
        rows_for_player = gw_rows.get(code_of.get(pid), []) if gw_rows else []
        cls, sub, tags, note = classify(row_eff, stats, now_view, comps, deadline,
                                        rows_for_player, gw)
        hist = previous.get(pid, [])
        # consecutive non-starts of a healthy regular, counting this week
        streak = 0
        for h in reversed([*hist, dict(cls=cls, subtype=sub)]):
            if h.get('cls') == 'minutes_loss':
                streak += 1
            else:
                break
        next_gw = gw + 1
        proj_next = None
        if newp:
            v = newp.get('proj_by_gw') or []
            proj_next = round(v[next_gw - 1], 2) if 0 <= next_gw - 1 < len(v) else None
        start_prev = row.get('baseline_start')
        start_now = newp.get('baseline_start_rate') if newp else None
        move = None
        if start_prev is not None and start_now is not None:
            d = start_now - start_prev
            move = (f'start estimate {start_prev * 100:.0f}% -> {start_now * 100:.0f}%'
                    if abs(d) >= 0.02 else 'start estimate unchanged')
        if cls == 'minutes_loss':
            note += (' — second consecutive non-start: sell-grade.' if streak >= 2
                     else ' First non-start of a regular: check Friday\'s presser; '
                          'two in a row is sell-grade.')
            if move == 'start estimate unchanged':
                note += ' (The model has NOT registered the benching — W1/W2 still open.)'
        entry = dict(
            id=pid, name=row['name'], team=row['team'], pos=row['pos'],
            price=row.get('price'), proj=round(_f(row.get('proj')), 2),
            proj_recon=proj_recon, actual=actual_total,
            minutes=int(_f(stats.get('minutes'))), starts=stats.get('starts'),
            xg=round(_f(stats.get('expected_goals')), 2),
            xa=round(_f(stats.get('expected_assists')), 2),
            goals=int(_f(stats.get('goals_scored'))), assists=int(_f(stats.get('assists'))),
            bonus=int(_f(stats.get('bonus'))),
            p_start=row.get('p_start'), status=row.get('status'),
            status_now=now_view['status'],
            components=comps, subtype=sub, tags=tags,
            note=note, streak=streak, rates_source=rates_source,
            history=[dict(gw=h['gw'], cls=h['cls']) for h in hist][-(HISTORY_LEN - 1):],
            proj_next=proj_next, next_gw=next_gw, start_move=move,
            start_prev=start_prev, start_now=start_now,
        )
        entry['class'] = cls
        out.append(entry)
    counts = Counter(r['class'] for r in out)
    unexplained = [r for r in out if abs(r['components']['unexplained']) > 0.01]
    return dict(
        gw=gw, deadline=deadline,
        generated=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        checked=bool(actuals.get('checked')),
        n_players=len(out), counts=dict(counts),
        n_unexplained=len(unexplained),
        squad=snap.get('squad') or [],
        thresholds=dict(likely_start=LIKELY_START, residual_band=RESIDUAL_BAND,
                        chance_band=CHANCE_BAND, xgi_match_sd=XGI_MATCH_SD),
        players=out,
    )


def roll_summary(retro, prior=None):
    """data/retro.json: per-class counts per gameweek and, once the scorecard
    has graded a following week, the per-class next-week outcome."""
    prior = prior or {'gws': []}
    gws = [g for g in prior.get('gws', []) if g.get('gw') != retro['gw']]
    gws.append(dict(gw=retro['gw'], n=retro['n_players'], counts=retro['counts'],
                    generated=retro['generated']))
    gws.sort(key=lambda g: g['gw'])
    graded = {}
    sc = ROOT / 'data' / 'scorecard.json'
    if sc.exists():
        try:
            for g in json.loads(sc.read_text()).get('gws', []):
                seen_bare = set()
                for cls, v in (g.get('retro_class') or {}).items():
                    # the scorecard keys by class/subtype; roll both the
                    # qualified key and the bare class
                    keys = [cls]
                    bare = cls.split('/')[0]
                    if bare != cls:
                        keys.append(bare)
                    for key in keys:
                        agg = graded.setdefault(key, dict(n=0, started=0.0, resid=0.0, gws=0))
                        agg['n'] += v['n']
                        agg['started'] += v['next_start_rate'] * v['n']
                        agg['resid'] += v['next_residual_mean'] * v['n']
                        if key not in seen_bare:
                            agg['gws'] += 1
                            seen_bare.add(key)
        except (OSError, ValueError):
            pass
    by_class = {cls: dict(n=v['n'], gws=v['gws'],
                          next_start_rate=round(v['started'] / v['n'], 3) if v['n'] else None,
                          next_residual_mean=round(v['resid'] / v['n'], 2) if v['n'] else None)
                for cls, v in graded.items()}
    return dict(generated=retro['generated'], latest_gw=retro['gw'], gws=gws,
                classes_graded_gws=max([v['gws'] for v in graded.values()], default=0),
                by_class=by_class,
                note='Classes never change a projection or a transfer number; '
                     'they change what the digest shows first and how it words it.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gw', type=int, help='gameweek to review (default: the latest '
                                          'finished gameweek with a snapshot)')
    ap.add_argument('--no-fetch', action='store_true',
                    help='use cached bootstrap/actuals only')
    args = ap.parse_args()

    import scorecard as SC
    if args.no_fetch:
        if not BOOT_CACHE.exists():
            raise SystemExit('no cached bootstrap; run fetch.py first')
        boot = json.loads(BOOT_CACHE.read_text())
    else:
        try:
            boot = SC.api('bootstrap-static/')
        except Exception:
            boot = json.loads(BOOT_CACHE.read_text())
    events = boot['events']
    finished = [e['id'] for e in events if e.get('finished')]
    gw = args.gw or (max(finished) if finished else None)
    if not gw:
        print('retro: no finished gameweek yet')
        return
    snap = load_snapshot(gw)
    if not snap:
        print(f'retro: no snapshot for GW{gw} (data/history/gw{gw}.json)')
        return
    actuals = SC.actuals_for(gw, events)
    if not actuals:
        print(f'retro: GW{gw} not finished yet')
        return
    elements = {int(e['id']): e for e in boot['elements']}
    new_proj, calibration = load_new_projections()
    gw_rows = load_gw_rows()
    previous = load_previous_retro(gw)
    retro = review(gw, snap, actuals, elements, new_proj, calibration, gw_rows, previous)
    HISTORY.mkdir(parents=True, exist_ok=True)
    path = HISTORY / f'gw{gw}_retro.json'
    path.write_text(json.dumps(retro, separators=(',', ':')))
    prior = json.loads(OUT.read_text()) if OUT.exists() else None
    OUT.write_text(json.dumps(roll_summary(retro, prior), indent=1))
    counts = ', '.join(f'{c} {retro["counts"].get(c, 0)}' for c in CLASS_ORDER)
    print(f'retro GW{gw}: {retro["n_players"]} players classified ({counts}); '
          f'{retro["n_unexplained"]} with an unexplained residual -> {path}')
    for r in sorted(retro['players'], key=lambda r: -abs(r['actual'] - r['proj']))[:12]:
        c = r['components']
        print(f"  {r['name']:<14}{r['proj']:>5.1f}{r['actual']:>5.0f}{r['minutes']:>5} "
              f"{c['minutes']:+5.1f}/{c['chance']:+5.1f}/{c['finishing']:+5.1f}/"
              f"{c['team']:+5.1f}/{c['bonus']:+5.1f}  {r['class']}"
              + (f"/{r['subtype']}" if r['subtype'] else ''))


if __name__ == '__main__':
    main()
