"""
Per-gameweek walk-forward harness for the in-season constants.

Runs on the per-GW rows import_gw_history.py puts in gw_stat for
2022/23-2025/26 (the FPL API only serves the current season's rows). Every
prediction at gameweek n uses rows with round <= n only; the prior seasons
come from season_stat rows strictly earlier than the target season; team
strength comes from a Dixon-Coles fit on matches before 1 July of the
target season's opening year (backtest_totals' leak barrier). Prices are the
row's own start-of-season value, never today's (the naive_price leak in
backtest.py).

Known gap, stated once: availability at each historical deadline is not in
the dataset, so every fixture counts as evidence for the minutes rules —
"condition on played" — and the retro replay assumes status 'a'. The forward
scorecard settles the availability-conditioned versions.

    --minutes   P2. For every GW n >= 2 predict "starts in GW n+1" with the
                aggregate rule (production) and the recency rule over
                K in {2,4,6,8} x HALF_LIFE in {2,3,5,inf}; Brier and log-loss,
                by season phase and by prior-season start band.
    --rates     P5. For n in {3,5,8,12} predict rest-of-season xG/90 and xA/90
                with the multi-season blend, the blend with the current
                season's minutes weight x m in {1,2,3,5,10}, and the current
                season alone; MAE and Spearman, split by context changed
                (new club / new manager / promoted) vs stable.
    --retro     P3 layer 3. Replay the classifier with as-of projections and
                report per class: next-GW start rate, next-3-GW residual,
                rest-of-season xGI/90 error of the three-start window vs the
                prior; plus the hold-vs-swap policy simulation for every
                `variance` player-week.

    python v2/backtest_inseason.py --minutes --rates --retro
"""
import argparse
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import player_model as PM          # noqa: E402
import retro as RT                 # noqa: E402
import backtest_totals as BT       # noqa: E402
import teams_model as TM           # noqa: E402
from manager_changes import new_manager_clubs   # noqa: E402

DB = HERE / 'fpl.db'
SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26']
K_GRID = (2.0, 4.0, 6.0, 8.0)
HL_GRID = (2.0, 3.0, 5.0, math.inf)
RATE_N = (3, 5, 8, 12)
RATE_MULT = (1, 2, 3, 5, 10)
# relative season weights for an as-of blend: the current season and the one
# before it at 1.0, then 0.75, 0.5, 0.3 — production's ladder re-based
LADDER = {0: 1.0, 1: 1.0, 2: 0.75, 3: 0.50, 4: 0.30}
FULL_SEASON_MINS = PM.FULL_SEASON_MINS
POSITIONS = ('GKP', 'DEF', 'MID', 'FWD')


# --------------------------------------------------------------- loading
def load_gw_panel(seasons):
    """{season: {code: [row dicts in kickoff order]}}; rows carry team, pos,
    opponent, was_home, round, fixture_id, price and every stat."""
    cx = sqlite3.connect(DB)
    if not cx.execute("SELECT name FROM sqlite_master WHERE name='gw_stat'").fetchone():
        raise SystemExit('gw_stat is empty: run v2/import_gw_history.py first')
    cols = ('code', 'season', 'round', 'fixture_id', 'team', 'pos', 'opponent',
            'was_home', 'kickoff', 'minutes', 'starts', 'points', 'goals', 'assists',
            'clean_sheets', 'goals_conceded', 'own_goals', 'pens_saved', 'pens_missed',
            'xg', 'xa', 'xgc', 'defcon', 'bps', 'bonus', 'saves', 'yellow', 'red', 'price')
    panel = {s: defaultdict(list) for s in seasons}
    q = (f"SELECT {','.join(cols)} FROM gw_stat WHERE season IN "
         f"({','.join('?' * len(seasons))}) AND round IS NOT NULL")
    for rec in cx.execute(q, seasons):
        r = dict(zip(cols, rec))
        r['mins'] = r['minutes'] or 0
        r['started'] = (int(r['starts'] > 0) if r['starts'] is not None
                        else int(r['mins'] >= 60))
        r['xg'] = r['xg'] if r['xg'] is not None else None
        panel[r['season']][r['code']].append(r)
    cx.close()
    for s in panel:
        for rows in panel[s].values():
            rows.sort(key=lambda r: (r['kickoff'] or '', r['round'], r['fixture_id'] or 0))
    return panel


