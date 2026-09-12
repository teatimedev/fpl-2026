from datetime import datetime, timezone

import pytest

from v2.playing_time import estimate, scoring_probabilities
from v2.playing_time_benchmark import forecast_cases


PRIOR = dict(p_cameo=.2, p60_start=.9, p60_cameo=.05,
             start_minutes=80., cameo_minutes=25.)


def test_repeated_cameos_change_cameo_probability_without_inventing_starts():
    model = estimate(PRIOR, [(i, 0, 25) for i in range(4)], 4, 6)
    assert model['p_cameo'] > PRIOR['p_cameo']
    assert model['p60_start'] == PRIOR['p60_start']
    mixture = scoring_probabilities(model, .5)
    assert 0 <= mixture['p60'] <= mixture['p_play'] <= 1
    assert 0 <= mixture['minutes'] <= 90 * mixture['p_play']


def test_unknown_starts_do_not_become_benchings():
    assert estimate(PRIOR, [(0, None, 90)]) == estimate(PRIOR)


def test_same_week_matches_and_late_previous_rounds_cannot_leak_into_forecast():
    def row(gw, day, fixture, minutes):
        return dict(code=1, season='2023/24', round=gw, fixture_id=fixture,
                    time=datetime(2023, 9, day, 15, tzinfo=timezone.utc),
                    starts=1, minutes=minutes, pos='MID')
    rows = [row(1, 1, 1, 90), row(2, 8, 2, 80), row(1, 9, 3, 70), row(2, 10, 4, 60)]
    targets = [c for c in forecast_cases(rows, '2023/24') if c[0]['round'] == 2]
    assert len(targets) == 2
    assert targets[0][1] == targets[1][1] == [(0, 1, 90)]


def test_invalid_start_probability_is_rejected():
    with pytest.raises(ValueError):
        scoring_probabilities(PRIOR, float('nan'))
