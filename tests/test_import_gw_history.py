import pytest

from v2.fetch import GW_STAT_COLUMNS
from v2.import_gw_history import rows_from_merged


def imported(season='2022/23', **changes):
    row = dict(element='1', round='15', fixture='1', team='Arsenal',
               position='MID', starts='0', minutes='90', expected_goals='0',
               expected_assists='0', expected_goals_conceded='0')
    row.update(changes)
    rows, _ = rows_from_merged(season, [row], {1: 101}, {1: 'CHE'},
                               {1: 'FWD'}, {1: 'ARS'}, {'Arsenal': 'ARS'})
    return dict(zip(GW_STAT_COLUMNS, rows[0]))


def test_placeholder_zeros_remain_unknown_not_observed_benchings_or_zero_xg():
    row = imported()
    assert row['minutes'] == 90
    assert all(row[key] is None for key in ('starts', 'xg', 'xa', 'xgc', 'defcon'))


def test_recorded_zero_is_preserved_after_measurement_begins():
    for row in (imported(round='16'), imported(season='2023/24', round='1')):
        assert row['starts'] == 0
        assert row['xg'] == 0


def test_fixture_membership_and_position_win_over_end_of_season_metadata():
    row = imported()
    assert row['team'] == 'ARS'
    assert row['pos'] == 'MID'
    with pytest.raises(ValueError, match='unknown historical club'):
        imported(team='Unmapped historical club')
