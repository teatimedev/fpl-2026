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
                K in {0.5,1,1.5,2,4,6,8} x HALF_LIFE in {2,3,5,inf}; Brier and log-loss,
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
    --mps       Compare the preseason player minutes-per-start prior with
                manager/club-position blends on started rows. Manager evidence
                is strictly before the target GW and excludes the target player.
    --club      Club-level "eleven start" constraint on the production start
                rule: variants chosen on 2022/23-2023/24, judged once on
                2024/25-2025/26 (start Brier/log-loss, minutes MAE), plus the
                same constraint on this season's archived deadline forecasts.
    --volume    How fixture team xG scales a player's xG/xA: per player-fixture
                given actual minutes, variants chosen on 2023/24 and judged
                once on 2024/25-2025/26 (deviance, MAE, rank, conservation).

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
# The historical total/rates modes need scipy through teams_model. Import
# those modules lazily so the narrower --mps measurement can run without it.
BT = None
TM = None
from manager_changes import new_manager_clubs   # noqa: E402
import manager_minutes as MM       # noqa: E402

DB = HERE / 'fpl.db'
SEASONS = ['2022/23', '2023/24', '2024/25', '2025/26']
K_GRID = (0.5, 1.0, 1.5, 2.0, 4.0, 6.0, 8.0)
HL_GRID = (2.0, 3.0, 5.0, math.inf)
RATE_N = (3, 5, 8, 12)
MPS_WEIGHTS = (0.25, 0.50, 0.75)
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
        r['starts_observed'] = r['starts'] is not None
        r['xg'] = r['xg'] if r['xg'] is not None else None
        panel[r['season']][r['code']].append(r)
    cx.close()
    for s in panel:
        for rows in panel[s].values():
            rows.sort(key=lambda r: (r['kickoff'] or '', r['round'], r['fixture_id'] or 0))
    return panel


def load_mps_history():
    """Minimal season_stat history needed by minutes_prior(), with no scipy."""
    cx = sqlite3.connect(DB)
    meta = {}
    for code, name, dob, team in cx.execute(
            'SELECT code, web_name, birth_date, team FROM player'):
        meta[code] = dict(name=name, dob=dob, cur_team=team)
    rows = defaultdict(dict)
    for code, season, pos, mins, starts in cx.execute(
            'SELECT code, season, pos, minutes, starts FROM season_stat'):
        if mins:
            rows[code][season] = dict(
                season=season, pos=pos, mins=mins, starts=starts or 0)
    cx.close()
    return meta, rows


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
    print('Historical availability unknown: all player-fixture rows count. '
          'Missing start labels are excluded as targets; earlier missing '
          'labels use minutes >=60 only as an explicit evidence proxy.')
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
        priors = {code: PM.minutes_prior(p, players) for code, p in players.items()}
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
                    if not t['starts_observed']:
                        continue
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


# --------------------------------------------------------------- --club
CLUB_VARIANTS = {
    'none': None,
    'logit cap split': dict(mode='logit', two_sided=False, split_gk=True),
    'logit cap total': dict(mode='logit', two_sided=False, split_gk=False),
    'logit 2-sided split': dict(mode='logit', two_sided=True, split_gk=True),
    'logit 2-sided total': dict(mode='logit', two_sided=True, split_gk=False),
    'prop cap split': dict(mode='proportional', two_sided=False, split_gk=True),
    'prop cap total': dict(mode='proportional', two_sided=False, split_gk=False),
    'prop 2-sided split': dict(mode='proportional', two_sided=True, split_gk=True),
    'prop 2-sided total': dict(mode='proportional', two_sided=True, split_gk=False),
    'gk only': dict(mode='logit', two_sided=False, split_gk=True, gk_only=True),
}
CLUB_SELECT = ('2022/23', '2023/24')     # choose the variant here...
CLUB_HOLDOUT = ('2024/25', '2025/26')    # ...and judge it here, once


