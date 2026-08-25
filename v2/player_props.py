"""
Anytime-goalscorer odds as a market view of a PLAYER (P8.2) — shadow only.

The fixture odds already blended into the team layer price a match; this is
the only way to get a market estimate for a player, and it folds in exactly
the role / penalty / line-up news the model lacks. Nothing here changes a
projection: the market P(goal) and the model's own P(goal) are archived side
by side in the snapshot (weekly.py --snapshot) and scorecard.py reports the
log-loss of each on "scored at least once". Blend into the projection only
once a month of graded deadlines says the market wins.

Source: the-odds-api.com player props (`player_goal_scorer_anytime` on
`soccer_epl`), ~10 requests per gameweek. Needs ODDS_API_KEY and, because
props may not be included in every plan, ODDS_API_PLAYER_PROPS=1 to opt in.

    python v2/player_props.py [--gw N]      # fetch and archive gw{N}_props.json
    from player_props import load_props_for_snapshot   # used by weekly.py
"""
import argparse
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
HISTORY = ROOT / 'data' / 'history'
CACHE = HERE / 'cache'
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
ODDS_API = 'https://api.the-odds-api.com/v4/sports/soccer_epl'
# a single-outcome "Yes" price with no "No" side carries the book's margin;
# ~8% is typical for anytime-scorer props (approximation, documented)
SINGLE_SIDE_MARGIN = 0.92


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _norm(s):
    s = (s or '').casefold()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s.replace('-', ' '))
    return re.sub(r'\s+', ' ', s).strip()


def devig_yes(yes, no=None):
    """P(goal) from a decimal 'Yes' price and, when the book offers it, the
    'No' price (a proper two-way de-vig); otherwise a fixed margin."""
    if not yes or yes <= 1.0:
        return None
    py = 1.0 / yes
    if no and no > 1.0:
        pn = 1.0 / no
        return py / (py + pn)
    return min(0.95, py * SINGLE_SIDE_MARGIN)


def model_goal_probability(player, gw, view):
    """1 - exp(-sum over the gameweek's fixtures of xg90 * E[min]/90 * vol),
    with the projection's own shrunk, age- and overlay-adjusted xg90, its
    expected minutes for the gameweek and the fixture volume."""
    xg90 = float(player.get('xg90') or 0.0)
    mins = player.get('mins_by_gw') or []
    if not (0 <= gw - 1 < len(mins)):
        return None
    minutes = float(mins[gw - 1] or 0.0)
    fixtures = (view.get(player['team']) or {}).get(str(gw)) or []
    if not fixtures or minutes <= 0 or xg90 <= 0:
        return 0.0
    lam = sum(xg90 * minutes / 90.0 * (float(f['xg']) / 1.45) for f in fixtures)
    return round(1.0 - math.exp(-lam), 4)


def match_players(outcomes, candidates):
    """Map the book's player descriptions to FPL ids among `candidates`
    (players of the two clubs): exact normalised full/web name first, then a
    unique surname. Unmatched names are returned for the log."""
    by_full = {}
    by_surname = {}
    for p in candidates:
        for key in (_norm(p.get('full_name')), _norm(p.get('name'))):
            if key:
                by_full.setdefault(key, p['id'])
        sur = _norm(p.get('full_name')).split(' ')[-1] if p.get('full_name') else ''
        if sur:
            by_surname.setdefault(sur, []).append(p['id'])
    out, unmatched = {}, []
    for desc, prices in outcomes.items():
        key = _norm(desc)
        pid = by_full.get(key)
        if pid is None:
            sur = key.split(' ')[-1] if key else ''
            ids = by_surname.get(sur, [])
            pid = ids[0] if len(ids) == 1 else None
        if pid is None:
            unmatched.append(desc)
            continue
        out[pid] = prices
    return out, unmatched


