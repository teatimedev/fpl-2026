"""Exact squad scoring of affordable packages; frozen forecasts, no future moves."""
import json
import gzip
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'v2'))
from squad_evaluator import evaluate_squad
from planner import plan
OUT = ROOT/'research'/'experiments-2026-09-06'


def main():
    with gzip.open(OUT/'live-projections.json.gz','rt') as fh:
        raw = json.load(fh)
    views = {v:{int(k):p for k,p in rows.items()} for v,rows in raw.items()}
    prior = json.loads((OUT/'transfers.json').read_text())
    owned = [p['id'] for p in prior['variants']['fresh_recency']['players']]
    sells = {int(k):v for k,v in prior['sell_prices'].items()}
    report = {'packages':{},'diagnostic_plans':{}}
    packages = set()
    for v,result in prior['variants'].items():
        for ft,paths in result['plans'].items():
            if paths.get('act'):
                w = paths['act']['weeks'][0]
                packages.add((tuple(sorted(w['out'])),tuple(sorted(w['in']))))
    for v in ['fresh_aggregate','no_attack_overlay','adaptive']:
        rows = views[v]
        evaluate = lambda ids,a=4,b=9:evaluate_squad([rows[i] for i in ids],a,b).total
        base = evaluate(owned)
        rest = [i for i in owned if i not in [70,106]]
        clubs = Counter(rows[i]['team'] for i in rest)
        funds = sells[70]+sells[106]+prior['bank']
        pools = {pos:[p for p in rows.values() if p['id'] not in owned and p['pos']==pos
                      and p['status']!='u' and p['start_rate']>=.4
                      and clubs[p['team']]<3] for pos in ['MID','FWD']}
        candidates = []
        for mid in pools['MID']:
            for fwd in pools['FWD']:
                if mid['price']+fwd['price']>funds+1e-8:
                    continue
                if mid['team']==fwd['team'] and clubs[mid['team']]>1:
                    continue
                squad = rest+[mid['id'],fwd['id']]
                candidates.append(dict(ids=[mid['id'],fwd['id']],incoming=[mid['name'],fwd['name']],
                                       gain=evaluate(squad)-base,cost=mid['price']+fwd['price']))
        candidates.sort(key=lambda x:-x['gain'])
        report['packages'][v] = dict(evaluated=len(candidates),top=candidates[:10])
        for pair in candidates[:5]:
            packages.add(((70,106),tuple(sorted(pair['ids']))))
        print('PAIR',v,len(candidates),candidates[:3],flush=True)
    scores = []
    for outgoing,incoming in sorted(packages):
        values = {}
        rest = [i for i in owned if i not in outgoing]
        for v in ['fresh_aggregate','fresh_recency','no_attack_overlay','adaptive']:
            rows = views[v]
            ev = lambda ids,a,b:evaluate_squad([rows[i] for i in ids],a,b).total
            values[v] = dict(gw4_gain=ev(rest+list(incoming),4,4)-ev(owned,4,4),
                             six_gw_gain=ev(rest+list(incoming),4,9)-ev(owned,4,9))
        rows = views['fresh_aggregate']
        cash = prior['bank']+sum(sells[i] for i in outgoing)-sum(rows[i]['price'] for i in incoming)
        scores.append(dict(out=[rows[i]['name'] for i in outgoing],incoming=[rows[i]['name'] for i in incoming],
                           bank=round(cash,2),values=values))
    report['package_sensitivity'] = scores
    (OUT/'packages.json').write_text(json.dumps(report,indent=2))
    # Observe actual solver gaps before treating small path differences as evidence.
    rows = views['fresh_recency']
    for hold in [False,True]:
        result = plan(rows,owned,prior['bank'],4,4,9,freeze_this_week=hold,time_limit=30,sell_prices=sells)
        report['diagnostic_plans']['hold' if hold else 'act'] = result
        print('DIAGNOSTIC',hold,result['total'] if result else None,result.get('solver') if result else None,flush=True)
        (OUT/'packages.json').write_text(json.dumps(report,indent=2))
    print('PACKAGE DONE',flush=True)


if __name__=='__main__':
    main()
