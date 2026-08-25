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
  - availability: start / appearance Brier and minutes error, by source tier,
    and the two in-season minutes rules side by side (P2: p_start_recency vs
    p_start_aggregate, both archived in the snapshot)
  - level: sum(actual) / sum(proj) per position over likely starters, per
    gameweek and cumulative — the drift measure the frozen calibration (P4)
    makes meaningful, and the input to the deferred feedback loop (P7)
  - FPL's own ep_next, graded next to the model's projection (P7): the
    in-season equivalent of the naive_price benchmark
  - retro_class: next-week start rate and residual grouped by the PREVIOUS
    week's retrospective class (P3's forward validation of its own wording)
  - goal probabilities: log-loss of the model's P(goal) against the market's
    anytime-scorer price, when player_props.py has archived one (P8.2)

Results accumulate in data/scorecard.json and are shown in the app. Actuals
are cached (data/history/gw{n}_actual.json) once FPL marks the round
data-checked, so finished gameweeks are never fetched twice. The cache keeps
the per-stat points breakdown (`explain`) and the raw stats so retro.py's
actual side is exact.

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
POSITIONS = ('GKP', 'DEF', 'MID', 'FWD')
STAT_FIELDS = ('minutes', 'starts', 'goals_scored', 'assists', 'clean_sheets',
               'goals_conceded', 'own_goals', 'penalties_saved',
               'penalties_missed', 'yellow_cards', 'red_cards', 'saves',
               'bonus', 'bps', 'defensive_contribution', 'expected_goals',
               'expected_assists', 'expected_goal_involvements',
               'expected_goals_conceded', 'total_points')


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


def brier(pairs):
    """pairs = [(probability, outcome 0/1)]"""
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 3) if pairs else None


def logloss(pairs, eps=1e-6):
    if not pairs:
        return None
    tot = 0.0
    for p, y in pairs:
        p = min(1 - eps, max(eps, p))
        tot -= math.log(p) if y else math.log(1 - p)
    return round(tot / len(pairs), 4)


def started_outcome(actual):
    """Boolean start target; FPL reports a count in double gameweeks."""
    if len(actual) > 2 and actual[2] is not None:
        return int(actual[2] > 0)
    return int(actual[1] >= 60)


