"""Minutes per start by club, season and position: the manager's hook.

The GW scorecard grades minutes MAE at ~20.3 min per player-GW — the largest
single error source in the forecast. Starts are the solved half (the recency
rule is what ships); minutes PER START are not. Hypothesis under test: some
managers hook their players early systematically (the winger who always comes
off at 65'), so a player's expected minutes per start should move toward his
manager's measured substitution tendency, not just his own history.
backtest_inseason.py --mps is the measurement; until it wins there, this
module is shadow-only and nothing in production imports it.

A "manager" is approximated by (team, season): gw_stat has no manager column.
Summer appointments per season are listed in manager_changes.py; mid-season
sackings deliberately blur a cell — half a season under each coach — and
nothing here can split them, so a cell that straddles a sacking reads as one
tendency somewhere between the two coaches. Trust a single club's cell only
as far as that approximation allows.

Cells are computed over gw_stat rows with starts=1 for each (team, season,
pos):

  mps        mean minutes per start
  hook_rate  share of starts subbed before 60' (minutes < 60; the handful of
             red-carded starts per season count as hooks — under 1% of
             starts, they do not move a 100-start cell)
  full90     share of starts that played the full 90

Each cell is shrunk toward its league (season, pos) mean with trust
n / (n + K), K = 6 starts. K sits just above player_model's
CURRENT_TRUST_K = 4 for the same reason that constant exists: a brand-new
cell is mostly league. It is higher because a cell pools several players'
starts (3-5 in DEF/MID) and earns trust faster than any single player would;
the thinnest real cells — a keeper, a lone striker — are half-trusted after
six starts, roughly a season's first month.
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / 'fpl.db'
K = 6.0                    # starts at which a cell is half-trusted (see head)
DEFAULT_MPS = 82.0         # player_model.minutes_prior's no-history default


def _cell_stats(minutes):
    """(n, mps, hook_rate, full90) over the minutes of started rows."""
    n = len(minutes)
    return (n,
            sum(minutes) / n,
            sum(1 for m in minutes if m < 60) / n,
            sum(1 for m in minutes if m >= 90) / n)


def load_from_db(seasons, db=None):
    """Manager hook table for `seasons` from gw_stat: {'cells':
    {(team, season, pos): cell}, 'league': {(season, pos): cell},
    'provenance': {...}}. Cells shrink toward their league (season, pos) mean
    with trust n/(n+K); league rows ARE the prior (raw = shrunk) and carry
    n, mps, hook_rate, full90. `db` overrides the module's fpl.db (tests)."""
    seasons = list(seasons)

    def empty(source='gw_stat rows with starts=1'):
        return dict(cells={}, league={}, provenance=dict(
            generated=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            seasons=list(seasons), start_rows=0, shrink_k=K, source=source,
            manager='(team, season) proxy; mid-season sackings blur a cell'))

    if not seasons:
        return empty()
    raw = defaultdict(list)                     # (team, season, pos) -> [mins]
    contributions = defaultdict(
        lambda: defaultdict(lambda: dict(n=0, minutes_sum=0)))
    n_rows = 0
    cx = sqlite3.connect(str(db) if db else DB)
    try:
        exists = cx.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gw_stat'"
        ).fetchone()
        if not exists:
            return empty('gw_stat unavailable; manager blend disabled')
        columns = {row[1] for row in cx.execute('PRAGMA table_info(gw_stat)')}
        required = {'team', 'season', 'pos', 'code', 'minutes', 'starts', 'round'}
        missing = required - columns
        if missing:
            return empty('gw_stat missing required columns: ' + ', '.join(sorted(missing)))
        q = ("SELECT team, season, pos, code, minutes FROM gw_stat "
             f"WHERE season IN ({','.join('?' * len(seasons))}) AND starts = 1 "
             "AND minutes IS NOT NULL AND round IS NOT NULL")
        for team, season, pos, code, minutes in cx.execute(q, seasons):
            n_rows += 1
            key = (team, season, pos or 'MID')
            raw[key].append(minutes)
            contribution = contributions[key][code]
            contribution['n'] += 1
            contribution['minutes_sum'] += minutes
    finally:
        cx.close()

    lg_raw = defaultdict(list)                  # (season, pos) -> [mins]
    for (_team, season, pos), ms in raw.items():
        lg_raw[(season, pos)].extend(ms)
    league = {}
    for key, ms in sorted(lg_raw.items()):
        n, mps, hook, full = _cell_stats(ms)
        league[key] = dict(n=n, mps=mps, hook_rate=hook, full90=full)

    cells = {}
    for key, ms in sorted(raw.items()):
        season, pos = key[1], key[2]
        lg = league[(season, pos)]
        n, mps, hook, full = _cell_stats(ms)
        trust = n / (n + K)
        cells[key] = dict(
            n=n, raw_mps=mps,
            mps=trust * mps + (1 - trust) * lg['mps'],
            hook_rate=trust * hook + (1 - trust) * lg['hook_rate'],
            full90=trust * full + (1 - trust) * lg['full90'],
            contributions=dict(contributions[key]))

    prov = dict(generated=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                seasons=list(seasons), start_rows=n_rows, shrink_k=K,
                source='gw_stat rows with starts=1',
                manager='(team, season) proxy; mid-season sackings blur a cell')
    return dict(cells=cells, league=league, provenance=prov)


