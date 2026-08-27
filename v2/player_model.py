"""
Player model v2.

Every shrinkage weight below comes from the measured year-over-year stability in
stability.py rather than from judgement. The headline findings that shaped it:

  metric        stability   what it means for the model
  ------------  ---------   -----------------------------------------------
  xGI/90            0.91    the most repeatable attacking signal there is
  xG/90             0.90    and it beats goals/90 (0.82) at predicting goals
  xA/90             0.84    beats assists/90 (0.59) everywhere, hugely for FWDs
  DefCon/90         0.56    a real, persistent skill, but needs real shrinkage
  starts            0.46    only moderately repeatable
  clean sheets/90   0.21    ALMOST NO SIGNAL -- 0.09 for MID and FWD
  bonus/90 (DEF)    0.14    defender bonus is close to pure noise

Two of those change the design outright:

  * CLEAN SHEETS COME FROM THE TEAM MODEL, NOT THE PLAYER. A player's own
    clean-sheet history barely predicts his next season (0.21). v1 used it
    directly, which was wrong. v2 takes the clean-sheet probability from the
    fitted Dixon-Coles scoreline distribution for that specific fixture.

  * ATTACK IS BUILT ON xG AND xA, NOT GOALS AND ASSISTS. Finishing regresses;
    chance quality persists.

Rates are shrunk towards a positional prior by an empirical-Bayes weight that
falls out of the stability coefficient and the sample size, with older seasons
discounted and a small age adjustment (peak 24, 13% swing across the range).
"""
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
SEASON_VIEW = ROOT / 'v2' / 'season_view.json'
OUT = ROOT / 'v2' / 'projections_v2.json'

SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26', '2026/27']
SEASON_WEIGHT = {'2022/23': 0.30, '2023/24': 0.50, '2024/25': 0.75, '2025/26': 1.0,
                 '2026/27': 1.0}
CURRENT = '2026/27'
# How the model learns in-season. The current season is one more row in the
# panel, weighted by minutes like every other. Because a regular's prior
# seasons already carry ~7,000 weighted minutes, his own 2026/27 rates are only
# ~10% of his evidence by GW10 and ~17% by GW19 (a one-season player: ~21% and
# ~30%); see RESEARCH-INSEASON-LEARNING.md §1.3. For a stable player that is
# right (xG/90 repeats at 0.90); a per-player `current_mult` (P5) can raise the
# current season's weight once the measurement in backtest_inseason.py --rates
# says by how much.
#
# Minutes are handled separately in minutes_model(). Two rules exist:
#
#   aggregate  this season's start rate = starts / team games played, trusted
#              n / (n + CURRENT_TRUST_K) after n team games (production).
#   recency    per-fixture evidence (gw_stat, P1): each fixture the player was
#              AVAILABLE for counts as a start (1) or not (0), weighted
#              0.5 ** (games_ago / RECENCY_HALF_LIFE); the weighted rate is
#              trusted n_eff / (n_eff + RECENCY_K). Games he was injured,
#              suspended or not yet signed for are not evidence (P2 / W2).
#
# MINUTES_RULE picks production; the other rule is always computed too and
# archived in the snapshot (p_start_recency / p_start_aggregate) so
# scorecard.py grades both side by side (recency_vs_aggregate_lift).
#
# MEASURED 27 Aug 2026 (backtest_inseason.py --minutes, 107,801 player-GW
# predictions over 2022/23-2025/26, "starts in GW n+1" from rows through n):
#   aggregate (K=4)           Brier 0.1181   regulars (prior start >= 0.7) 0.1735
#   recency K=2  HL=3               0.1014                                 0.1472
#   recency K=1  HL=3               0.0952                                 0.1348
#   recency K=0.5 HL=2              0.0911  (grid edge)                    0.1272
# Recency wins in every phase (GW2-8, 9-24, 25-37) and every prior-start
# band, and keeps improving to the smallest K and shortest half-life tested:
# the last one or two games are nearly all the evidence that matters. The
# harness cannot see injuries (every club fixture counts as evidence), so
# some of that edge is the rule "predicting" a continued absence that the
# availability layer handles in production; hence one step inside the
# optimum: K=1, HL=3. At K=1/HL=3 a 0.9 regular is 0.45 after one benching
# and 0.26 after three (the plan asked for < 0.4). The forward scorecard's
# recency_vs_aggregate_lift is the check that this survives contact with
# real availability; revisit K=0.5/HL=2 if it does over >= 4 gameweeks.
#
# Minutes per start use their own trust constant: the grid measured start
# probability only, and K=1 would let one 67' start move a regular's 90 to
# 78. RECENCY_MPS_K keeps the old, unmeasured behaviour for that half.
CURRENT_TRUST_K = 4.0
RECENCY_K = 1.0
RECENCY_HALF_LIFE = 3.0
RECENCY_MPS_K = 4.0
MINUTES_RULE = os.environ.get('FPL_MINUTES_RULE', 'recency')
if MINUTES_RULE not in ('aggregate', 'recency'):
    raise SystemExit(f'FPL_MINUTES_RULE must be aggregate or recency, not {MINUTES_RULE!r}')
# Deadline availability per archived gameweek, {gw: {player id: (status,
# p_start)}}, read from data/history/gw{n}.json by main(): the "was he
# available" signal the recency rule conditions on.
SNAPSHOT_STATUS = {}
GW_ROWS_LOADED = False

# The projection window ROLLS: it starts at the next gameweek and runs six
# ahead. `proj_by_gw` is indexed by absolute gameweek (entry 0 = GW1) so every
# consumer can keep asking for `proj_by_gw[gw - 1]`; gameweeks already played
# hold a zero. HORIZON is the last gameweek covered, START_GW the first.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gwclock import window as _gw_window          # noqa: E402
from availability import (  # noqa: E402
    availability_forecast,
    deadline_start_probability,
    load_overrides,
    status_for_gameweek,
)
START_GW, HORIZON = _gw_window()
WINDOW = HORIZON - START_GW + 1
LAST_GW = 38
SEASON = {}                       # id -> per-gameweek projection to LAST_GW
OUT_SEASON = ROOT / 'v2' / 'projections_season.json'
AVAILABILITY_OVERRIDES = load_overrides()
BOOT_CACHE = ROOT / 'v2' / 'cache' / 'bootstrap.json'
if BOOT_CACHE.exists():
    _events = json.loads(BOOT_CACHE.read_text()).get('events', [])
    GW_DEADLINES = {int(event['id']): event['deadline_time'] for event in _events}
