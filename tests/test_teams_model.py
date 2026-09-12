from datetime import datetime
from unittest.mock import patch

import numpy as np
import pytest

from v2 import teams_model as tm


@pytest.mark.parametrize('lam,mu,rho', [(1.7, 1.2, -.05), (6., 1., -.2),
                                     (3., 3., .2), (12., 12., 0), (0., 0., -.2)])
def test_scoreline_probabilities_are_valid_and_retain_supplied_marginal_means(lam, mu, rho):
    matrix = tm.dc_matrix(lam, mu, rho)
    k = np.arange(matrix.shape[0])
    assert matrix.min() >= 0
    assert matrix.sum() == pytest.approx(1.)
    assert (matrix * k[:, None]).sum() == pytest.approx(lam, abs=1e-8)
    assert (matrix * k[None, :]).sum() == pytest.approx(mu, abs=1e-8)
    assert matrix[:, 0].sum() == pytest.approx(np.exp(-mu), abs=1e-10)
    assert matrix[0, :].sum() == pytest.approx(np.exp(-lam), abs=1e-10)


def test_broken_market_odds_cannot_produce_negative_probabilities():
    for odds in [(0, 3, 4), (-1, 3, 4), (float('nan'), 3, 4), (2, None, 4)]:
        assert tm.devig(*odds) is None


def test_unconverged_fit_is_not_published_and_future_results_cannot_enter():
    from types import SimpleNamespace
    matches = [dict(home='A', away='B', hg=1, ag=0, date=datetime(2025, 1, 1))]
    with patch.object(tm, 'minimize', return_value=SimpleNamespace(success=False, message='limit')):
        with pytest.raises(ValueError, match='converge'):
            tm.fit(matches)
    with pytest.raises(ValueError, match='reference date'):
        tm.fit(matches, ref_date=datetime(2024, 12, 31))


def test_rolling_blocks_cannot_train_on_other_results_from_the_test_day():
    rows = [dict(id=i, date=datetime(2025, 1, i // 3 + 1)) for i in range(18)]
    blocks = list(tm.date_blocks(rows, min_train=4, step=4))
    assert len(blocks[0][0]) == 6
    tested = []
    for train, test in blocks:
        assert max(r['date'] for r in train) < min(r['date'] for r in test)
        tested.extend(r['id'] for r in test)
    assert tested == list(range(6, 18))
