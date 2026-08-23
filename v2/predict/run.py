"""Run the whole prediction chain in order."""
import subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [
    ('yoy', [], 'year-to-year drift in club strength'),
    ('promoted', [], 'width of the promoted-club prior'),
    ('boot_prod', [], 'estimation error per club'),
    ('minutes_resid', [], 'minutes residual around a shrunk prediction'),
    ('components', [], 'split projections into their scoring parts'),
    ('volume_test', [], 'is the club volume multiplier double-counting?'),
    ('season_sim', ['100000'], '100,000 seasons'),
    ('validate_shape', [], 'does the simulated table look real?'),
    ('expectation', [], 'the baseline to beat'),
    ('player_sim', ['0.85', '0.86', '0.72'], '40,000 player seasons'),
    ('over_under', [], 'player over/underachievers'),
    ('report', [], 'the answers'),
]
only = sys.argv[1:] or None
for name, args, why in STEPS:
    if only and name not in only:
        continue
    print(f'\n{"=" * 72}\n== {name}.py — {why}\n{"=" * 72}')
    r = subprocess.run([sys.executable, str(HERE / f'{name}.py'), *args])
    if r.returncode:
        sys.exit(f'{name}.py failed')
