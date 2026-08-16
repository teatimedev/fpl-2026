"""
How good is this thing, actually?

Every refresh before a deadline archives what the model believed
(data/history/gw{n}.json, written by weekly.py --snapshot). Once that gameweek
is finished this script fetches what really happened and grades the belief:

  - rank correlation between projected and actual points, over everyone the
    model thought might play, and over likely starters
  - calibration: mean actual points by projected decile — does a player the
    model puts at 5.0 really average 5.0?
  - captaincy: what the model's captain scored, what YOUR captain scored, and
    the best you could have picked in hindsight
  - the model's XI versus the XI you actually set (from the picks endpoint)
  - clean sheets: Brier score of the team model's clean-sheet probabilities

Results accumulate in data/scorecard.json and are shown in the app. Actuals are
cached (data/history/gw{n}_actual.json) once FPL marks the round data-checked,
so finished gameweeks are never fetched twice.

Nothing here changes the model. It exists so that by October you know whether
to trust it more or less than you do today — and so the DefCon dispersion and
shrinkage questions can be answered with real numbers.

    python v2/scorecard.py            # grade every finished, archived gameweek
"""
import json
import math
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HISTORY = ROOT / 'data' / 'history'
OUT = ROOT / 'data' / 'scorecard.json'
FPL = 'https://fantasy.premierleague.com/api'
UA = {'User-Agent': 'Mozilla/5.0'}


