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

Individual player projections do not include an armband bonus. Squad scoring
does: the optimiser, weekly evaluator and simulation select a captain in every
gameweek, then fall back only to the vice-captain if the captain does not play.

### Changelog

- **6 Aug 2026** — fixed the minutes model (see point 5). Previously it used
  `last season's minutes / 38`, which had Isak — Liverpool's first-choice striker
  and No.1 penalty taker — projected at 19 minutes a game off the back of 8
  starts at 86.8 minutes each. Isak's GW1–6 projection went 6.6 → 20.8, Estêvão
  8.1 → 11.7, Doku 19.4 → 23.4. Durable players fell slightly as an old 1.05
  inflation factor was removed (Bruno Fernandes 41.2 → 39.0).

`overlay.py` holds durable role and rate changes. Short-lived deadline news is
kept separately in `v2/availability.json`: every entry has an explicit
gameweek range, source and confidence, plus separate start/cameo probabilities
and minutes. This prevents a predicted lineup from silently affecting six
weeks of projections.

**These are estimates, not forecasts.** They are most useful for comparing
players, least useful as absolute point predictions.

## Optimiser

`optimise.py` solves an integer program (HiGHS via PuLP) subject to the real
constraints: £100.0m, 2/5/5/3, max 3 per club, and a legal XI. Its linear solver
selects the XI and captain separately in every gameweek, and iteratively refits
per-gameweek bench activation to the selected 15 instead of
using the old flat 12% bench weight; final squads are then scored with
formation- and bench-order-aware probabilities through the same
`v2/squad_evaluator.py` used by weekly transfers and the simulator. A bounded
same-position local search then rejects any legal one-swap improvement under
that full score, covering interactions the linear bench proxy cannot encode.

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
real value lies. Its auto-sub term is a selected-squad linear approximation;
the chosen weeks are re-scored exactly, but the displayed path is not guaranteed
to be the global optimum under that nonlinear score. Read its output as a floor.

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
- **Auto-subs**, with FPL's real rules — formation constraints, bench order, a
  separate reserve goalkeeper, and each bench player used once.
- **Captaincy**, falling only to the vice-captain when the captain doesn't play.

The XI, bench order, captain and vice are reselected for every gameweek rather
than being frozen from a six-week average. Player outcomes are also shared
between compared squads, so head-to-head probabilities are paired comparisons.

The simulation's attacking rates are calibrated per player so its expected mean
matches the projection model. Raw xG and xA run light on realised attacking
returns while defenders come out roughly right — left uncorrected, that would
have biased the whole comparison towards defensive squads. The projection model
is anchored on last season's actual points, so it is the better estimate of the
*mean*; the simulation's job is the *shape*.

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

Weekly use: `.venv/bin/python v2/weekly.py --team <your FPL entry id> --plan`

The optimiser, validator and web app now run on v2 projections
(`v2/to_csv.py` writes them into the v1 schema). v1's projections are kept at
`data/projections_v1.csv` for comparison.

**In season (from 16 Aug 2026):** the projection window rolls (next gameweek plus
five, `v2/gwclock.py`); bookmaker odds are blended into the team layer when
posted (`teams_model.market_view`); `weekly.py` grades transfers by the lift to
your best XI, searches two-move combinations net of hits, tells you when to hold,
diffs your set lineup against the model's, and with `--plan` solves the six-week
transfer path (`v2/planner.py`). A GitHub Actions workflow refreshes at T-24h and
T-2h before every deadline (plus Thursdays), pushes a summary to your phone via
ntfy, archives each gameweek's projections and grades them afterwards
(`v2/scorecard.py`, shown in the app's Scorecard tab). RESUME.md has the details.
