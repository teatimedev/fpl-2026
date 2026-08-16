"""
Data ingestion for the v2 model.

Pulls three sources into one SQLite database:

  1. FPL bootstrap-static   -- current prices, positions, ownership, availability
  2. FPL element-summary    -- FOUR seasons of per-player history for all 572
                               players, with xG/xA/xGC and DefCon components
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
    """)


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


def load_histories(cx, boot):
    """Four seasons of per-player history. 572 requests, run politely in parallel."""
    ids = [p['id'] for p in boot['elements']]
    code_pos = {p['code']: {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}[p['element_type']]
                for p in boot['elements']}

    def one(pid):
        try:
            return get(f'{FPL}/element-summary/{pid}/')
        except Exception:
            return None

    out, done = [], 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(one, ids):
            done += 1
            if done % 100 == 0:
                print(f'    {done}/{len(ids)}', flush=True)
            if not res:
                continue
            for h in res.get('history_past', []):
                code = h['element_code']
                out.append((
                    code, h['season_name'], None, code_pos.get(code),
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
                ))
    cx.executemany(
        'INSERT OR REPLACE INTO season_stat VALUES '
        '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', out)
    print(f'  season-stat rows {len(out)}')


def load_results(cx):
    total = 0
    for s in SEASONS:
        raw = get(f'{FD}/{s}/E0.csv', binary=True)
        (CACHE / f'E0_{s}.csv').write_bytes(raw)
        rdr = csv.DictReader(io.StringIO(raw.decode('utf-8-sig', 'ignore')))
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

    print('Forward market odds')
    load_market(cx)
    cx.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
               ('fetched_at', time.strftime('%Y-%m-%dT%H:%M:%S')))
    cx.commit()

    print(f'\nwrote {DB}')
    for t in ('player', 'season_stat', 'match', 'fixture', 'market'):
        print(f'  {t:<12} {cx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]:>6}')
    cx.close()


if __name__ == '__main__':
    main()