def club_predictions(season, rows_by_code, hist_rows, meta):
    """{(n, fixture_id, team): [(code, pos, p_start, mps, row)]}: every
    player with a row in a club's GW n+1 fixture, with the production start
    rule (recency K=1, HL=3 over the club's fixtures through GW n) — or the
    preseason prior for a player with no row yet (a new signing is still in
    the club's list). Prices/pecking order as of round 1, as in --retro."""
    players = asof_players(season, rows_by_code, hist_rows, meta)
    peers = {c: p for c, p in players.items() if rows_by_code[c][0]['round'] <= 1}
    for pos in POSITIONS:
        prices = sorted(q['price'] for q in peers.values() if q['pos'] == pos)
        PM.PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5
    priors = {code: PM.minutes_prior(p, peers) for code, p in players.items()}
    seqs = team_sequences(rows_by_code)
    fixtures = defaultdict(list)
    for code, rows in rows_by_code.items():
        by_fixture = {r['fixture_id']: r for r in rows}
        for t in rows:
            n = t['round'] - 1
            if n < 2:
                continue
            seq = [x for x in seqs.get(t['team'], []) if x[2] <= n]
            prior_rate, mps = priors[code]
            evidence = []
            for games_ago, (_, fid, _) in enumerate(reversed(seq)):
                r = by_fixture.get(fid)
                if r is not None:
                    evidence.append((games_ago, r['started'], r['mins']))
            p, mps = PM.recency_update(players[code], prior_rate, mps, evidence=evidence)
            fixtures[(n, t['fixture_id'], t['team'])].append(
                (code, t['pos'] or 'MID', p, mps, t))
    return fixtures


def run_club(panel, hist_rows, meta, seasons):
    """Item 2 (2026-09-23): does a club-level constraint on start
    probabilities (Σ ≤ 11, keepers ≤ 1) improve per-player start Brier /
    log-loss and minutes MAE? Variants are chosen on CLUB_SELECT and scored
    once on CLUB_HOLDOUT. Availability is unknown here, exactly as in
    --minutes: every club fixture counts as evidence."""
    print('\n' + '=' * 78)
    print('CLUB  start probabilities constrained to 11 per club fixture')
    print('=' * 78)
    scores = defaultdict(lambda: defaultdict(list))   # variant -> season -> [(p, y, e_min, min)]
    sums = defaultdict(list)                           # season -> raw club sums
    gk_sums = defaultdict(list)
    for season in seasons:
        rows_by_code = panel[season]
        if not rows_by_code:
            continue
        for key, entries in club_predictions(season, rows_by_code, hist_rows, meta).items():
            raw = [dict(p=p, pos=pos) for _, pos, p, _, _ in entries]
            sums[season].append(sum(e['p'] for e in raw))
            gk_sums[season].append(sum(e['p'] for e in raw if e['pos'] == 'GKP'))
            for name, cfg in CLUB_VARIANTS.items():
                if cfg is None:
                    probs = [e['p'] for e in raw]
                elif cfg.get('gk_only'):
                    probs = PM.normalise_club_starts(
                        [dict(e, fixed=e['pos'] != 'GKP') for e in raw], mode='logit')
                else:
                    probs = PM.normalise_club_starts(raw, **cfg)
                for (code, pos, _, mps, row), p in zip(entries, probs):
                    if not row['starts_observed']:
                        continue
                    cameo = 0.0 if pos == 'GKP' else 0.2
                    e_min = p * mps + (1 - p) * cameo * 25.0
                    scores[name][season].append((p, row['started'], e_min, row['mins'], row['round']))
    for season in seasons:
        if sums[season]:
            arr, gk = np.array(sums[season]), np.array(gk_sums[season])
            print(f'{season}: raw Σ P(start) per club fixture mean {arr.mean():.2f} '
                  f'(p10 {np.percentile(arr, 10):.2f}, p90 {np.percentile(arr, 90):.2f}, '
                  f'share > 11 {np.mean(arr > 11):.0%}); keepers mean {gk.mean():.2f}, '
                  f'share > 1 {np.mean(gk > 1):.0%}')

    def block(label, pick):
        print(f'\n--- {label} ---')
        print(f"{'variant':<22}{'n':>8}{'Brier':>9}{'log-loss':>10}{'min MAE':>9}")
        rows = []
        for name in CLUB_VARIANTS:
            obs = [o for s in pick for o in scores[name].get(s, [])]
            if not obs:
                continue
            rows.append((name, len(obs), brier([(o[0], o[1]) for o in obs]),
                         logloss([(o[0], o[1]) for o in obs]),
                         float(np.mean([abs(o[2] - o[3]) for o in obs]))))
        best = min(r[2] for r in rows)
        for name, n, b, ll, mae in rows:
            print(f'{name:<22}{n:>8}{b:>9.5f}{ll:>10.5f}{mae:>9.3f}'
                  + ('  <-- best Brier' if b == best else ''))
        return {r[0]: r for r in rows}

    select = block('SELECT on ' + ', '.join(CLUB_SELECT), [s for s in CLUB_SELECT if s in seasons])
    chosen = min((r for r in select.values() if r[0] != 'none'), key=lambda r: r[2])[0]
    print(f'\nchosen on the selection seasons: {chosen}')
    for season in CLUB_HOLDOUT:
        if season in seasons:
            block(f'HOLD-OUT {season}', [season])
    held = block('HOLD-OUT pooled ' + ', '.join(CLUB_HOLDOUT),
                 [s for s in CLUB_HOLDOUT if s in seasons])
    if chosen in held and 'none' in held:
        c, b = held[chosen], held['none']
        print(f'\n{chosen} vs none on the hold-out: Brier {c[2] - b[2]:+.5f}, '
              f'log-loss {c[3] - b[3]:+.5f}, minutes MAE {c[4] - b[4]:+.3f}')
        # paired gameweek-block bootstrap of the Brier difference: resample
        # whole (season, gameweek) blocks, the unit the club constraint acts on
        blocks = defaultdict(lambda: [0.0, 0])
        for s in CLUB_HOLDOUT:
            for a, z in zip(scores[chosen].get(s, []), scores['none'].get(s, [])):
                blk = blocks[(s, a[4])]
                blk[0] += (a[0] - a[1]) ** 2 - (z[0] - z[1]) ** 2
                blk[1] += 1
        keys = list(blocks)
        rng = np.random.default_rng(20260923)
        boot = []
        for _ in range(2000):
            pick = rng.integers(0, len(keys), len(keys))
            tot = sum(blocks[keys[i]][0] for i in pick)
            cnt = sum(blocks[keys[i]][1] for i in pick)
            boot.append(tot / cnt)
        lo, hi = np.percentile(boot, [2.5, 97.5])
        print(f'Brier difference 95% gameweek-block interval [{lo:+.5f}, {hi:+.5f}] '
              f'over {len(keys)} blocks')
    club_forward_check(chosen)
    return chosen


