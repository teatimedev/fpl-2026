"""
FPL 2026/27 projection model.

Produces an expected-points-per-gameweek estimate for every player, blending:
  1. last season's scoring rate (points per 90)     -- the strongest single signal
  2. a price-implied prior                          -- covers players with no PL history
  3. projected minutes                              -- the dominant risk in FPL
  4. GW1-6 fixture difficulty                       -- what actually differs at GW1
  5. team defensive strength (for GKP/DEF)          -- clean sheet share

Everything is derived from the official FPL API dump in data/bootstrap.json plus a
small hand-curated overlay in overlay.py for information the API does not carry
(manager changes, pre-season form, role changes).
"""
import json, csv, math
from collections import defaultdict
from overlay import OVERLAY, NEW_MANAGER, PRESEASON_FORM

BOOT = json.load(open('data/bootstrap.json'))
FIX = json.load(open('data/fixtures.json'))

TEAM = {t['id']: t['short_name'] for t in BOOT['teams']}
POS = {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}
HORIZON = 6  # GW1-6: the window a GW1 squad is really built for


# ---------------------------------------------------------------- fixtures
def fixture_factors():
    """Per-team attacking and defensive multipliers over GW1..HORIZON.

    FDR runs 2..5 in this season's data. We map difficulty to a multiplier
    centred on 1.0, and apply a mild home bonus. Defensive (clean-sheet)
    returns are more fixture-sensitive than attacking returns, so they get a
    steeper curve.
    """
    att, dfn, games = defaultdict(float), defaultdict(float), defaultdict(int)
    for f in FIX:
        gw = f.get('event')
        if gw is None or gw > HORIZON:
            continue
        for side, diff_key, home in (('team_h', 'team_h_difficulty', True),
                                     ('team_a', 'team_a_difficulty', False)):
            t, d = f[side], f[diff_key]
            # difficulty 2 -> easy, 5 -> hard. Centre on 3.5.
            att[t] += (1.0 + (3.5 - d) * 0.09) * (1.04 if home else 0.96)
            dfn[t] += (1.0 + (3.5 - d) * 0.15) * (1.08 if home else 0.92)
            games[t] += 1
    return ({t: att[t] / games[t] for t in games},
            {t: dfn[t] / games[t] for t in games},
            dict(games))


def fixture_factors_by_gw():
    """Same multipliers, but kept per gameweek instead of averaged.

    Averaging over the horizon is right for a hold-all-15 projection, but it
    hides exactly the information a transfer plan trades on: a player is worth
    more in the weeks his club has an easy fixture. Blank gameweeks come back as
    (0, 0) so a player with no fixture scores nothing that week.
    """
    att = {t: [0.0] * HORIZON for t in TEAM}
    dfn = {t: [0.0] * HORIZON for t in TEAM}
    for f in FIX:
        gw = f.get('event')
        if gw is None or gw > HORIZON:
            continue
        for side, diff_key, home in (('team_h', 'team_h_difficulty', True),
                                     ('team_a', 'team_a_difficulty', False)):
            t, d = f[side], f[diff_key]
            att[t][gw - 1] = (1.0 + (3.5 - d) * 0.09) * (1.04 if home else 0.96)
            dfn[t][gw - 1] = (1.0 + (3.5 - d) * 0.15) * (1.08 if home else 0.92)
    return att, dfn


ATT_F, DEF_F, GAMES = fixture_factors()
ATT_GW, DEF_GW = fixture_factors_by_gw()


# ------------------------------------------------------- team clean sheets
def team_cs_rate():
    """Per-team clean sheet probability, from last season's xGC per 90.

    Uses the squad's minutes-weighted xGC/90 (defenders and keepers only, who
    play whole matches) and converts to a Poisson P(0 goals conceded).
    Promoted clubs have no PL data, so they fall back to a league-worst prior.
    """
    num, den = defaultdict(float), defaultdict(float)
    for p in BOOT['elements']:
        if p['element_type'] not in (1, 2) or p['minutes'] < 900:
            continue
        w = p['minutes']
        num[p['team']] += float(p['expected_goals_conceded_per_90']) * w
        den[p['team']] += w
    out = {}
    for t in TEAM:
        if den[t] > 0:
            xgc90 = num[t] / den[t]
        else:
            xgc90 = 1.75  # promoted-club prior: leaky
        out[t] = math.exp(-xgc90)
    return out