def _contribution(cell, code):
    contributions = cell.get('contributions') or {}
    return contributions.get(code) or contributions.get(str(code))


def _league_mps_excluding(cells, league, season, pos, code):
    """League-position mean with every row for `code` removed."""
    lg = league.get((season, pos))
    if not lg:
        return DEFAULT_MPS
    n = lg['n']
    minutes_sum = lg['mps'] * n
    for (_team, cell_season, cell_pos), cell in cells.items():
        if cell_season != season or cell_pos != pos:
            continue
        own = _contribution(cell, code)
        if own:
            n -= own['n']
            minutes_sum -= own['minutes_sum']
    return minutes_sum / n if n else DEFAULT_MPS


def mps_expectation(table, team, pos, season=None, exclude_code=None):
    """Shrunk mean minutes per start for (team, pos[, season]). Pure — no DB.

    Unknown cell -> that (season, pos)'s league mean; unknown season -> the
    club pooled n-weighted across seasons, then the pos league mean pooled
    over seasons; an empty table -> DEFAULT_MPS.

    With ``exclude_code``, every row for the target is subtracted from both
    the club cell and its league-position shrinkage prior. This is the exact
    leave-one-player-out signal measured by `backtest_inseason.py --mps`.
    Tables created before contributions were recorded retain their estimates.
    """
    cells, league = table['cells'], table['league']
    if season is not None:
        league_mps = (_league_mps_excluding(cells, league, season, pos, exclude_code)
                      if exclude_code is not None
                      else (league.get((season, pos)) or {}).get('mps'))
        c = cells.get((team, season, pos))
        if c:
            if exclude_code is None or 'contributions' not in c:
                return c['mps']
            own = _contribution(c, exclude_code) or {'n': 0, 'minutes_sum': 0}
            n = c['n'] - own['n']
            if not n:
                return league_mps
            raw = (c['raw_mps'] * c['n'] - own['minutes_sum']) / n
            trust = n / (n + K)
            return trust * raw + (1 - trust) * league_mps
        if league_mps is not None:
            return league_mps
    own = [v for (t, _s, p), v in cells.items() if t == team and p == pos]
    if own:
        w = sum(v['n'] for v in own)
        return sum(v['mps'] * v['n'] for v in own) / w
    lg = [v for (_s, p), v in league.items() if p == pos]
    if lg:
        w = sum(v['n'] for v in lg)
        return sum(v['mps'] * v['n'] for v in lg) / w
    if cells:
        w = sum(v['n'] for v in cells.values())
        return sum(v['mps'] * v['n'] for v in cells.values()) / w
    return DEFAULT_MPS


