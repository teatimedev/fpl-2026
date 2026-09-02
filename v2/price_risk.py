"""
Price-change risk from the accumulating snapshot log — SHADOW-ONLY.

weekly.py --price-log drops data/price_log/{date}.csv on every refresh (id,
price, sel_pct, tin, tout, dcost_event, status). Stacked up, that is the raw
material for the one thing the planner cannot see while it holds prices
frozen: that a transfer target may be +0.1m dearer by the deadline, or a
squad player's value may leak -0.1m while we sit on the free transfer.

State of the science: 5 snapshots exist (2026-08-16 .. 2026-09-02). That is
not enough to fit anything, so every coefficient below is a
community-heuristic placeholder — the ~40k net-in that moves a lightly-owned
player versus ~150k+ for a heavily-owned one, the logistic softness, the
[0.02, 0.35] clamps — NOT a measurement. Nothing here feeds the planner, the
digest or CI; treat the ordering as the signal and the probabilities as
rough. A rule not validated in backtest stays shadow-only; this one has not
been validated at all yet (see the calibration TODO).

Mechanics that shape the code:

  * tin/tout are cumulative WITHIN a gameweek event and reset at each
    deadline, so net-across-a-boundary is meaningless (a +500k GW1 net that
    becomes +30k in GW2 is not -470k of selling). Flows between snapshots are
    therefore reset-aware on two signals: any DECREASE in tin or tout
    (counters are monotone within an event; a sum comparison is not enough —
    a heavily-owned player's new-event volume can exceed the previous
    snapshot's cumulative sum, which misread João Pedro's GW2->GW3 as -78k
    of selling on 2026-09-02), or a known gameweek deadline falling between
    the two snapshot dates (Calafiori's counters grew across the 08-27 ->
    09-02 pair spanning the 28 Aug deadline, hiding the restart from the
    counters entirely). In either case the flow is the new event's net so
    far. Deadlines come from load_deadlines() — the cached bootstrap, local
    file only, never the network. Without cached deadlines the counter rule
    is only a fallback and can miss a restart once both counters surpass
    their previous values.
  * The move threshold scales with ownership — FPL needs proportionally more
    net-in to shift a template player than a differential. "Lightly/heavily
    owned" is the player's sel_pct quartile among meaningfully-owned players
    (sel >= 1%; measured on 2026-09-02, full-population quartiles are a
    useless [0.1, 0.2, 1.4] because 2/3 of the list is bench fodder, while
    the >=1% population cuts at [1.6, 3.3, 8.3]).
  * p_rise is a logistic in net/threshold — dimensionless, so one shape
    covers a 1% differential and a 30% template player.

TODO (calibration — the exact step, once >=6 weekly spans of price_log exist,
i.e. ~30 snapshots covering >=6 gameweeks with >=2 refreshes inside each):
for every (player, snapshot) pair take x = net/threshold and label y = 1 iff
price rose >= +0.1m by the next dated snapshot (both values are already in
the log: price at t, price at t+1). Fit logistic y ~ x by MLE
(scipy.optimize.minimize on log-loss, seed 0), replace BASE_K/TOP_K,
LOGIT_WIDTH and the clamps with the fitted values, and hold out the last
gameweek to check rank correlation before anything here leaves shadow mode.
"""
import csv
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PRICE_LOG = ROOT / 'data' / 'price_log'
DB = HERE / 'fpl.db'

# Heuristic placeholders — see module docstring. NOT measurements.
BASE_K = 40.0    # net-in (thousands) that should move a lightly-owned (Q0) player
TOP_K = 150.0    # ... and a heavily-owned / template (Q3) one
OWNED_MIN = 1.0  # sel_pct floor for the quartile population (see docstring)
LOGIT_WIDTH = 0.35      # logistic softness, in units of net/threshold
P_MIN, P_MAX = 0.02, 0.35   # probability clamps: humble by construction
WATCH_R, HIGH_R = 0.5, 1.0  # tier cuts on net/threshold


