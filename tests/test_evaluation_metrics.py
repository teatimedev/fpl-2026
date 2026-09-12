import math
import pytest
from v2.evaluation_metrics import average_ranks, rank_correlation


def test_ties_use_average_ranks_and_do_not_depend_on_input_order():
    assert average_ranks([2, 1, 2, 1]) == [2.5, .5, 2.5, .5]
    a, b = [1, 1, 2, 2], [0, 1, 1, 2]
    expected = 1 / math.sqrt(2)
    assert rank_correlation(a, b) == pytest.approx(expected)
    order = [3, 1, 0, 2]
    assert rank_correlation([a[i] for i in order], [b[i] for i in order]) == pytest.approx(expected)


def test_constant_forecasts_have_no_rank_correlation():
    assert rank_correlation([1, 1, 1], [0, 2, 3]) is None
    assert rank_correlation([float('nan'), 1], [0, 1]) is None
    with pytest.raises(ValueError):
        rank_correlation([1], [1, 2])
