"""
Import per-gameweek player rows for PAST seasons into gw_stat (P2's backward
validation data), from the public vaastav/Fantasy-Premier-League dataset.

FPL's own API only serves the current season's per-round history; the
repository's four seasons of history are season aggregates. vaastav's
`data/<season>/gws/merged_gw.csv` has per-GW rows (minutes, starts from
2022-23, xG/xA from 2022-23, points, bps, opponent, home/away, kickoff) keyed
by per-season element ids, and `players_raw.csv` carries the stable `code`
that season_stat and player are keyed on.

What this does NOT give the backtests: availability at each historical
deadline (status/chance are not in merged_gw), so the backward test can only
condition on "played" — the known gap the plan records.

    python v2/import_gw_history.py [--seasons 2022/23 2023/24 ...] [--offline]

Downloads are cached under v2/cache/vaastav/ (ignored by git).
"""
import argparse
import csv
import io
import sqlite3
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch import DB, GW_STAT_COLUMNS, GW_STAT_INSERT, schema, f as _f, i as _i  # noqa: E402

BASE = 'https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data'
CACHE = HERE / 'cache' / 'vaastav'
SEASON_DIR = {'2022/23': '2022-23', '2023/24': '2023-24',
              '2024/25': '2024-25', '2025/26': '2025-26'}
POS = {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}
POS_NAME = {'GK': 'GKP', 'GKP': 'GKP', 'DEF': 'DEF', 'MID': 'MID', 'FWD': 'FWD'}
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}


def fetch_text(url, cache_path, offline=False):
    if cache_path.exists():
        return cache_path.read_text(encoding='utf-8')
    if offline:
        raise FileNotFoundError(f'{cache_path} missing and --offline set')
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode('utf-8-sig', 'ignore')
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(raw, encoding='utf-8')
    return raw


def read_csv(text):
    return list(csv.DictReader(io.StringIO(text)))


def rows_from_merged(season, merged, code_of, team_of, pos_of, team_short, team_by_name=None):
    """gw_stat rows from merged_gw.csv rows. Returns (rows, missing_columns).

    The club is taken from the ROW's `team` column (the club he was at for
    that fixture) when merged_gw carries it, not from players_raw.csv, which
    holds the club at the season's end — stamping that on every row would
    hand the backtests a January move before it happened (look-ahead)."""
    header = set(merged[0].keys()) if merged else set()
    team_by_name = team_by_name or {}
    missing = [c for c in ('starts', 'expected_goals', 'expected_assists',
                           'expected_goals_conceded', 'defensive_contribution')
               if c not in header]
    rows = []
    for r in merged:
        try:
            element = int(r['element'])
        except (KeyError, ValueError):
            continue
        code = code_of.get(element)
        if code is None:
            continue
        team = team_by_name.get((r.get('team') or '').strip()) or team_of.get(element)
        pos = pos_of.get(element) or POS_NAME.get((r.get('position') or '').upper())
        opponent = team_short.get(_i(r.get('opponent_team')))
        starts = _i(r.get('starts')) if 'starts' in header else None
        rows.append((
            code, season, _i(r.get('round') or r.get('GW')), _i(r.get('fixture')), team,
            pos, opponent, 1 if str(r.get('was_home', '')).strip().lower() in ('true', '1') else 0,
            r.get('kickoff_time'),
            _i(r.get('minutes')) or 0, starts, _i(r.get('total_points')) or 0,
            _i(r.get('goals_scored')) or 0, _i(r.get('assists')) or 0,
            _i(r.get('clean_sheets')) or 0, _i(r.get('goals_conceded')) or 0,
            _i(r.get('own_goals')) or 0, _i(r.get('penalties_saved')) or 0,
            _i(r.get('penalties_missed')) or 0,
            _f(r.get('expected_goals')), _f(r.get('expected_assists')),
            _f(r.get('expected_goals_conceded')), _i(r.get('defensive_contribution')),
            _i(r.get('bps')) or 0, _i(r.get('bonus')) or 0, _i(r.get('saves')) or 0,
            _i(r.get('yellow_cards')) or 0, _i(r.get('red_cards')) or 0,
            _f(r.get('threat')), _f(r.get('creativity')), _f(r.get('influence')),
            _i(r.get('value')), _i(r.get('selected')),
        ))
    assert all(len(row) == len(GW_STAT_COLUMNS) for row in rows)
    return rows, missing


