"""
Who is the crowd moving on — from the daily price log.

weekly.py --price-log writes data/price_log/{date}.csv every refresh: every
player's price, ownership and this gameweek's transfers in and out. Stacked up
over the season that is the transfer-movement history: who is being bought,
who is being dumped, how fast, and how price has responded.

This turns the log into data/movers.json for the app:

  per player   ownership now, change over 1 and 7 days, price now, change over
               7 days and since the start of the season, net transfers this
               gameweek, and a compact 14-day ownership sparkline
  headlines    the ten most bought and most sold by ownership swing (7 days),
               and by transfer flow this gameweek

Nothing here predicts. It shows momentum, so a rising differential or a
falling template player is visible before the price move confirms it.
"""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG = ROOT / 'data' / 'price_log'
OUT = ROOT / 'data' / 'movers.json'
DAYS_SPARK = 14


def load_log():
    days = {}
    for path in sorted(LOG.glob('*.csv')):
        day = path.stem
        rows = {}
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    rows[int(r['id'])] = dict(price=float(r['price']),
                                              sel=float(r['sel_pct']),
                                              tin=int(r['tin']), tout=int(r['tout']),
                                              status=r.get('status', 'a'))
                except (ValueError, KeyError):
                    continue
        days[day] = rows
    return days


def nearest(days, target):
    """The logged day on or before `target` (YYYY-MM-DD), else None."""
    cands = [d for d in days if d <= target]
    return max(cands) if cands else None


def main():
    days = load_log()
    if not days:
        OUT.write_text(json.dumps({'days': 0, 'players': {}, 'top': {}}))
        print('movers: no price log yet')
        return
    latest = max(days)
    now = days[latest]
    d1 = nearest(days, (datetime.fromisoformat(latest) - timedelta(days=1)).strftime('%Y-%m-%d'))
    d7 = nearest(days, (datetime.fromisoformat(latest) - timedelta(days=7)).strftime('%Y-%m-%d'))
    first = min(days)
    spark_days = sorted(days)[-DAYS_SPARK:]

    players = {}
    for pid, cur in now.items():
        p1 = days[d1].get(pid) if d1 else None
        p7 = days[d7].get(pid) if d7 else None
        p0 = days[first].get(pid)
        players[pid] = dict(
            sel=cur['sel'], price=cur['price'],
            d_sel_1=round(cur['sel'] - p1['sel'], 2) if p1 else 0.0,
            d_sel_7=round(cur['sel'] - p7['sel'], 2) if p7 else 0.0,
            d_price_7=round(cur['price'] - p7['price'], 1) if p7 else 0.0,
            d_price_season=round(cur['price'] - p0['price'], 1) if p0 else 0.0,
            net_event=cur['tin'] - cur['tout'],
            spark=[round(days[d][pid]['sel'], 1) for d in spark_days if pid in days[d]],
        )

    def top(key, n=10, reverse=True):
        rows = sorted(players.items(), key=lambda kv: kv[1][key], reverse=reverse)
        return [dict(id=pid, **{key: v[key]}) for pid, v in rows[:n] if v[key] != 0]

    out = dict(
        days=len(days), latest=latest, first=first,
        players=players,
        top=dict(bought_7d=top('d_sel_7'), sold_7d=top('d_sel_7', reverse=False),
                 bought_event=top('net_event'), sold_event=top('net_event', reverse=False),
                 risen_7d=top('d_price_7'), fallen_7d=top('d_price_7', reverse=False)),
    )
    OUT.write_text(json.dumps(out, separators=(',', ':')))
    print(f'movers: {len(days)} day(s) of log, {len(players)} players -> {OUT}')


if __name__ == '__main__':
    main()