else:
    GW_DEADLINES = {}

# Measured year-over-year stability, used as the empirical-Bayes reliability of
# a full season of evidence. A metric at 0.90 keeps nearly all of a player's own
# number; one at 0.21 is pulled almost entirely to the positional average.
STABILITY = {'xg90': 0.90, 'xa90': 0.84, 'dc90': 0.56, 'bonus90': 0.54,
             'saves90': 0.70, 'yellow90': 0.45}
STABILITY_DEF_BONUS = 0.14      # defender bonus specifically is near-noise

FULL_SEASON_MINS = 2200.0       # what counts as "one full season of evidence"

# Metrics the API only started recording partway through the panel. Seasons
# before these dates hold a zero that means "not measured", not "measured zero",
# and must be excluded from both the shrinkage and the prior it shrinks towards.
METRIC_FIRST_SEASON = {'dc90': '2024/25'}

GOAL_PTS = {'GKP': 6, 'DEF': 6, 'MID': 5, 'FWD': 4}
CS_PTS = {'GKP': 4, 'DEF': 4, 'MID': 1, 'FWD': 0}
DC_THRESHOLD = {'GKP': None, 'DEF': 10, 'MID': 12, 'FWD': 12}

# Aging: per-90 output relative to the peak at 24, from the measured curve.
AGE_CURVE = {19: 0.94, 20: 0.97, 21: 0.96, 22: 0.99, 23: 0.99, 24: 1.00,
             25: 1.00, 26: 0.97, 27: 0.99, 28: 0.98, 29: 0.95, 30: 0.95,
             31: 0.92, 32: 0.94, 33: 0.95, 34: 0.96}


def age_factor(dob, season_year=2026):
    if not dob:
        return 1.0
    a = season_year - int(dob[:4])
    return AGE_CURVE.get(min(34, max(19, a)), 0.95)


# --------------------------------------------------------------- loading
def load():
    cx = sqlite3.connect(DB)
    players = {}
    for row in cx.execute("""
        SELECT id, code, web_name, full_name, team, pos, price, sel_pct,
               status, news, chance, joined, birth_date, pens, corners, fk
        FROM player"""):
        (pid, code, name, full, team, pos, price, sel, status, news, chance,
         joined, dob, pens, corners, fk) = row
        players[pid] = dict(id=pid, code=code, name=name, full_name=full,
                            team=team, pos=pos, price=price, sel_pct=sel,
                            status=status, news=news or '', chance=chance,
                            joined=joined or '', dob=dob, pens=pens,
                            corners=corners, fk=fk, hist=[])
    by_code = defaultdict(list)
    for row in cx.execute(f"""
        SELECT code, season, minutes, starts, points, goals, assists, xg, xa,
               xgc, defcon, clean_sheets, bonus, saves, yellow, bps
        FROM season_stat WHERE season IN ({','.join('?' * len(SEASONS))})""", SEASONS):
        (code, season, mins, starts, pts, g, a, xg, xa, xgc, dc, cs, bonus,
         saves, yellow, bps) = row
        # A past season with no minutes carries no information and is dropped.
        # THIS season's zero row is different: it is the record of a player
        # who has not played while his team has, and the minutes model needs
        # it (P0 / W1 in RESEARCH-INSEASON-LEARNING.md). Dropping it meant a
        # benched regular kept his pre-season start rate until his first
        # minute, while a five-minute cameo by someone else was penalised.
        # The rate consumers are unaffected: shrink() needs 200+ minutes and
        # positional_priors() 450+, so a zero row never reaches either.
        if not mins and season != CURRENT:
            continue
        mins = mins or 0
        p90 = mins / 90.0 if mins else None

        def per90(v):
            return (v or 0) / p90 if p90 else 0.0
        by_code[code].append(dict(
            season=season, mins=mins, starts=starts or 0, pts=pts or 0,
            pts90=per90(pts), xg90=per90(xg), xa90=per90(xa),
            dc90=per90(dc), bonus90=per90(bonus),
            saves90=per90(saves), yellow90=per90(yellow),
            g=g or 0, a=a or 0, cs=cs or 0, defcon_raw=dc or 0,
            xg=xg or 0.0, xa=xa or 0.0))
    # this season's per-fixture rows (P1): the sequence behind the aggregate,
    # which the recency-weighted minutes rule (P2) and the retrospective read.
    gw_by_code = defaultdict(list)
    if cx.execute("SELECT name FROM sqlite_master WHERE name = 'gw_stat'").fetchone():
        for row in cx.execute("""
            SELECT code, round, fixture_id, opponent, was_home, kickoff, minutes,
                   starts, points, goals, assists, xg, xa, xgc, defcon, bonus,
                   bps, saves, yellow, red
            FROM gw_stat WHERE season = ? ORDER BY kickoff, fixture_id""", (CURRENT,)):
            (code, rnd, fid, opp, home, kickoff, mins, starts, pts, g, a, xg, xa,
             xgc, dc, bonus, bps, saves, yellow, red) = row
            gw_by_code[code].append(dict(
                round=rnd, fixture_id=fid, opp=opp, home=bool(home),
                # starts stays None when the source did not record it, so
                # match_evidence() can fall back to the 60-minute rule
                kickoff=kickoff or '', mins=mins or 0, starts=starts,
                pts=pts or 0, g=g or 0, a=a or 0, xg=xg or 0.0, xa=xa or 0.0,
                xgc=xgc or 0.0, defcon=dc or 0, bonus=bonus or 0, bps=bps or 0,
                saves=saves or 0, yellow=yellow or 0, red=red or 0))
    for p in players.values():
        p['hist'] = sorted(by_code.get(p['code'], []), key=lambda h: h['season'])
        # this season's row is kept separately too: it is what the minutes
        # model reads for "how often is he starting NOW"
        p['now'] = next((h for h in p['hist'] if h['season'] == CURRENT), None)
        p['gw'] = gw_by_code.get(p['code'], [])
    cx.close()
    return players


def games_played():
    """{team short: matches finished this season} from the cached fixture list."""
    path = ROOT / 'v2' / 'cache' / 'fixtures.json'
    boot = ROOT / 'v2' / 'cache' / 'bootstrap.json'
    if not path.exists() or not boot.exists():
        return {}
    short = {t['id']: t['short_name'] for t in json.loads(boot.read_text())['teams']}
    out = defaultdict(int)
    for x in json.loads(path.read_text()):
        if x.get('finished') and x.get('team_h_score') is not None:
            out[short[x['team_h']]] += 1
            out[short[x['team_a']]] += 1
    return dict(out)