def team_sequences(rows_by_code):
    """{team: [(kickoff, fixture_id, round)]} in order, from the rows."""
    seq = defaultdict(set)
    for rows in rows_by_code.values():
        for r in rows:
            seq[r['team']].add((r['kickoff'] or '', r['fixture_id'], r['round']))
    return {t: sorted(v) for t, v in seq.items()}


def season_index(season):
    return SEASONS.index(season)


def asof_players(season, panel_rows, hist_rows, meta):
    """player_model-shaped dicts for every player with rows in `season`, with
    history strictly before it and the season's opening price."""
    out = {}
    idx = season_index(season)
    for code, rows in panel_rows.items():
        first = rows[0]
        past = [hist_rows[code][s] for s in SEASONS[:idx]
                if code in hist_rows and s in hist_rows[code]]
        hist = sorted(past, key=lambda h: h['season'])
        assert all(h['season'] < season for h in hist)          # leak barrier
        price = (first['price'] or 50) / 10.0
        out[code] = dict(id=f'bt-{code}', code=code, name=meta.get(code, {}).get('name', code),
                         team=first['team'], pos=first['pos'] or 'MID', price=price,
                         joined='', dob=meta.get(code, {}).get('dob'), hist=hist, now=None,
                         gw=[], status='a', news='', chance=None)
    return out


# ------------------------------------------------------------ statistics
def brier(pairs):
    return float(np.mean([(p - y) ** 2 for p, y in pairs])) if pairs else float('nan')


def logloss(pairs, eps=1e-6):
    if not pairs:
        return float('nan')
    tot = 0.0
    for p, y in pairs:
        p = min(1 - eps, max(eps, p))
        tot -= math.log(p) if y else math.log(1 - p)
    return tot / len(pairs)


def spearman(a, b):
    return BT.spearman(a, b)


def phase(n):
    return 'GW2-8' if n <= 8 else ('GW9-24' if n <= 24 else 'GW25-37')


def prior_band(rate):
    return '<0.4' if rate < 0.4 else ('0.4-0.7' if rate < 0.7 else '>=0.7')


