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

# Verified 12 September 2026 against the official appointment-date table:
# https://www.premierleague.com/en/managers
# De Zerbi (31 March 2026) and Carrick (13 January 2026) already managed the
# previous season's closing matches, so neither is a summer appointment.
CURRENT_MANAGER_APPOINTMENTS = {
    'BOU': ('Marco Rose', '2026-06-01'),
    'CHE': ('Xabi Alonso', '2026-07-01'),
    'CRY': ('Pierre Sage', '2026-06-15'),
    'FUL': ('Alvaro Arbeloa', '2026-07-07'),
    'IPS': ("Gary O'Neil", '2026-06-23'),
    'LIV': ('Andoni Iraola', '2026-06-04'),
    'MCI': ('Enzo Maresca', '2026-06-29'),
    'NEW': ('Matthias Jaissle', '2026-08-05'),
    'NFO': ('Oliver Glasner', '2026-07-06'),
}
NEW_MANAGER = set(CURRENT_MANAGER_APPOINTMENTS)

# Past seasons. Sources: the appointment dates as reported at the time.
#   2022/23  MUN ten Hag (summer 22).
#   2023/24  CHE Pochettino; TOT Postecoglou; BOU Iraola; WOL O'Neil (Aug 23);
#   2024/25  LIV Slot; CHE Maresca; BHA Hurzeler; LEI Cooper; WHU Lopetegui.
#   2025/26  TOT Frank; BRE Andrews.
# First full seasons are not summer changes: Lampard (31 Jan 2022), Dyche
# (30 Jan 2023) and Potter (9 Jan 2025) already coached the previous season.
# Official appointment records:
# https://www.premierleague.com/en/news/2468044
# https://www.premierleague.com/en/news/3039338
# https://www.premierleague.com/en/news/4219619
NEW_MANAGER_BY_SEASON = {
    '2022/23': {'MUN'},
    '2023/24': {'CHE', 'TOT', 'BOU', 'WOL'},
    '2024/25': {'LIV', 'CHE', 'BHA', 'LEI', 'WHU'},
    '2025/26': {'TOT', 'BRE'},
    '2026/27': NEW_MANAGER,
}


def new_manager_clubs(season):
    """Clubs with a new manager at the start of `season` ('2024/25')."""
    return set(NEW_MANAGER_BY_SEASON.get(season, set()))