def load_snapshots(directory):
    """Parse every YYYY-MM-DD.csv under `directory` (str or Path).

    -> [{'date': '2026-08-27', 'rows': {id: row}}, ...] sorted by date
    (ISO stems sort chronologically), where row = dict(price m, sel %, tin,
    tout, dcost, status). Malformed rows are skipped silently, matching
    movers.load_log(); a same-day re-refresh overwrote the file, so dates
    are unique per directory.
    """
    snaps = []
    for path in sorted(Path(directory).glob('*.csv')):
        rows = {}
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    rows[int(r['id'])] = dict(
                        price=float(r['price']), sel=float(r['sel_pct']),
                        tin=int(r['tin']), tout=int(r['tout']),
                        dcost=int(r.get('dcost_event') or 0),
                        status=r.get('status', 'a'))
                except (ValueError, KeyError, TypeError):
                    continue
        snaps.append({'date': path.stem, 'rows': rows})
    return snaps

def load_deadlines(cache=None):
    """Gameweek deadline DATES ('YYYY-MM-DD', sorted) from the cached
    bootstrap — the reset signal the counters alone cannot always give.
    Local file only (never the network; gwclock's live fallback is exactly
    what this module must not do). No cache or bad JSON -> []; momentum()
    then falls back to counter decreases, which cannot detect every reset.
    """
    path = Path(cache) if cache else HERE / 'cache' / 'bootstrap.json'
    try:
        events = json.loads(path.read_text())['events']
        return sorted(e['deadline_time'][:10] for e in events
                      if e.get('deadline_time'))
    except (OSError, ValueError, KeyError, TypeError):
        return []


def momentum(snapshots, player_id, deadlines=()):
    """Price history and net-transfer trend for one player.

    -> dict:
      n              snapshots the player appears in
      prices         [(date, price)]
      price_changes  [(d0, d1, delta_m)] consecutive price deltas
      nets           [(date, net)] event-cumulative tin - tout per snapshot
      flows          [(d0, d1, net_flow)] BETWEEN snapshots, reset-aware:
                     a restart is any DECREASE in tin or tout (monotone
                     within an event) or a `deadlines` date in [d0, d1);
                     the flow is then the new event's net so far, never
                     net_now - net_prev
      price_delta    last price - first (None if n < 2)
      net_event      net at the latest snapshot (None if absent everywhere)
      flow_latest    most recent between-snapshot flow (None if n < 2)

    A player missing from every snapshot gets n=0 and empty lists — callers
    treat that as 'no evidence', never as an error.
    """
    hist = [(s['date'], s['rows'][player_id]) for s in snapshots
            if player_id in s['rows']]
    prices = [(d, r['price']) for d, r in hist]
    nets = [(d, r['tin'] - r['tout']) for d, r in hist]
    flows = []
    for (d0, a), (d1, b) in zip(hist, hist[1:]):
        if (b['tin'] < a['tin'] or b['tout'] < a['tout']
                or any(d0 <= dl < d1 for dl in deadlines)):
            flow = b['tin'] - b['tout']          # event counters restarted
        else:
            flow = (b['tin'] - a['tin']) - (b['tout'] - a['tout'])
        flows.append((d0, d1, flow))
    return dict(
        n=len(hist),
        prices=prices,
        price_changes=[(d0, d1, b - a) for (d0, a), (d1, b) in
                       zip(prices, prices[1:])],
        nets=nets,
        flows=flows,
        price_delta=prices[-1][1] - prices[0][1] if len(prices) >= 2 else None,
        net_event=nets[-1][1] if nets else None,
        flow_latest=flows[-1][2] if flows else None,
    )