# ------------------------------------------------------------ --minutes
def run_minutes(panel, hist_rows, meta, seasons):
    print('\n' + '=' * 78)
    print('P2  MINUTES: predict "starts in GW n+1" from rows through GW n')
    print('=' * 78)
    print('availability proxy: every club fixture counts (played-only conditioning)')
    scores = defaultdict(list)          # (rule, phase, band) -> [(p, y)]
    n_obs = 0
    for season in seasons:
        rows_by_code = panel[season]
        if not rows_by_code:
            continue
        players = asof_players(season, rows_by_code, hist_rows, meta)
        seqs = team_sequences(rows_by_code)
        for pos in POSITIONS:
            prices = sorted(q['price'] for q in players.values() if q['pos'] == pos)
            PM.PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5
        priors = {code: PM.minutes_prior(p, peers) for code, p in players.items()}
        for code, rows in rows_by_code.items():
            prior_rate, mps = priors[code]
            seq = seqs.get(rows[0]['team'], [])
            by_fixture = {r['fixture_id']: r for r in rows}
            by_round = defaultdict(list)
            for r in rows:
                by_round[r['round']].append(r)
            for n in range(2, 38):
                targets = by_round.get(n + 1)
                if not targets:
                    continue
                through = [r for r in rows if r['round'] <= n]
                if not through:
                    continue
                games_n = sum(1 for _, _, rnd in seq if rnd <= n)
                if games_n == 0:
                    continue
                starts_n = sum(r['started'] for r in through)
                trust = games_n / (games_n + PM.CURRENT_TRUST_K)
                p_agg = trust * min(1.0, starts_n / games_n) + (1 - trust) * prior_rate
                p_agg = max(0.0, min(0.97, p_agg))
                evidence = []
                for games_ago, (_, fid, rnd) in enumerate(reversed([x for x in seq if x[2] <= n])):
                    r = by_fixture.get(fid)
                    if r is None:
                        continue
                    evidence.append((games_ago, r['started'], r['mins']))
                grid = {}
                for k in K_GRID:
                    for hl in HL_GRID:
                        grid[(k, hl)] = PM.recency_update(
                            players[code], prior_rate, mps, k=k, half_life=hl,
                            evidence=evidence)[0]
                for t in targets:
                    y = t['started']
                    key = (phase(n), prior_band(prior_rate))
                    scores[('prior', *key)].append((max(0.0, min(0.97, prior_rate)), y))
                    scores[('aggregate', *key)].append((p_agg, y))
                    for (k, hl), p in grid.items():
                        hl_s = 'inf' if math.isinf(hl) else f'{hl:g}'
                        scores[(f'recency K={k:g} HL={hl_s}', *key)].append((p, y))
                    n_obs += 1
    print(f'{n_obs} player-gameweek predictions over {", ".join(seasons)}\n')
    rules = sorted({k[0] for k in scores}, key=lambda s: (s != 'prior', s != 'aggregate', s))

    def block(title, select):
        rows = []
        for rule in rules:
            pairs = [pr for key, v in scores.items() if key[0] == rule and select(key)
                     for pr in v]
            if pairs:
                rows.append((rule, len(pairs), brier(pairs), logloss(pairs)))
        if not rows:
            return
        best = min(r[2] for r in rows)
        print(f'--- {title} ---')
        print(f"{'rule':<26}{'n':>7}{'Brier':>9}{'log-loss':>10}")
        for rule, n, b, ll in rows:
            print(f'{rule:<26}{n:>7}{b:>9.4f}{ll:>10.4f}' + ('  <-- best' if b == best else ''))
        print()

    block('all', lambda key: True)
    for ph in ('GW2-8', 'GW9-24', 'GW25-37'):
        block(ph, lambda key, ph=ph: key[1] == ph)
    for band in ('<0.4', '0.4-0.7', '>=0.7'):
        block(f'prior-season start band {band}', lambda key, band=band: key[2] == band)
    print('Read: the recency K/HALF_LIFE pair with the lowest Brier among regulars '
          '(band >=0.7) is the one to quote in player_model.py; if the aggregate '
          'rule wins there, keep it and report why.')


# -------------------------------------------------------------- --rates
def blend_rate(past, cur_mins, cur_rate, m, prior, stab=0.90):
    """shrink()'s formula as-of a season: past = [(distance, mins, rate)],
    current season at relative weight 1.0 x m."""
    num = den = 0.0
    for dist, mins, rate in past:
        w = LADDER.get(dist, 0.3) * mins
        num += rate * w
        den += w
    if cur_mins > 0 and m > 0:
        w = 1.0 * cur_mins * m
        num += cur_rate * w
        den += w
    if den <= 0:
        return prior
    own = num / den
    n_eff = den / FULL_SEASON_MINS
    k = max(0.15, (1.0 - stab) / max(stab, 0.05))
    w_own = n_eff / (n_eff + k)
    return w_own * own + (1 - w_own) * prior


def positional_prior_asof(season, hist_rows, metric):
    idx = season_index(season)
    acc = defaultdict(lambda: [0.0, 0.0])
    first = PM.METRIC_FIRST_SEASON.get(metric, '0000/00')
    for code, by in hist_rows.items():
        for s in SEASONS[:idx]:
            h = by.get(s)
            if not h or h['mins'] < 450 or s < first:
                continue          # a DefCon "0" before 2024/25 means unrecorded
            acc[h['pos']][0] += h[metric] * h['mins']
            acc[h['pos']][1] += h['mins']
    return {pos: (v[0] / v[1] if v[1] else 0.0) for pos, v in acc.items()}


