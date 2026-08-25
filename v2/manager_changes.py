"""Which clubs started a season under a new manager.

Two consumers:

  * season_view.py shrinks this season's NEW_MANAGER clubs towards the league
    mean (and P6 lets that shrink decay with matches played);
  * backtest_inseason.py --rates (P5) and season_view.py --validate-decay (P6)
    need the same fact for PAST seasons, to split players/clubs whose context
    changed from those whose did not.

The historical table is a hand list of SUMMER appointments — the manager in
charge at the season's first match was not in charge at the end of the
previous one. Mid-season sackings are deliberately not listed: the split the
backtests need is "were the prior season's rates earned under this coach".
Promoted clubs are a context change for their players regardless and are
handled by the callers from the fixture list, so they are not repeated here.
Verify against your own records before trusting a conclusion that leans on
one club; the split is the point, not any single row.
"""

# 2026/27 (the season being modelled) — mirrors overlay.NEW_MANAGER by club
NEW_MANAGER = {'BOU', 'CHE', 'CRY', 'FUL', 'IPS', 'LIV', 'MCI', 'NEW', 'NFO', 'TOT'}

# Past seasons. Sources: the appointment dates as reported at the time.
#   2022/23  MUN ten Hag (summer 22); EVE Lampard's first full season.
#   2023/24  CHE Pochettino; TOT Postecoglou; BOU Iraola; WOL O'Neil (Aug 23);
#            EVE Dyche's first full season.
#   2024/25  LIV Slot; CHE Maresca; BHA Hurzeler; LEI Cooper; WHU Lopetegui.
#   2025/26  TOT Frank; BRE Andrews; WHU Potter's first full season.
#            (The least certain row — check it.)
NEW_MANAGER_BY_SEASON = {
    '2022/23': {'MUN', 'EVE'},
    '2023/24': {'CHE', 'TOT', 'BOU', 'WOL', 'EVE'},
    '2024/25': {'LIV', 'CHE', 'BHA', 'LEI', 'WHU'},
    '2025/26': {'TOT', 'BRE', 'WHU'},
    '2026/27': NEW_MANAGER,
}


def new_manager_clubs(season):
    """Clubs with a new manager at the start of `season` ('2024/25')."""
    return set(NEW_MANAGER_BY_SEASON.get(season, set()))