def _quartile(sels, sel):
    """Quartile index 0-3 of `sel` within the owned population (sel >= 1%).

    Players under the floor (bench fodder whose sel_pct quartiles would be
    [0.1, 0.2, 1.4] — measured 2026-09-02) sit in Q0: they move on tiny
    flow, so the lightest threshold is if anything too strict for them.
    """
    owned = sorted(s for s in sels if s >= OWNED_MIN)
    if sel < OWNED_MIN or not owned:
        return 0
    cuts = np.percentile(owned, [25, 50, 75])
    return int(np.searchsorted(cuts, sel, side='right'))


def _threshold_k(q):
    """Net transfers (thousands) heuristic says moves a Q`q` player."""
    return BASE_K + (TOP_K - BASE_K) * q / 3.0


def _logistic(r):
    """P(move) as a function of net/threshold; 0.5 at r = 1."""
    return 1.0 / (1.0 + math.exp(-(r - 1.0) / LOGIT_WIDTH))


def risk(snapshots, player, deadlines=()):
    """Heuristic price-move risk for one player — SHADOW-ONLY.

    `player` is a mapping with int 'id' (FPL element id, as logged); the
    latest snapshot supplies price/ownership/flow. Rise and fall are
    symmetric: logistic in net_transfer_pressure = net/threshold, threshold
    scaled by sel_pct quartile, both probabilities clamped to [0.02, 0.35].

    -> {'p_rise': float, 'p_fall': float, 'tier': 'high'|'watch'|'low',
        'evidence': str}
    Tier cuts are on net/threshold: >=1.0 high, >=0.5 watch. A player whose
    most recent between-snapshot flow reversed against a large event net is
    downgraded one notch — FPL moves on SUSTAINED pressure — and the
    evidence says so.
    """
    pid = int(player['id'])
    if not snapshots or pid not in snapshots[-1]['rows']:
        return {'p_rise': P_MIN, 'p_fall': P_MIN, 'tier': 'low',
                'evidence': f'player {pid} not in the latest snapshot'}
    row = snapshots[-1]['rows'][pid]
    m = momentum(snapshots, pid, deadlines)
    q = _quartile([r['sel'] for r in snapshots[-1]['rows'].values()], row['sel'])
    thr = _threshold_k(q)
    net_k = (row['tin'] - row['tout']) / 1000.0
    r_rise = max(0.0, net_k) / thr
    r_fall = max(0.0, -net_k) / thr
    p_rise = min(P_MAX, max(P_MIN, _logistic(r_rise)))
    p_fall = min(P_MAX, max(P_MIN, _logistic(r_fall)))

    r_top = max(r_rise, r_fall)
    tier = 'high' if r_top >= HIGH_R else 'watch' if r_top >= WATCH_R else 'low'
    note = ''
    flow = m['flow_latest']
    if (flow is not None and flow != 0 and r_top >= WATCH_R
            and (net_k > 0) != (flow > 0)):
        if tier == 'high':
            tier = 'watch'
        note = '; latest flow reversed, downgraded'

    ev = (f"net {net_k:+,.0f}k this event (in {row['tin']/1000:,.0f}k / "
          f"out {row['tout']/1000:,.0f}k), sel {row['sel']:.1f}% = Q{q} "
          f"(~{thr:.0f}k to move), price {row['price']:.1f} over {m['n']} "
          f"snap{'s' if m['n'] != 1 else ''}")
    if m['price_delta'] is not None:
        ev += f" ({m['price_delta']:+.1f}m in window)"
    if row['dcost']:
        ev += f", already {row['dcost']:+d}x0.1m this event"
    return {'p_rise': p_rise, 'p_fall': p_fall, 'tier': tier,
            'evidence': ev + note}


