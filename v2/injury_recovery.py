"""
How long a flagged player stays out, and what holding him costs in value.

The availability layer used to expire every undated FPL flag at the deadline:
a player marked injured with "Unknown return date" was projected fully fit
from the following gameweek. That is how GW9's advice held Saka and João
Pedro while proposing to sell a 50%-doubtful Szoboszlai.

This script measures the decay from the season's own deadline snapshots
(data/history/gw{n}.json: every player's status at the deadline) against
what each player actually did afterwards (data/gw_stats.csv minutes), and
writes the frozen curve v2/recovery.json that availability.py reads.

  availability[status][k-1]  P(plays in GW n+k | flagged at deadline n)
                             divided by the same rate for fit regulars,
                             so the model's own rotation and fresh-injury
                             decay are not counted twice. Regulars only
                             (baseline start >= 0.6) — the players the
                             planner actually weighs.
  price_drop[status][k-1]    mean price change (in £m, a loss as a positive
                             number) over 7k days for owned (>= 2%) players
                             with that status, from data/price_log.

Measured per deadline, so a long injury is observed at several deadlines; the
per-deadline rate is exactly the decision the planner faces. Dated absences
("Expected back 28 Nov", "Suspended until") keep their own exact handling and
are not driven by this curve. Holdout: fitted on early deadlines and scored
on later ones, printed below; the curve ships only if it beats the old
assumption (multiplier 1.0) there.

    python v2/injury_recovery.py           # measure, report, write
    python v2/injury_recovery.py --dry-run # report only
"""
import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / 'data' / 'history'
GW_STATS = ROOT / 'data' / 'gw_stats.csv'
PRICE_LOG = ROOT / 'data' / 'price_log'
BOOTSTRAP = ROOT / 'v2' / 'cache' / 'bootstrap.json'
OUT = ROOT / 'v2' / 'recovery.json'

STATUSES = ('i', 'd')
MAX_K = 4                 # horizons with enough evidence to measure
REGULAR = 0.6             # baseline start rate that makes a player a regular
OWNED = 2.0               # sel_pct that makes a price move matter


def load_snapshots():
    snaps = {}
    for path in sorted(HISTORY.glob('gw*.json')):
        name = path.stem
        if not name[2:].isdigit():
            continue
        snaps[int(name[2:])] = json.loads(path.read_text())
    return snaps


def load_minutes():
    played = defaultdict(int)
    last = 0
    with GW_STATS.open() as fh:
        for row in csv.DictReader(fh):
            gw = int(row['round'])
            played[(int(row['code']), gw)] += int(float(row['minutes'] or 0))
            last = max(last, gw)
    return played, last


def play_counts(snaps, codes, played, last, deadlines):
    """{status: {k: [played, n]}} over regulars at the given deadlines."""
    out = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for n in deadlines:
        for p in snaps[n]['players']:
            if (p.get('baseline_start') or 0) < REGULAR or p['id'] not in codes:
                continue
            status = p['status'] if p['status'] in STATUSES else 'a' if p['status'] == 'a' else None
            if status is None:
                continue
            for k in range(1, MAX_K + 1):
                if n + k > last:
                    break
                cell = out[status][k]
                cell[1] += 1
                cell[0] += played[(codes[p['id']], n + k)] > 0
    return out


def fit_curve(counts, status):
    """Ratio to fit regulars per horizon, made non-decreasing in k."""
    curve = []
    for k in range(1, MAX_K + 1):
        flagged, fit = counts[status].get(k), counts['a'].get(k)
        if not flagged or not flagged[1] or not fit or not fit[1]:
            curve.append(curve[-1] if curve else 1.0)
            continue
        ratio = (flagged[0] / flagged[1]) / (fit[0] / fit[1])
        curve.append(min(1.0, max(ratio, curve[-1] if curve else 0.0)))
    return [round(v, 3) for v in curve]


def brier(counts, status, curve):
    """Brier on 'plays in GW n+k' for flagged regulars: model vs old rule."""
    new = old = n = 0.0
    for k in range(1, MAX_K + 1):
        flagged, fit = counts[status].get(k), counts['a'].get(k)
        if not flagged or not fit or not fit[1]:
            continue
        base = fit[0] / fit[1]
        for p_hat, acc in ((base * curve[k - 1], 'new'), (base, 'old')):
            err = flagged[0] * (1 - p_hat) ** 2 + (flagged[1] - flagged[0]) * p_hat ** 2
            if acc == 'new':
                new += err
            else:
                old += err
        n += flagged[1]
    return (new / n, old / n, int(n)) if n else (None, None, 0)