GAMES_PLAYED = {}


def team_fixtures():
    """{team short: [dict(fixture_id, event, kickoff)] finished this season,
    in kickoff order} — the sequence the recency-weighted minutes rule (P2)
    walks. Same source as games_played(); empty when there is no cache."""
    path = ROOT / 'v2' / 'cache' / 'fixtures.json'
    boot = ROOT / 'v2' / 'cache' / 'bootstrap.json'
    if not path.exists() or not boot.exists():
        return {}
    short = {t['id']: t['short_name'] for t in json.loads(boot.read_text())['teams']}
    out = defaultdict(list)
    for x in json.loads(path.read_text()):
        if x.get('finished') and x.get('team_h_score') is not None:
            row = dict(fixture_id=x['id'], event=x.get('event'),
                       kickoff=x.get('kickoff_time') or '')
            out[short[x['team_h']]].append(row)
            out[short[x['team_a']]].append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (r['kickoff'], r['fixture_id']))
    return dict(out)


TEAM_FIXTURES = {}


def positional_priors(players):
    """Minutes-weighted positional means -- the target of the shrinkage.

    Metrics that did not exist for the whole panel must only average the seasons
    where they were actually recorded. Defensive contributions arrived in
    2024/25, so the two earlier seasons carry a literal zero for every player.
    Averaging those in halved the prior — 4.55 against a true 7.55 for defenders
    and 5.01 against 8.50 for midfielders — and every player's DefCon estimate
    was then shrunk towards that phantom value. shrink() already excluded those
    rows; this did not, and the two have to agree.
    """
    acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for p in players.values():
        for h in p['hist']:
            if h['mins'] < 450:
                continue
            for m in ('xg90', 'xa90', 'dc90', 'bonus90', 'saves90', 'yellow90'):
                if h['season'] < METRIC_FIRST_SEASON.get(m, '0000/00'):
                    continue
                acc[p['pos']][m][0] += h[m] * h['mins']
                acc[p['pos']][m][1] += h['mins']
    return {pos: {m: (v[0] / v[1] if v[1] else 0.0) for m, v in d.items()}
            for pos, d in acc.items()}


PRICE_MEDIAN = {}


