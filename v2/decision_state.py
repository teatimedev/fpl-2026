"""Reproducible input identity and public purchase-price reconstruction."""
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

DECISION_VERSION = json.loads(Path(__file__).with_name('decision_version.json').read_text())['version']

def forecast_id(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:20]


def selling_value(buy_tenths, market_tenths):
    """Use integer tenths: half of a price gain, all of a loss."""
    return min(market_tenths, buy_tenths + max(0, market_tenths - buy_tenths) // 2)


def public_selling_prices(ids, elements, transfers, history, db_path):
    """Latest permanent acquisition, using official transfer costs or GW1 value.

    GW1 prices are deadline prices, not authenticated purchase prices. Report
    that assumption. Ignore temporary Free Hit acquisitions. Missing histories
    produce unknown prices rather than spending unverified market profits.
    """
    freehits = {c['event'] for c in history.get('chips', []) if c['name'] == 'freehit'}
    acquisitions = {}
    for t in sorted(transfers, key=lambda r: r['time']):
        if t['event'] in freehits:
            continue
        acquisitions.pop(t['element_out'], None)
        acquisitions[t['element_in']] = t['element_in_cost']
    cx = sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro', uri=True)
    result, unknown = {}, []
    for pid in ids:
        e = elements.get(pid)
        if e is None:
            unknown.append(pid)
            continue
        buy = acquisitions.get(pid)
        if buy is None:
            row = cx.execute('SELECT price FROM gw_stat WHERE code=? AND season=? '
                             'AND round=1 AND price IS NOT NULL ORDER BY kickoff LIMIT 1',
                             (e['code'], '2026/27')).fetchone()
            buy = row[0] if row else None
        if buy is None:
            unknown.append(pid)
        else:
            result[pid] = selling_value(int(buy), int(e['now_cost'])) / 10
    cx.close()
    return result, unknown


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False)
    # Each writer owns its temporary file. A fixed .tmp name allows concurrent
    # writers to replace or delete one another's in-progress output.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
            prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
        tmp = Path(handle.name)
        try:
            handle.write(encoded)
            handle.flush()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
