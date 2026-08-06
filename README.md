# FPL 2026/27

Research, projections and an interactive squad builder for the 2026/27 Fantasy
Premier League season. **GW1 deadline: Fri 21 Aug 2026, 18:30 BST.**

- **[RESEARCH.md](RESEARCH.md)** — the written brief: rule changes, ten new
  managers, the transfer window, pre-season form, injuries, penalty takers,
  opening fixtures and the traps.
- **[SQUADS.md](SQUADS.md)** — three costed 15-player squads with reasoning.
- **`app/`** — the interactive builder.

## Run the app

```bash
cd app
npm install
npm run dev
```

## Rebuild the data

Prices are locked by FPL until the GW1 deadline, so the dataset is stable. To
refresh anyway (or after the deadline, when prices start moving daily):

```bash
python3 -m venv .venv && .venv/bin/pip install pulp highspy

curl -s https://fantasy.premierleague.com/api/bootstrap-static/ -o data/bootstrap.json
curl -s https://fantasy.premierleague.com/api/fixtures/         -o data/fixtures.json

.venv/bin/python project.py          # projections  -> data/projections.{csv,json}
.venv/bin/python optimise.py --json  # squads       -> data/squads.json
.venv/bin/python validate.py         # independent rules check
.venv/bin/python export_app_data.py  # bundle       -> app/src/data/fpl.json
```

## How the projection model works

`project.py` estimates points per gameweek over GW1–6 for all 572 players. The
core idea is that a player's scoring rate is not one number — it is three, and
they respond to different things.

1. **Decompose last season's rate** into clean-sheet points, DefCon points and
   an attacking residual (goals, assists, bonus, saves, appearance).
   - DefCon is modelled properly: the API's `defensive_contribution` is a raw
     count of qualifying actions, so expected DefCon points are
     `2 × P(Poisson(rate) ≥ threshold)`, with a threshold of 10 for defenders
     and 12 for midfielders and forwards.
2. **Re-project each component** against the new season:
   - clean sheets scale with the opening fixtures and, for players who changed
     club, the new club's actual clean-sheet rate;
   - **DefCon scales inversely with team strength** — join a side that dominates
     possession and the qualifying actions dry up. This is why Elliot Anderson's
     move to Man City is a downgrade to his biggest scoring source;
   - attacking output scales with the fixtures and the new club's attacking
     strength.
3. **Players who stayed put get no team-strength multiplier.** Their historical
   rate already embeds their club's quality — applying it again would
   double-count, which at one point had Gabriel projected at 8.6 points a game.
4. **Players with no Premier League history** fall back to a price-implied
   prior fitted per position, discounted 12% for adaptation risk.
5. **Minutes** split into two questions, because they behave differently:
   - *how long he plays when he starts* — stable, 85–93 minutes for nearly every
     first-choice player;
   - *how often he starts* — **not** stable, and copying last season's start
     count forward serves an injury-hit season twice. The observed start rate is
     shrunk towards a club/position pecking-order prior, weighted by how much of
     it was actually observed. Availability flags and `overlay.py` apply on top.

### Known limitation

**Captaincy is not modelled.** The armband doubles a score, so premiums the
optimiser rates poorly on raw points per £m are worth more than they look. Read
small projection gaps between squads accordingly.

### Changelog

- **6 Aug 2026** — fixed the minutes model (see point 5). Previously it used
  `last season's minutes / 38`, which had Isak — Liverpool's first-choice striker
  and No.1 penalty taker — projected at 19 minutes a game off the back of 8
  starts at 86.8 minutes each. Isak's GW1–6 projection went 6.6 → 20.8, Estêvão
  8.1 → 11.7, Doku 19.4 → 23.4. Durable players fell slightly as an old 1.05
  inflation factor was removed (Bruno Fernandes 41.2 → 39.0).

`overlay.py` holds everything the API cannot express — manager changes,
pre-season role changes, squad-depth reality checks. Every entry traces to a
source in RESEARCH.md, and multipliers are kept within roughly 0.75–1.25 so the
model stays data-driven.

**These are estimates, not forecasts.** They are most useful for comparing
players, least useful as absolute point predictions.

## Optimiser

`optimise.py` solves an integer program (HiGHS via PuLP) for the highest
projected starting XI subject to the real constraints: £100.0m, 2/5/5/3, max 3
per club, and a legal XI. The bench is weighted at 12% of a starting point,
which is what produces the usual "stack the XI, cheap bench" shape rather than
15 evenly priced players.

`validate.py` re-checks every squad against the rules using the raw API prices
and positions, deliberately without importing the optimiser, so a bug in the
solver cannot hide behind a bug in its own validation.

## Transfer planning

```bash
.venv/bin/python plan.py
```

Solves GW1–6 as a single multi-period integer program: which 15 to own in every
gameweek, the XI, the captain, and when to transfer — subject to the squad rules
holding *in every week* plus FPL's free-transfer accounting (one a week, bankable
to five, −4 per extra). Uses the per-gameweek projections from `project_by_gw()`
rather than a horizon average, since that week-to-week swing is the only thing a
transfer plan can trade on.

Assumes static prices and ignores FPL's sell-price rule. Plans against expected
values, so it cannot anticipate injuries — which is where most of a transfer's
real value lies. Read its output as a floor.

## Simulation

```bash
.venv/bin/python simulate.py --sims 40000
```

A projection gives one number; the decision needs the distribution. `simulate.py`
runs 40,000 Monte Carlo seasons of GW1–6 and models three things a projected XI
total cannot:

- **Team-level correlation.** Goals for and against are drawn per club per
  gameweek and shared by every player at that club, so three Arsenal defenders
  blank together. Squads concentrated in a few clubs are correctly shown as
  riskier than their projection implies.
- **Auto-subs**, with FPL's real rules — at most three outfield subs, each bench
  player used once. This turned out to be worth 6.6 points between the top two
  squads.
- **Captaincy**, falling through to the vice-captain when the captain doesn't play.

The simulation's attacking rates are calibrated per player so its mean matches
the projection model (mean absolute error 0.16 pts/GW). Raw xG and xA run about
20–30% light on realised attacking returns while defenders come out roughly
right — left uncorrected, that would have biased the whole comparison towards
defensive squads. The projection model is anchored on last season's actual
points, so it is the better estimate of the *mean*; the simulation's job is the
*shape*.

Two bugs found and fixed while building it, both of which had inverted the
result: auto-subs let one bench player cover every blank in the XI
simultaneously, and the "template" benchmark was built greedily from the
most-owned players without a budget constraint, producing a **£111.5m** squad
that beat everything because it had 11.5% more money to spend.

## Notes

- PuLP ships an x86-only CBC binary that will not run on Apple Silicon, hence
  HiGHS.
- The FPL API needs no authentication.

---

## v2 — the professional rebuild

Everything above is v1. `v2/` replaces the hand-tuned heuristics with fitted
models validated against outside benchmarks: a Dixon-Coles team strength model
checked against Pinnacle's closing odds, shrinkage weights set by measured
year-over-year stability, and a hold-out backtest. See **[v2/README.md](v2/README.md)**.

Weekly use: `.venv/bin/python v2/weekly.py --team <your FPL entry id>`

The optimiser, validator and web app now run on v2 projections
(`v2/to_csv.py` writes them into the v1 schema). v1's projections are kept at
`data/projections_v1.csv` for comparison.