def run_rates(panel, hist_rows, meta, seasons):
    print('\n' + '=' * 78)
    print('P5  RATES: rest-of-season xG/90 and xA/90 from the first n gameweeks')
    print('=' * 78)
    results = defaultdict(list)     # (metric, n, variant, context) -> [(pred, actual, w)]
    for season in seasons:
        rows_by_code = panel[season]
        if not rows_by_code or all(r['xg'] is None for rows in rows_by_code.values() for r in rows):
            print(f'  {season}: no xG columns; skipped')
            continue
        idx = season_index(season)
        prev = SEASONS[idx - 1] if idx else None
        teams_now = {rows[0]['team'] for rows in rows_by_code.values()}
        teams_prev = ({rows[0]['team'] for rows in panel.get(prev, {}).values()}
                      if prev and prev in panel else set())
        prev_team = {code: rows[0]['team'] for code, rows in panel.get(prev, {}).items()} \
            if prev and prev in panel else {}
        new_mgr = new_manager_clubs(season)
        promoted = teams_now - teams_prev if teams_prev else set()
        pos_prior = {m: positional_prior_asof(season, hist_rows, m) for m in ('xg90', 'xa90')}
        for code, rows in rows_by_code.items():
            team = rows[0]['team']
            pos = rows[0]['pos'] or 'MID'
            changed = (team in new_mgr or team in promoted
                       or (code in prev_team and prev_team[code] != team)
                       or (prev_team and code not in prev_team))
            context = 'changed' if changed else 'stable'
            past = []
            for s in SEASONS[:idx]:
                h = hist_rows.get(code, {}).get(s)
                if h and h['mins'] >= 200:
                    past.append((idx - SEASONS.index(s), h['mins'], h))
            for n in RATE_N:
                through = [r for r in rows if r['round'] <= n]
                rest = [r for r in rows if r['round'] > n]
                mins_t = sum(r['mins'] for r in through)
                mins_r = sum(r['mins'] for r in rest)
                if mins_r < 450:
                    continue
                for metric, key in (('xg90', 'xg'), ('xa90', 'xa')):
                    actual = sum((r[key] or 0.0) for r in rest) / mins_r * 90.0
                    cur_rate = (sum((r[key] or 0.0) for r in through) / mins_t * 90.0
                                if mins_t > 0 else 0.0)
                    prior = pos_prior[metric].get(pos, 0.0)
                    past_m = [(d, mins, h[metric]) for d, mins, h in past]
                    preds = {'prior_only': blend_rate(past_m, 0, 0.0, 0, prior)}
                    for m in RATE_MULT:
                        preds[f'blend_m{m}'] = blend_rate(past_m, mins_t, cur_rate, m, prior)
                    preds['current_only'] = cur_rate if mins_t >= 90 else preds['prior_only']
                    for variant, pred in preds.items():
                        results[(metric, n, variant, context)].append((pred, actual, mins_r))
    if not results:
        print('  nothing to score')
        return
    for metric in ('xg90', 'xa90'):
        print(f'\n--- {metric}: rest-of-season, weighted by rest-of-season minutes ---')
        print(f"{'n':>3} {'context':<8}{'variant':<14}{'obs':>6}{'wMAE':>9}{'Spearman':>10}")
        for n in RATE_N:
            for context in ('stable', 'changed'):
                rows_ = []
                for variant in ['prior_only'] + [f'blend_m{m}' for m in RATE_MULT] + ['current_only']:
                    obs = results.get((metric, n, variant, context), [])
                    if len(obs) < 20:
                        continue
                    pred = np.array([o[0] for o in obs])
                    act = np.array([o[1] for o in obs])
                    w = np.array([o[2] for o in obs], float)
                    rows_.append((variant, len(obs), float(np.average(np.abs(pred - act), weights=w)),
                                  spearman(pred, act)))
                if not rows_:
                    continue
                best = min(r[2] for r in rows_)
                for variant, k, mae, rho in rows_:
                    print(f'{n:>3} {context:<8}{variant:<14}{k:>6}{mae:>9.4f}{rho:>10.3f}'
                          + ('  <-- best' if mae == best else ''))
                print()
    print('Read: the multiplier m that wins for "changed" at the n you care about '
          '(GW3-8) is CONTEXT_CURRENT_MULT in player_model.py; if m=1 wins for '
          '"stable" the shrinkage is right as it stands.')