def import_season(cx, season, offline=False):
    folder = SEASON_DIR[season]
    cache = CACHE / folder
    players_raw = read_csv(fetch_text(f'{BASE}/{folder}/players_raw.csv',
                                      cache / 'players_raw.csv', offline))
    teams = read_csv(fetch_text(f'{BASE}/{folder}/teams.csv', cache / 'teams.csv', offline))
    merged = read_csv(fetch_text(f'{BASE}/{folder}/gws/merged_gw.csv',
                                 cache / 'merged_gw.csv', offline))
    team_short = {_i(t['id']): t['short_name'] for t in teams}
    team_by_name = {t['name'].strip(): t['short_name'] for t in teams}
    team_by_name.update({t['short_name']: t['short_name'] for t in teams})
    code_of = {_i(p['id']): _i(p['code']) for p in players_raw}
    team_of = {_i(p['id']): team_short.get(_i(p['team'])) for p in players_raw}
    pos_of = {_i(p['id']): POS.get(_i(p['element_type'])) for p in players_raw}
    rows, missing = rows_from_merged(season, merged, code_of, team_of, pos_of, team_short,
                                     team_by_name)
    cx.execute('DELETE FROM gw_stat WHERE season = ?', (season,))
    cx.executemany(GW_STAT_INSERT, rows)
    cx.commit()
    note = f' (minutes-only columns missing: {", ".join(missing)})' if missing else ''
    n_players = len({r[0] for r in rows})
    n_rounds = len({r[2] for r in rows})
    print(f'  {season}: {len(rows)} rows, {n_players} players, {n_rounds} rounds{note}')
    return rows, missing


def check_against_season_stat(cx, season):
    """Where the player is in the repository's history, the per-GW minutes
    must add up to the season aggregate. Mismatches usually mean a player who
    moved clubs mid-season (two element ids) — printed, not fatal."""
    q = ("SELECT s.code, s.minutes, IFNULL(g.m, 0) FROM season_stat s "
         "LEFT JOIN (SELECT code, SUM(minutes) AS m FROM gw_stat WHERE season = ? "
         "GROUP BY code) g ON g.code = s.code WHERE s.season = ? AND s.minutes > 0")
    bad = [(c, m, g) for c, m, g in cx.execute(q, (season, season)) if m != g]
    total = cx.execute('SELECT COUNT(*) FROM season_stat WHERE season = ? AND minutes > 0',
                       (season,)).fetchone()[0]
    print(f'    minutes agree with season_stat for {total - len(bad)}/{total} players'
          + (f'; first mismatches: {bad[:5]}' if bad else ''))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seasons', nargs='*', default=list(SEASON_DIR))
    ap.add_argument('--offline', action='store_true', help='use cached CSVs only')
    args = ap.parse_args()
    cx = sqlite3.connect(DB)
    schema(cx)
    print('Importing per-gameweek rows from vaastav/Fantasy-Premier-League')
    for season in args.seasons:
        if season not in SEASON_DIR:
            print(f'  unknown season {season}; expected one of {list(SEASON_DIR)}')
            continue
        try:
            import_season(cx, season, offline=args.offline)
        except Exception as ex:
            print(f'  {season}: import failed ({ex})')
            continue
        check_against_season_stat(cx, season)
    n = cx.execute('SELECT season, COUNT(*) FROM gw_stat GROUP BY season').fetchall()
    print('gw_stat rows by season: ' + ', '.join(f'{s} {c}' for s, c in n))
    cx.close()


if __name__ == '__main__':
    main()
