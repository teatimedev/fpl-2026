"""
Data ingestion for the v2 model.

Pulls three sources into one SQLite database:

  1. FPL bootstrap-static   -- current prices, positions, ownership, availability
  2. FPL element-summary    -- FOUR seasons of per-player history for all 572
                               players, with xG/xA/xGC and DefCon components,
                               plus the CURRENT season's per-round rows
                               (gw_stat, exported to data/gw_stats.csv)
  3. football-data.co.uk    -- four seasons of real match results (1,520 matches)
                               plus closing bookmaker odds, and forward fixtures
                               with market odds once they are posted

The third source is what makes a professional model possible: FPL's own fixture
difficulty is a 2-5 hand-wave, whereas real results let us fit actual team
strength, and Pinnacle's closing line is the sharpest public estimate of a
match's true probabilities. Having both means the model can be checked against
the market rather than only against itself.

Usage:  python v2/fetch.py [--refresh]
"""
import argparse
import csv
import io
import json
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'v2' / 'fpl.db'
CACHE = ROOT / 'v2' / 'cache'
FPL = 'https://fantasy.premierleague.com/api'
FD = 'https://www.football-data.co.uk/mmz4281'
SEASONS = ['2223', '2324', '2425', '2526']
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}


def get(url, binary=False, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            return raw if binary else json.loads(raw)
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f'unreachable: {url}')


def schema(cx):
    cx.executescript("""
    CREATE TABLE IF NOT EXISTS player (
        id INTEGER PRIMARY KEY, code INTEGER, web_name TEXT, full_name TEXT,
        team TEXT, team_id INTEGER, pos TEXT, price REAL, sel_pct REAL,
        status TEXT, news TEXT, chance INTEGER, joined TEXT, birth_date TEXT,
        pens INTEGER, corners INTEGER, fk INTEGER
    );
    CREATE TABLE IF NOT EXISTS season_stat (
        code INTEGER, season TEXT, team TEXT, pos TEXT,
        minutes INTEGER, starts INTEGER, points INTEGER,
        goals INTEGER, assists INTEGER, clean_sheets INTEGER,
        goals_conceded INTEGER, saves INTEGER, bonus INTEGER, bps INTEGER,
        yellow INTEGER, red INTEGER,
        xg REAL, xa REAL, xgi REAL, xgc REAL,
        cbi INTEGER, tackles INTEGER, recoveries INTEGER, defcon INTEGER,
        start_cost INTEGER, end_cost INTEGER,
        PRIMARY KEY (code, season)
    );
    CREATE TABLE IF NOT EXISTS match (
        season TEXT, date TEXT, home TEXT, away TEXT,
        hg INTEGER, ag INTEGER,
        hs INTEGER, aws INTEGER, hst INTEGER, ast INTEGER,
        hc INTEGER, ac INTEGER, hy INTEGER, ay INTEGER, hr INTEGER, ar INTEGER,
        odds_h REAL, odds_d REAL, odds_a REAL, odds_o25 REAL, odds_u25 REAL,
        PRIMARY KEY (season, home, away)
    );
    CREATE TABLE IF NOT EXISTS fixture (
        id INTEGER PRIMARY KEY, event INTEGER, kickoff TEXT,
        team_h INTEGER, team_a INTEGER, fdr_h INTEGER, fdr_a INTEGER
    );
    CREATE TABLE IF NOT EXISTS team (
        id INTEGER PRIMARY KEY, short TEXT, name TEXT, fd_name TEXT
    );
    CREATE TABLE IF NOT EXISTS market (
        date TEXT, home TEXT, away TEXT,
        odds_h REAL, odds_d REAL, odds_a REAL, odds_o25 REAL, odds_u25 REAL,
        PRIMARY KEY (date, home, away)
    );
    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
    -- One row per player per fixture: the per-round history the element-summary
    -- endpoint returns for the CURRENT season (P1). Past seasons can be imported
    -- from the public vaastav dataset (import_gw_history.py) into the same
    -- table; the season column keeps them apart.
    CREATE TABLE IF NOT EXISTS gw_stat (
        code INTEGER, season TEXT, round INTEGER, fixture_id INTEGER,
        team TEXT, pos TEXT, opponent TEXT, was_home INTEGER, kickoff TEXT,
        minutes INTEGER, starts INTEGER, points INTEGER,
        goals INTEGER, assists INTEGER, clean_sheets INTEGER,
        goals_conceded INTEGER, own_goals INTEGER,
        pens_saved INTEGER, pens_missed INTEGER,
        xg REAL, xa REAL, xgc REAL, defcon INTEGER,
        bps INTEGER, bonus INTEGER, saves INTEGER, yellow INTEGER, red INTEGER,
        threat REAL, creativity REAL, influence REAL,
        price INTEGER, selected INTEGER,
        PRIMARY KEY (code, season, fixture_id)
    );
    """)