CS = team_cs_rate()


def team_attack_strength():
    """Per-team attacking strength index, mean-normalised to 1.0.

    Sum of squad expected goal involvements per 90 weighted by minutes, which
    tracks how many attacking returns a team generates for its players to share.
    Promoted clubs get a below-average prior.
    """
    tot = defaultdict(float)
    for p in BOOT['elements']:
        if p['minutes'] < 450:
            continue
        tot[p['team']] += float(p['expected_goal_involvements_per_90']) * p['minutes'] / 90.0
    vals = [v for v in tot.values() if v > 0]
    mean = sum(vals) / len(vals)
    out = {}
    for t in TEAM:
        out[t] = (tot[t] / mean) if tot[t] > 0 else 0.72
    return out


ATK = team_attack_strength()


def team_def_workload():
    """How much defending a team's players have to do, mean-normalised to 1.0.

    Proxied by the team's xGC per 90: sides that concede more chances make more
    clearances, blocks, interceptions and tackles, so their players clear the
    DefCon threshold more often. This matters because a player moving to a
    stronger side typically sees their DefCon output FALL.
    """
    num, den = defaultdict(float), defaultdict(float)
    for p in BOOT['elements']:
        if p['element_type'] not in (1, 2) or p['minutes'] < 900:
            continue
        num[p['team']] += float(p['expected_goals_conceded_per_90']) * p['minutes']
        den[p['team']] += p['minutes']
    raw = {t: (num[t] / den[t] if den[t] else 1.75) for t in TEAM}
    mean = sum(raw.values()) / len(raw)
    return {t: raw[t] / mean for t in raw}


WORKLOAD = team_def_workload()

# DefCon thresholds: 10 qualifying actions for defenders, 12 for midfielders
# and forwards. Goalkeepers do not score DefCon points.
DC_THRESHOLD = {1: None, 2: 10, 3: 12, 4: 12}


def _poisson_at_least(mean, k):
    """P(X >= k) for X ~ Poisson(mean). Used to turn a per-90 action rate into
    the probability of clearing the DefCon threshold in a given match."""
    if mean <= 0:
        return 0.0
    # P(X < k) = sum_{i<k} e^-m m^i / i!
    term = math.exp(-mean)
    cum = term
    for i in range(1, k):
        term *= mean / i
        cum += term
    return max(0.0, min(1.0, 1.0 - cum))


def defcon_points_90(dc90, et):
    """Expected DefCon points per 90 from a per-90 qualifying-action rate."""
    thr = DC_THRESHOLD[et]
    if thr is None or dc90 <= 0:
        return 0.0
    return 2.0 * _poisson_at_least(dc90, thr)


# ------------------------------------------------------------ price prior
def price_priors():
    """Expected points/90 as a function of price, fitted per position.

    FPL prices encode the game-makers' own expectations, so for players with no
    Premier League minutes last season the price is the best prior available.
    We fit a simple linear rate = a + b*price on players who DID play, then use
    it for those who didn't.
    """
    fits = {}
    for et in (1, 2, 3, 4):
        pts = [(p['now_cost'] / 10, p['total_points'] / (p['minutes'] / 90))
               for p in BOOT['elements']
               if p['element_type'] == et and p['minutes'] >= 1200]
        n = len(pts)
        mx = sum(x for x, _ in pts) / n
        my = sum(y for _, y in pts) / n
        sxy = sum((x - mx) * (y - my) for x, y in pts)
        sxx = sum((x - mx) ** 2 for x, _ in pts)
        b = sxy / sxx if sxx else 0
        fits[et] = (my - b * mx, b)
    return fits