# ---------------------------------------------------------------- actuals
def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def actuals_for(gw, events):
    """{player_id: (points, minutes, starts)}, per-stat breakdown, raw stats,
    {team_short: clean_sheet_bool}, checked."""
    cache = HISTORY / f'gw{gw}_actual.json'
    if cache.exists():
        d = json.loads(cache.read_text())
        # older caches predate the stat/explain fields the retro needs
        if d.get('checked') and 'stats' in d:
            return d
    ev = next((e for e in events if e['id'] == gw), None)
    if not ev or not ev.get('finished'):
        return None
    live = api(f'event/{gw}/live/')
    pts, stats, explain = {}, {}, {}
    for e in live['elements']:
        s = e['stats']
        pts[str(e['id'])] = [s['total_points'], s['minutes'], s.get('starts')]
        row = {}
        for k in STAT_FIELDS:
            v = s.get(k)
            row[k] = _float(v) if k.startswith('expected') else v
        stats[str(e['id'])] = row
        explain[str(e['id'])] = [
            dict(fixture=x.get('fixture'),
                 stats=[dict(identifier=st.get('identifier'), points=st.get('points'),
                             value=st.get('value')) for st in x.get('stats', [])])
            for x in e.get('explain', [])]
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
    d = dict(gw=gw, points=pts, stats=stats, explain=explain, cs=cs,
             checked=bool(ev.get('data_checked')),
             fetched=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    cache.write_text(json.dumps(d, separators=(',', ':')))
    return d


def load_retro(gw):
    """The retrospective written for gameweek `gw` (retro.py), or None."""
    path = HISTORY / f'gw{gw}_retro.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ grade
def grade(snap, act, prev_retro=None):
    pts = act['points']
    stats = act.get('stats') or {}
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

    # P7: the level, per position, over likely starters. sum(actual)/sum(proj)
    # rather than a mean of ratios, so blanks do not divide by zero. The raw
    # sums are kept so the summary can accumulate across gameweeks.
    level_sums = {}
    for p, a in rows:
        if p.get('start_rate', 0) >= 0.6 and p['status'] != 'u':
            for key in (p['pos'], 'ALL'):
                s = level_sums.setdefault(key, [0.0, 0.0, 0])
                s[0] += a[0]
                s[1] += p['proj']
                s[2] += 1
    g['level_sums'] = {k: [round(v[0], 2), round(v[1], 3), v[2]] for k, v in level_sums.items()}
    g['level_ratio'] = {k: (round(v[0] / v[1], 3) if v[1] > 0 else None)
                        for k, v in level_sums.items()}

    # P7: FPL's own projection as the benchmark to beat
    ep_pool = [(p['ep_next'], a[0]) for p, a in rows
               if p.get('ep_next') is not None and p['proj'] >= 0.5 and p['status'] != 'u']
    ep_starters = [(p['ep_next'], a[0]) for p, a in rows
                   if p.get('ep_next') is not None and p.get('start_rate', 0) >= 0.6
                   and p['status'] != 'u']
    if ep_pool:
        g['ep_next'] = dict(
            n=len(ep_pool),
            spearman_pool=spearman([x for x, _ in ep_pool], [y for _, y in ep_pool]),
            spearman_starters=spearman([x for x, _ in ep_starters],
                                       [y for _, y in ep_starters]),
            mae_starters=(round(sum(abs(x - y) for x, y in ep_starters) / len(ep_starters), 2)
                          if ep_starters else None),
        )

    # Deadline availability calibration. Older snapshots did not archive these
    # three fields, so they remain gradeable for points but are skipped here.
    availability = [(p, a) for p, a in rows
                    if p.get('p_start') is not None and p.get('p_play') is not None
                    and p.get('expected_minutes') is not None]
    if availability:
        start_sq = play_sq = minute_abs = minute_bias = 0.0
        for player, actual in availability:
            minutes = actual[1]
            started = started_outcome(actual)
            appeared = int(minutes > 0)
            start_sq += (player['p_start'] - started) ** 2
            play_sq += (player['p_play'] - appeared) ** 2
            minute_abs += abs(player['expected_minutes'] - minutes)
            minute_bias += minutes - player['expected_minutes']
        n_avail = len(availability)
        g['availability'] = dict(
            n=n_avail,
            start_brier=round(start_sq / n_avail, 3),
            appearance_brier=round(play_sq / n_avail, 3),
            minutes_mae=round(minute_abs / n_avail, 2),
            minutes_bias=round(minute_bias / n_avail, 2),
        )
        baseline = [(player, actual) for player, actual in availability
                    if player.get('baseline_start') is not None]
        if baseline:
            baseline_brier = sum(
                (player['baseline_start'] - started_outcome(actual)) ** 2
                for player, actual in baseline
            ) / len(baseline)
            g['availability']['baseline_start_brier'] = round(baseline_brier, 3)
            g['availability']['start_brier_lift'] = round(
                baseline_brier - g['availability']['start_brier'], 3)
        # P2: the two in-season minutes rules, graded on identical rows
        both = [(player, actual) for player, actual in availability
                if player.get('p_start_recency') is not None
                and player.get('p_start_aggregate') is not None]
        if both:
            rec = brier([(pl['p_start_recency'], started_outcome(ac)) for pl, ac in both])
            agg = brier([(pl['p_start_aggregate'], started_outcome(ac)) for pl, ac in both])
            g['availability']['minutes_rule'] = both[0][0].get('minutes_rule')
            g['availability']['n_rules'] = len(both)
            g['availability']['recency_start_brier'] = rec
            g['availability']['aggregate_start_brier'] = agg
            g['availability']['recency_vs_aggregate_lift'] = round(agg - rec, 4)
            # and over the likely starters only, where a lost place costs most
            reg = [(pl, ac) for pl, ac in both if pl.get('baseline_start', 0) >= 0.6]
            if reg:
                g['availability']['recency_start_brier_regulars'] = brier(
                    [(pl['p_start_recency'], started_outcome(ac)) for pl, ac in reg])
                g['availability']['aggregate_start_brier_regulars'] = brier(
                    [(pl['p_start_aggregate'], started_outcome(ac)) for pl, ac in reg])

        groups = {}
        dimensions = {
            'confidence': lambda player: player.get('availability_confidence') or 'unknown',
            'source_tier': lambda player: (
                'baseline' if player.get('availability_source') == 'model baseline' else
                'official_fpl' if str(player.get('availability_source', '')).startswith('FPL ') else
                'deadline_override'
            ),
            'claim_type': lambda player: player.get('generation_rule') or 'baseline',
        }
        for dimension, classify in dimensions.items():
            grouped = {}
            for player, actual in availability:
                grouped.setdefault(classify(player), []).append((player, actual))
            groups[dimension] = {}
            for label, group in grouped.items():
                sq = 0.0
                for player, actual in group:
                    started = started_outcome(actual)
                    sq += (player['p_start'] - started) ** 2
                groups[dimension][label] = {'n': len(group), 'start_brier': round(sq / len(group), 3)}
        g['availability_groups'] = groups

    # P3's forward validation: what happened THIS week to players the previous
    # week's retrospective put in each class. After minutes_loss, what fraction
    # started? After variance, is the residual ~0? After a haul on low xG, is
    # the residual ~0 (the empirical basis for "do not chase")?
    if prev_retro and prev_retro.get('players'):
        by_class = {}
        by_note = {}
        for r in prev_retro['players']:
            entry = next(((p, a) for p, a in rows if p['id'] == r['id']), None)
            if entry is None:
                continue
            p, a = entry
            resid = a[0] - p['proj']
            started = started_outcome(a)
            cls = r.get('class') or 'unknown'
            if r.get('subtype'):
                cls = f"{cls}/{r['subtype']}"
            by_class.setdefault(cls, []).append((started, resid, a[1] > 0))
            for tag in r.get('tags') or []:
                by_note.setdefault(tag, []).append((started, resid, a[1] > 0))

        def summarise(rows_):
            n = len(rows_)
            return dict(n=n,
                        next_start_rate=round(sum(s for s, _, _ in rows_) / n, 3),
                        next_play_rate=round(sum(1 for _, _, pl in rows_ if pl) / n, 3),
                        next_residual_mean=round(sum(r for _, r, _ in rows_) / n, 2))
        g['retro_class'] = {cls: summarise(v) for cls, v in by_class.items()}
        if by_note:
            g['retro_tags'] = {tag: summarise(v) for tag, v in by_note.items()}
        g['retro_gw'] = prev_retro.get('gw')

    # P8.2: goal probabilities, model vs market, shadow only
    goal_rows = [(p, stats.get(str(p['id']))) for p, _ in rows
                 if p.get('p_goal_model') is not None and p.get('p_goal_market') is not None]
    goal_rows = [(p, s) for p, s in goal_rows if s and s.get('goals_scored') is not None]
    if goal_rows:
        y = [(int((s['goals_scored'] or 0) > 0)) for _, s in goal_rows]
        g['goals'] = dict(
            n=len(goal_rows),
            logloss_model=logloss([(p['p_goal_model'], yy) for (p, _), yy in zip(goal_rows, y)]),
            logloss_market=logloss([(p['p_goal_market'], yy) for (p, _), yy in zip(goal_rows, y)]),
            brier_model=brier([(p['p_goal_model'], yy) for (p, _), yy in zip(goal_rows, y)]),
            brier_market=brier([(p['p_goal_market'], yy) for (p, _), yy in zip(goal_rows, y)]),
            actual_rate=round(sum(y) / len(y), 3),
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
    brier_cs, n, pred_sum, act_sum = 0.0, 0, 0.0, 0
    for t, fx in team_cs.items():
        if t not in act_cs or not fx:
            continue
        # probability of at least one clean sheet in the gameweek
        p_none = 1.0
        for f in fx:
            p_none *= (1 - f['cs'])
        p = 1 - p_none
        y = 1 if act_cs[t] else 0
        brier_cs += (p - y) ** 2; n += 1; pred_sum += p; act_sum += y
    if n:
        g['cs'] = dict(n=n, brier=round(brier_cs / n, 3),
                       predicted_rate=round(pred_sum / n, 3),
                       actual_rate=round(act_sum / n, 3))
    return g


def cumulative_level(graded):
    """{pos: sum(actual)/sum(proj) across every graded gameweek} plus the
    number of gameweeks behind it — P7's drift measure."""
    sums = {}
    for g in graded:
        for pos, (act, proj, n) in (g.get('level_sums') or {}).items():
            s = sums.setdefault(pos, [0.0, 0.0, 0])
            s[0] += act
            s[1] += proj
            s[2] += n
    return {pos: (round(v[0] / v[1], 3) if v[1] > 0 else None) for pos, v in sums.items()}


def main():
    HISTORY.mkdir(parents=True, exist_ok=True)
    events = api('bootstrap-static/')['events']
    prior = json.loads(OUT.read_text()) if OUT.exists() else {'gws': []}
    done = {g['gw']: g for g in prior.get('gws', [])}
    graded = []
    for path in sorted(HISTORY.glob('gw*.json')):
        if path.name.endswith('_actual.json') or path.name.endswith('_retro.json') \
                or path.name.endswith('_props.json'):
            continue
        snap = json.loads(path.read_text())
        gw = snap['gw']
        # regrade only if we haven't, or if the earlier grade was provisional,
        # or if a retrospective for the previous week has since appeared
        prev_retro = load_retro(gw - 1)
        if gw in done and done[gw].get('checked') and \
                (prev_retro is None or done[gw].get('retro_gw') == gw - 1):
            graded.append(done[gw])
            continue
        act = actuals_for(gw, events)
        if not act:
            print(f'  GW{gw}: not finished yet')
            continue
        g = grade(snap, act, prev_retro)
        g['checked'] = act.get('checked', False)
        graded.append(g)
        av = g.get('availability') or {}
        rules = ''
        if av.get('recency_start_brier') is not None:
            rules = (f'; start Brier recency {av["recency_start_brier"]} vs '
                     f'aggregate {av["aggregate_start_brier"]}')
        print(f'  GW{gw}: rank corr {g["spearman_starters"]} over {g["n_starters"]} '
              f'likely starters; captain model {g.get("captain", {}).get("model", {}).get("pts", "—")}'
              f' / yours {g.get("captain", {}).get("yours", {}).get("pts", "—")}'
              f' / best {g.get("captain", {}).get("best", {}).get("pts", "—")}'
              f'; level {g.get("level_ratio", {}).get("ALL", "—")}{rules}'
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
        start_brier=avg('availability', 'start_brier'),
        appearance_brier=avg('availability', 'appearance_brier'),
        minutes_mae=avg('availability', 'minutes_mae'),
        minutes_bias=avg('availability', 'minutes_bias'),
        baseline_start_brier=avg('availability', 'baseline_start_brier'),
        start_brier_lift=avg('availability', 'start_brier_lift'),
        recency_start_brier=avg('availability', 'recency_start_brier'),
        aggregate_start_brier=avg('availability', 'aggregate_start_brier'),
        recency_vs_aggregate_lift=avg('availability', 'recency_vs_aggregate_lift'),
        n_rule_gws=sum(1 for g in graded
                       if (g.get('availability') or {}).get('recency_start_brier') is not None),
        recency_wins=sum(1 for g in graded
                         if ((g.get('availability') or {}).get('recency_vs_aggregate_lift') or 0) > 0),
        level_ratio_cum=cumulative_level(graded),
        spearman_ep_next_starters=avg('ep_next', 'spearman_starters'),
        spearman_ep_next_pool=avg('ep_next', 'spearman_pool'),
        mae_ep_next_starters=avg('ep_next', 'mae_starters'),
        n_retro_gws=sum(1 for g in graded if g.get('retro_class')),
        goal_logloss_model=avg('goals', 'logloss_model'),
        goal_logloss_market=avg('goals', 'logloss_market'),
    )
    out = dict(generated=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
               summary=summary, gws=graded,
               notes=['Hold-out backtest before the season: rank correlation ~0.46 '
                      'on points per 90 among established players.',
                      'spearman_starters is over players the model gave a 60%+ chance '
                      'of starting; spearman_pool over everyone projected 0.5+.',
                      'Captain and XI rows need a known squad at snapshot time.',
                      'level_ratio_cum is sum(actual)/sum(proj) over likely starters, '
                      'cumulative; with the calibration frozen (P4) it measures drift. '
                      'Do not feed it back before GW8 (P7).',
                      'recency_vs_aggregate_lift > 0 means the recency minutes rule '
                      'had the lower start Brier that week; production switches only '
                      'after it wins over four or more gameweeks (P2).'])
    OUT.write_text(json.dumps(out, indent=1))
    print(f'scorecard: {len(graded)} gameweek(s) graded -> {OUT}')


if __name__ == '__main__':
    main()
