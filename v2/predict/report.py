"""The answers, side by side with what they are being measured against."""
from pathlib import Path as _P
import os as _os, sys as _sys
_HERE = _P(__file__).resolve().parent
OUT = _HERE / '_out'
OUT.mkdir(exist_ok=True)
ROOTDIR = _HERE.parents[1]
_os.chdir(ROOTDIR)
_sys.path.insert(0, str(_HERE))
_sys.path.insert(0, str(_HERE.parent))
import json, numpy as np

L = {r['team']: r for r in json.load(open(OUT / 'league.json'))}
E = json.load(open(OUT / 'expectation.json'))
er, val, lastp = E['expected_rank'], E['value'], E['last_pos']

mod = {t: i + 1 for i, t in enumerate(sorted(L, key=lambda t: L[t]['posmean']))}
print('MODEL vs EXPECTATION\n')
print(f"{'team':<6}{'model':>7}{'pts':>7}{'exp':>6}{'gap':>6}{'last':>6}"
      f"{'£m':>8}   {'title':>7}{'top4':>7}{'top6':>7}{'rel':>7}")
rows = []
for t in sorted(L, key=lambda t: L[t]['posmean']):
    gap = er[t] - mod[t]          # + = model has them above expectation
    rows.append((t, gap))
    lp = lastp[t] or '-'
    print(f"{t:<6}{mod[t]:>7}{L[t]['pts']:>7.1f}{er[t]:>6}{gap:>+6}{str(lp):>6}"
          f"{val[t]:>8.1f}   {L[t]['title']*100:>6.1f}%{L[t]['top4']*100:>6.1f}%"
          f"{L[t]['top6']*100:>6.1f}%{L[t]['rel']*100:>6.1f}%")

print('\nbiggest gaps to the consensus')
for t, g in sorted(rows, key=lambda r: -r[1])[:4]:
    print(f'  OVER  {t:<5} expected {er[t]:>2}, model has them {mod[t]:>2}  ({g:+d})')
for t, g in sorted(rows, key=lambda r: r[1])[:4]:
    print(f'  UNDER {t:<5} expected {er[t]:>2}, model has them {mod[t]:>2}  ({g:+d})')

# vs last season alone, for the clubs that were in the league
print('\nvs last season alone')
surv = sorted([t for t in L if lastp[t]], key=lambda t: lastp[t])
sr = {t: i + 1 for i, t in enumerate(surv)}
d = sorted(((t, sr[t] - mod[t]) for t in surv), key=lambda r: -r[1])
for t, g in d[:4]:
    print(f'  OVER  {t:<5} {sr[t]:>2} -> {mod[t]:<2} ({g:+d})')
for t, g in d[-4:]:
    print(f'  UNDER {t:<5} {sr[t]:>2} -> {mod[t]:<2} ({g:+d})')

print('\nTOP FOUR — probability of a Champions League place')
for t in sorted(L, key=lambda t: -L[t]['top4'])[:9]:
    print(f"  {t:<5}{L[t]['top4']*100:>6.1f}%   (top 5 {L[t]['top5']*100:.0f}%, "
          f"top 6 {L[t]['top6']*100:.0f}%)")

print('\nRELEGATION — probability of finishing in the bottom three')
for t in sorted(L, key=lambda t: -L[t]['rel'])[:10]:
    print(f"  {t:<5}{L[t]['rel']*100:>6.1f}%   squad £{val[t]:.1f}m")
tri = ['IPS', 'COV', 'HUL']
print(f"\n  all three promoted clubs down: "
      f"{np.prod([L[t]['rel'] for t in tri])*100:.0f}% (independent approximation)")
print(f"  expected number of promoted clubs relegated: "
      f"{sum(L[t]['rel'] for t in tri):.2f} of 3")
