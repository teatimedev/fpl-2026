"""Validated, coherent bookmaker lines and their source basis.

Football-data identifies closing columns with C. Its Pinnacle feed has been
unreliable since 23 July 2025: https://www.football-data.co.uk/data.php .
Never combine individual outcomes from different bookmakers to invent a line.
"""
import math


def valid_line(values):
    try:
        result = tuple(float(v) for v in values)
    except (ValueError, TypeError):
        return None
    if not all(math.isfinite(v) and v > 1 for v in result):
        return None
    return result


def football_data_line(row, *, closing=False, totals=False):
    """Prefer the provider's market average, otherwise one complete book.

    Do not fall back from closing to opening prices in the benchmark. Exclude
    the provider's unreliable Pinnacle feed from both historical and live use.
    """
    suffixes = ('>2.5', '<2.5') if totals else ('H', 'D', 'A')
    for book in ('Avg', 'B365', 'BW', 'WH'):
        prefix = book + ('C' if closing else '')
        line = valid_line([row.get(prefix + suffix) for suffix in suffixes])
        if line is not None:
            return line, prefix
    return (None,) * len(suffixes), None