GW_STAT_COLUMNS = (
    'code', 'season', 'round', 'fixture_id', 'team', 'pos', 'opponent', 'was_home',
    'kickoff', 'minutes', 'starts', 'points', 'goals', 'assists',
    'clean_sheets', 'goals_conceded', 'own_goals', 'pens_saved', 'pens_missed',
    'xg', 'xa', 'xgc', 'defcon', 'bps', 'bonus', 'saves', 'yellow', 'red',
    'threat', 'creativity', 'influence', 'price', 'selected',
)
GW_STAT_INSERT = ('INSERT OR REPLACE INTO gw_stat VALUES ('
                  + ','.join('?' * len(GW_STAT_COLUMNS)) + ')')
GW_STATS_CSV = ROOT / 'data' / 'gw_stats.csv'


# football-data.co.uk uses its own club names; map them to FPL short codes.
FD_TO_SHORT = {
    'Arsenal': 'ARS', 'Aston Villa': 'AVL', 'Bournemouth': 'BOU',
    'Brentford': 'BRE', 'Brighton': 'BHA', 'Burnley': 'BUR', 'Chelsea': 'CHE',
    'Coventry': 'COV', 'Crystal Palace': 'CRY', 'Everton': 'EVE',
    'Fulham': 'FUL', 'Hull': 'HUL', 'Ipswich': 'IPS', 'Leeds': 'LEE',
    'Leicester': 'LEI', 'Liverpool': 'LIV', 'Luton': 'LUT',
    'Man City': 'MCI', 'Man United': 'MUN', 'Newcastle': 'NEW',
    "Nott'm Forest": 'NFO', 'Sheffield United': 'SHU', 'Southampton': 'SOU',
    'Sunderland': 'SUN', 'Tottenham': 'TOT', 'West Ham': 'WHU', 'Wolves': 'WOL',
}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def load_fpl(cx):
    boot = get(f'{FPL}/bootstrap-static/')
    (CACHE / 'bootstrap.json').write_text(json.dumps(boot))
    teams = {t['id']: t for t in boot['teams']}
    pos = {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}

    cx.executemany('INSERT OR REPLACE INTO team VALUES (?,?,?,?)',
                   [(t['id'], t['short_name'], t['name'], None)
                    for t in boot['teams']])

    rows = []
    for p in boot['elements']:
        rows.append((
            p['id'], p['code'], p['web_name'],
            f"{p['first_name']} {p['second_name']}".strip(),
            teams[p['team']]['short_name'], p['team'], pos[p['element_type']],
            p['now_cost'] / 10, float(p['selected_by_percent']),
            p['status'], p['news'], p['chance_of_playing_next_round'],
            p['team_join_date'], p['birth_date'],
            p['penalties_order'], p['corners_and_indirect_freekicks_order'],
            p['direct_freekicks_order'],
        ))
    cx.executemany(
        'INSERT OR REPLACE INTO player VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        rows)

    fixtures = get(f'{FPL}/fixtures/')
    (CACHE / 'fixtures.json').write_text(json.dumps(fixtures))
    cx.executemany('INSERT OR REPLACE INTO fixture VALUES (?,?,?,?,?,?,?)',
                   [(x['id'], x['event'], x['kickoff_time'], x['team_h'],
                     x['team_a'], x['team_h_difficulty'], x['team_a_difficulty'])
                    for x in fixtures])
    print(f'  players {len(rows)}  teams {len(boot["teams"])}  fixtures {len(fixtures)}')
    return boot


def gw_rows_for(code, team, pos, res, team_short, season=None):
    """gw_stat rows from one element-summary response's per-round `history`.

    The endpoint returns every fixture of the current season the player's club
    has played, including the ones he did not appear in (minutes 0, starts 0)
    — that non-appearance row is the whole point of the table (P1/P2).
    `team_short` maps FPL team ids to short names; `team` and `pos` are the
    player's current club and position (history rows do not carry them).
    """
    season = season or CURRENT_SEASON
    rows = []
    for h in res.get('history', []) or []:
        rows.append((
            code, season, i(h.get('round')), i(h.get('fixture')), team, pos,
            team_short.get(h.get('opponent_team')),
            1 if h.get('was_home') else 0, h.get('kickoff_time'),
            i(h.get('minutes')) or 0, i(h.get('starts')), i(h.get('total_points')) or 0,
            i(h.get('goals_scored')) or 0, i(h.get('assists')) or 0,
            i(h.get('clean_sheets')) or 0, i(h.get('goals_conceded')) or 0,
            i(h.get('own_goals')) or 0, i(h.get('penalties_saved')) or 0,
            i(h.get('penalties_missed')) or 0,
            f(h.get('expected_goals')), f(h.get('expected_assists')),
            f(h.get('expected_goals_conceded')), i(h.get('defensive_contribution')),
            i(h.get('bps')) or 0, i(h.get('bonus')) or 0, i(h.get('saves')) or 0,
            i(h.get('yellow_cards')) or 0, i(h.get('red_cards')) or 0,
            f(h.get('threat')), f(h.get('creativity')), f(h.get('influence')),
            i(h.get('value')), i(h.get('selected')),
        ))
    return rows


def check_gw_stats(cx, boot, fixtures=None, season=None):
    """P1 validation, run on every fetch: for every player the per-round rows
    must add up to the bootstrap's running season totals (minutes, points,
    starts), and no player may have more rows than his club has played
    fixtures (fewer is legitimate: a mid-season signing's history starts at
    his first club fixture). Returns human-readable discrepancies (empty = ok).
    """
    season = season or CURRENT_SEASON
    sums = {}
    for code, mins, pts, starts, n in cx.execute(
            'SELECT code, SUM(minutes), SUM(points), SUM(starts), COUNT(*) '
            'FROM gw_stat WHERE season = ? GROUP BY code', (season,)):
        sums[code] = (mins or 0, pts or 0, starts or 0, n)
    live = any(e.get('is_current') or e.get('finished') for e in boot['events'])
    problems = []
    if not live:
        return problems
    played = {}
    for x in fixtures or []:
        if x.get('finished') and x.get('team_h_score') is not None:
            played[x['team_h']] = played.get(x['team_h'], 0) + 1
            played[x['team_a']] = played.get(x['team_a'], 0) + 1
    for p in boot['elements']:
        got = sums.get(p['code'])
        want = (p.get('minutes') or 0, p.get('total_points') or 0, p.get('starts') or 0)
        if got is None:
            if any(want):
                problems.append(f"{p['web_name']}: bootstrap has {want[0]} minutes "
                                f"but no gw_stat rows")
            continue
        if got[:3] != want:
            problems.append(f"{p['web_name']}: gw_stat sums {got[:3]} vs bootstrap "
                            f"(minutes, points, starts) {want}")
        if fixtures is not None and got[3] > played.get(p['team'], 0):
            problems.append(f"{p['web_name']}: {got[3]} gw_stat rows but his club "
                            f"has played {played.get(p['team'], 0)} fixture(s)")
    return problems


def export_gw_stats(cx, path=GW_STATS_CSV, season=None):
    """data/gw_stats.csv: the current season's per-round rows, so the app,
    retro.py and offline analysis have them without the (uncommitted) DB."""
    season = season or CURRENT_SEASON
    rows = cx.execute(
        f"SELECT {','.join(GW_STAT_COLUMNS)} FROM gw_stat WHERE season = ? "
        'ORDER BY round, fixture_id, code', (season,)).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(GW_STAT_COLUMNS)
        w.writerows(rows)
    return len(rows)


def load_histories(cx, boot):
    """Four seasons of per-player history. 572 requests, run politely in parallel.

    The same response also carries the per-round `history` array for the
    current season; it used to be discarded (W3). It now fills gw_stat — zero
    extra requests."""
    ids = [p['id'] for p in boot['elements']]
    code_pos = {p['code']: {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}[p['element_type']]
                for p in boot['elements']}
    team_short = {t['id']: t['short_name'] for t in boot['teams']}
    by_id = {p['id']: p for p in boot['elements']}

    def one(pid):
        # (pid, result|None): a None here is a silent price-prior regression
        # waiting to happen, so the pid is remembered for the retry pass below.
        try:
            return pid, get(f'{FPL}/element-summary/{pid}/')
        except Exception:
            return pid, None

    def rows_for(res):
        return [(
            h['element_code'], h['season_name'], None, code_pos.get(h['element_code']),
            h['minutes'], h.get('starts'), h['total_points'],
            h['goals_scored'], h['assists'], h['clean_sheets'],
            h['goals_conceded'], h.get('saves'), h.get('bonus'),
            h.get('bps'), h.get('yellow_cards'), h.get('red_cards'),
            f(h.get('expected_goals')), f(h.get('expected_assists')),
            f(h.get('expected_goal_involvements')),
            f(h.get('expected_goals_conceded')),
            h.get('clearances_blocks_interceptions'), h.get('tackles'),
            h.get('recoveries'), h.get('defensive_contribution'),
            h.get('start_cost'), h.get('end_cost'),
        ) for h in res.get('history_past', [])]

    def gw_rows(pid, res):
        p = by_id[pid]
        return gw_rows_for(p['code'], team_short[p['team']], code_pos.get(p['code']),
                           res, team_short)

    out, gw_out, done, failed = [], [], 0, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for pid, res in ex.map(one, ids):
            done += 1
            if done % 100 == 0:
                print(f'    {done}/{len(ids)}', flush=True)
            if not res:
                failed.append(pid)
                continue
            out.extend(rows_for(res))
            gw_out.extend(gw_rows(pid, res))

    # Sequential second pass: the parallel fan-out can trip rate limiting that
    # has usually cleared by the time the rest of the requests finish.
    if failed:
        print(f'  {len(failed)} element-summary fetch(es) failed; retrying once: {failed}')
        for pid in list(failed):
            try:
                res = get(f'{FPL}/element-summary/{pid}/')
            except Exception:
                continue
            failed.remove(pid)
            out.extend(rows_for(res))
            gw_out.extend(gw_rows(pid, res))
    if failed:
        # CI starts with an empty DB every run (see weekly.yml), so a player
        # whose history never arrives silently falls back to the price prior.
        # A red build is the honest outcome here, not a quietly worse model.
        print(f'FATAL: element-summary unavailable for {len(failed)} player(s) '
              f'after retries: {sorted(failed)}', file=sys.stderr)
        sys.exit(1)
    cx.executemany(
        'INSERT OR REPLACE INTO season_stat VALUES '
        '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', out)
    # The current season's rows are replaced wholesale: a postponed fixture
    # that is re-keyed, or a player who moved clubs mid-season, must not leave
    # a stale row behind.
    cx.execute('DELETE FROM gw_stat WHERE season = ?', (CURRENT_SEASON,))
    cx.executemany(GW_STAT_INSERT, gw_out)
    print(f'  season-stat rows {len(out)}; gw-stat rows {len(gw_out)}')
    fixtures_cache = CACHE / 'fixtures.json'
    fixtures = json.loads(fixtures_cache.read_text()) if fixtures_cache.exists() else None
    problems = check_gw_stats(cx, boot, fixtures)
    if problems:
        print(f'  WARNING: {len(problems)} player(s) whose per-round rows do not '
              f'add up to the bootstrap totals:', file=sys.stderr)
        for line in problems[:10]:
            print(f'    {line}', file=sys.stderr)
    n_csv = export_gw_stats(cx)
    print(f'  gw_stats.csv: {n_csv} rows -> {GW_STATS_CSV}')


CURRENT_SEASON = '2026/27'
CURRENT_FD = '2627'


def load_current_season(cx, boot, fixtures):
    """This season, as it happens — the rows that let the model LEARN in-season.

    Two things, both from data fetch.py already has in hand:

    1. A season_stat row per player for the current season, from the bootstrap's
       running totals (minutes, starts, xG, xA, DefCon, bonus, saves, cards...).
       The player model treats it as one more season in the panel, weighted by
       minutes played, so a player's own 2026/27 record gradually takes over
       from his history — and the minutes model reads this season's starts
       directly. Before Gameweek 1 every row is zeros and is ignored.

    2. A match row per finished fixture, from the FPL fixture list (scores are
       posted within minutes of full time). football-data's file for the new
       season lags and only appears some weeks in; when it does, its rows —
       which carry the closing odds — replace these (INSERT OR IGNORE here,
       INSERT OR REPLACE there, and football-data loads first).
    """
    teams = {t['id']: t['short_name'] for t in boot['teams']}
    pos = {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}
    # Until the first deadline passes, the API's element aggregates still hold
    # LAST season's totals (Haaland shows 2,953 minutes in August). Writing
    # those as 2026/27 would double-count a whole season. Only start once a
    # gameweek is current or finished.
    live = any(e.get('is_current') or e.get('finished') for e in boot['events'])
    if not live:
        cx.execute('DELETE FROM season_stat WHERE season = ?', (CURRENT_SEASON,))
        cx.execute('DELETE FROM match WHERE season = ?', (CURRENT_SEASON,))
        print(f'  {CURRENT_SEASON}: season not started — the API still shows last '
              f'season\'s totals, so nothing recorded yet')
        return 0
    rows = []
    for p in boot['elements']:
        rows.append((
            p['code'], CURRENT_SEASON, teams[p['team']], pos[p['element_type']],
            p.get('minutes', 0), p.get('starts'), p.get('total_points', 0),
            p.get('goals_scored', 0), p.get('assists', 0), p.get('clean_sheets', 0),
            p.get('goals_conceded', 0), p.get('saves'), p.get('bonus'), p.get('bps'),
            p.get('yellow_cards'), p.get('red_cards'),
            f(p.get('expected_goals')), f(p.get('expected_assists')),
            f(p.get('expected_goal_involvements')), f(p.get('expected_goals_conceded')),
            p.get('clearances_blocks_interceptions'), p.get('tackles'),
            p.get('recoveries'), p.get('defensive_contribution'),
            p['now_cost'] - (p.get('cost_change_start') or 0), p['now_cost'],
        ))
    cx.executemany(
        'INSERT OR REPLACE INTO season_stat VALUES '
        '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    played = sum(1 for r in rows if (r[4] or 0) > 0)

    results = []
    for x in fixtures:
        if not x.get('finished') or x.get('team_h_score') is None:
            continue
        # match.date is football-data's dd/mm/yyyy; teams_model parses that
        d = (x.get('kickoff_time') or '')[:10]
        date = f'{d[8:10]}/{d[5:7]}/{d[0:4]}' if len(d) == 10 else ''
        results.append((CURRENT_SEASON, date, teams[x['team_h']], teams[x['team_a']],
                        x['team_h_score'], x['team_a_score'],
                        None, None, None, None, None, None, None, None, None, None,
                        None, None, None, None, None))
    cx.executemany(
        'INSERT OR IGNORE INTO match VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        results)
    print(f'  {CURRENT_SEASON}: {played} players with minutes, '
          f'{len(results)} finished matches')
    return len(results)


def load_results(cx):
    total = 0
    for s in SEASONS + [CURRENT_FD]:
        try:
            raw = get(f'{FD}/{s}/E0.csv', binary=True)
        except Exception as e:
            print(f'  20{s[:2]}/{s[2:]}: football-data file unavailable ({e})')
            continue
        text = raw.decode('utf-8-sig', 'ignore')
        if 'HomeTeam' not in text[:2000]:
            # the current season's file does not exist until a few weeks in;
            # the site answers with a redirect to an HTML page
            print(f'  20{s[:2]}/{s[2:]}: no results file yet')
            continue
        (CACHE / f'E0_{s}.csv').write_bytes(raw)
        rdr = csv.DictReader(io.StringIO(text))
        season = f'20{s[:2]}/{s[2:]}'
        rows = []
        for r in rdr:
            if not r.get('HomeTeam') or r.get('FTHG') in (None, ''):
                continue
            h = FD_TO_SHORT.get(r['HomeTeam'].strip())
            a = FD_TO_SHORT.get(r['AwayTeam'].strip())
            if not h or not a:
                print(f'    unmapped club: {r["HomeTeam"]} / {r["AwayTeam"]}',
                      file=sys.stderr)
                continue
            rows.append((
                season, r['Date'], h, a, i(r['FTHG']), i(r['FTAG']),
                i(r.get('HS')), i(r.get('AS')), i(r.get('HST')), i(r.get('AST')),
                i(r.get('HC')), i(r.get('AC')), i(r.get('HY')), i(r.get('AY')),
                i(r.get('HR')), i(r.get('AR')),
                f(r.get('PSH') or r.get('AvgH') or r.get('B365H')),
                f(r.get('PSD') or r.get('AvgD') or r.get('B365D')),
                f(r.get('PSA') or r.get('AvgA') or r.get('B365A')),
                f(r.get('P>2.5') or r.get('Avg>2.5') or r.get('B365>2.5')),
                f(r.get('P<2.5') or r.get('Avg<2.5') or r.get('B365<2.5')),
            ))
        cx.executemany(
            'INSERT OR REPLACE INTO match VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            rows)
        total += len(rows)
        print(f'  {season}: {len(rows)} matches')
    return total


def _iso_date(d):
    """football-data writes dd/mm/yyyy; the-odds-api writes ISO. Store ISO so
    the same fixture from two sources lands on one row."""
    d = (d or '').strip()
    if '/' in d:
        dd, mm, yy = d.split('/')
        yy = yy if len(yy) == 4 else '20' + yy
        return f'{yy}-{int(mm):02d}-{int(dd):02d}'
    return d[:10]


def load_market(cx):
    """Forward fixtures with bookmaker odds. Empty until ~a week before a round.

    Two feeds. football-data.co.uk posts a fixtures file about a week out
    (free, no key). the-odds-api gives live prices from many books — Pinnacle
    when available — but needs a key: set ODDS_API_KEY (free tier is 500
    requests a month; this uses one per refresh). Its rows overwrite
    football-data's for the same fixture, because they are fresher.
    """
    rows = []
    try:
        raw = get('https://www.football-data.co.uk/fixtures.csv', binary=True)
        (CACHE / 'fixtures_market.csv').write_bytes(raw)
        rdr = csv.DictReader(io.StringIO(raw.decode('utf-8-sig', 'ignore')))
        for r in rdr:
            if r.get('Div') != 'E0':
                continue
            h = FD_TO_SHORT.get((r.get('HomeTeam') or '').strip())
            a = FD_TO_SHORT.get((r.get('AwayTeam') or '').strip())
            if not h or not a:
                continue
            rows.append((_iso_date(r['Date']), h, a,
                         f(r.get('PSH') or r.get('AvgH') or r.get('B365H')),
                         f(r.get('PSD') or r.get('AvgD') or r.get('B365D')),
                         f(r.get('PSA') or r.get('AvgA') or r.get('B365A')),
                         f(r.get('Avg>2.5') or r.get('B365>2.5')),
                         f(r.get('Avg<2.5') or r.get('B365<2.5'))))
    except Exception as e:
        print(f'  football-data market odds unavailable: {e}')
    n_fd = len(rows)
    n_api = 0
    api_rows = load_odds_api()
    if api_rows:
        # dedupe on the pair: the API row wins
        pairs = {(h, a) for _, h, a, *_ in api_rows}
        rows = [r for r in rows if (r[1], r[2]) not in pairs] + api_rows
        n_api = len(api_rows)
    # `market` is "current forward prices", nothing else: clear it every fetch
    # so odds for fixtures already played (or in an old date format) cannot
    # linger and be matched to next season's reverse fixture
    cx.execute('DELETE FROM market')
    cx.executemany('INSERT OR REPLACE INTO market VALUES (?,?,?,?,?,?,?,?)', rows)
    print(f'  forward market odds: {len(rows)} Premier League fixtures'
          f' ({n_fd} football-data, {n_api} the-odds-api)'
          + ('' if rows else '  (none posted yet — normal this far out)'))
    return len(rows)


# the-odds-api names clubs in full; FPL short codes are what the model uses
ODDS_API_TO_SHORT = {
    'arsenal': 'ARS', 'aston villa': 'AVL', 'bournemouth': 'BOU',
    'brentford': 'BRE', 'brighton and hove albion': 'BHA', 'brighton': 'BHA',
    'burnley': 'BUR', 'chelsea': 'CHE', 'coventry city': 'COV', 'coventry': 'COV',
    'crystal palace': 'CRY', 'everton': 'EVE', 'fulham': 'FUL', 'hull city': 'HUL',
    'hull': 'HUL', 'ipswich town': 'IPS', 'ipswich': 'IPS', 'leeds united': 'LEE',
    'leeds': 'LEE', 'leicester city': 'LEI', 'liverpool': 'LIV', 'luton town': 'LUT',
    'manchester city': 'MCI', 'manchester united': 'MUN', 'newcastle united': 'NEW',
    'newcastle': 'NEW', 'nottingham forest': 'NFO', 'sheffield united': 'SHU',
    'southampton': 'SOU', 'sunderland': 'SUN', 'tottenham hotspur': 'TOT',
    'tottenham': 'TOT', 'west ham united': 'WHU', 'west ham': 'WHU',
    'wolverhampton wanderers': 'WOL', 'wolves': 'WOL',
}


def load_odds_api():
    """Live match odds from the-odds-api.com, if ODDS_API_KEY is set.

    Uses Pinnacle's price where it is offered, else the average across books.
    Returns rows in the `market` shape, or [] (no key, no credit, no network).
    The raw response is cached for inspection.
    """
    import os
    key = os.environ.get('ODDS_API_KEY')
    if not key:
        return []
    url = ('https://api.the-odds-api.com/v4/sports/soccer_epl/odds/'
           f'?apiKey={key}&regions=uk,eu&markets=h2h,totals&oddsFormat=decimal')
    try:
        events = get(url)
    except Exception as e:
        print(f'  the-odds-api unavailable: {e}')
        return []
    (CACHE / 'odds_api.json').write_text(json.dumps(events))
    rows = []
    for ev in events:
        h = ODDS_API_TO_SHORT.get((ev.get('home_team') or '').strip().lower())
        a = ODDS_API_TO_SHORT.get((ev.get('away_team') or '').strip().lower())
        if not h or not a:
            continue
        date = _iso_date(ev.get('commence_time'))
        # collect per-book prices, prefer pinnacle
        h2h, tot = {}, {}
        for bk in ev.get('bookmakers', []):
            for mk in bk.get('markets', []):
                if mk['key'] == 'h2h':
                    o = {x['name']: x['price'] for x in mk['outcomes']}
                    if ev['home_team'] in o and ev['away_team'] in o and 'Draw' in o:
                        h2h[bk['key']] = (o[ev['home_team']], o['Draw'], o[ev['away_team']])
                elif mk['key'] == 'totals':
                    o = {(x['name'], x.get('point')): x['price'] for x in mk['outcomes']}
                    if ('Over', 2.5) in o and ('Under', 2.5) in o:
                        tot[bk['key']] = (o[('Over', 2.5)], o[('Under', 2.5)])
        if not h2h:
            continue

        def pick(d):
            if 'pinnacle' in d:
                return d['pinnacle']
            cols = list(zip(*d.values()))
            return tuple(sum(c) / len(c) for c in cols)
        oh, od, oa = pick(h2h)
        oo, ou = pick(tot) if tot else (None, None)
        rows.append((date, h, a, oh, od, oa, oo, ou))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-histories', action='store_true')
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(DB)
    schema(cx)

    print('FPL bootstrap + fixtures')
    boot = load_fpl(cx)
    cx.commit()

    if not args.skip_histories:
        print('Player histories (4 seasons, 572 players)')
        load_histories(cx, boot)
        cx.commit()

    print('Match results')
    n = load_results(cx)
    cx.commit()

    print('This season so far')
    fixtures = json.loads((CACHE / 'fixtures.json').read_text())
    load_current_season(cx, boot, fixtures)
    cx.commit()

    print('Forward market odds')
    load_market(cx)
    cx.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
               ('fetched_at', time.strftime('%Y-%m-%dT%H:%M:%S')))
    cx.commit()

    print(f'\nwrote {DB}')
    for t in ('player', 'season_stat', 'gw_stat', 'match', 'fixture', 'market'):
        print(f'  {t:<12} {cx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]:>6}')
    cx.close()


if __name__ == '__main__':
    main()