def club_forward_check(chosen, history=None):
    """The same constraint on this season's ARCHIVED deadline forecasts
    (data/history/gw{n}.json: availability flags and overrides included,
    written before each deadline) against 2026/27 gw_stat starts. Rows whose
    availability came from a flag or override are held fixed."""
    import json
    history = history or (HERE.parent / 'data' / 'history')
    boot = HERE / 'cache' / 'bootstrap.json'
    if not boot.exists():
        return
    code_of = {e['id']: e['code'] for e in json.loads(boot.read_text())['elements']}
    cx = sqlite3.connect(DB)
    started = defaultdict(dict)
    for code, rnd, st in cx.execute(
            "SELECT code, round, starts FROM gw_stat WHERE season = '2026/27'"):
        if st is not None:
            started[code][rnd] = max(started[code].get(rnd, 0), int(st > 0))
    cx.close()
    out = defaultdict(list)
    sums = []
    for path in sorted(history.glob('gw*.json')):
        if not path.stem[2:].isdigit():
            continue
        snap = json.loads(path.read_text())
        gw = int(snap['gw'])
        by_team = defaultdict(list)
        for r in snap['players']:
            code = code_of.get(r['id'])
            if code is None or gw not in started.get(code, {}) or r.get('fixture_count', 1) != 1:
                continue
            by_team[r['team']].append((r, started[code][gw]))
        for team, rows in by_team.items():
            raw = [dict(p=r['p_start'], pos=r['pos'],
                        fixed=r.get('availability_source') != 'model baseline') for r, _ in rows]
            sums.append(sum(e['p'] for e in raw))
            cfg = dict(CLUB_VARIANTS[chosen])
            cfg.pop('gk_only', None)
            for name, probs in (('none', [e['p'] for e in raw]),
                                (chosen, PM.normalise_club_starts(raw, **cfg))):
                out[name].extend((p, y) for p, (_, y) in zip(probs, rows))
    if not sums:
        return
    print(f'\n--- FORWARD 2026/27 archived deadline forecasts (GW1-{gw}, availability-aware) ---')
    print(f'raw Σ P(start) per club fixture mean {np.mean(sums):.2f} '
          f'(min {min(sums):.2f}, max {max(sums):.2f})')
    for name, pairs in out.items():
        print(f'{name:<22}{len(pairs):>8}{brier(pairs):>9.5f}{logloss(pairs):>10.5f}')


