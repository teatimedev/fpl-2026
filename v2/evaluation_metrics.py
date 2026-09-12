"""Evaluation statistics shared by retrospective and forward reports."""
import math


def average_ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2.0
        for offset in range(start, end):
            ranks[order[offset]] = rank
        start = end
    return ranks


def rank_correlation(a, b):
    """Spearman correlation with average ranks for ties; None if undefined."""
    if len(a) != len(b):
        raise ValueError('Correlation inputs must have the same length')
    if len(a) < 2:
        return None
    if any(not math.isfinite(float(x)) for x in list(a) + list(b)):
        return None
    ra, rb = average_ranks(a), average_ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    numerator = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    denominator = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return numerator / denominator if denominator else None
