from v2.market_odds import football_data_line, valid_line


def test_historical_benchmark_uses_closing_columns_not_opening_odds():
    row = dict(AvgH=2, AvgD=3, AvgA=4, AvgCH=1.8, AvgCD=3.5, AvgCA=4.5)
    assert football_data_line(row) == ((2., 3., 4.), 'Avg')
    assert football_data_line(row, closing=True) == ((1.8, 3.5, 4.5), 'AvgC')


def test_incomplete_lines_are_not_mixed_and_stale_pinnacle_is_not_preferred():
    row = dict(AvgH=2, AvgD=3, PSH=1.5, PSD=4, PSA=6, B365H=2.1, B365D=3.1, B365A=4.1)
    assert football_data_line(row) == ((2.1, 3.1, 4.1), 'B365')
    assert football_data_line(row, closing=True) == ((None, None, None), None)


def test_invalid_odds_never_become_probabilities():
    for line in [(1, 3, 4), (0, 3, 4), (-2, 3, 4), (float('nan'), 3, 4), (2, None, 4)]:
        assert valid_line(line) is None


def test_direct_provider_needs_future_kickoff_and_recent_quote():
    from copy import deepcopy
    from datetime import datetime, timezone
    from v2.fetch import odds_api_rows
    now = datetime(2026, 9, 12, 11, tzinfo=timezone.utc)
    event = dict(home_team='Manchester United', away_team='Manchester City',
                 commence_time='2026-09-13T15:30:00Z', bookmakers=[dict(
                     key='pinnacle', last_update='2026-09-12T10:00:00Z', markets=[dict(
                         key='h2h', outcomes=[dict(name='Manchester United', price=3),
                                               dict(name='Draw', price=3.5),
                                               dict(name='Manchester City', price=2.3)])])])
    assert len(odds_api_rows([event], now)) == 1
    for timestamp in ['2026-09-01T10:00:00Z', '2026-09-13T10:00:00Z', None]:
        changed = deepcopy(event)
        changed['bookmakers'][0]['last_update'] = timestamp
        assert odds_api_rows([changed], now) == []
    event['commence_time'] = '2026-09-11T15:30:00Z'
    assert odds_api_rows([event], now) == []