def price_drops():
    snaps = {}
    for path in sorted(PRICE_LOG.glob('*.csv')):
        try:
            day = date.fromisoformat(path.stem[:10])
        except ValueError:
            continue
        with path.open() as fh:
            snaps[day] = {int(r['id']): r for r in csv.DictReader(fh)}
    days = sorted(snaps)
    drops = {s: [] for s in STATUSES}
    for status in STATUSES:
        for k in range(1, MAX_K + 1):
            moves = []
            for day in days:
                later = [d for d in days if 7 * k <= (d - day).days <= 7 * k + 3]
                if not later:
                    continue
                after = snaps[later[0]]
                for pid, row in snaps[day].items():
                    if row['status'] == status and float(row['sel_pct']) >= OWNED and pid in after:
                        moves.append(float(row['price']) - float(after[pid]['price']))
            drops[status].append((len(moves), sum(moves) / len(moves) if moves else None))
    curve = {}
    for status, rows in drops.items():
        values = []
        for n, mean in rows:
            value = mean if mean is not None and n >= 20 else (values[-1] if values else 0.0)
            values.append(round(max(value, values[-1] if values else 0.0, 0.0), 3))
        curve[status] = values
    return curve, drops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    snaps = load_snapshots()
    codes = {e['id']: e['code'] for e in json.loads(BOOTSTRAP.read_text())['elements']}
    played, last = load_minutes()
    deadlines = sorted(n for n in snaps if n < last)
    full = play_counts(snaps, codes, played, last, deadlines)

    half = deadlines[:max(1, len(deadlines) // 2)]
    rest = [n for n in deadlines if n not in half]
    train = play_counts(snaps, codes, played, last, half)
    test = play_counts(snaps, codes, played, last, rest)

    curves, report = {}, {}
    for status in STATUSES:
        curve = fit_curve(full, status)
        holdout_curve = fit_curve(train, status)
        new, old, n = brier(test, status, holdout_curve)
        curves[status] = curve
        report[status] = dict(
            per_k={k: f'{v[0]}/{v[1]}' for k, v in sorted(full[status].items())},
            fit_per_k={k: f'{v[0]}/{v[1]}' for k, v in sorted(full['a'].items())},
            curve=curve, holdout_curve=holdout_curve,
            holdout_brier_new=round(new, 4) if new is not None else None,
            holdout_brier_old=round(old, 4) if old is not None else None,
            holdout_n=n)
        print(f'{status}: curve {curve}  (holdout fit {holdout_curve}: Brier '
              f'{new:.4f} vs {old:.4f} with no decay, n={n})' if new is not None
              else f'{status}: curve {curve} (no holdout)')
        if new is not None and new >= old:
            raise SystemExit(f'{status}: decay curve does not beat the old rule on holdout; not writing')

    # Beyond the measured horizons the remaining gap closes at the rate the
    # measured horizons imply (clamped: long injuries must not heal at once,
    # and nothing stays out forever on an undated flag).
    tails = {}
    for status, curve in curves.items():
        first, final = 1 - curve[0], 1 - curve[-1]
        rate = (final / first) ** (1 / (len(curve) - 1)) if first > 0 and final > 0 else 0.5
        tails[status] = round(min(0.95, max(0.5, rate)), 3)

    drop_curve, drop_raw = price_drops()
    for status in STATUSES:
        print(f'{status}: price drop £m by week {drop_curve[status]}  '
              f'(n per horizon {[n for n, _ in drop_raw[status]]})')

    out = dict(
        generated=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        source=f'deadline snapshots GW{deadlines[0]}–{deadlines[-1]}, minutes through GW{last}, '
               f'price log {len(list(PRICE_LOG.glob("*.csv")))} snapshots',
        note='availability: multiplier on fitness k gameweeks after an undated FPL flag '
             '(beyond the last entry the gap closes geometrically); price_drop: expected £m '
             'lost k weeks after the deadline (held flat beyond the last entry).',
        availability=curves,
        tail_rate=tails,
        price_drop=drop_curve,
        report=report,
    )
    if args.dry_run:
        return
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n')
    print(f'wrote {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
