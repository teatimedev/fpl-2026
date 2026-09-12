"""Source-specific measurement gaps, distinct from observed zero events.

The vaastav 2022/23 merged fixture CSV contains placeholder zeros for starts
and expected statistics through GW15 (all 2,818 60+ minute rows have starts=0).
Season aggregates are a different source and must not inherit this cutoff.
"""

UNRECORDED_2022_GW_FIELDS = {
    'starts', 'expected_goals', 'expected_assists',
    'expected_goals_conceded', 'expected_goal_involvements',
}


def fixture_metric_recorded(season, gameweek, field):
    return not (season == '2022/23' and gameweek <= 15
                and field in UNRECORDED_2022_GW_FIELDS)
