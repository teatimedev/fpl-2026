"""Hypothetical scouting stress test, NOT fitted downgrades or recommendations.

Reconstruct the attacking component from frozen rounded model outputs;
reduce chance-creation rates only. All other players, minutes and components
stay fixed. This identifies sensitivity, not the probability of deterioration.
"""
import copy
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'v2'))
from squad_evaluator import evaluate_squad

OUT = ROOT/'research'/'experiments-2026-09-06'


def main():
    with gzip.open(OUT/'live-projections.json.gz','rt') as fh:
        base = {int(k):p for k,p in json.load(fh)['fresh_recency'].items()}
    with gzip.open(OUT/'inputs'/'team-view.json.gz','rt') as fh:
        view = json.load(fh)['view']
    state = json.loads((OUT/'transfers.json').read_text())
    owned = [p['id'] for p in state['variants']['fresh_recency']['players']]
    observations = []
    for outgoing,incoming in [(106,165),(70,453),(70,68)]:
        for until in [6,9]:
            for reduction in [0,.1,.2,.3]:
                players = copy.deepcopy(base)
                p = players[outgoing]
                goal_points = {'FWD':4,'MID':5}[p['pos']]
                for gw in range(4,until+1):
                    volume = sum(f['xg']/1.45 for f in view[p['team']].get(str(gw),[]))
                    attack = ((p['xg90']*goal_points+3*p['xa90'])*p['mins_by_gw'][gw-1]/90
                              *volume*p['calibration_k'])
                    p['proj_by_gw'][gw-1] -= reduction*attack
                    assert p['proj_by_gw'][gw-1] >= 0
                evaluate = lambda ids,lo,hi:evaluate_squad([players[i] for i in ids],lo,hi).total
                changed = [i for i in owned if i!=outgoing]+[incoming]
                observations.append(dict(out=p['name'],incoming=players[incoming]['name'],
                                         chance_creation_reduction=reduction,through_gw=until,
                                         six_week_gain=evaluate(changed,4,9)-evaluate(owned,4,9),
                                         gw4_gain=evaluate(changed,4,4)-evaluate(owned,4,4)))
    payload = dict(method=__doc__,snapshot=state['snapshot_at'],hypothetical=True,results=observations)
    (OUT/'scouting-sensitivity.json').write_text(json.dumps(payload,indent=2)+'\n')
    for row in observations:
        if row['through_gw']==9:
            print(row)


if __name__=='__main__':
    main()