PRIOR = price_priors()


# --------------------------------------------------------------- minutes
# Expected share of games started, by a player's price rank within his own club
# and position. This is the pecking-order prior used when last season's start
# count is not a reliable guide to next season's.
# The curve has to match how many of that position actually start a match: one
# goalkeeper, around four defenders and four midfielders, one or two forwards.
# A flat table would treat the second-choice midfielder as a bench player when
# in reality he starts every week.
RANK_START_RATE = {
    1: [0.92, 0.09, 0.03, 0.02],                          # 1 starts
    2: [0.85, 0.80, 0.73, 0.63, 0.46, 0.29, 0.16, 0.08],  # ~4 start
    3: [0.85, 0.78, 0.68, 0.56, 0.40, 0.25, 0.14, 0.07],  # ~4 start
    4: [0.82, 0.52, 0.28, 0.14, 0.07],                    # 1-2 start
}


def _rank_start_rate(p):
    peers = sorted((q['now_cost'] for q in BOOT['elements']
                    if q['team'] == p['team']
                    and q['element_type'] == p['element_type']), reverse=True)
    rank = peers.index(p['now_cost'])
    table = RANK_START_RATE[p['element_type']]
    return table[min(rank, len(table) - 1)]


def projected_minutes(p):
    """Expected minutes per gameweek over the horizon (0-90).

    Splits the question in two, because the halves behave very differently:

      * HOW LONG he plays when he starts -- a stable trait. Nearly every
        first-choice player sits between 85 and 93 minutes per start.
      * HOW OFTEN he starts -- NOT stable, and this is where a naive model goes
        wrong. Copying last season's start count forward means an injury-hit
        season is served twice: once in the points total, again in the minutes.
        Isak started 8 games while averaging 87 minutes in them; carried forward
        literally that makes Liverpool's first-choice striker a 19-minute player.

    So the observed start rate is shrunk towards the club/position pecking order,
    weighted by how many starts we actually observed. A player with 37 starts is
    described well by his own record; a player with 8 is described better by
    where he sits in his squad. Cheap squad players are unaffected -- their
    pecking-order rate is low too, so there is nothing to shrink towards.
    """
    ov = OVERLAY.get(p['id'], {})
    if 'mins' in ov:
        base = ov['mins']
    elif p['minutes'] >= 450 and p['starts'] >= 5:
        mins_per_start = min(92.0, p['minutes'] / p['starts'])
        observed_rate = min(1.0, p['starts'] / 38.0)
        rank_rate = _rank_start_rate(p)
        # trust the observed rate in proportion to how much of it we saw
        w = observed_rate
        start_rate = w * observed_rate + (1 - w) * rank_rate
        base = min(90.0, start_rate * mins_per_start)
    elif p['minutes'] >= 450:
        # played, but almost always off the bench
        base = min(90.0, p['minutes'] / 38.0 * 1.05)
    else:
        # no meaningful PL history: fall back on the pecking order alone
        base = _rank_start_rate(p) * (88.0 if p['element_type'] != 1 else 90.0)

    # availability
    status = p['status']
    cop = p['chance_of_playing_next_round']
    if status in ('u',):                     # left the club
        return 0.0
    if status == 's':                        # suspended
        return base * 0.35
    if status == 'i':
        news = (p['news'] or '').lower()
        if 'expected back' in news:
            return base * 0.45               # back during the horizon
        return base * 0.08                   # unknown return: assume out
    if status == 'd' and cop is not None:
        return base * (cop / 100.0) * 0.92
    return base


