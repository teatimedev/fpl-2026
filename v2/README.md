# v2 — the professional rebuild

v1 was one season of aggregates plus a hand-tuned fixture heuristic. v2 replaces
the heuristic with a fitted model, replaces the guessed shrinkage weights with
measured ones, and — most importantly — checks itself against outside benchmarks
instead of only against its own assumptions.

## The pipeline

```bash
.venv/bin/python v2/fetch.py          # all data -> v2/fpl.db (+ gw_stat, data/gw_stats.csv)
.venv/bin/python v2/teams_model.py    # Dixon-Coles fit + market validation
.venv/bin/python v2/season_view.py    # 2026/27 fixture parameters (post-fit adjustments decay)
.venv/bin/python v2/stability.py      # what actually predicts next season
.venv/bin/python v2/player_model.py   # projections (calibration frozen in v2/calibration.json)
.venv/bin/python v2/backtest.py       # hold-out evaluation
.venv/bin/python v2/to_csv.py         # feed the optimiser and web app
.venv/bin/python v2/scorecard.py      # grade finished gameweeks
.venv/bin/python v2/retro.py          # why each player diverged last week (feeds the digest)
```

In-season learning — what moves each week, what does not, and the shadow
columns that decide the next switch — is documented in
[`../RESEARCH-INSEASON-LEARNING.md`](../RESEARCH-INSEASON-LEARNING.md). The
backward harnesses (`import_gw_history.py` + `backtest_inseason.py`) and the
DefCon dispersion test (`defcon_dispersion.py`) live alongside.

Or just `python v2/weekly.py --team <entry id>`, which runs the lot.

## What the data actually is

| source | what it gives |
|---|---|
| FPL `bootstrap-static` | prices, ownership, availability, set-piece order |
| FPL `element-summary` × 572 | **four seasons** of per-player history with xG, xA, xGC, DefCon |
| football-data.co.uk | **1,520 real matches** with shots, corners, cards |
| football-data.co.uk odds | **Pinnacle/B365 closing lines** — the sharpest public probability estimate |
| football-data `fixtures.csv` | forward odds, published about a week before each round |

## 1. Team model (Dixon-Coles)

FPL's fixture difficulty is a hand-assigned 2–5. This fits attack and defence
ratings by maximum likelihood on four seasons of results, with the Dixon-Coles
low-score correction and exponential time decay.

**Validation against the market** — the only honest benchmark:

| | log-loss | Brier |
|---|---|---|
| model | 0.9895 | 0.5911 |
| Pinnacle closing line | 0.9737 | 0.5804 |

1.6% behind the closing line on 914 walk-forward matches, never looking ahead.
That is a credible result for a model with no team news or lineup information.
The optimal time-decay half-life came out at **365 days**, home advantage at
**×1.193**, and the promoted-club prior (−0.337 attack) landed almost exactly on
Ipswich's actual rating — three independent sanity checks that all passed.

**Why it matters:** FPL's FDR correlates only **−0.60** with real clean-sheet
probability, and within a single FDR bucket the true range is 20%–68%. Arsenal
at home to Chelsea (FDR 4) is a better clean-sheet bet than Coventry at home to
Newcastle (FDR 2). FDR measures the opponent; what you need is the outcome.

## 2. What actually predicts next season

Measured on 432 consecutive player-season pairs, minutes-weighted:

| metric | stability | consequence |
|---|---|---|
| xGI/90 | **0.91** | the most repeatable attacking signal |
| xG/90 | 0.90 | and it beats goals/90 (0.82) at predicting goals |
| xA/90 | 0.84 | beats assists/90 (0.59) everywhere; 0.21→0.50 for forwards |
| DefCon/90 | 0.56 | a real skill, but needs real shrinkage |
| starts | 0.46 | only moderately repeatable |
| **clean sheets/90** | **0.21** | almost no signal — 0.09 for MID and FWD |
| bonus/90 (DEF) | **0.14** | defender bonus is close to noise |

Two of these changed the design outright:

- **Clean sheets now come from the team model, not the player.** v1 used a
  player's own clean-sheet history, which the data says barely predicts anything.
- **Attack is built on xG and xA, not goals and assists.** Finishing regresses;
  chance quality persists.

The 0.46 stability of starts is independent confirmation of the minutes fix made
earlier by intuition — last season's start count genuinely is a weak guide.

