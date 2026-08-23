# Season predictions

v2 answers "how many points will this player score over the next six
gameweeks". These scripts answer a different question — who wins the league,
who goes down, who tops the scoring charts — which needs two things v2 does not
provide: a whole season, and distributions instead of point estimates.

Everything here sits on top of v2. It refits nothing.

## Run it

```bash
.venv/bin/python v2/predict/run.py
```

Intermediate artefacts land in `v2/predict/_out/`. The write-up is
[`PREDICTIONS.md`](../../PREDICTIONS.md) at the repo root.

## What each script does

| script | what it establishes |
|---|---|
| `yoy.py` | genuine year-to-year drift in club strength: attack sd **0.111**, defence **0.099**, measured over 51 club-seasons after bootstrapping out estimation noise |
| `promoted.py` | the promoted-club prior has a **width** — nine promoted club-seasons scatter around it with attack sd 0.158, defence 0.119 |
| `boot_prod.py` | per-club estimation error in the production fit (Sunderland ±0.196, Spurs ±0.064) |
| `minutes_resid.py` | spread of realised starts around a **shrunk** prediction: sd 0.274 of a season, left-skewed, 21% chance of under 20 starts |
| `volume_test.py` | v2's season-level club volume multiplier is applied at 1.00; the measured coefficient is **0.56** |
| `components.py` | splits every season projection into goals / assists / clean sheets / DefCon / appearance / bonus, so each can be varied on its own terms |
| `season_sim.py` | 100,000 simulated seasons of the real 380-fixture schedule, with the measured uncertainties redrawn per season |
| `player_sim.py` | 40,000 player seasons on the same club draws, so teammates correlate |
| `expectation.py` | the baseline the over/underachiever picks are measured against |
| `validate_shape.py` | does a simulated table look like a real one? |
| `report.py`, `over_under.py` | the answers |

## Why the extra uncertainty layer exists

`season_view.json` hands over one attack and one defence number per club as
though they were known. Simulating that directly answers "how do these exact
ratings play out", which is not the question — it makes every favourite look
better than it is, and it produces a table nobody would recognise.

With drift, estimation error and the promoted-club spread added, the simulated
champion lands on 86 points against a real four-season mean of 87, fourth place
on 68 against 68, and the survival line on 36 against 38. Without them: 81, 64
and 40.

## What this exposed in v2

Three things, all documented in `PREDICTIONS.md` and none of which change the
ordering enough to move the picks:

1. **The cameo term over-credits substitutes.** `project()` scales attacking,
   DefCon and bonus by `p_play` — which includes a 20% cameo probability — but
   leaves the minutes fraction at minutes-per-*start*. A 25-minute substitute
   gets a full starter's chance of reaching 10 defensive actions.
2. **`calibrate()` is fitted on a survivor cohort** (2,000+ minutes last
   season) and then applied across 38 gameweeks to everyone. v2 projects 38
   players past 150 points against 16–28 in the real seasons.
3. **The volume multiplier double-counts club strength over a full season.**
   Fine as a fixture adjustment, wrong as a season-level one for a player who
   stayed put — his own xG/90 was measured at that club.