# ---------------------------------------------------------------- scoring
def project(p, fixture=None):
    """Expected points per gameweek. `fixture` overrides the horizon-average
    fixture multipliers with a specific week's (attack, defence) pair, which is
    how the per-gameweek projections used by the transfer planner are built."""
    et = p['element_type']
    mins = projected_minutes(p)
    if mins <= 4:
        return 0.0, mins

    t = p['team']
    af = ATT_F.get(t, 1.0) if fixture is None else fixture[0]
    df = DEF_F.get(t, 1.0) if fixture is None else fixture[1]
    ov = OVERLAY.get(p['id'], {})

    # `settled` = the historical rate was earned at this same club, so it already
    # embeds this team's quality. Applying a team-strength multiplier on top would
    # double-count. Movers and newcomers do get the adjustment.
    settled = p['minutes'] >= 900 and (p['team_join_date'] or '') < '2026-05-01'

    if p['minutes'] >= 900:
        # ---- decompose last season's rate into its scoring components ----
        per90 = p['minutes'] / 90.0
        rate = p['total_points'] / per90

        # clean sheets (GKP/DEF only; midfielders get 1pt and it is folded into
        # the residual rather than modelled separately)
        cs_rate_last = p['clean_sheets'] / per90 if per90 else 0.0
        cs_pts = (4.0 if et in (1, 2) else 0.0) * cs_rate_last

        # DefCon
        dc_pts = defcon_points_90(p['defensive_contribution_per_90'], et)

        # whatever is left: goals, assists, bonus, saves, appearance, cards
        resid = max(0.0, rate - cs_pts - dc_pts)

        # shrink the whole thing towards the price prior when the sample is thin
        w = min(1.0, p['minutes'] / 2400.0)
        a, b = PRIOR[et]
        prior_rate = max(1.5, a + b * p['now_cost'] / 10)
        if w < 1.0:
            scale = (w * rate + (1 - w) * prior_rate) / rate if rate > 0 else 1.0
            cs_pts, dc_pts, resid = cs_pts * scale, dc_pts * scale, resid * scale

        # ---- re-project each component under the new club and fixtures ----
        if settled:
            cs_new = cs_pts * df
            dc_new = dc_pts
        else:
            cs_mean = sum(CS.values()) / len(CS)
            team_def = 1.0 + (CS[t] / cs_mean - 1.0) * 0.6
            cs_new = cs_pts * df * team_def
            # DefCon moves with how much defending the new club actually does:
            # join a stronger side and the qualifying actions dry up.
            dc90_new = p['defensive_contribution_per_90'] * (
                1.0 + (WORKLOAD[t] - 1.0) * 0.7)
            dc_new = defcon_points_90(dc90_new, et)

        resid_new = resid * af * (1.0 if settled else 1.0 + (ATK[t] - 1.0) * 0.5)
        rate_adj = cs_new + dc_new + resid_new
    else:
        # no meaningful Premier League history: lean on the price prior
        a, b = PRIOR[et]
        rate = max(1.5, a + b * p['now_cost'] / 10) * 0.88
        cs_mean = sum(CS.values()) / len(CS)
        team_def = 1.0 + (CS[t] / cs_mean - 1.0) * 0.6
        team_att = 1.0 + (ATK[t] - 1.0) * 0.5
        cs_share = {1: 0.55, 2: 0.42, 3: 0.06, 4: 0.0}[et]
        rate_adj = rate * ((1 - cs_share) * af * team_att
                           + cs_share * df * team_def)

    rate_adj *= ov.get('rate_mult', 1.0)

    # new-manager uncertainty: widen the error bar by trimming the mean a touch
    if t in NEW_MANAGER:
        rate_adj *= 0.97

    pts_per_gw = rate_adj * (mins / 90.0)

    # appearance points are earned even when the underlying rate is low
    if mins >= 60:
        pts_per_gw = max(pts_per_gw, 2.0 * (mins / 90.0))

    # a club with no fixture that week scores nothing
    if fixture is not None and fixture[0] == 0.0 and fixture[1] == 0.0:
        return 0.0, mins

    return pts_per_gw, mins


def project_by_gw(p):
    """This player's expected points in each of GW1..HORIZON."""
    t = p['team']
    return [round(project(p, fixture=(ATT_GW[t][g], DEF_GW[t][g]))[0], 3)
            for g in range(HORIZON)]


