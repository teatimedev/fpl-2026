"""
Season replay: the weekly decision policy on past seasons, scored in real points.

Every constant in the transfer policy (the 2-point churn bar, the six-week
window) was hand-set, and nothing measured the policy as a whole: the
backtests grade projections, threshold_sweep is a one-candidate toy, and the
in-season policy benchmarks have not yet separated (every buffer held). This
replays whole seasons instead. At each deadline it rebuilds every player's
projection from what was knowable then, runs the production planner and
choose_plan() under the policy being tested, makes the transfers at that
gameweek's real prices and selling-price rule, picks the XI and captain with
the production lineup search, and scores the real points with FPL's auto-subs.

As-of inputs (the leak barriers are backtest_inseason's):
  * minutes: production's recency rule over the player's rows before the
    deadline, against the price/history prior;
  * rates: the multi-season shrinkage blend through the previous gameweek;
  * team strength: Dixon-Coles fitted on matches before 1 July of the season
    (no in-season update — production adjusts teams in-season, so replay
    projections are noisier than live ones);
  * fixtures: the club's schedule, not the player's own future rows; prices:
    the gameweek's real price; departures: a player missing from his club's
    last played round is unavailable.

Known gaps, stated once: no injury news or FPL flags (every player is status
`a`; the minutes rule only sees absences after they happen), no chips, no
calibration k (1.0), set-piece orders unknown, DefCon scored only from
2025/26, and every season is played under today's rules (bank up to five
free transfers). Absolute totals are therefore below what a manager with news
would score; the comparison between policies on the same seasons is the
point.

    python v2/season_replay.py --build                  # cache projections
    python v2/season_replay.py --bars 0 1 2 3 --never   # replay, ~20 min on 10 cores
"""
import argparse
import json
import math
import pickle
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backtest_inseason as BI     # noqa: E402
import player_model as PM          # noqa: E402
import retro as RT                 # noqa: E402

ROOT = HERE.parent
CACHE = HERE / 'cache' / 'replay'
OUT = ROOT / 'data' / 'season_replay.json'
SEASONS = ('2023/24', '2024/25', '2025/26')   # 2022/23 has no team fit behind it
WINDOW = 6                                    # production's GW n..n+5
METRICS = (('xg90', 'xg'), ('xa90', 'xa'), ('dc90', 'defcon'),
           ('bonus90', 'bonus'), ('saves90', 'saves'), ('yellow90', 'yellow'))
BUDGET = 1000                                 # tenths


def _load_history():
    import backtest_totals
    import teams_model
    BI.BT, BI.TM = backtest_totals, teams_model
    meta, hist_rows = backtest_totals.load_panel()
    return meta, hist_rows