def manager_cell(starts, exclude_code=None, before_round=None, league_mps=None,
                 fallback=None, k=K):
    """Shrunk (team, pos) mean minutes per start from `starts` — a list of
    (round, code, minutes) rows the caller selected. Drops `exclude_code`'s
    rows (the target player's own starts must not sit in his manager cell)
    and, when `before_round` is given, every row at or after it (as-of).
    Returns {'n', 'raw_mps', 'mps'}: raw over the surviving rows, shrunk
    toward `league_mps` with trust n/(n+k); empty cell -> `league_mps`, and
    if that is missing too -> `fallback`. Pure — the as-of replay in
    backtest_inseason.py builds its leak barriers out of these knobs."""
    rows = [m for rd, code, m in starts
            if code != exclude_code and (before_round is None or rd < before_round)]
    n = len(rows)
    prior = league_mps if league_mps is not None else fallback
    if not n:
        return dict(n=0, raw_mps=None, mps=prior)
    raw = sum(rows) / n
    if prior is None:
        return dict(n=n, raw_mps=raw, mps=raw)
    trust = n / (n + k)
    return dict(n=n, raw_mps=raw, mps=trust * raw + (1 - trust) * prior)


def write_json(table, path):
    """Write flat tuple keys and string contributor IDs with provenance."""
    cells = {}
    for key, value in table['cells'].items():
        encoded = dict(value)
        if 'contributions' in encoded:
            encoded['contributions'] = {
                str(code): stats for code, stats in encoded['contributions'].items()}
        cells['|'.join(key)] = encoded
    league = {'|'.join(key): value for key, value in table['league'].items()}
    Path(path).write_text(json.dumps(
        dict(provenance=table['provenance'], league=league, cells=cells),
        indent=1, sort_keys=True))


def read_json(path):
    """Load write_json() output into the tuple/int-keyed in-memory shape."""
    encoded = json.loads(Path(path).read_text())
    cells = {}
    for key, value in encoded['cells'].items():
        decoded = dict(value)
        decoded['contributions'] = {
            int(code): stats
            for code, stats in decoded.get('contributions', {}).items()}
        cells[tuple(key.split('|'))] = decoded
    league = {tuple(key.split('|')): value
              for key, value in encoded['league'].items()}
    return dict(cells=cells, league=league,
                provenance=encoded['provenance'])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='manager hook-tendency table (minutes per start) from gw_stat')
    ap.add_argument('--out', default='v2/manager_minutes.json')
    ap.add_argument('--db', default=None)
    args = ap.parse_args(argv)
    cx = sqlite3.connect(str(args.db) if args.db else DB)
    seasons = [s for (s,) in cx.execute(
        'SELECT DISTINCT season FROM gw_stat ORDER BY season')]
    cx.close()
    table = load_from_db(seasons, db=args.db)
    write_json(table, args.out)
    p = table['provenance']
    print(f"{p['start_rows']} starts over {', '.join(p['seasons'])} -> "
          f"{len(table['cells'])} cells, shrink k={K:g}")
    print(f'wrote {args.out}')
    ranked = sorted(((k, v) for k, v in table['cells'].items() if v['n'] >= 20),
                    key=lambda kv: kv[1]['mps'] - table['league'][(kv[0][1], kv[0][2])]['mps'])
    for label, sl in (('most-hooked cells (n>=20)', ranked[:3]),
                      ('most-secure cells (n>=20)', list(reversed(ranked[-3:])))):
        if not sl:
            continue
        print(label)
        for (team, season, pos), c in sl:
            lg = table['league'][(season, pos)]
            print(f"  {team:<4}{season} {pos}  mps {c['mps']:5.1f} vs league "
                  f"{lg['mps']:5.1f}, hook {c['hook_rate']:.2f}, full90 {c['full90']:.2f}, n={c['n']}")


if __name__ == '__main__':
    main()