# ------------------------------------------------------------------ main
def build():
    rows = []
    for p in BOOT['elements']:
        pts, mins = project(p)
        ov = OVERLAY.get(p['id'], {})
        rows.append({
            'id': p['id'],
            'name': p['web_name'],
            'full_name': (p['first_name'] + ' ' + p['second_name']).strip(),
            'team': TEAM[p['team']],
            'team_id': p['team'],
            'pos': POS[p['element_type']],
            'pos_id': p['element_type'],
            'price': p['now_cost'] / 10,
            'proj_gw': round(pts, 3),
            'proj_6gw': round(pts * HORIZON, 2),
            'proj_by_gw': project_by_gw(p),
            'mins_proj': round(mins),
            'value': round(pts / (p['now_cost'] / 10), 4),
            'sel_pct': float(p['selected_by_percent']),
            'pts_last': p['total_points'],
            'mins_last': p['minutes'],
            'ppg_last': float(p['points_per_game']),
            'goals_last': p['goals_scored'],
            'assists_last': p['assists'],
            'xgi90_last': p['expected_goal_involvements_per_90'],
            'defcon_last': p['defensive_contribution'],
            'cs_last': p['clean_sheets'],
            'bonus_last': p['bonus'],
            'status': p['status'],
            'news': p['news'],
            'joined': p['team_join_date'] or '',
            'is_new': (p['team_join_date'] or '') >= '2026-05-01',
            'pens': p['penalties_order'],
            'corners': p['corners_and_indirect_freekicks_order'],
            'fk': p['direct_freekicks_order'],
            'fdr6': team_fdr(TEAM[p['team']]),
            'cs_rate': round(CS[p['team']], 3),
            'note': ov.get('note', '') or PRESEASON_FORM.get(p['id'], ''),
        })
    rows.sort(key=lambda r: -r['proj_6gw'])
    return rows


_FDR_CACHE = {}


def team_fdr(short):
    if not _FDR_CACHE:
        tot = defaultdict(int)
        for f in FIX:
            gw = f.get('event')
            if gw is None or gw > HORIZON:
                continue
            tot[TEAM[f['team_h']]] += f['team_h_difficulty']
            tot[TEAM[f['team_a']]] += f['team_a_difficulty']
        _FDR_CACHE.update(tot)
    return _FDR_CACHE[short]


def team_schedule():
    """GW1-6 opponent list per team, for the app."""
    sched = defaultdict(dict)
    for f in FIX:
        gw = f.get('event')
        if gw is None or gw > HORIZON:
            continue
        sched[TEAM[f['team_h']]][gw] = {'opp': TEAM[f['team_a']], 'home': True,
                                        'fdr': f['team_h_difficulty']}
        sched[TEAM[f['team_a']]][gw] = {'opp': TEAM[f['team_h']], 'home': False,
                                        'fdr': f['team_a_difficulty']}
    return {t: [sched[t].get(g) for g in range(1, HORIZON + 1)] for t in sched}


if __name__ == '__main__':
    rows = build()
    with open('data/projections.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    json.dump({'players': rows, 'schedule': team_schedule(),
               'meta': {'horizon': HORIZON,
                        'deadline': '2026-08-21T17:30:00Z',
                        'budget': 100.0}},
              open('data/projections.json', 'w'), indent=None)
    print(f'projected {len(rows)} players -> data/projections.csv + .json')
    print()
    for pos in ('GKP', 'DEF', 'MID', 'FWD'):
        print(f'--- top 12 {pos} by projected points over GW1-{HORIZON} ---')
        for r in [x for x in rows if x['pos'] == pos][:12]:
            print(f"  {r['proj_6gw']:>6} £{r['price']:>4}m {r['name']:<16} {r['team']:<4} "
                  f"mins~{r['mins_proj']:>2} val={r['value']:.3f} sel={r['sel_pct']}%")
        print()