def advisory(snapshots, squad_rows, target_rows, deadlines=()):
    """Plain-English hold-vs-wait notes from price risk. SHADOW-ONLY.

    Targets are checked for RISES (postponing the buy gets dearer), squad
    players for FALLS (the bank leaks while we hold; a squad player RISING
    costs us nothing, so those are silent). A target drifting to a fall gets
    the same fact with the opposite advice: no rush. Lines are emitted only
    for tier 'watch' or 'high' — [] when everything is 'low', when the log
    is empty, or when there is simply nothing worth saying.
    """
    lines = []
    if not snapshots:
        return lines
    last = snapshots[-1]['rows']

    def net_k(pid):
        row = last.get(int(pid))
        return (row['tin'] - row['tout']) / 1000.0 if row else 0.0

    for t in target_rows:
        r = risk(snapshots, t, deadlines)
        if r['tier'] == 'low':
            continue
        name = t.get('name') or f"#{t['id']}"
        if r['p_rise'] >= r['p_fall']:
            lines.append(
                f"Target {name} (net {net_k(t['id']):+,.0f}k this event) is "
                f"at rise risk; waiting past the deadline could cost +0.1m "
                f"[{r['evidence']}]")
        else:
            lines.append(
                f"Target {name} (net {net_k(t['id']):+,.0f}k this event) is "
                f"drifting towards a fall; no rush — waiting could save 0.1m "
                f"[{r['evidence']}]")
    for s in squad_rows:
        r = risk(snapshots, s, deadlines)
        if r['tier'] == 'low' or r['p_rise'] >= r['p_fall']:
            continue
        name = s.get('name') or f"#{s['id']}"
        lines.append(
            f"Squad player {name} (net {net_k(s['id']):+,.0f}k this event) "
            f"at fall risk (-0.1m hurts the bank) [{r['evidence']}]")
    return lines


def _demo_names():
    """id -> 'Web Name (TEAM)' from the player table, read-only. Empty map
    if the DB is unreachable — the demo then prints bare ids."""
    try:
        con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
        with con:
            return {i: f'{n} ({tm})' for i, n, tm in
                    con.execute('SELECT id, web_name, team FROM player')}
    except sqlite3.Error:
        return {}


def main(argv=None):
    """CLI: --demo runs on the real data/price_log and prints the current
    risk table for the top movers (ranked by risk, then by |net|)."""
    argv = sys.argv[1:] if argv is None else argv
    if '--demo' not in argv:
        print('usage: python -m v2.price_risk --demo')
        return 1
    deadlines = load_deadlines()
    snaps = load_snapshots(PRICE_LOG)
    if not snaps:
        print(f'price_risk: no snapshots under {PRICE_LOG}')
        return 1
    names = _demo_names()
    table = []
    for pid, row in snaps[-1]['rows'].items():
        r = risk(snaps, {'id': pid}, deadlines)
        table.append(dict(id=pid, name=names.get(pid, f'#{pid}'),
                          net=(row['tin'] - row['tout']) / 1000.0,
                          sel=row['sel'], price=row['price'], **r))
    table.sort(key=lambda t: (max(t['p_rise'], t['p_fall']), abs(t['net'])),
               reverse=True)
    flagged = [t for t in table if t['tier'] != 'low'] or table

    print(f'price_risk --demo: {len(snaps)} snapshots '
          f'{snaps[0]["date"]}..{snaps[-1]["date"]}, '
          f'{len(snaps[-1]["rows"])} players in the latest')
    print('SHADOW-ONLY heuristic prior — coefficients are placeholders, not '
          'measurements (v2/price_risk.py docstring has the calibration TODO)')
    print(f'{sum(t["tier"] == "high" for t in table)} high / '
          f'{sum(t["tier"] == "watch" for t in table)} watch of '
          f'{len(table)} players; top movers:')
    print(f'{"p_rise":>6} {"p_fall":>6} {"tier":>5} {"net/event":>10} '
          f'{"sel%":>5} {"price":>5}  player')
    for t in flagged[:12]:
        print(f'{t["p_rise"]:6.2f} {t["p_fall"]:6.2f} {t["tier"]:>5} '
              f'{t["net"]:+10,.0f}k {t["sel"]:5.1f} {t["price"]:5.1f}  '
              f'{t["name"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