# -------------------------------------------------------------- --retro
def asof_fixture_params(target):
    """{(home, away): dict(lam, mu, cs_h, cs_a)} for every match of `target`
    from a Dixon-Coles fit that has seen nothing on or after 1 July of the
    season's opening year."""
    cutoff = datetime(int(target[:4]), 7, 1)
    matches = BT.load_matches_cached()
    train = [m for m in matches if m['date'] < cutoff]
    assert max(m['date'] for m in train) < cutoff
    model = TM.fit(train, half_life_days=TM.DEFAULT_HALF_LIFE,
                   ref_date=max(m['date'] for m in train))
    pa, pdf = TM.promoted_prior(train)
    mean_atk = float(np.mean(list(model['atk'].values())))
    mean_dfn = float(np.mean(list(model['dfn'].values())))
    atk, dfn = dict(model['atk']), dict(model['dfn'])
    out = {}
    for m in matches:
        if m['season'] != target:
            continue
        for t in (m['home'], m['away']):
            if t not in atk:
                atk[t] = mean_atk + pa
                dfn[t] = mean_dfn + pdf
    patched = dict(model, atk=atk, dfn=dfn)
    for m in matches:
        if m['season'] != target:
            continue
        M, lam, mu = TM.score_matrix(patched, m['home'], m['away'])
        out[(m['home'], m['away'])] = dict(lam=lam, mu=mu, cs_h=float(M[:, 0].sum()),
                                           cs_a=float(M[0, :].sum()))
    return out


def fixture_view(params, row):
    """The season_view-style fixture dict for one player row."""
    home, away = (row['team'], row['opponent']) if row['was_home'] else (row['opponent'], row['team'])
    f = params.get((home, away))
    if not f:
        return dict(xg=1.45, xgc=1.45, cs=0.25)
    if row['was_home']:
        return dict(xg=f['lam'], xgc=f['mu'], cs=f['cs_h'])
    return dict(xg=f['mu'], xgc=f['lam'], cs=f['cs_a'])


