"""GW4 transfer sensitivity on a frozen, fresh snapshot. Research only.

Uses the September 4 team/fixture model and September 6 player data. Exact
single-transfer selling prices. Multiweek planner tracks the original
acquisition lot, charges its sale discount once, and charges full price
on repurchase. Future price changes remain outside this experiment.
"""
import copy
import gzip
import json
import math
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'v2'))
import player_model as pm
import planner
from manager_minutes import load_from_db
from squad_evaluator import evaluate_squad

OUT = ROOT/'research'/'experiments-2026-09-06'
LAB = Path('/tmp/fpl-experiments-2026-09-06')


def main(project_only=False):
    pm.DB = LAB/'fpl.db'
    pm.START_GW, pm.HORIZON, pm.WINDOW = 4, 9, 6
    # Require the existing frozen calibration; never fit/write production data.
    assert pm.load_calibration() is not None
    players = pm.load()
    boot = json.loads((LAB/'cache'/'bootstrap.json').read_text())
    fixtures = json.loads((LAB/'cache'/'fixtures.json').read_text())
    account = json.loads((LAB/'account.json').read_text())
    owned = [p['element'] for p in account['picks']['picks']]
    assert not account['transfers'], 'Purchase lots must be reconstructed for traded squads'
    bank = account['picks']['entry_history']['bank']/10
    price_rows = {p['id']:p for p in boot['elements']}
    sells = {}
    for pid in owned:
        p = price_rows[pid]
        bought = p['now_cost']-p['cost_change_start']
        sells[pid] = (p['now_cost'] if p['now_cost']<=bought else bought+(p['now_cost']-bought)//2)/10
    liquidation_discount = sum(players[i]['price']-sells[i] for i in owned)
    teams = {t['id']:t['short_name'] for t in boot['teams']}
    completed = {f['id'] for f in fixtures if f.get('finished') or f.get('finished_provisional')}
    # Some element-summary responses include a zero row for Sunday's unplayed
    # match. Exclude it from both the attack experiment and minutes evidence.
    for p in players.values():
        p['gw'] = [r for r in p.get('gw',[]) if r['fixture_id'] in completed]
    pm.GW_ROWS_LOADED = True
    pm.SNAPSHOT_STATUS.update(pm.load_snapshot_status())
    pm.MANAGER_MPS_TABLE = load_from_db([pm.CURRENT],db=pm.DB)
    with gzip.open(OUT/'inputs'/'team-view.json.gz','rt') as fh:
        view = json.load(fh)
    priors = pm.positional_priors(players)
    original_overlay = copy.deepcopy(pm.OVERLAY)
    original_shrink = pm.shrink

    def adaptive_shrink(p, metric, positional, current_mult=None):
        base, confidence = original_shrink(p,metric,positional,current_mult)
        if metric not in ('xg90','xa90') or not p.get('gw'):
            return base, confidence
        slow = [original_shrink(p,m,positional)[0] for m in ['xg90','xa90']]
        n, counts = 0., [0.,0.]
        for j,r in enumerate(reversed(p['gw'])):
            w = .5**(j/12)
            n += w*r['mins']/90
            for k,m in enumerate(['xg','xa']):
                counts[k] += w*(r.get(m) or 0)
        z2 = sum((counts[k]-n*slow[k])**2/(n*max(slow[k],.05)+.25) for k in [0,1])
        q = 1-math.exp(-z2/2)
        k = 0 if metric=='xg90' else 1
        fast = (counts[k]+3*slow[k])/(n+3)
        return (1-q)*base+q*fast, confidence

    all_rows = {}
    for variant in ['finished_only','finished_recency','fresh_aggregate','fresh_recency','no_attack_overlay','adaptive']:
        pm.GAMES_PLAYED.clear()
        pm.TEAM_FIXTURES.clear()
        for f in fixtures:
            done = f.get('finished') or (not variant.startswith('finished_') and f.get('finished_provisional'))
            if not done:
                continue
            for tid in [f['team_h'],f['team_a']]:
                team = teams[tid]
                pm.GAMES_PLAYED[team] = pm.GAMES_PLAYED.get(team,0)+1
                pm.TEAM_FIXTURES.setdefault(team,[]).append(dict(fixture_id=f['id'],event=f['event'],kickoff=f['kickoff_time']))
        for fs in pm.TEAM_FIXTURES.values():
            fs.sort(key=lambda f:f['kickoff'])
        pm.MINUTES_RULE = 'aggregate' if variant in ['finished_only','fresh_aggregate'] else 'recency'
        pm.OVERLAY = copy.deepcopy(original_overlay)
        if variant in ['no_attack_overlay','adaptive']:
            for ov in pm.OVERLAY.values():
                ov.pop('rate_mult',None)
        pm.shrink = adaptive_shrink if variant=='adaptive' else original_shrink
        rows = {p['id']:p for p in pm.project(players,view,priors)}
        all_rows[variant] = rows
        print('PROJECTED',variant,[(rows[i]['name'],rows[i]['start_rate'],rows[i]['proj_6gw']) for i in [70,106,398]],flush=True)
    with gzip.open(OUT/'live-projections.json.gz','wt') as fh:
        json.dump(all_rows,fh)
    if project_only:
        return

    report = dict(snapshot_at=account['at'], gw=4, horizon=9, bank=bank,
                  inferred_ft=4, sell_prices=sells, initial_sale_discount=liquidation_discount,
                  assumptions=__doc__, variants={})
    for variant in ['fresh_aggregate','fresh_recency','no_attack_overlay','adaptive']:
        rows = all_rows[variant]
        evaluate = lambda ids,a=4,b=9:evaluate_squad([rows[i] for i in ids],a,b).total
        base = evaluate(owned)
        result = dict(squad_ev=base, players=[rows[i] for i in owned], singles={},plans={})
        for outgoing in [70,106,398]:
            rest = [i for i in owned if i!=outgoing]
            clubs = Counter(rows[i]['team'] for i in rest)
            candidates = [p for p in rows.values() if p['id'] not in owned
                          and p['pos']==rows[outgoing]['pos'] and p['status']!='u'
                          and p['price']<=sells[outgoing]+bank+1e-8 and clubs[p['team']]<3]
            moves = []
            for p in candidates:
                total = evaluate(rest+[p['id']])
                moves.append(dict(out=rows[outgoing]['name'],incoming=p['name'],id=p['id'],price=p['price'],
                                  gain=total-base,gw4_gain=evaluate(rest+[p['id']],4,4)-evaluate(owned,4,4),
                                  start=p['start_rate'],ev=p['proj_6gw']))
            moves.sort(key=lambda x:-x['gain'])
            result['singles'][str(outgoing)] = moves[:10]
            print('SINGLES',variant,rows[outgoing]['name'],moves[:3],flush=True)
        report['variants'][variant] = result
        (OUT/'transfers.json').write_text(json.dumps(report,indent=2))
        for ft in ([2,4] if variant=='fresh_aggregate' else [4]):
            paths = {}
            for hold in [False,True]:
                path = planner.plan(rows,owned,bank=bank,ft=ft,gw=4,horizon=9,
                                    freeze_this_week=hold,time_limit=15,sell_prices=sells)
                paths['hold' if hold else 'act'] = path
                print('PLAN',variant,ft,hold,planner.describe(path,rows),flush=True)
            if all(paths.values()):
                paths['act_now_gain'] = round(paths['act']['total']-paths['hold']['total'],2)
                paths['additional_hold_bar'] = 2*len(paths['act']['weeks'][0]['out'])
            result['plans'][str(ft)] = paths
            (OUT/'transfers.json').write_text(json.dumps(report,indent=2))
    print('TRANSFER DONE',flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-only',action='store_true')
    args = parser.parse_args()
    main(project_only=args.project_only)
