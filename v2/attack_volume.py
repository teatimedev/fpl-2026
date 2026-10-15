"""How a fixture's expected team goals scale a player's attacking rate.

A player's xG/90 and xA/90 were measured at his club, so they already carry
that club's attacking level. project() used to multiply them by the
fixture's team xG over a league-average 1.45, which applies the club's level
a second time: Manchester City's players summed to 1.34x City's fixture xG
in the GW6 forecast of 23 Sep 2026, Hull's to 0.74x.

The 'relative' rule scales by the fixture against the club's OWN season
average instead, attenuated: (xG_f / club mean xG) ** MU. MEASURED 23 Sep
2026 (backtest_inseason.py --volume; research/model-phase3-2026-09-23.md,
item 3): per player-fixture xG given actual minutes, variants chosen on
2023/24 and judged once on 2024/25-2025/26 (22,452 player-fixtures),
Poisson deviance with level constants from 2023/24:

    current  xG_f / 1.45                 xG 0.17196   xA 0.09722
    relative (xG_f / club mean) ** 0.5   xG 0.16506   xA 0.09291
    (league-scaled (xG_f / 1.45) ** 0.25: 0.16587 / 0.09373; share-of-team
     rules that force club sums to the team total: 0.1684-0.1707)

FPL_ATTACK_VOLUME=league restores the old rule.
"""
import os

LEAGUE_XG = 1.45
RELATIVE_MU = 0.5
RULE = os.environ.get('FPL_ATTACK_VOLUME', 'relative')
if RULE not in ('relative', 'league'):
    raise SystemExit(f'FPL_ATTACK_VOLUME must be relative or league, not {RULE!r}')


def club_mean_xg(view):
    """{club: mean expected goals over every fixture in the season view}.
    The view covers the whole season (every opponent home and away), so this
    is the club's level against a balanced schedule, not the next six weeks."""
    out = {}
    for team, by_gw in (view or {}).items():
        values = [float(f['xg']) for fixtures in by_gw.values() for f in (fixtures or [])
                  if f.get('xg') is not None]
        if values:
            out[team] = sum(values) / len(values)
    return out


def attack_volume(fixture_xg, club_xg=None, rule=None):
    """The multiplier on a player's xG/90 and xA/90 for one fixture. Without a
    club level (an old snapshot row) it falls back to the league rule, which
    is what every forecast before 23 Sep 2026 used."""
    rule = rule or RULE
    fixture_xg = float(fixture_xg)
    if rule == 'league' or not club_xg:
        return fixture_xg / LEAGUE_XG
    return (fixture_xg / float(club_xg)) ** RELATIVE_MU