def run_retro(panel, hist_rows, meta, seasons):
    print('\n' + '=' * 78)
    print('P3  RETRO REPLAY: the classifier on past seasons with as-of projections')
    print('=' * 78)
    print('approximations: status a for everyone (played proxy), k = 1, set-piece '
          'orders unknown, team strength as of the season start')
    metrics = ('xg90', 'xa90', 'dc90', 'bonus90', 'saves90', 'yellow90')
    by_class = defaultdict(lambda: dict(n=0, next_start=0, next_play=0, resid3=[], xgi_err=[],
                                        prior_err=[]))
    policy = []
    for season in seasons:
        rows_by_code = panel[season]
        if not rows_by_code:
            continue
        idx = season_index(season)
        try:
            params = asof_fixture_params(season)
        except Exception as ex:
            print(f'  {season}: no team fit ({ex}); skipped')
            continue
        players = asof_players(season, rows_by_code, hist_rows, meta)
        # the pecking order and price median a deadline could see: players
        # in the game from round 1 (a January signing's price is not
        # information available in August)
        peers = {c: p for c, p in players.items() if rows_by_code[c][0]['round'] <= 1}
        for pos in POSITIONS:
            prices = sorted(q['price'] for q in peers.values() if q['pos'] == pos)
            PM.PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5
        priors_min = {code: PM.minutes_prior(p, peers) for code, p in players.items()}
        pos_prior = {m: positional_prior_asof(season, hist_rows, m) for m in metrics}
        seqs = team_sequences(rows_by_code)
        # per-player prefix sums so rates "through n" are O(1)
        prefix = {}
        for code, rows in rows_by_code.items():
            # every key seeded, so a player whose first row is mid-season
            # reads zeros before it rather than raising
            acc = {k: 0.0 for k in ('mins', 'starts', 'xg', 'xa', 'defcon', 'bonus',
                                    'saves', 'yellow', 'points')}
            pre = {0: dict(acc)}
            by_round = defaultdict(list)
            for r in rows:
                by_round[r['round']].append(r)
            for rnd in range(1, 39):
                for r in by_round.get(rnd, []):
                    acc['mins'] += r['mins']
                    acc['starts'] += r['started']
                    for key in ('xg', 'xa', 'defcon', 'bonus', 'saves', 'yellow', 'points'):
                        acc[key] += (r[key] or 0.0)
                pre[rnd] = dict(acc)
            prefix[code] = (pre, by_round)

        def rates_through(code, n):
            pre = prefix[code][0][n]
            p = players[code]
            past = {m: [] for m in metrics}
            for s in SEASONS[:idx]:
                h = hist_rows.get(code, {}).get(s)
                if h and h['mins'] >= 200:
                    for m in metrics:
                        if m == 'dc90' and s < '2024/25':
                            continue
                        past[m].append((idx - SEASONS.index(s), h['mins'], h[m]))
            mins = pre['mins']
            out = {}
            for m, key in (('xg90', 'xg'), ('xa90', 'xa'), ('dc90', 'defcon'),
                           ('bonus90', 'bonus'), ('saves90', 'saves'), ('yellow90', 'yellow')):
                cur = (pre[key] / mins * 90.0) if mins >= 200 else 0.0
                stab = PM.STABILITY.get(m, 0.5)
                if m == 'bonus90' and p['pos'] == 'DEF':
                    stab = PM.STABILITY_DEF_BONUS
                out[m] = blend_rate(past[m], mins if mins >= 200 else 0, cur, 1,
                                    pos_prior[m].get(p['pos'], 0.0), stab)
            if season < '2024/25':
                out['dc90'] = 0.0
            n_eff = (sum(LADDER.get(d, 0.3) * mn for d, mn, _ in past['xg90'])
                     + (mins if mins >= 200 else 0)) / FULL_SEASON_MINS
            out['evidence'] = n_eff / (n_eff + 0.111) if n_eff > 0 else 0.0
            out['dc_evidence'] = n_eff / (n_eff + 0.79) if n_eff > 0 else 0.0
            return out

        def start_through(code, n):
            prior_rate, mps = priors_min[code]
            seq = seqs.get(players[code]['team'], [])
            games = sum(1 for _, _, rnd in seq if rnd <= n)
            if games == 0:
                return max(0.0, min(0.97, prior_rate)), mps
            pre = prefix[code][0][n]
            trust = games / (games + PM.CURRENT_TRUST_K)
            rate = trust * min(1.0, pre['starts'] / games) + (1 - trust) * prior_rate
            return max(0.0, min(0.97, rate)), mps

        def snapshot_row(code, n_asof, rnd):
            """A snapshot-shaped row for the player's GW `rnd` fixture(s),
            believed as of the end of GW n_asof."""
            p = players[code]
            p_start, mps = start_through(code, n_asof)
            rates = rates_through(code, n_asof)
            fixtures = [fixture_view(params, r) for r in prefix[code][1].get(rnd, [])]
            row = dict(id=code, name=p['name'], team=p['team'], pos=p['pos'], price=p['price'],
                       status='a', p_start=p_start, p_cameo=0.2 if p['pos'] != 'GKP' else 0.0,
                       start_minutes=mps, cameo_minutes=25.0,
                       expected_minutes=p_start * mps + (1 - p_start) * (0.2 if p['pos'] != 'GKP' else 0) * 25,
                       k=1.0, availability_source='model baseline', pens=None, corners=None,
                       fk=None, **rates)
            p_cameo = (1 - p_start) * row['p_cameo']
            E = RT.expected_components(row, fixtures, p_start, p_cameo, mps, row['cameo_minutes'], 1.0)
            row['proj'] = round(E['total'], 3)
            return row, fixtures

        def stats_of(rows_in_round):
            s = defaultdict(float)
            for r in rows_in_round:
                for src, dst in (('mins', 'minutes'), ('started', 'starts'), ('goals', 'goals_scored'),
                                 ('assists', 'assists'), ('clean_sheets', 'clean_sheets'),
                                 ('goals_conceded', 'goals_conceded'), ('own_goals', 'own_goals'),
                                 ('pens_saved', 'penalties_saved'), ('pens_missed', 'penalties_missed'),
                                 ('yellow', 'yellow_cards'), ('red', 'red_cards'), ('saves', 'saves'),
                                 ('bonus', 'bonus'), ('bps', 'bps'), ('defcon', 'defensive_contribution'),
                                 ('xg', 'expected_goals'), ('xa', 'expected_assists'),
                                 ('points', 'total_points')):
                    s[dst] += (r[src] or 0)
            return dict(s)

        proj3_cache = {}
        for code, rows in rows_by_code.items():
            pre, by_round = prefix[code]
            for n in range(2, 36):
                this = by_round.get(n)
                if not this or not any(r['round'] < n for r in rows):
                    continue
                row, fixtures = snapshot_row(code, n - 1, n)
                if row['proj'] < RT.POOL_MIN_PROJ and row['p_start'] * 1.2 < RT.POOL_MIN_PLAY:
                    continue
                stats = stats_of(this)
                comps, _, _ = RT.decompose(row, fixtures, stats, None)
                gw_rows = [dict(round=r['round'], fixture_id=r['fixture_id'], mins=r['mins'],
                                starts=r['started'], xg=r['xg'] or 0.0, xa=r['xa'] or 0.0)
                           for r in rows if r['round'] <= n]
                cls, sub, tags, _ = RT.classify(row, stats, dict(status='a'), comps, '',
                                                gw_rows, n)
                key = cls if not sub else f'{cls}/{sub}'
                agg = by_class[key]
                nxt = by_round.get(n + 1)
                agg['n'] += 1
                if nxt:
                    agg['next_start'] += int(any(r['started'] for r in nxt))
                    agg['next_play'] += int(any(r['mins'] > 0 for r in nxt))
                # next-3-GW residual against the projection as of GW n
                resid = 0.0
                seen_any = False
                for g in (n + 1, n + 2, n + 3):
                    fut = by_round.get(g)
                    if not fut:
                        continue
                    frow, ffx = snapshot_row(code, n, g)
                    resid += sum(r['points'] or 0 for r in fut) - frow['proj']
                    seen_any = True
                if seen_any:
                    agg['resid3'].append(resid)
                # rest-of-season xGI/90: three-start window vs the prior blend
                rest = [r for r in rows if r['round'] > n]
                mins_rest = sum(r['mins'] for r in rest)
                window = RT.xgi_window(gw_rows, row, n)
                if window and mins_rest >= 450 and any(r['xg'] is not None for r in rest):
                    actual = sum((r['xg'] or 0) + (r['xa'] or 0) for r in rest) / mins_rest * 90
                    last = [r for r in gw_rows if r['starts'] and r['mins'] >= 60][-RT.ROLE_WINDOW_STARTS:]
                    w_mins = sum(r['mins'] for r in last)
                    w_rate = (window[0] / w_mins * 90) if w_mins else 0.0
                    agg['xgi_err'].append(abs(w_rate - actual))
                    agg['prior_err'].append(abs((row['xg90'] + row['xa90']) - actual))
                if cls == 'variance' and resid == resid and seen_any:
                    # hold vs the best same-position, same-or-cheaper alternative
                    # by as-of projection over the next three gameweeks
                    best, best_proj = None, -1.0
                    for other, orows in rows_by_code.items():
                        if other == code or players[other]['pos'] != row['pos']:
                            continue
                        if players[other]['price'] > row['price'] + 1e-9:
                            continue
                        opre, oby = prefix[other]
                        if opre[n]['mins'] < 90:
                            continue
                        if (other, n) not in proj3_cache:
                            proj3 = 0.0
                            for g in (n + 1, n + 2, n + 3):
                                if oby.get(g):
                                    proj3 += snapshot_row(other, n, g)[0]['proj']
                            proj3_cache[(other, n)] = proj3
                        proj3 = proj3_cache[(other, n)]
                        if proj3 > best_proj:
                            best, best_proj = other, proj3
                    if best is not None:
                        hold_pts = sum(r['points'] or 0 for g in (n + 1, n + 2, n + 3)
                                       for r in by_round.get(g, []))
                        alt_pts = sum(r['points'] or 0 for g in (n + 1, n + 2, n + 3)
                                      for r in prefix[best][1].get(g, []))
                        policy.append(hold_pts - (alt_pts - 4.0))
    if not by_class:
        print('  nothing classified')
        return
    print(f"\n{'class':<24}{'n':>7}{'next start':>12}{'next play':>11}{'resid 3GW':>11}"
          f"{'xGI win err':>13}{'prior err':>11}")
    for key in sorted(by_class, key=lambda k: -by_class[k]['n']):
        a = by_class[key]
        ns = a['next_start'] / a['n'] if a['n'] else float('nan')
        npl = a['next_play'] / a['n'] if a['n'] else float('nan')
        r3 = float(np.mean(a['resid3'])) if a['resid3'] else float('nan')
        xe = float(np.mean(a['xgi_err'])) if a['xgi_err'] else float('nan')
        pe = float(np.mean(a['prior_err'])) if a['prior_err'] else float('nan')
        print(f'{key:<24}{a["n"]:>7}{ns:>12.3f}{npl:>11.3f}{r3:>11.2f}{xe:>13.3f}{pe:>11.3f}')
    if policy:
        arr = np.array(policy)
        print(f'\nHold-vs-swap after a `variance` week (n={len(arr)}): holding beats the best '
              f'same-position, same-or-cheaper alternative minus the 4-point hit by '
              f'{arr.mean():+.2f} points over the next three gameweeks '
              f'(sd {arr.std(ddof=1):.2f}, holding wins {np.mean(arr > 0) * 100:.0f}%).')
    print('\nRead: minutes_loss should show next-start ~0.4-0.6 after one; variance '
          'should show resid ~0; if the three-start xGI window beats the prior for '
          'role_change/xgi, P5\'s multiplier has a case.')


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', action='store_true')
    ap.add_argument('--rates', action='store_true')
    ap.add_argument('--retro', action='store_true')
    ap.add_argument('--seasons', nargs='*', default=SEASONS)
    args = ap.parse_args()
    if not (args.minutes or args.rates or args.retro):
        args.minutes = args.rates = args.retro = True
    meta, hist_rows = BT.load_panel()
    panel = load_gw_panel(args.seasons)
    seasons = [s for s in args.seasons if panel.get(s)]
    print('per-GW rows: ' + ', '.join(f'{s} {sum(len(v) for v in panel[s].values())}'
                                      for s in seasons))
    if not seasons:
        raise SystemExit('no per-GW rows; run v2/import_gw_history.py')
    if args.minutes:
        run_minutes(panel, hist_rows, meta, seasons)
    if args.rates:
        run_rates(panel, hist_rows, meta, seasons)
    if args.retro:
        run_retro(panel, hist_rows, meta, seasons)


if __name__ == '__main__':
    main()