# ------------------------------------------------------------ --volume
VOLUME_SELECT = ('2023/24',)             # tune lambda / level constants here...
VOLUME_HOLDOUT = ('2024/25', '2025/26')  # ...and judge the variants here, once
VOLUME_LAMBDAS = (0.0, 0.25, 0.4, 0.5, 0.56, 0.6, 0.75, 1.0)
LEAGUE_XG = 1.45                          # project()'s league-average divisor


def poisson_deviance(pred, act):
    pred = np.maximum(np.asarray(pred, float), 1e-9)
    act = np.asarray(act, float)
    term = np.where(act > 0, act * np.log(np.where(act > 0, act, 1) / pred), 0.0)
    return float(np.mean(2 * (term - (act - pred))))


def team_fixture_actuals(panel, seasons):
    """{(season, fixture_id, team): (Σ player xG, Σ player xA)} — the club's
    realised chance volume in that match, from every player row."""
    out = defaultdict(lambda: [0.0, 0.0])
    for s in seasons:
        for rows in panel.get(s, {}).values():
            for r in rows:
                if r['xg'] is None:
                    continue
                t = out[(s, r['fixture_id'], r['team'])]
                t[0] += r['xg'] or 0.0
                t[1] += r['xa'] or 0.0
    return out


def run_volume(panel, hist_rows, meta, seasons):
    """Item 3 (2026-09-23): is a club's fixture xG counted twice?

    Per player-fixture, predict the player's xG and xA GIVEN his actual
    minutes (the attack formula is the question, not the minutes model), with
    rates as of the end of the previous gameweek and team xG from the as-of
    Dixon-Coles fit:

      current     xg90 * m/90 * xG_f / 1.45           (project() today)
      lambda=L    xg90 * m/90 * (xG_f / 1.45) ** L     (L tuned on VOLUME_SELECT)
      rate-share  xG_f * (xg90_i m_i) / Σ_club (xg90_j m_j): the same rates,
                  reconciled so the club's players sum to its fixture xG
      share       xG_f * s_i * m/90 with s_i the player's shrunk share of his
                  club's xG while on the pitch, from per-fixture rows
      share-rec   the same, reconciled to the club total

    Each variant gets one level constant per metric fitted on VOLUME_SELECT
    (ratio of sums), so the hold-out compares shape, not an arbitrary level.
    Scores: Poisson deviance and MAE per player-fixture, Spearman, the
    attacking-points MAE (goals x position points + 3 x assists), and club
    conservation (Σ predicted player xG / predicted team xG by club tier).
    """
    print('\n' + '=' * 78)
    print('VOLUME  per player-fixture xG / xA, conditioned on actual minutes')
    print('=' * 78)
    all_seasons = [s for s in SEASONS if s in panel]
    team_act = team_fixture_actuals(panel, all_seasons)
    preds = defaultdict(lambda: defaultdict(list))   # variant -> season -> rows
    conserve = defaultdict(list)                     # (variant, tier) -> ratios
    for season in seasons:
        if season not in (*VOLUME_SELECT, *VOLUME_HOLDOUT):
            continue
        rows_by_code = panel[season]
        idx = season_index(season)
        try:
            params = asof_fixture_params(season)
        except Exception as ex:
            print(f'  {season}: no team fit ({ex}); skipped')
            continue
        pos_prior = {m: positional_prior_asof(season, hist_rows, m) for m in ('xg90', 'xa90')}
        # each club's average fixture xG over the season (the fixture list is
        # known at the deadline): the level its players' rates already carry
        club_fx = defaultdict(list)
        for (home, away), f in params.items():
            club_fx[home].append(f['lam'])
            club_fx[away].append(f['mu'])
        club_mean = {t: float(np.mean(v)) for t, v in club_fx.items()}
        # positional share prior and per-player past shares from earlier
        # seasons' per-fixture rows (xG recorded only)
        share_prior = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        past_share = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]))
        for s in SEASONS[:idx]:
            dist = idx - SEASONS.index(s)
            for code, rows in panel.get(s, {}).items():
                for r in rows:
                    if r['xg'] is None or r['mins'] <= 0:
                        continue
                    tx, ta = team_act[(s, r['fixture_id'], r['team'])]
                    exp = r['mins'] / 90.0
                    pos = r['pos'] or 'MID'
                    sp = share_prior[pos]
                    sp[0] += r['xg'] or 0.0
                    sp[1] += (r['xa'] or 0.0)
                    sp[2] += tx * exp
                    sp[3] += ta * exp
                    ps = past_share[code][dist]
                    ps[0] += r['xg'] or 0.0
                    ps[1] += r['xa'] or 0.0
                    ps[2] += tx * exp
                    ps[3] += ta * exp
        pos_share = {pos: (v[0] / v[2] if v[2] else 0.0, v[1] / v[3] if v[3] else 0.0)
                     for pos, v in share_prior.items()}
        # running current-season sums per player, updated after each round
        cur = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])  # mins, xg, xa, team_xg_exp, team_xa_exp
        by_round = defaultdict(list)
        for code, rows in rows_by_code.items():
            for r in rows:
                by_round[r['round']].append((code, r))
        for rnd in sorted(by_round):
            fixtures = defaultdict(list)
            for code, r in by_round[rnd]:
                if rnd < 2 or r['mins'] <= 0 or r['xg'] is None:
                    continue
                pos = r['pos'] or 'MID'
                c = cur[code]
                past = [(d, h['mins'], h) for s in SEASONS[:idx]
                        for d, h in [(idx - SEASONS.index(s), hist_rows.get(code, {}).get(s))]
                        if h and h['mins'] >= 200]
                rate = {}
                for m, j in (('xg90', 1), ('xa90', 2)):
                    cur_rate = c[j] / c[0] * 90 if c[0] >= 90 else 0.0
                    rate[m] = blend_rate([(d, mn, h[m]) for d, mn, h in past],
                                         c[0] if c[0] >= 90 else 0, cur_rate, 1,
                                         pos_prior[m].get(pos, 0.0),
                                         PM.STABILITY.get(m, 0.9))
                shares = []
                for j, (stab, prior_s) in enumerate(((PM.STABILITY['xg90'], pos_share.get(pos, (0, 0))[0]),
                                                     (PM.STABILITY['xa90'], pos_share.get(pos, (0, 0))[1]))):
                    num = den = 0.0
                    for d, ps in past_share.get(code, {}).items():
                        w = LADDER.get(d, 0.3)
                        num += w * ps[j]
                        den += w * ps[2 + j]
                    num += c[1 + j]
                    den += c[3 + j]
                    # exposure in shrink()'s units: FULL_SEASON_MINS of an
                    # average club's xG
                    n_eff = den / (LEAGUE_XG * FULL_SEASON_MINS / 90.0)
                    k = max(0.15, (1 - stab) / max(stab, 0.05))
                    w_own = n_eff / (n_eff + k) if den > 0 else 0.0
                    own = num / den if den > 0 else prior_s
                    shares.append(w_own * own + (1 - w_own) * prior_s)
                f = fixture_view(params, r)
                fixtures[(r['fixture_id'], r['team'])].append(dict(
                    code=code, pos=pos, mins=r['mins'], xg=r['xg'] or 0.0, xa=r['xa'] or 0.0,
                    goals=r['goals'] or 0, assists=r['assists'] or 0, team=r['team'],
                    xg90=rate['xg90'], xa90=rate['xa90'], share_g=shares[0], share_a=shares[1],
                    fxg=f['xg'], club_mean=club_mean.get(r['team'], LEAGUE_XG)))
            for (fid, team), plist in fixtures.items():
                fxg = plist[0]['fxg']
                vol = fxg / LEAGUE_XG
                tot_g = sum(p['xg90'] * p['mins'] for p in plist)
                tot_a = sum(p['xa90'] * p['mins'] for p in plist)
                sh_g = sum(p['share_g'] * p['mins'] / 90 for p in plist)
                sh_a = sum(p['share_a'] * p['mins'] / 90 for p in plist)
                tier = 'strong' if fxg >= 1.7 else ('weak' if fxg < 1.2 else 'mid')
                for p in plist:
                    e = p['mins'] / 90.0
                    out = {'current': (p['xg90'] * e * vol, p['xa90'] * e * vol)}
                    for lam in VOLUME_LAMBDAS:
                        out[f'lambda={lam:g}'] = (p['xg90'] * e * vol ** lam, p['xa90'] * e * vol ** lam)
                    rel = fxg / p['club_mean']
                    for mu in (0.5, 1.0):
                        out[f'relative mu={mu:g}'] = (p['xg90'] * e * rel ** mu,
                                                      p['xa90'] * e * rel ** mu)
                    out['rate-share'] = (fxg * p['xg90'] * p['mins'] / tot_g if tot_g else 0.0,
                                         fxg * p['xa90'] * p['mins'] / tot_a if tot_a else 0.0)
                    out['share'] = (p['share_g'] * e * fxg, p['share_a'] * e * fxg)
                    out['share-rec'] = (fxg * p['share_g'] * e / sh_g if sh_g else 0.0,
                                        fxg * p['share_a'] * e / sh_a if sh_a else 0.0)
                    for name, (pg, pa) in out.items():
                        preds[name][season].append((pg, pa, p['xg'], p['xa'], p['pos'],
                                                    p['goals'], p['assists'], (fid, team), tier, fxg,
                                                    p['code'], rnd, p['mins']))
            # the round is over: fold its rows into the running sums
            for code, r in by_round[rnd]:
                if r['xg'] is None or r['mins'] <= 0:
                    continue
                tx, ta = team_act[(season, r['fixture_id'], r['team'])]
                c = cur[code]
                c[0] += r['mins']
                c[1] += r['xg'] or 0.0
                c[2] += r['xa'] or 0.0
                c[3] += tx * r['mins'] / 90.0
                c[4] += ta * r['mins'] / 90.0

    names = list(preds)
    # level constants on the selection season(s)
    level = {}
    for name in names:
        sel = [o for s in VOLUME_SELECT for o in preds[name].get(s, [])]
        if not sel:
            continue
        level[name] = (sum(o[2] for o in sel) / max(sum(o[0] for o in sel), 1e-9),
                       sum(o[3] for o in sel) / max(sum(o[1] for o in sel), 1e-9))

    def table(label, pick, lams=True, best=None):
        print(f'\n--- {label} ---')
        print(f"{'variant':<17}{'n':>7}{'xG dev':>9}{'shape':>9}{'xG MAE':>9}{'xG rho':>8}"
              f"{'xA dev':>9}{'shape':>9}{'att-pts MAE':>13}{'Σp/Σa xG':>10}")
        res = {}
        for name in names:
            if not lams and name.startswith('lambda') and name not in ('lambda=0', 'lambda=0.56', best):
                continue
            obs = [o for s in pick for o in preds[name].get(s, [])]
            if not obs or name not in level:
                continue
            kg, ka = level[name]
            pg = np.array([o[0] * kg for o in obs])
            pa = np.array([o[1] * ka for o in obs])
            ag = np.array([o[2] for o in obs])
            aa = np.array([o[3] for o in obs])
            gp = np.array([PM.GOAL_PTS.get(o[4], 5) for o in obs])
            pts_pred = pg * gp + pa * 3
            pts_act = np.array([o[5] for o in obs]) * gp + np.array([o[6] for o in obs]) * 3
            # "shape": the same deviance with each season's level set to the
            # truth (an oracle constant), so only who-gets-how-much is scored
            seas = np.array([s for s in pick for _ in preds[name].get(s, [])])
            sg, sa = pg.copy(), pa.copy()
            for s in pick:
                m = seas == s
                if m.any():
                    sg[m] *= ag[m].sum() / max(pg[m].sum(), 1e-9)
                    sa[m] *= aa[m].sum() / max(pa[m].sum(), 1e-9)
            res[name] = (poisson_deviance(pg, ag), float(np.mean(np.abs(pg - ag))),
                         poisson_deviance(sg, ag), poisson_deviance(sa, aa))
            print(f'{name:<17}{len(obs):>7}{res[name][0]:>9.5f}{res[name][2]:>9.5f}'
                  f'{res[name][1]:>9.5f}'
                  f'{spearman(pg, ag):>8.3f}{poisson_deviance(pa, aa):>9.5f}'
                  f'{res[name][3]:>9.5f}'
                  f'{float(np.mean(np.abs(pts_pred - pts_act))):>13.4f}'
                  f'{pg.sum() / max(ag.sum(), 1e-9):>10.3f}')
        return res

    sel = table('SELECT ' + ', '.join(VOLUME_SELECT) + ' (level constants fitted here)',
                list(VOLUME_SELECT))
    best_lam = min((n for n in sel if n.startswith('lambda')), key=lambda n: sel[n][0])
    print(f'\nlambda chosen on the selection season (xG deviance): {best_lam}')
    for s in VOLUME_HOLDOUT:
        table(f'HOLD-OUT {s}', [s], lams=False, best=best_lam)
    table('HOLD-OUT pooled', list(VOLUME_HOLDOUT))

    # conservation: Σ predicted player xG per club fixture / predicted team xG,
    # before any level constant, by fixture-xG tier
    print('\n--- club conservation on the hold-out: Σ player xG / fixture team xG '
          '(raw, before level constants) ---')
    print(f"{'variant':<17}" + ''.join(f'{t:>9}' for t in ('weak', 'mid', 'strong', 'all')))
    for name in ('current', best_lam, 'relative mu=1', 'rate-share', 'share', 'share-rec'):
        agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        for s in VOLUME_HOLDOUT:
            for o in preds[name].get(s, []):
                a = agg[o[8]][(s, o[7])]
                a[0] += o[0]
                a[1] = o[9]
        cells = []
        for tier in ('weak', 'mid', 'strong', 'all'):
            fx = ([v for t in ('weak', 'mid', 'strong') for v in agg[t].values()]
                  if tier == 'all' else list(agg[tier].values()))
            cells.append(np.mean([v[0] / v[1] for v in fx if v[1] > 0]) if fx else float('nan'))
        print(f'{name:<17}' + ''.join(f'{c:>9.3f}' for c in cells))
    print('(players on the pitch cover ~990 of 990 team minutes, so a conserving '
          'rule reads 1.00 in every tier)')

    # Between players, which is what squad selection ranks: sum each player's
    # predictions and outcomes over a window (level constants applied), then
    # rank players against each other. Minutes are still the actual ones.
    print('\n--- between players on the hold-out: per-player window totals '
          '(>= 450 minutes in the window) ---')
    print(f"{'variant':<17}{'window':<9}{'n':>6}{'xG rho':>8}{'xGI rho':>9}"
          f"{'att-pts rho':>12}{'xG dev':>9}")
    for name in ('current', best_lam, 'lambda=0', 'relative mu=0.5', 'relative mu=1', 'share'):
        kg, ka = level[name]
        for label, lo, hi in (('GW2-8', 2, 8), ('GW2-38', 2, 38)):
            tot = defaultdict(lambda: [0.0] * 7)
            for s in VOLUME_HOLDOUT:
                for o in preds[name].get(s, []):
                    if not lo <= o[11] <= hi:
                        continue
                    t = tot[(s, o[10])]
                    gp = PM.GOAL_PTS.get(o[4], 5)
                    t[0] += o[0] * kg
                    t[1] += o[2]
                    t[2] += o[0] * kg + o[1] * ka
                    t[3] += o[2] + o[3]
                    t[4] += o[0] * kg * gp + o[1] * ka * 3
                    t[5] += o[5] * gp + o[6] * 3
                    t[6] += o[12]
            v = [t for t in tot.values() if t[6] >= 450]
            if len(v) < 20:
                continue
            col = lambda i: np.array([t[i] for t in v])
            print(f'{name:<17}{label:<9}{len(v):>6}{spearman(col(0), col(1)):>8.3f}'
                  f'{spearman(col(2), col(3)):>9.3f}{spearman(col(4), col(5)):>12.3f}'
                  f'{poisson_deviance(col(0), col(1)):>9.4f}')
    return best_lam