def fetch_market(gw, players, boot, fixtures, key):
    """{player id: dict(p_goal_market, odds_yes, odds_no, books)} for the
    gameweek's fixtures, from the-odds-api. Returns ({}, note) on any
    failure — this is a shadow signal and must never sink a run."""
    from fetch import ODDS_API_TO_SHORT
    team_short = {t['id']: t['short_name'] for t in boot['teams']}
    pairs = {(team_short[x['team_h']], team_short[x['team_a']]) for x in fixtures
             if x.get('event') == gw}
    if not pairs:
        return {}, 'no fixtures for this gameweek'
    try:
        events = _get(f'{ODDS_API}/events?apiKey={key}')
    except Exception as ex:
        return {}, f'events unavailable: {ex}'
    by_team = {}
    for p in players.values():
        by_team.setdefault(p['team'], []).append(p)
    out, notes = {}, []
    for ev in events:
        h = ODDS_API_TO_SHORT.get((ev.get('home_team') or '').strip().lower())
        a = ODDS_API_TO_SHORT.get((ev.get('away_team') or '').strip().lower())
        if (h, a) not in pairs:
            continue
        try:
            odds = _get(f'{ODDS_API}/events/{ev["id"]}/odds?apiKey={key}&regions=uk,eu'
                        f'&markets=player_goal_scorer_anytime&oddsFormat=decimal')
        except Exception as ex:
            notes.append(f'{h}-{a}: {ex}')
            continue
        (CACHE / 'props').mkdir(parents=True, exist_ok=True)
        (CACHE / 'props' / f'gw{gw}_{h}_{a}.json').write_text(json.dumps(odds))
        per_player = {}
        for bk in odds.get('bookmakers', []):
            for mk in bk.get('markets', []):
                if mk.get('key') != 'player_goal_scorer_anytime':
                    continue
                for o in mk.get('outcomes', []):
                    desc = o.get('description') or o.get('name')
                    side = (o.get('name') or 'Yes').lower()
                    slot = per_player.setdefault(desc, {})
                    slot.setdefault(bk['key'], {})[side] = o.get('price')
        outcomes = {}
        for desc, books in per_player.items():
            probs = [devig_yes(b.get('yes'), b.get('no')) for b in books.values()]
            probs = [p for p in probs if p is not None]
            if not probs:
                continue
            pin = books.get('pinnacle')
            p = devig_yes(pin.get('yes'), pin.get('no')) if pin else None
            if p is None:
                probs.sort()
                p = probs[len(probs) // 2]
            outcomes[desc] = dict(p_goal_market=round(p, 4), books=len(probs),
                                  odds_yes=(pin or next(iter(books.values()))).get('yes'))
        matched, unmatched = match_players(outcomes, by_team.get(h, []) + by_team.get(a, []))
        out.update(matched)
        if unmatched:
            notes.append(f'{h}-{a}: unmatched {", ".join(unmatched[:5])}')
    return out, '; '.join(notes) if notes else f'{len(out)} players priced'


def build(gw, players, view, market):
    props = {}
    for pid, p in players.items():
        pm = model_goal_probability(p, gw, view)
        if pm is None:
            continue
        row = dict(p_goal_model=pm)
        if pid in market:
            row.update(market[pid])
        props[pid] = row
    return props


def props_path(gw):
    return HISTORY / f'gw{gw}_props.json'


def load_props_for_snapshot(gw, players):
    """{player id: {p_goal_model, p_goal_market?}} for weekly.snapshot().
    Model probabilities are always computed (no network); market ones come
    from an archived gw{n}_props.json, or a fetch when opted in."""
    view_path = HERE / 'season_view.json'
    view = json.loads(view_path.read_text())['view'] if view_path.exists() else {}
    market = {}
    path = props_path(gw)
    if path.exists():
        try:
            d = json.loads(path.read_text())
            market = {int(k): v for k, v in (d.get('market') or {}).items()}
        except (OSError, ValueError):
            market = {}
    key = os.environ.get('ODDS_API_KEY')
    if not market and key and os.environ.get('ODDS_API_PLAYER_PROPS') == '1':
        boot_path, fx_path = CACHE / 'bootstrap.json', CACHE / 'fixtures.json'
        if boot_path.exists() and fx_path.exists():
            boot = json.loads(boot_path.read_text())
            fixtures = json.loads(fx_path.read_text())
            market, note = fetch_market(gw, players, boot, fixtures, key)
            HISTORY.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(
                gw=gw, fetched=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                note=note, market={str(k): v for k, v in market.items()}),
                separators=(',', ':')))
            print(f'  player goal props: {note}')
    return build(gw, players, view, market)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gw', type=int)
    args = ap.parse_args()
    from gwclock import next_gw
    gw = args.gw or next_gw()[0]
    proj = json.loads((HERE / 'projections_v2.json').read_text())
    players = {p['id']: p for p in proj['players']}
    props = load_props_for_snapshot(gw, players)
    both = [v for v in props.values() if v.get('p_goal_market') is not None]
    print(f'GW{gw}: model P(goal) for {len(props)} players, market for {len(both)}')
    for pid, v in sorted(props.items(), key=lambda kv: -kv[1]['p_goal_model'])[:10]:
        print(f"  {players[pid]['name']:<16} model {v['p_goal_model']:.2f}"
              + (f"  market {v['p_goal_market']:.2f}" if v.get('p_goal_market') is not None else ''))


if __name__ == '__main__':
    main()