def load_overlay():
    """Research the data cannot contain: confirmed line-up decisions, role
    changes, squad-depth reality. Reuses the v1 overlay, which is keyed by FPL
    element id and still current. Price-rank ordering cannot resolve two keepers
    on the same price -- only a press conference can."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('ov', ROOT / 'overlay.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.OVERLAY, mod.PRESEASON_FORM


OVERLAY, PRESEASON_FORM = load_overlay()


# P5: how much MORE to believe the current season's rates for a player whose
# context changed (new club, new manager). 1.0 = the plain minutes weight.
# MEASURED 27 Aug 2026 (backtest_inseason.py --rates, rest-of-season xG/90
# and xA/90 from the first n gameweeks, 2022/23-2025/26): for changed-context
# players the plain blend already learns — prior-only wMAE 0.0667 vs blend
# 0.0612 at n=3 (Spearman 0.656 -> 0.699) — and m=2 improves on m=1 by
# 0.0002 at best (n=3-5) while m>=5 is worse everywhere; for stable players
# m=1 is best at every n. So the multiplier stays at 1.0: the shrinkage is
# right as it stands and there is nothing to buy here.
CONTEXT_CURRENT_MULT = 1.0


def context_changed(p):
    """True when the player's prior-season rates were earned in a different
    context: a summer arrival, or a club with a new manager. (Position and
    penalty-duty changes are in the plan too, but the database does not keep
    a player's past-season position or pen order, so they cannot be detected
    here; the retrospective's role_change class surfaces pen changes weekly.)"""
    if (p.get('joined') or '') >= '2026-05-01':
        return True
    try:
        from manager_changes import NEW_MANAGER
    except ImportError:
        return False
    return p.get('team') in NEW_MANAGER


def context_multiplier(p):
    return CONTEXT_CURRENT_MULT if context_changed(p) else 1.0


def shrink(p, metric, priors, current_mult=None):
    """Empirical-Bayes estimate of a player's true rate for one metric.

    Blends his own (recency-weighted) history with the positional prior. The
    weight on his own number is n/(n+k), where n is his effective sample in
    full-season units and k is set by the measured stability: a stable metric
    needs little evidence to be believed, an unstable one needs a lot.

    `current_mult` scales the CURRENT season's minutes weight (P5); by default
    it is context_multiplier(p), which is 1.0 until measured.
    """
    prior = priors.get(p['pos'], {}).get(metric, 0.0)
    mult = context_multiplier(p) if current_mult is None else current_mult
    stab = STABILITY.get(metric, 0.5)
    if metric == 'bonus90' and p['pos'] == 'DEF':
        stab = STABILITY_DEF_BONUS
    # DefCon only exists from 2024/25, so older seasons carry no information
    first = METRIC_FIRST_SEASON.get(metric, '0000/00')
    seasons = [h for h in p['hist'] if h['mins'] >= 200 and h['season'] >= first]
    if not seasons:
        # The prior is the average of ESTABLISHED players at this position, so
        # handing it whole to someone with no Premier League record over-rates
        # unknown teenagers. Discount it, and lean on price, which encodes the
        # game-makers' own expectation.
        price_ratio = p['price'] / max(4.0, PRICE_MEDIAN.get(p['pos'], 5.5))
        return prior * 0.62 * min(1.6, max(0.55, price_ratio)), 0.0
    num = den = 0.0
    for h in seasons:
        w = SEASON_WEIGHT[h['season']] * h['mins']
        if h['season'] == CURRENT:
            w *= mult
        num += h[metric] * w
        den += w
    own = num / den
    n_eff = den / FULL_SEASON_MINS
    k = max(0.15, (1.0 - stab) / max(stab, 0.05))
    w_own = n_eff / (n_eff + k)
    return w_own * own + (1 - w_own) * prior, w_own


# --------------------------------------------------------------- minutes
def minutes_prior(p, players):
    """The pre-season part of the minutes model: (start rate, minutes per
    start) from past seasons, the club/position pecking order and the overlay.

    Start rate measured 0.46 stability year to year, so the observed rate is
    shrunk towards the club/position pecking order — the same correction v1
    arrived at by hand, now with a number behind it. This season's evidence is
    applied on top by minutes_model().
    """
    # past seasons: start rate over 38; this season is handled by the update
    # rules, because starts/38 is meaningless in October
    hist = [h for h in p['hist'] if h['mins'] >= 200 and h['season'] != CURRENT]
    if hist:
        num = den = 0.0
        mps_num = mps_den = 0.0
        for h in hist:
            w = SEASON_WEIGHT[h['season']]
            if h['starts']:
                num += (h['starts'] / 38.0) * w
                den += w
                mps_num += min(92.0, h['mins'] / h['starts']) * w * h['mins']
                mps_den += w * h['mins']
        observed = num / den if den else 0.4
        mps = mps_num / mps_den if mps_den else 80.0
    else:
        observed, mps = None, 82.0

    peers = sorted((q['price'] for q in players.values()
                    if q['team'] == p['team'] and q['pos'] == p['pos']), reverse=True)
    rank = peers.index(p['price']) if p['price'] in peers else len(peers) - 1
    table = {'GKP': [0.92, 0.09, 0.03, 0.02],
             'DEF': [0.85, 0.80, 0.73, 0.63, 0.46, 0.29, 0.16, 0.08],
             'MID': [0.85, 0.78, 0.68, 0.56, 0.40, 0.25, 0.14, 0.07],
             'FWD': [0.82, 0.52, 0.28, 0.14, 0.07]}[p['pos']]
    rank_rate = table[min(rank, len(table) - 1)]

    if observed is None:
        start_rate = rank_rate * 0.9
    else:
        w = min(1.0, observed)          # more starts seen = more trust
        # A start rate earned at a DIFFERENT club says little about the pecking
        # order at the new one. Meslier started most of last season for Leeds;
        # at Arsenal he is behind Raya. Cap the trust for summer arrivals so the
        # new club's ordering dominates.
        if p['joined'] >= '2026-05-01':
            w = min(w, 0.30)
        # Goalkeeping is winner-take-all: one keeper plays every minute and the
        # rest play none. However many games a reserve started elsewhere, the
        # pecking order at his current club decides it.
        if p['pos'] == 'GKP' and rank >= 1:
            w = min(w, 0.15)
        start_rate = w * observed + (1 - w) * rank_rate

    ov = OVERLAY.get(p['id'], {})
    if 'mins' in ov:
        # the overlay states expected minutes per gameweek directly — pre-season
        # research that replaces what history says
        start_rate = min(0.97, ov['mins'] / max(mps, 1.0))
    return start_rate, mps


def load_snapshot_status(history_dir=None):
    """{gw: {player id: (deadline status, deadline p_start)}} from the archived
    pre-deadline snapshots (weekly.py --snapshot). This is the availability
    record the recency rule needs: a healthy player who did not start was
    benched; a flagged one was absent, which is not evidence about his place."""
    history_dir = history_dir or (ROOT / 'data' / 'history')
    out = {}
    if not history_dir.exists():
        return out
    for path in sorted(history_dir.glob('gw*.json')):
        if not re.fullmatch(r'gw\d+\.json', path.name):
            continue
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        out[int(d['gw'])] = {
            int(r['id']): (r.get('status', 'a'),
                           float(r.get('p_start', r.get('start_rate', 1.0)) or 0.0))
            for r in d.get('players', [])}
    return out


def match_evidence(p, fixtures=None, snapshots=None, min_available_start=0.05):
    """The per-fixture start record the recency rule reads.

    Returns [(games_ago, started, minutes)] over this season's fixtures of the
    player's club, most recent first, keeping only fixtures he was AVAILABLE
    for: he played, or the archived deadline snapshot had him status 'a' with
    a start probability above `min_available_start` (i.e. not overridden to
    zero). Fixtures before he joined, fixtures with no row for him (not in the
    squad), and fixtures he was flagged for are skipped — they say nothing
    about whether the manager picks him. `games_ago` counts ALL club fixtures
    back from the latest, so evidence from before a long absence ages.
    """
    team_fx = (TEAM_FIXTURES if fixtures is None else fixtures).get(p['team'], [])
    snaps = SNAPSHOT_STATUS if snapshots is None else snapshots
    rows = {g['fixture_id']: g for g in (p.get('gw') or [])}
    joined = (p.get('joined') or '')[:10]
    out = []
    for games_ago, fx in enumerate(reversed(team_fx)):
        row = rows.get(fx['fixture_id'])
        if row is None:
            continue
        kickoff = (fx.get('kickoff') or row.get('kickoff') or '')[:10]
        if joined and kickoff and kickoff < joined:
            continue
        mins = row.get('mins') or 0
        started = row.get('starts')
        started = int(started > 0) if started is not None else int(mins >= 60)
        if mins <= 0 and not started:
            snap = snaps.get(fx.get('event'), {}).get(p['id'])
            if snap is not None:
                status, p_start = snap
                if status != 'a' or p_start < min_available_start:
                    continue          # absent, not benched: not evidence
        out.append((games_ago, started, mins))
    return out


def recency_update(p, prior_rate, prior_mps, k=None, half_life=None, evidence=None):
    """P2's rule: recency-weighted start rate over the fixtures the player was
    available for, trusted n_eff / (n_eff + k) against the prior; minutes per
    start from started fixtures only (no cameo contamination, W9)."""
    k = RECENCY_K if k is None else k
    half_life = RECENCY_HALF_LIFE if half_life is None else half_life
    ev = match_evidence(p) if evidence is None else evidence
    if not ev:
        return max(0.0, min(0.97, prior_rate)), prior_mps
    sw = ss = mw = mm = 0.0
    for games_ago, started, mins in ev:
        w = 1.0 if not half_life or math.isinf(half_life) else 0.5 ** (games_ago / half_life)
        sw += w
        ss += w * started
        if started:
            mw += w
            mm += w * min(92.0, mins)
    rate_now = ss / sw if sw else 0.0
    trust = sw / (sw + k)
    start_rate = trust * rate_now + (1 - trust) * prior_rate
    mps = prior_mps
    if mw > 0:
        t_m = mw / (mw + RECENCY_MPS_K)
        mps = t_m * (mm / mw) + (1 - t_m) * prior_mps
    return max(0.0, min(0.97, start_rate)), mps


def aggregate_update(p, prior_rate, prior_mps):
    """The original in-season rule: this season's starts / team games,
    trusted n / (n + CURRENT_TRUST_K) after n team games — a fifth after one
    game, half after four, three quarters after twelve. Order-blind and
    availability-blind (W2), and mps includes cameo minutes (W9)."""
    now, n_games = p.get('now'), GAMES_PLAYED.get(p['team'], 0)
    start_rate, mps = prior_rate, prior_mps
    if now and n_games > 0:
        rate_now = min(1.0, (now['starts'] or 0) / n_games)
        trust = n_games / (n_games + CURRENT_TRUST_K)
        start_rate = trust * rate_now + (1 - trust) * start_rate
        if now['starts']:
            mps_now = min(92.0, now['mins'] / now['starts'])
            mps = trust * mps_now + (1 - trust) * mps
    return max(0.0, min(0.97, start_rate)), mps


def minutes_model(p, players, rule=None):
    """Expected minutes per start and probability of starting.

    The prior (past seasons, pecking order, overlay — minutes_prior()) is then
    updated with this season's evidence by `rule`: MINUTES_RULE (production)
    unless one is named. 'recency' falls back to the aggregate rule only when
    no per-fixture rows have been loaded at all (an old database), never for
    an individual player without rows — for him the absence of rows IS the
    evidence (he is not in a matchday squad, or has just signed).
    """
    prior_rate, prior_mps = minutes_prior(p, players)
    rule = rule or MINUTES_RULE
    if rule == 'recency' and (GW_ROWS_LOADED or p.get('gw')):
        return recency_update(p, prior_rate, prior_mps)
    return aggregate_update(p, prior_rate, prior_mps)


def poisson_at_least(mean, k):
    if mean <= 0:
        return 0.0
    term = math.exp(-mean)
    cum = term
    for i in range(1, k):
        term *= mean / i
        cum += term
    return max(0.0, min(1.0, 1.0 - cum))


def expected_floor_div(mean, n, cap=20):
    """E[floor(X / n)] for X ~ Poisson(mean).

    FPL's scoring uses floors, not rates: a defender loses 1 point per TWO goals
    conceded, a keeper gains 1 per THREE saves. Charging `mean / n` instead is
    wrong in a way that does not average out — the floor discards the remainder
    every match, so `mean / n` always overstates the count. At a typical 1.4
    expected goals conceded the true penalty is 0.47 a match, not 0.70, so every
    defender and goalkeeper was being over-charged by about a quarter of a point
    a game, which is most of a defender's margin.
    """
    if mean <= 0:
        return 0.0
    term = math.exp(-mean)
    total = 0.0
    for k in range(cap + 1):
        if k:
            term *= mean / k
        total += (k // n) * term
    return total


def defcon_hit_prob(mean, k, evidence):
    """P(a player clears his DefCon threshold in a match).

    NOT `poisson_at_least(shrunk_mean, k)`. That substitutes a point estimate
    into a sharply convex function and badly understates the answer — Jensen's
    inequality. Shrinking Gabriel's rate from 9.07 to 6.96 (a 23% cut) collapsed
    his hit probability from 0.41 to 0.16, a 61% cut, purely as an artefact.

    Two things genuinely spread the count around its mean, and both fatten the
    upper tail:

      * match-to-match variation — defensive actions depend on game state,
        opponent and how much of the ball the side has, so counts are
        over-dispersed relative to Poisson;
      * uncertainty in the player's own true rate, which is exactly what the
        shrinkage is expressing.

    Both are handled by a gamma-Poisson mixture: the true per-match rate is
    Gamma-distributed about the shrunk mean, so the count is negative binomial.
    `evidence` (0-1, the empirical-Bayes weight on the player's own record) sets
    the dispersion — a well-evidenced player gets a tight distribution, a
    thinly-evidenced one a wide one.
    """
    if mean <= 0:
        return 0.0
    r = 4.0 + 11.0 * max(0.0, min(1.0, evidence))   # 4 (uncertain) .. 15 (solid)
    p = r / (r + mean)
    # P(X = 0) = p^r, then the standard negative-binomial recurrence
    term = p ** r
    cum = term
    for i in range(1, k):
        term *= (r + i - 1) / i * (1 - p)
        cum += term
    return max(0.0, min(1.0, 1.0 - cum))


# ------------------------------------------------------------ projection
def project(players, view, priors, refit_calibration=False, feedback=False):
    # median price per position, used to temper the prior for unknown players
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        prices = sorted(q['price'] for q in players.values() if q['pos'] == pos)
        PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5

    out = []
    shadow_rule = 'recency' if MINUTES_RULE == 'aggregate' else 'aggregate'
    for p in players.values():
        pos = p['pos']
        base_start_rate, mps = minutes_model(p, players)
        # the other minutes rule, archived for side-by-side grading (P2)
        shadow_start_rate, _ = minutes_model(p, players, rule=shadow_rule)
        rate_recency = shadow_start_rate if shadow_rule == 'recency' else base_start_rate
        rate_aggregate = shadow_start_rate if shadow_rule == 'aggregate' else base_start_rate

        xg90, w_xg = shrink(p, 'xg90', priors)
        xa90, _ = shrink(p, 'xa90', priors)
        dc90, w_dc = shrink(p, 'dc90', priors)
        bonus90, _ = shrink(p, 'bonus90', priors)
        saves90, _ = shrink(p, 'saves90', priors)
        yellow90, _ = shrink(p, 'yellow90', priors)

        af = age_factor(p['dob'])
        xg90 *= af
        xa90 *= af
        ov = OVERLAY.get(p['id'], {})
        rate_mult = ov.get('rate_mult', 1.0)
        xg90 *= rate_mult
        xa90 *= rate_mult

        fixtures = view['view'].get(p['team'], {})
        # zeros for gameweeks already played keep absolute indexing intact.
        # by_gw is the detailed window; season_by_gw runs to the last gameweek
        # under the same rates and minutes — coarse, but it is what chip timing
        # needs (which week is the bench worth most, where is the double).
        by_gw, total = [0.0] * (START_GW - 1), 0.0
        season_by_gw = [0.0] * (START_GW - 1)
        start_by_gw = [0.0] * (START_GW - 1)
        play_by_gw = [0.0] * (START_GW - 1)
        mins_by_gw = [0.0] * (START_GW - 1)
        availability_by_gw = [None] * (START_GW - 1)
        # the two minutes rules through the same deadline/override layer, so
        # the scorecard compares like with like
        start_recency_by_gw = [0.0] * (START_GW - 1)
        start_aggregate_by_gw = [0.0] * (START_GW - 1)
        for gw in range(START_GW, LAST_GW + 1):
            effective_status = status_for_gameweek(
                p['status'], gw, START_GW, news=p['news'],
                gw_deadline=GW_DEADLINES.get(gw),
            )

            def deadline_forecast(rate):
                start_input = (deadline_start_probability(
                    rate, effective_status, p['chance'], p['news']
                ) if effective_status != 'a' else rate)
                return availability_forecast(
                    player_id=p['id'], gw=gw, base_start=start_input,
                    base_start_minutes=mps, position=pos, status=effective_status,
                    overrides=AVAILABILITY_OVERRIDES,
                )
            av = deadline_forecast(base_start_rate)
            p_start = av.p_start
            p_cameo = (1.0 - p_start) * av.p_cameo
            p_play = av.p_play
            minute_share = av.expected_minutes / 90.0
            start_share = av.start_minutes / 90.0
            cameo_share = av.cameo_minutes / 90.0
            if gw <= HORIZON:
                start_by_gw.append(round(p_start, 3))
                play_by_gw.append(round(p_play, 3))
                mins_by_gw.append(round(av.expected_minutes, 1))
                if shadow_start_rate == base_start_rate:
                    shadow_p_start = p_start
                else:
                    shadow_p_start = deadline_forecast(shadow_start_rate).p_start
                start_recency_by_gw.append(round(
                    shadow_p_start if shadow_rule == 'recency' else p_start, 3))
                start_aggregate_by_gw.append(round(
                    shadow_p_start if shadow_rule == 'aggregate' else p_start, 3))
                availability_by_gw.append(dict(
                    source=av.source, confidence=av.confidence, note=av.note,
                    p_cameo=round(av.p_cameo, 3),
                    start_minutes=round(av.start_minutes, 1),
                    cameo_minutes=round(av.cameo_minutes, 1),
                    last_updated=av.last_updated,
                    from_gw=av.from_gw,
                    through_gw=av.through_gw,
                    # the generated rule behind an override, so the scorecard
                    # can grade by claim type (it was never written before,
                    # so the claim_type group was always 'baseline')
                    generation_rule=av.generation_rule,
                ))
            fx = fixtures.get(str(gw)) or []
            if not fx:
                season_by_gw.append(0.0)
                if gw <= HORIZON:
                    by_gw.append(0.0)
                continue
            pts = 0.0
            for f in fx:                       # handles double gameweeks
                # attacking, scaled by how many goals this team is expected to
                # score in THIS fixture relative to a league-average match
                vol = f['xg'] / 1.45
                pts += (xg90 * minute_share * vol * GOAL_PTS[pos]
                        + xa90 * minute_share * vol * 3.0)
                # clean sheet: straight from the fitted scoreline distribution
                if CS_PTS[pos]:
                    pts += CS_PTS[pos] * f['cs'] * p_start
                if pos in ('GKP', 'DEF'):
                    pts -= expected_floor_div(f['xgc'], 2) * p_start
                if pos == 'GKP':
                    pts += (expected_floor_div(saves90 * start_share, 3) * p_start
                            + expected_floor_div(saves90 * cameo_share, 3) * p_cameo)
                thr = DC_THRESHOLD[pos]
                if thr and dc90 > 0:
                    pts += 2.0 * (
                        p_start * defcon_hit_prob(dc90 * start_share, thr, w_dc)
                        + p_cameo * defcon_hit_prob(dc90 * cameo_share, thr, w_dc)
                    )
                pts += p_start * 2.0 + p_cameo
                pts += bonus90 * minute_share * 0.85
                pts -= yellow90 * minute_share
            pts = round(max(0.0, pts), 3)
            season_by_gw.append(pts)
            if gw <= HORIZON:
                by_gw.append(pts)
                total += pts

        SEASON[p['id']] = season_by_gw
        first = START_GW - 1
        current_availability = availability_by_gw[first]
        out.append(dict(
            id=p['id'], name=p['name'], full_name=p['full_name'], team=p['team'],
            pos=pos, price=p['price'], sel_pct=p['sel_pct'], status=p['status'],
            news=p['news'], joined=p['joined'], pens=p['pens'],
            corners=p['corners'], fk=p['fk'],
            proj_by_gw=by_gw, proj_6gw=round(total, 2),
            proj_gw=round(total / WINDOW, 3),
            value=round(total / p['price'], 4) if p['price'] else 0,
            start_rate=start_by_gw[first], mins_proj=round(mins_by_gw[first]),
            start_by_gw=start_by_gw, play_by_gw=play_by_gw,
            mins_by_gw=mins_by_gw, availability_by_gw=availability_by_gw,
            availability_source=current_availability['source'],
            availability_confidence=current_availability['confidence'],
            # the minutes model's own rate BEFORE the deadline flag/override
            # layer — what the scorecard's baseline_start should be — and the
            # shadow rule's, for side-by-side grading (P2)
            baseline_start_rate=round(base_start_rate, 4),
            start_minutes=round(mps, 1),
            minutes_rule=MINUTES_RULE,
            start_rate_recency=round(rate_recency, 4),
            start_rate_aggregate=round(rate_aggregate, 4),
            start_recency_by_gw=start_recency_by_gw,
            start_aggregate_by_gw=start_aggregate_by_gw,
            n_match_evidence=len(match_evidence(p)),
            xg90=round(xg90, 4), xa90=round(xa90, 4), dc90=round(dc90, 3),
            # the remaining shrunk rates, so a snapshot can reconstruct the
            # projection's components after the fact (P3 retro)
            bonus90=round(bonus90, 4), saves90=round(saves90, 4),
            yellow90=round(yellow90, 4), dc_evidence=round(w_dc, 3),
            evidence=round(w_xg, 2),
            seasons=len([h for h in p['hist'] if h['mins'] >= 450]),
            note=OVERLAY.get(p['id'], {}).get('note', '')
                 or PRESEASON_FORM.get(p['id'], ''),
            pts_last=next((h['pts'] for h in p['hist'] if h['season'] == '2025/26'), 0),
            # this season so far — what the model is now learning from
            pts_now=(p['now'] or {}).get('pts', 0) if p.get('now') else 0,
            mins_now=(p['now'] or {}).get('mins', 0) if p.get('now') else 0,
            starts_now=(p['now'] or {}).get('starts', 0) if p.get('now') else 0,
            games_now=GAMES_PLAYED.get(p['team'], 0),
        ))
    calibrate(out, players, refit=refit_calibration, feedback=feedback)
    out.sort(key=lambda r: -r['proj_6gw'])
    return out


# Calibration anchor seasons: outfield multipliers fit against the mean of
# pts/38 across BOTH completed seasons; an earlier season counts once its stint
# clears CAL_STINT_MINS (hold-out: research/totals-holdout.md, 2024/25 target).
CAL_ANCHORS = ('2024/25', '2025/26')
CAL_STINT_MINS = 900


CALIBRATION = ROOT / 'v2' / 'calibration.json'
# A calibration-cohort member whose modelled start probability over the window
# is below this fraction of his own baseline is being depressed by a status
# flag or an override (dated injury, suspension, tactical zero). He would drag
# the cohort's proj_gw down and inflate everyone else in his position, so he
# is left out of the FIT (never out of the application).
CALIBRATION_MIN_AVAIL = 0.6
# P7: feed the observed in-season level back into k only once there is enough
# of it. A starter's weekly points have sd ~3 and a position has ~60 starters,
# so one gameweek's cohort mean has sd ~0.4 on a mean of ~4: a 10% level error
# is 1 sigma per gameweek, 3 sigma at nine. Blending at n_gw / (n_gw + K_C)
# with K_C = 8 means nothing faster than that. Validate K_C on the imported
# per-GW rows (backtest_inseason.py) before relying on it.
FEEDBACK_MIN_GWS = 8
FEEDBACK_MIN_DRIFT = 0.10
FEEDBACK_K = 8.0


def load_calibration(path=None):
    path = path or CALIBRATION
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d.get('k'), dict):
        return None
    return d


def fit_calibration(rows, players):
    """Per-position multipliers, fitted the way the docstring of calibrate()
    describes, on the anchor cohort minus players whose window is depressed by
    availability. Returns {pos: dict(k, ratio, n, n_excluded)}."""
    hist = {}
    for p in players.values():
        obs = [(x['season'], x['mins'], x['pts'])
               for x in p['hist'] if x['season'] in CAL_ANCHORS]
        latest = next((o for o in obs if o[0] == CAL_ANCHORS[-1]), None)
        if not latest or latest[1] < 2000:
            continue
        qual = [o for o in obs if o[1] >= CAL_STINT_MINS]
        if not qual:
            continue
        hist[p['id']] = (
            sum(o[2] for o in qual) / len(qual) / 38.0,   # two-season mean
            latest[2] / 38.0,                             # most recent season
        )

    out = {}
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        proj, act, excluded = [], [], 0
        for r in rows:
            h = hist.get(r['id'])
            if r['pos'] != pos or h is None:
                continue
            window = [v for v in (r.get('start_by_gw') or [])[START_GW - 1:HORIZON]]
            baseline = r.get('baseline_start_rate')
            if window and baseline and (sum(window) / len(window)) < CALIBRATION_MIN_AVAIL * baseline:
                excluded += 1
                continue
            proj.append(r['proj_gw'])
            act.append(h[1] if pos == 'GKP' else h[0])
        if len(proj) < 6:
            continue
        ratio = (sum(proj) / len(proj)) / (sum(act) / len(act))
        if ratio <= 0:
            continue
        k = max(0.7, min(1.45, 1.0 / ratio))   # never rescale by more than ~45%
        out[pos] = dict(k=round(k, 4), ratio=round(ratio, 4), n=len(proj),
                        n_excluded=excluded)
    return out


def apply_calibration(rows, ks):
    for pos, k in ks.items():
        for r in rows:
            if r['pos'] != pos:
                continue
            r['proj_by_gw'] = [round(v * k, 3) for v in r['proj_by_gw']]
            r['proj_6gw'] = round(sum(r['proj_by_gw']), 2)
            r['proj_gw'] = round(r['proj_6gw'] / WINDOW, 3)
            if r['id'] in SEASON:
                SEASON[r['id']] = [round(v * k, 3) for v in SEASON[r['id']]]
            r['value'] = round(r['proj_6gw'] / r['price'], 4) if r['price'] else 0
            r['calibration_k'] = round(k, 4)


def feedback_blend(ks, level_ratios, n_gws, k_c=FEEDBACK_K,
                   min_gws=FEEDBACK_MIN_GWS, min_drift=FEEDBACK_MIN_DRIFT):
    """P7's deferred loop: blend the frozen k with the observed in-season
    level (`level_ratios` = sum(actual)/sum(proj) per position over likely
    starters, cumulative), at weight n_gws / (n_gws + k_c), and only when the
    drift is outside +-min_drift with at least min_gws graded. Returns a new
    {pos: k} and the list of positions it moved."""
    out, moved = dict(ks), []
    if n_gws < min_gws:
        return out, moved
    w = n_gws / (n_gws + k_c)
    for pos, k in ks.items():
        ratio = (level_ratios or {}).get(pos)
        if ratio is None or abs(ratio - 1.0) <= min_drift:
            continue
        # k_obs is the multiplier that would have made the level right
        k_obs = max(0.7, min(1.45, k * ratio))
        out[pos] = round((1 - w) * k + w * k_obs, 4)
        moved.append(pos)
    return out, moved


def calibrate(rows, players, refit=False, feedback=False):
    """Remove the positional level bias introduced by shrinkage.

    FROZEN IN-SEASON (P4). The multipliers are fitted once and stored in
    v2/calibration.json; later runs apply the stored values. Re-fitting on
    every refresh pinned the level of the established cohort to 2024/25-2025/26
    history, so anything the team or minutes model learned in-season about the
    LEVEL of scoring was rescaled straight back out (only ordering survived),
    and long-term absences in the anchor cohort inflated everyone else in the
    position. Pass refit=True (player_model.py --refit-calibration) to re-fit
    deliberately. With feedback=True (--feedback) the stored k is blended with
    the scorecard's cumulative level ratio under feedback_blend()'s guards.

    Shrinking every player towards a positional mean that includes fringe
    squad members drags regular starters down, and it does so unevenly: raw v2
    lands at 1.03x for keepers but only 0.78x for midfielders against what
    comparable players actually scored last season. Uncorrected, that makes a
    goalkeeper look like a captaincy pick.

    The multiplier is fitted so established players (2,000+ minutes in the most
    recent completed season) project in line with what they actually delivered.
    A single-season anchor inherits that season's scoring environment: the
    totals hold-out (research/totals-holdout.md) measured forwards calibrated
    on the record-high 2023/24 landing at 1.38x over in the low-scoring
    2024/25. Outfield positions therefore fit against the mean of pts/38 across
    the LAST TWO completed seasons, with an earlier season contributing once its
    stint clears CAL_STINT_MINS. Hold-out effect on the 2024/25 target:
    FWD sum(proj)/sum(act) 1.38 -> 1.28, MID 1.14 -> 1.12, DEF 1.23 -> 1.18,
    ALL Spearman 0.495 -> 0.501, >150-point counts inside tolerance; identical
    on 2023/24 by construction (only one training season exists behind it).
    Wider cohorts (450/900-minute fits) were also tested and REJECTED: they
    collapse the projected tail (>150 count 17 -> 9-10 vs 18 actual on 2023/24)
    because part-season players realise fewer points per 38 than the
    availability-aware projection assumes. Keepers keep the single-season
    anchor: pooling moved their level away from parity (sum p/a 0.99 -> 1.15)
    because keeper output swung most between the two environments.

    It rescales levels only -- the within-position ordering, which the
    backtest showed is v2's real strength, is untouched.
    """
    stored = None if refit else load_calibration()
    if stored:
        ks = {pos: float(v['k'] if isinstance(v, dict) else v)
              for pos, v in stored['k'].items()}
        print(f'  calibration: applying multipliers stored {stored.get("fitted_at", "?")} '
              f'(as of GW{stored.get("start_gw", "?")}; --refit-calibration to re-fit): '
              + ', '.join(f'{pos} {k:.3f}' for pos, k in sorted(ks.items())))
    else:
        fit = fit_calibration(rows, players)
        ks = {pos: v['k'] for pos, v in fit.items()}
        for pos, v in sorted(fit.items()):
            print(f'  calibration {pos}: raw was {v["ratio"]:.2f}x actual, scaled by '
                  f'{v["k"]:.3f} (cohort {v["n"]}, {v["n_excluded"]} excluded for '
                  f'availability)')
        from datetime import datetime, timezone
        CALIBRATION.write_text(json.dumps(dict(
            fitted_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            start_gw=START_GW, horizon=HORIZON, anchors=list(CAL_ANCHORS),
            stint_mins=CAL_STINT_MINS, min_avail=CALIBRATION_MIN_AVAIL,
            k={pos: v for pos, v in fit.items()},
            note='Frozen per-position level multipliers (P4). Re-fit with '
                 'player_model.py --refit-calibration.',
        ), indent=1))
        print(f'  calibration stored -> {CALIBRATION}')
    if feedback:
        sc = ROOT / 'data' / 'scorecard.json'
        summary = {}
        if sc.exists():
            try:
                summary = json.loads(sc.read_text()).get('summary', {})
            except (OSError, ValueError):
                summary = {}
        ks, moved = feedback_blend(ks, summary.get('level_ratio_cum') or {},
                                   int(summary.get('n_gws') or 0))
        print('  calibration feedback: '
              + (('moved ' + ', '.join(f'{p} -> {ks[p]:.3f}' for p in moved))
                 if moved else 'no position outside the drift/sample guards'))
    apply_calibration(rows, ks)
    return ks


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--refit-calibration', action='store_true',
                    help='re-fit the per-position level multipliers instead of '
                         'applying the ones frozen in v2/calibration.json (P4)')
    ap.add_argument('--feedback', action='store_true',
                    help='blend the frozen multipliers with the scorecard\'s '
                         'cumulative level ratio, subject to the GW8+/10%% guards (P7)')
    args = ap.parse_args()
    players = load()
    GAMES_PLAYED.update(games_played())
    TEAM_FIXTURES.update(team_fixtures())
    SNAPSHOT_STATUS.update(load_snapshot_status())
    GW_ROWS_LOADED = any(p.get('gw') for p in players.values())
    view = json.loads(SEASON_VIEW.read_text())
    priors = positional_priors(players)
    n_now = sum(1 for p in players.values() if p.get('now') and p['now']['mins'] >= 200)
    if GAMES_PLAYED:
        n_rows = sum(len(p.get('gw') or []) for p in players.values())
        print(f'This season: {max(GAMES_PLAYED.values())} rounds played, '
              f'{n_now} players with 200+ minutes feeding the model, '
              f'{n_rows} per-fixture rows, minutes rule "{MINUTES_RULE}" '
              f'(shadow: {"recency" if MINUTES_RULE == "aggregate" else "aggregate"}), '
              f'{len(SNAPSHOT_STATUS)} deadline snapshot(s) for availability')
    else:
        print('This season: no matches played yet — projections rest on history, '
              'price and pre-season research')

    print('Positional priors (minutes-weighted per-90 means)')
    print(f"{'pos':<5}{'xG90':>8}{'xA90':>8}{'DefCon90':>10}{'bonus90':>9}")
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        d = priors.get(pos, {})
        print(f"{pos:<5}{d.get('xg90',0):>8.3f}{d.get('xa90',0):>8.3f}"
              f"{d.get('dc90',0):>10.2f}{d.get('bonus90',0):>9.3f}")

    rows = project(players, view, priors, refit_calibration=args.refit_calibration,
                   feedback=args.feedback)
    json.dump({'players': rows, 'horizon': HORIZON, 'start_gw': START_GW,
               'window': WINDOW, 'minutes_rule': MINUTES_RULE,
               'calibration': {r['pos']: r.get('calibration_k') for r in rows
                               if r.get('calibration_k') is not None}},
              open(OUT, 'w'))
    print(f'\nprojected {len(rows)} players over GW{START_GW}-{HORIZON} -> {OUT}')
    # the coarse full-season projection, for chip timing (chips.py)
    season = [dict(id=r['id'], name=r['name'], team=r['team'], pos=r['pos'],
                   price=r['price'], status=r['status'], start_rate=r['start_rate'],
                   by_gw=SEASON.get(r['id'], []))
              for r in rows]
    json.dump({'players': season, 'start_gw': START_GW, 'last_gw': LAST_GW},
              open(OUT_SEASON, 'w'), separators=(',', ':'))
    print(f'season outlook GW{START_GW}-{LAST_GW} -> {OUT_SEASON}\n')

    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        print(f'--- top 10 {pos} (v2) ---')
        for r in [x for x in rows if x['pos'] == pos][:10]:
            print(f"  {r['proj_6gw']:>6.1f}  £{r['price']:>4}m  {r['name']:<16}"
                  f"{r['team']:<5} start {r['start_rate']*100:>3.0f}%  "
                  f"val {r['value']:.2f}  own {r['sel_pct']:>4.1f}%  "
                  f"{r['seasons']}sn")
        print()