# --------------------------------------------------------------- --mps
def run_mps(panel, hist_rows, meta, seasons):
    """Walk-forward minutes-per-start comparison on started player-GW rows.

    The player prediction is the production preseason prior: only season_stat
    rows before the target season reach minutes_prior(). The manager estimate
    uses this season's starts from earlier rounds only. Both its club cell and
    league-position prior subtract every earlier row belonging to the target
    player, so the purported manager signal cannot recycle the player's own
    minutes tendency.
    """
    print('\n' + '=' * 78)
    print('MPS  MINUTES PER START: player prior vs as-of manager blend')
    print('=' * 78)
    print('manager evidence: earlier GWs only; target player excluded')

    errors = defaultdict(list)  # (rule, pos) -> absolute errors
    manager_ns = []

    for season in seasons:
        rows_by_code = panel[season]
        if not rows_by_code:
            continue
        players = asof_players(season, rows_by_code, hist_rows, meta)
        for pos in POSITIONS:
            prices = sorted(q['price'] for q in players.values() if q['pos'] == pos)
            PM.PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5
        player_mps = {code: PM.minutes_prior(p, players)[1]
                      for code, p in players.items()}

        by_round = defaultdict(list)
        for code, rows in rows_by_code.items():
            for row in rows:
                if row['started']:
                    by_round[row['round']].append((code, row))

        # Sufficient statistics through the end of the previous round. Keeping
        # the update after the scoring loop is the mechanical no-leak barrier,
        # including for double-GW fixtures in the same round.
        team_sum = defaultdict(float)
        team_n = defaultdict(int)
        team_player_sum = defaultdict(float)
        team_player_n = defaultdict(int)
        league_sum = defaultdict(float)
        league_n = defaultdict(int)
        league_player_sum = defaultdict(float)
        league_player_n = defaultdict(int)

        for rnd in sorted(by_round):
            targets = by_round[rnd]
            for code, row in targets:
                pos = row['pos'] or 'MID'
                team = row['team']
                actual = row['mins']
                prior = player_mps[code]

                lp_key = (pos, code)
                ln = league_n[pos] - league_player_n[lp_key]
                league = ((league_sum[pos] - league_player_sum[lp_key]) / ln
                          if ln else prior)

                cell_key = (team, pos)
                cp_key = (team, pos, code)
                cn = team_n[cell_key] - team_player_n[cp_key]
                if cn:
                    raw = ((team_sum[cell_key] - team_player_sum[cp_key]) / cn)
                    trust = cn / (cn + MM.K)
                    manager = trust * raw + (1 - trust) * league
                else:
                    manager = league
                manager_ns.append(cn)

                errors[('player', pos)].append(abs(prior - actual))
                for weight in MPS_WEIGHTS:
                    pred = (1 - weight) * prior + weight * manager
                    errors[(f'w={weight:g}', pos)].append(abs(pred - actual))

            for code, row in targets:
                pos = row['pos'] or 'MID'
                team = row['team']
                mins = row['mins']
                cell_key = (team, pos)
                cp_key = (team, pos, code)
                lp_key = (pos, code)
                team_sum[cell_key] += mins
                team_n[cell_key] += 1
                team_player_sum[cp_key] += mins
                team_player_n[cp_key] += 1
                league_sum[pos] += mins
                league_n[pos] += 1
                league_player_sum[lp_key] += mins
                league_player_n[lp_key] += 1

    rules = ('player', *(f'w={w:g}' for w in MPS_WEIGHTS))

    def mae(rule, pos=None):
        vals = (errors[(rule, pos)] if pos else
                [e for p in POSITIONS for e in errors[(rule, p)]])
        return float(np.mean(vals)) if vals else float('nan')

    n_obs = sum(len(errors[('player', pos)]) for pos in POSITIONS)
    print(f'{n_obs} started player-fixture predictions over {", ".join(seasons)}')
    print(f'{"rule":<10}{"overall":>10}' +
          ''.join(f'{pos:>10}' for pos in POSITIONS))
    for rule in rules:
        print(f'{rule:<10}{mae(rule):>10.3f}' +
              ''.join(f'{mae(rule, pos):>10.3f}' for pos in POSITIONS))

    best_weight = min(MPS_WEIGHTS, key=lambda w: mae(f'w={w:g}'))
    baseline = mae('player')
    best = mae(f'w={best_weight:g}')
    verdict = 'WINS' if best < baseline else 'DOES NOT WIN'
    print(f'best weight: {best_weight:g} (MAE {best:.3f} vs player {baseline:.3f}; '
          f'manager blend {verdict}, delta {best - baseline:+.3f})')
    if manager_ns:
        print(f'manager peer starts available: median {float(np.median(manager_ns)):.0f}')
    return dict(n=n_obs, player_mae=baseline, best_weight=best_weight,
                best_mae=best, manager_wins=best < baseline)


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
                    if any(r[key] is None and r['mins'] > 0 for r in through + rest):
                        continue  # no false-zero rates or partial-season denominators
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
    ap.add_argument('--mps', action='store_true')
    ap.add_argument('--club', action='store_true')
    ap.add_argument('--volume', action='store_true')
    ap.add_argument('--seasons', nargs='*', default=SEASONS)
    args = ap.parse_args()
    if not (args.minutes or args.rates or args.retro or args.mps or args.club or args.volume):
        args.minutes = args.rates = args.retro = True
    global BT, TM
    if args.minutes or args.rates or args.retro or args.club or args.volume:
        import backtest_totals as backtest_totals
        import teams_model as teams_model
        BT, TM = backtest_totals, teams_model
        meta, hist_rows = BT.load_panel()
    else:
        meta, hist_rows = load_mps_history()
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
    if args.mps:
        run_mps(panel, hist_rows, meta, seasons)
    if args.club:
        run_club(panel, hist_rows, meta, seasons)
    if args.volume:
        run_volume(panel, hist_rows, meta, seasons)
    if args.retro:
        run_retro(panel, hist_rows, meta, seasons)


if __name__ == '__main__':
    main()