Aging peaks at 24 with a 13% swing across 19–34, which is small enough to be a
minor correction rather than a driver.

## 3. Hold-out backtest

Predicting a season never seen during fitting.

| target 2025/26 | MAE | RMSE | Spearman |
|---|---|---|---|
| naive_last | 0.955 | 1.192 | 0.443 |
| naive_price | 0.725 | 0.914 | 0.429 |
| positional | 0.739 | 0.973 | 0.208 |
| v1_style | 0.845 | 1.032 | 0.457 |
| **v2_shrunk** | 0.730 | 0.922 | **0.463** |

**v2 beats v1 in both hold-outs**, on error and on ranking. But the margin over
*price alone* is thin — price is a very strong baseline because it is itself an
expert forecast. Rank correlation around **0.46 is the realistic ceiling** for
this problem; anyone claiming much more is fooling themselves.

2025/26 was markedly harder to predict than 2024/25 for *every* method. That is
the DefCon rule change breaking continuity — which implies 2026/27 should be
easier now the rule has bedded in.

### A bug worth recording

The first run of this backtest showed `naive_price` winning everything. It was
look-ahead bias: it used the **2026/27** price to predict **2025/26**, and that
price was set in summer 2026, after the season it was being asked to predict.
Switching to each season's own `start_cost` removed the leak and the result
changed. A backtest that flatters a method is usually leaking.

## 4. Known weaknesses

- **Calibration is applied, not earned.** Raw v2 under-projected outfield
  players (MID at 0.78× actual) because shrinkage drags starters towards a mean
  that includes fringe players. A per-position multiplier corrects the level.
  Ordering — v2's real strength — is untouched, but the levels are fitted, not
  derived.
- **Share × volume could not be tested.** FPL's `history_past` does not record
  which club a player was at, and that is the only case where share and absolute
  rates differ. Absolute rates are kept because the alternative is unvalidated.
- **Ten new managers.** Ratings describe sides coached by someone else. Those
  clubs are shrunk 20% toward the mean, which is a judgement, not a measurement.
- **Unknown players lean on the price prior.** Kostoulas and Thiaw rate well on
  little evidence. Treat low-ownership, low-history names in the output with
  suspicion.
- **Per-match player data is now kept** (`gw_stat`, filled from the same
  element-summary responses; past seasons via `import_gw_history.py`). The
  DefCon dispersion test (`defcon_dispersion.py`) runs once players have six
  full matches; until it has, `r = 4 + 11·evidence` remains a judgement.

## Deadline availability and squad value

The historical minutes model is only the baseline. `availability.json` can
override one or more explicit gameweeks with separate probabilities of
starting and appearing from the bench, minutes in each role, source, confidence
and an expiry. `player_model.py` prices starter and cameo minutes separately;
a substitute is no longer credited with a starter's full attacking exposure.
FPL injury and suspension flags with a stated return date remain active through
each deadline before that date; undated temporary flags affect only the current
deadline rather than contaminating the full forecast window.

`squad_evaluator.py` is the shared rules layer for the weekly digest, optimiser,
planner, chips and simulation. It selects a legal XI each week, applies exact
formation-sensitive autosubs in realised scenarios, limits captain fallback to
the vice-captain, and values expected bench cover from the selected XI's
non-appearance distribution with formation, bench order and substitute DNPs.
The optimiser/planner iteratively refit a per-gameweek linear DNP proxy to the
selected squad inside their integer programs, then report it through the full
evaluator. The squad optimiser also performs a bounded exact one-swap search
after the linear solve. The multiweek transfer planner reports exact scores for
its chosen weeks but still selects its path with the linear bench proxy, so the
path is directional rather than a guaranteed nonlinear global optimum. Transfer
advice reports starting-XI/captain gain
and auto-sub resilience gain separately, and unavailable squad members are
flagged even when their raw best-XI gain is zero.

## 5. Weekly use

```bash
.venv/bin/python v2/weekly.py --team 1234567
```

Refreshes prices and availability, pulls any newly published bookmaker odds,
refits, re-projects, reads your real squad from the FPL API, then reports:
captain, starting XI, every worthwhile transfer with a verdict on whether it
justifies a hit, availability flags, and price-change pressure. Writes
`v2/digest.md`.

Before Gameweek 1 your picks are not public, so list your 15 in
`v2/my_squad.txt` instead. Add `--full` once a week to refresh the four-season
histories; the default skips them.