def api(path):
    req = urllib.request.Request(f'{FPL}/{path}', headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# ------------------------------------------------------------ statistics
def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return round(num / den, 3) if den else None


def deciles(pairs, n=10):
    """pairs = [(proj, actual)] -> [{lo, hi, proj, actual, n}] by projected decile."""
    if len(pairs) < n * 2:
        return []
    s = sorted(pairs)
    size = len(s) / n
    out = []
    for d in range(n):
        chunk = s[int(d * size):int((d + 1) * size)]
        if not chunk:
            continue
        out.append(dict(lo=round(chunk[0][0], 2), hi=round(chunk[-1][0], 2),
                        proj=round(sum(p for p, _ in chunk) / len(chunk), 2),
                        actual=round(sum(a for _, a in chunk) / len(chunk), 2),
                        n=len(chunk)))
    return out


# ---------------------------------------------------------------- actuals
def actuals_for(gw, events):
    """{player_id: (points, minutes)}, {team_short: clean_sheet_bool}, checked."""
    cache = HISTORY / f'gw{gw}_actual.json'
    if cache.exists():
        d = json.loads(cache.read_text())
        if d.get('checked'):
            return d
    ev = next((e for e in events if e['id'] == gw), None)
    if not ev or not ev.get('finished'):
        return None
    live = api(f'event/{gw}/live/')
    pts = {str(e['id']): [e['stats']['total_points'], e['stats']['minutes']]
           for e in live['elements']}
    fixtures = api(f'fixtures/?event={gw}')
    boot_teams = {t['id']: t['short_name'] for t in api('bootstrap-static/')['teams']}
    cs = {}
    for f in fixtures:
        if not f.get('finished'):
            continue
        h, a = boot_teams[f['team_h']], boot_teams[f['team_a']]
        # a team can play twice in a double gameweek; count any clean sheet
        cs[h] = cs.get(h, False) or (f['team_a_score'] == 0)
        cs[a] = cs.get(a, False) or (f['team_h_score'] == 0)
    d = dict(gw=gw, points=pts, cs=cs, checked=bool(ev.get('data_checked')),
             fetched=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    cache.write_text(json.dumps(d, separators=(',', ':')))
    return d


# ------------------------------------------------------------------ grade
def grade(snap, act):
    pts = act['points']
    name = {p['id']: p['name'] for p in snap['players']}
    rows = [(p, pts.get(str(p['id']))) for p in snap['players']]
    rows = [(p, a) for p, a in rows if a is not None]

    # everyone the model gave a non-trivial chance of playing
    pool = [(p['proj'], a[0]) for p, a in rows if p['proj'] >= 0.5 and p['status'] != 'u']
    starters = [(p['proj'], a[0]) for p, a in rows
                if p.get('start_rate', 0) >= 0.6 and p['status'] != 'u']
    played = [(p['proj'], a[0]) for p, a in rows if a[1] > 0]

    top = sorted(rows, key=lambda r: -r[0]['proj'])
    top20 = [a[0] for _, a in top[:20]]
    actual_top = sorted(rows, key=lambda r: -r[1][0])
    actual_top50 = {p['id'] for p, _ in actual_top[:50]}
    hits20 = sum(1 for p, _ in top[:20] if p['id'] in actual_top50)

    g = dict(
        gw=snap['gw'], generated=snap['generated'],
        n_pool=len(pool), n_starters=len(starters),
        spearman_pool=spearman([x for x, _ in pool], [y for _, y in pool]),
        spearman_starters=spearman([x for x, _ in starters], [y for _, y in starters]),
        spearman_played=spearman([x for x, _ in played], [y for _, y in played]),
        mae_starters=round(sum(abs(x - y) for x, y in starters) / len(starters), 2)
        if starters else None,
        bias_starters=round(sum(y - x for x, y in starters) / len(starters), 2)
        if starters else None,
        top20_mean_actual=round(sum(top20) / len(top20), 2) if top20 else None,
        top20_in_actual_top50=hits20,
        deciles=deciles(starters),
    )

    # captaincy and XI, if a squad was known at snapshot time
    squad = snap.get('squad') or []
    model, yours = snap.get('model') or {}, snap.get('yours') or {}
    if squad:
        sq_pts = {i: pts.get(str(i), [0, 0])[0] for i in squad}
        best_id = max(sq_pts, key=sq_pts.get)
        cap = {}
        if model.get('captain') in sq_pts:
            cap['model'] = dict(id=model['captain'], name=name.get(model['captain']),
                                pts=sq_pts[model['captain']])
        if yours.get('captain') in sq_pts:
            cap['yours'] = dict(id=yours['captain'], name=name.get(yours['captain']),
                                pts=sq_pts[yours['captain']])
        cap['best'] = dict(id=best_id, name=name.get(best_id), pts=sq_pts[best_id])
        g['captain'] = cap
        xi = {}
        if model.get('xi'):
            xi['model'] = sum(sq_pts.get(i, 0) for i in model['xi'])
        if yours.get('xi'):
            xi['yours'] = sum(sq_pts.get(i, 0) for i in yours['xi'])
        # hindsight-best legal XI is a fair ceiling
        pos = {p['id']: p['pos'] for p in snap['players']}
        bypos = {}
        for i in squad:
            bypos.setdefault(pos.get(i), []).append(i)
        best_xi, used = [], {}
        for ps, mn in (('GKP', 1), ('DEF', 3), ('MID', 2), ('FWD', 1)):
            for i in sorted(bypos.get(ps, []), key=lambda i: -sq_pts[i])[:mn]:
                best_xi.append(i); used[ps] = used.get(ps, 0) + 1
        mx = {'GKP': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
        for i in sorted((i for i in squad if i not in best_xi), key=lambda i: -sq_pts[i]):
            if len(best_xi) >= 11:
                break
            if used.get(pos.get(i), 0) < mx.get(pos.get(i), 0):
                best_xi.append(i); used[pos.get(i)] = used.get(pos.get(i), 0) + 1
        xi['best'] = sum(sq_pts[i] for i in best_xi)
        g['xi'] = xi

    # clean sheets
    team_cs, act_cs = snap.get('team_cs') or {}, act.get('cs') or {}
    brier, n, pred_sum, act_sum = 0.0, 0, 0.0, 0
    for t, fx in team_cs.items():
        if t not in act_cs or not fx:
            continue
        # probability of at least one clean sheet in the gameweek
        p_none = 1.0
        for f in fx:
            p_none *= (1 - f['cs'])
        p = 1 - p_none
        y = 1 if act_cs[t] else 0
        brier += (p - y) ** 2; n += 1; pred_sum += p; act_sum += y
    if n:
        g['cs'] = dict(n=n, brier=round(brier / n, 3),
                       predicted_rate=round(pred_sum / n, 3),
                       actual_rate=round(act_sum / n, 3))
    return g


def main():
    HISTORY.mkdir(parents=True, exist_ok=True)
    events = api('bootstrap-static/')['events']
    prior = json.loads(OUT.read_text()) if OUT.exists() else {'gws': []}
    done = {g['gw']: g for g in prior.get('gws', [])}
    graded = []
    for path in sorted(HISTORY.glob('gw*.json')):
        if path.name.endswith('_actual.json'):
            continue
        snap = json.loads(path.read_text())
        gw = snap['gw']
        # regrade only if we haven't, or if the earlier grade was provisional
        if gw in done and done[gw].get('checked'):
            graded.append(done[gw])
            continue
        act = actuals_for(gw, events)
        if not act:
            print(f'  GW{gw}: not finished yet')
            continue
        g = grade(snap, act)
        g['checked'] = act.get('checked', False)
        graded.append(g)
        print(f'  GW{gw}: rank corr {g["spearman_starters"]} over {g["n_starters"]} '
              f'likely starters; captain model {g.get("captain", {}).get("model", {}).get("pts", "—")}'
              f' / yours {g.get("captain", {}).get("yours", {}).get("pts", "—")}'
              f' / best {g.get("captain", {}).get("best", {}).get("pts", "—")}'
              + ('' if g['checked'] else '  (provisional, bonus not final)'))

    graded.sort(key=lambda g: g['gw'])

    def avg(key, sub=None):
        vals = []
        for g in graded:
            v = g.get(key)
            if sub and isinstance(v, dict):
                v = v.get(sub)
                v = v.get('pts') if isinstance(v, dict) else v
            if isinstance(v, (int, float)):
                vals.append(v)
        return round(sum(vals) / len(vals), 3) if vals else None

    summary = dict(
        n_gws=len(graded),
        spearman_starters=avg('spearman_starters'),
        spearman_pool=avg('spearman_pool'),
        mae_starters=avg('mae_starters'),
        bias_starters=avg('bias_starters'),
        captain_model=avg('captain', 'model'),
        captain_yours=avg('captain', 'yours'),
        captain_best=avg('captain', 'best'),
        xi_model=avg('xi', 'model'), xi_yours=avg('xi', 'yours'), xi_best=avg('xi', 'best'),
        cs_brier=avg('cs', 'brier'),
        cs_predicted_rate=avg('cs', 'predicted_rate'),
        cs_actual_rate=avg('cs', 'actual_rate'),
    )
    out = dict(generated=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
               summary=summary, gws=graded,
               notes=['Hold-out backtest before the season: rank correlation ~0.46 '
                      'on points per 90 among established players.',
                      'spearman_starters is over players the model gave a 60%+ chance '
                      'of starting; spearman_pool over everyone projected 0.5+.',
                      'Captain and XI rows need a known squad at snapshot time.'])
    OUT.write_text(json.dumps(out, indent=1))
    print(f'scorecard: {len(graded)} gameweek(s) graded -> {OUT}')


if __name__ == '__main__':
    main()