class SeasonModel:
    """As-of projections and real outcomes for one past season."""

    def __init__(self, season, rows_by_code, hist_rows, meta):
        self.season = season
        self.rows = rows_by_code
        self.idx = BI.season_index(season)
        self.params = BI.asof_fixture_params(season)
        self.players = BI.asof_players(season, rows_by_code, hist_rows, meta)
        peers = {c: p for c, p in self.players.items() if rows_by_code[c][0]['round'] <= 1}
        for pos in BI.POSITIONS:
            prices = sorted(q['price'] for q in peers.values() if q['pos'] == pos)
            PM.PRICE_MEDIAN[pos] = prices[len(prices) // 2] if prices else 5.5
        self.priors = {c: PM.minutes_prior(p, peers) for c, p in self.players.items()}
        self.pos_prior = {m: BI.positional_prior_asof(season, hist_rows, m) for m, _ in METRICS}
        self.hist = hist_rows
        self.seqs = BI.team_sequences(rows_by_code)
        self.club_fixtures = defaultdict(dict)        # (team, round) -> {fid: fixture}
        self.by_round, self.by_fixture, self.prefix = {}, {}, {}
        for code, rows in rows_by_code.items():
            by_round = defaultdict(list)
            for r in rows:
                by_round[r['round']].append(r)
                self.club_fixtures[(r['team'], r['round'])][r['fixture_id']] = dict(
                    team=r['team'], opponent=r['opponent'], was_home=r['was_home'])
            self.by_round[code] = by_round
            self.by_fixture[code] = {r['fixture_id']: r for r in rows}
            acc = {k: 0.0 for k in ('mins', 'xg', 'xa', 'defcon', 'bonus', 'saves', 'yellow')}
            pre = {0: dict(acc)}
            for rnd in range(1, 39):
                for r in by_round.get(rnd, []):
                    acc['mins'] += r['mins']
                    for key in ('xg', 'xa', 'defcon', 'bonus', 'saves', 'yellow'):
                        acc[key] += (r[key] or 0.0)
                pre[rnd] = dict(acc)
            self.prefix[code] = pre
        self.last_team_round = defaultdict(int)       # (team, n) handled in departed()

    # ------------------------------------------------------------ state at n
    def latest_row(self, code, n):
        rows = [r for r in self.rows[code] if r['round'] <= n]
        return rows[-1] if rows else None

    def departed(self, code, n):
        """Missing from his club's most recent played round before deadline n."""
        last = self.latest_row(code, n - 1)
        if last is None:
            return False
        team_rounds = [rnd for _, _, rnd in self.seqs.get(last['team'], []) if rnd <= n - 1]
        return bool(team_rounds) and last['round'] < max(team_rounds)

    def price(self, code, n):
        row = self.latest_row(code, n)
        return int(row['price'] or 50) if row else None

    def rates(self, code, n):
        pre, p = self.prefix[code][n], self.players[code]
        past = {m: [] for m, _ in METRICS}
        for s in BI.SEASONS[:self.idx]:
            h = self.hist.get(code, {}).get(s)
            if h and h['mins'] >= 200:
                for m, _ in METRICS:
                    if m == 'dc90' and s < '2024/25':
                        continue
                    past[m].append((self.idx - BI.SEASONS.index(s), h['mins'], h[m]))
        mins = pre['mins']
        out = {}
        for m, key in METRICS:
            cur = (pre[key] / mins * 90.0) if mins >= 200 else 0.0
            stab = PM.STABILITY.get(m, 0.5)
            if m == 'bonus90' and p['pos'] == 'DEF':
                stab = PM.STABILITY_DEF_BONUS
            out[m] = BI.blend_rate(past[m], mins if mins >= 200 else 0, cur, 1,
                                   self.pos_prior[m].get(p['pos'], 0.0), stab)
        if self.season < '2025/26':
            out['dc90'] = 0.0          # DefCon points did not exist
        n_eff = (sum(BI.LADDER.get(d, 0.3) * mn for d, mn, _ in past['xg90'])
                 + (mins if mins >= 200 else 0)) / BI.FULL_SEASON_MINS
        out['evidence'] = n_eff / (n_eff + 0.111) if n_eff > 0 else 0.0
        out['dc_evidence'] = n_eff / (n_eff + 0.79) if n_eff > 0 else 0.0
        return out

    def minutes(self, code, team, n):
        prior_rate, mps = self.priors[code]
        evidence = []
        played = [x for x in self.seqs.get(team, []) if x[2] <= n]
        for games_ago, (_, fid, _) in enumerate(reversed(played)):
            r = self.by_fixture[code].get(fid)
            if r is not None:
                evidence.append((games_ago, r['started'], r['mins']))
        return PM.recency_update(self.players[code], prior_rate, mps, evidence=evidence)

    def projections(self, n):
        """{code: planner-shaped player} believed at deadline n (rows through n-1)."""
        last_gw = min(38, n + WINDOW - 1)
        out = {}
        for code, p in self.players.items():
            row = self.latest_row(code, n)
            if row is None:
                continue                                   # not in the game yet
            team = row['team']
            gone = self.departed(code, n)
            p_start, mps = self.minutes(code, team, n - 1)
            rates = self.rates(code, n - 1)
            cameo = 0.0 if p['pos'] == 'GKP' else 0.2
            p_cameo = (1 - p_start) * cameo
            base = dict(pos=p['pos'], k=1.0, pens=None, corners=None, fk=None, **rates)
            proj, play = [0.0] * 38, [0.0] * 38
            for g in range(n, last_gw + 1):
                if gone:
                    continue
                fixtures = [BI.fixture_view(self.params, f)
                            for f in self.club_fixtures.get((team, g), {}).values()]
                if not fixtures:
                    continue
                E = RT.expected_components(base, fixtures, p_start, p_cameo, mps, 25.0, 1.0)
                proj[g - 1] = E['total']
                p_play = p_start + p_cameo
                play[g - 1] = 1 - (1 - p_play) ** len(fixtures)
            out[code] = dict(id=code, name=p['name'], pos=p['pos'], team=team,
                             price=self.price(code, n) / 10.0, status='u' if gone else 'a',
                             news='', chance=None, proj_by_gw=proj, play_by_gw=play)
        return out

    def outcome(self, code, g):
        rows = self.by_round.get(code, {}).get(g, [])
        return sum(r['points'] or 0 for r in rows), any(r['mins'] > 0 for r in rows)


def build(season):
    meta, hist_rows = _load_history()
    panel = BI.load_gw_panel([season])
    model = SeasonModel(season, panel[season], hist_rows, meta)
    weeks = {n: model.projections(n) for n in range(1, 39)}
    outcomes = {(code, g): model.outcome(code, g)
                for code in model.players for g in range(1, 39)}
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f'{season.replace("/", "-")}.pkl'
    path.write_bytes(pickle.dumps(dict(weeks=weeks, outcomes=outcomes,
                                       price={(c, n): model.price(c, n) for c in model.players
                                              for n in range(1, 39)})))
    return path


# ------------------------------------------------------------------ replay
def sell_price(now, bought):
    return now if now <= bought else bought + (now - bought) // 2


def opening_squad(players):
    """A legal cheapest 15; the GW1 solve then rebuilds it for free."""
    from weekly import SQUAD_SHAPE
    clubs, squad = defaultdict(int), []
    for pos, n in SQUAD_SHAPE.items():
        for p in sorted((q for q in players.values() if q['pos'] == pos),
                        key=lambda q: (q['price'], -sum(q['proj_by_gw']))):
            if sum(1 for i in squad if players[i]['pos'] == pos) >= n:
                break
            if clubs[p['team']] < 3:
                squad.append(p['id'])
                clubs[p['team']] += 1
    return squad


def replay(season, policy, time_limit=20):
    """One season under one policy. policy: dict(name, bar) — bar None = never move."""
    from planner import plan, transfer_ledger
    from squad_evaluator import apply_autosubs, captain_replacement, pick_lineup
    from weekly import choose_plan, forced_out, move_bar
    data = pickle.loads((CACHE / f'{season.replace("/", "-")}.pkl').read_bytes())
    weeks, outcomes, prices = data['weeks'], data['outcomes'], data['price']

    players = weeks[1]
    seed = opening_squad(players)
    spent = sum(int(round(players[i]['price'] * 10)) for i in seed)
    first = plan(players, seed, (BUDGET - spent) / 10, 15, 1, WINDOW, time_limit=time_limit)
    squad = first['weeks'][0]['squad']
    bought = {i: int(round(players[i]['price'] * 10)) for i in squad}
    bank = BUDGET - sum(bought.values())
    ft, log, total = 1, [], 0
    for n in range(1, 39):
        players = dict(weeks[n])
        for i in squad:                     # an owned player who left the game
            if i not in players:
                players[i] = dict(id=i, name=str(i), pos=None, team=None,
                                  price=(prices.get((i, n)) or bought[i]) / 10.0,
                                  status='u', news='', proj_by_gw=[0.0] * 38,
                                  play_by_gw=[0.0] * 38)
        moves_out, moves_in, hits = [], [], 0
        if n > 1:
            horizon = min(38, n + WINDOW - 1)
            now = {i: int(round(players[i]['price'] * 10)) for i in squad}
            sells = {i: sell_price(now[i], bought[i]) / 10 for i in squad}
            hold = plan(players, squad, bank / 10, ft, n, horizon, sell_prices=sells,
                        freeze_this_week=True, time_limit=time_limit)
            if hold is None:            # never let a solver failure pass as "held"
                raise RuntimeError(f'{season} GW{n}: holding the squad is infeasible')
            chosen = hold
            if policy['bar'] is not None:
                paths = [('unconstrained', plan(players, squad, bank / 10, ft, n, horizon,
                                                sell_prices=sells, time_limit=time_limit))]
                dead = [i for i in squad if forced_out(players[i], n)]
                if dead:
                    paths.append(('clear unavailable', plan(
                        players, squad, bank / 10, ft, n, horizon, sell_prices=sells,
                        must_sell=dead, time_limit=time_limit)))
                chosen, _, _ = choose_plan(paths, hold, bar=lambda w: move_bar(
                    w['out'], players, n, ft, per_move=policy['bar']))
            if chosen:
                week = chosen['weeks'][0]
                moves_out, moves_in, hits = week['out'], week['in'], week['hits']
            for o in moves_out:
                bank += int(round(sells[o] * 10))
                del bought[o]
            for i in moves_in:
                bought[i] = int(round(players[i]['price'] * 10))
                bank -= bought[i]
            if bank < 0:
                raise RuntimeError(f'{season} GW{n}: overspent by {-bank / 10:.1f}m')
            squad = [i for i in squad if i not in moves_out] + list(moves_in)
            ft = transfer_ledger(ft, len(moves_out), n)['ft_next']
        lineup = pick_lineup([players[i] for i in squad], n)
        played = {i: outcomes.get((i, n), (0, False))[1] for i in squad}
        points = {i: outcomes.get((i, n), (0, False))[0] for i in squad}
        subs = apply_autosubs(lineup.xi, lineup.bench, played)
        captain = captain_replacement(lineup.captain['id'] if lineup.captain else None,
                                      lineup.vice['id'] if lineup.vice else None, played)
        score = (sum(points[i] for i in subs.scoring_ids)
                 + (points[captain] if captain is not None else 0) - 4 * hits)
        expected = (sum(players[p['id']]['proj_by_gw'][n - 1] for p in lineup.xi)
                    + (players[lineup.captain['id']]['proj_by_gw'][n - 1] if lineup.captain else 0))
        total += score
        log.append(dict(gw=n, points=score, expected=round(expected, 2), hits=hits,
                        out=moves_out, in_=moves_in, ft_after=ft, bank=bank))
    return dict(season=season, policy=policy['name'], total=total,
                transfers=sum(len(w['out']) for w in log[1:]),
                hits=sum(w['hits'] for w in log), weeks=log)


def paired(results, baseline='bar 2'):
    """Per-gameweek paired differences against production's bar, all seasons.

    Policies share every season's realised points, so the honest error bar is
    on the weekly difference, not on two independent totals.
    """
    by = defaultdict(dict)
    for r in results:
        by[r['policy']][r['season']] = {w['gw']: w['points'] for w in r['weeks']}
    base = by.get(baseline, {})
    rows = []
    for name, seasons in by.items():
        if name == baseline or set(seasons) != set(base):
            continue
        diffs = [seasons[s][g] - base[s][g] for s in seasons for g in seasons[s]]
        mean = sum(diffs) / len(diffs)
        sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1))
        rows.append(dict(policy=name, per_season=mean * 38, se_per_season=sd / math.sqrt(len(diffs)) * 38,
                         weeks=len(diffs)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true', help='cache as-of projections first')
    ap.add_argument('--seasons', nargs='*', default=list(SEASONS))
    ap.add_argument('--bars', nargs='*', type=float, default=[0.0, 1.0, 2.0, 3.0])
    ap.add_argument('--never', action='store_true', help='also replay "never transfer"')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--time-limit', type=int, default=20)
    ap.add_argument('--summary', action='store_true', help='re-print the last saved run')
    args = ap.parse_args()

    if args.summary:
        saved = json.loads(OUT.read_text())
        for row in paired(saved['results']):
            print(f'{row["policy"]:<16} {row["per_season"]:+7.1f} pts/season vs bar 2 '
                  f'(± {row["se_per_season"]:.1f} s.e., {row["weeks"]} paired gameweeks)')
        return

    if args.build:
        with ProcessPoolExecutor(max_workers=len(args.seasons)) as ex:
            for path in ex.map(build, args.seasons):
                print(f'cached {path.relative_to(ROOT)}', flush=True)

    policies = [dict(name=f'bar {b:g}', bar=b) for b in args.bars]
    if args.never:
        policies.append(dict(name='never transfer', bar=None))
    jobs = [(s, p) for s in args.seasons for p in policies]
    results, started = [], time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(replay, s, p, args.time_limit): (s, p['name']) for s, p in jobs}
        for fut in as_completed(futures):
            s, name = futures[fut]
            try:
                res = fut.result()
            except Exception as err:            # one failed run must not lose the rest
                print(f'{s} {name}: FAILED {err!r}', flush=True)
                continue
            results.append(res)
            print(f'{s} {name}: {res["total"]} pts, {res["transfers"]} transfers, '
                  f'{res["hits"]} hits  ({time.time() - started:.0f}s)', flush=True)

    by = defaultdict(dict)
    for r in results:
        by[r['policy']][r['season']] = r
    print(f'\n{"policy":<16}' + ''.join(f'{s:>10}' for s in args.seasons) + f'{"total":>10}{"moves":>8}{"hits":>6}')
    for name in [p['name'] for p in policies]:
        row = by.get(name, {})
        if len(row) != len(args.seasons):
            continue
        print(f'{name:<16}' + ''.join(f'{row[s]["total"]:>10}' for s in args.seasons)
              + f'{sum(r["total"] for r in row.values()):>10}'
              + f'{sum(r["transfers"] for r in row.values()):>8}'
              + f'{sum(r["hits"] for r in row.values()):>6}')
    for row in paired(results):
        print(f'{row["policy"]:<16} {row["per_season"]:+7.1f} pts/season vs bar 2 '
              f'(± {row["se_per_season"]:.1f} s.e., {row["weeks"]} paired gameweeks)')
    OUT.write_text(json.dumps(dict(generated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                   window=WINDOW, seasons=args.seasons, results=results),
                              indent=1) + '\n')
    print(f'wrote {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
