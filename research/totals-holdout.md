# Season-totals hold-out — level calibration of the shipped stack

Date: 2026-08-23
Command: `.venv/bin/python v2/backtest_totals.py` (runs in <1 s; no caching needed)
Feeds: the LevelFixes wave (volume attenuation, calibrate refit).

## Why this exists

`backtest.py` only scores pts90 rate estimators and conditions on >=900 minutes
in the target season (selection on the outcome). It cannot judge changes that act
on season totals: the club-volume multiplier (`predict/volume_test.py` measured a
slope of 0.56 against the 1.00 `project()` applies) and `calibrate()`'s refit.
This hold-out scores WINDOW TOTALS instead — full-season totals, see below.

## Method

For each target season S in {2023/24, 2024/25} (the panel's four seasons minus
the newest, 2025/26; 2022/23 has no training window behind it), rebuild every
player's projection using only what was knowable before S:

- **Histories**: `season_stat` rows strictly earlier than S. The shrinkage
  machinery is imported unchanged from `player_model` (`shrink()`,
  `positional_priors()`, `minutes_model()`); exclusion of S is done by not
  putting S-or-later rows in `p['hist']`. Weights are season-keyed
  (`SEASON_WEIGHT`), so no cutoff argument was needed anywhere.
- **Team strength**: the Dixon-Coles model (`teams_model.fit`) refit on matches
  played strictly before 1 July of S's opening year, then scored against the
  real S fixture list. Promoted clubs absent from the fit take the
  `promoted_prior` offset off the fitted league mean.
- **Calibration**: production's `calibrate()` logic aimed one season back — one
  multiplier per position so established players (2,000+ minutes in S-1)
  project to what they actually delivered in S-1. Capped at [0.7, 1.45] exactly
  like production.
- **Price**: start-of-S price from `season_stat.start_cost`, never today's
  `player.price` (the leak documented under `naive_price` in backtest.py).
- **Availability**: everyone fully available; `status`/`news`/`chance` are 2026
  facts and must not reach a historical week. Non-starters come on with
  p=0.20, as in `predict/components.py`.

**Window choice — full-season totals, deliberately.** `season_stat` holds season
aggregates only: there are no per-gameweek histories, so a realised first-6-GW
window cannot be built at all. Scaling full-season totals by 6/38 would be a
positive constant that changes neither Spearman nor proj/actual ratios. As a
bonus, full-season totals make the >150-point counts meaningful.

**Variants**

| variant | attacking volume |
|---|---|
| `current` | `vol = f['xg']/1.45` per fixture, exactly as `project()` applies it |
| `vol_lambda_0_56` | season-level club volume attenuated: attacking points × vol_t^(0.56−1), renormalised league-wide so total attacking points are unchanged (`predict/components.py`'s VOL_LAMBDA pattern with the measured slope) |

Over a full season every fixture of a club carries the same volume, so the
variant moves exactly the season-average club level that volume_test flagged.

**Scoring**: Spearman (order), mean per-player ratio proj/actual among actual>0
(`mean p/a`; dominated by fringe players under a no-minutes filter — read it
together with `sum p/a`), aggregate Σproj/Σact (`sum p/a`; the level-calibration
number), expected means, and counts above 150 points both sides.

## How the no-look-ahead guarantee is enforced

Mechanically, not by discipline:

1. `asof_players()` asserts every history row satisfies `season < target`
   before building a case; every downstream consumer (`shrink`,
   `positional_priors`, `minutes_model`) reads only `p['hist']`.
2. `asof_team_view()` asserts `max(train match date) < 1 July of S's opening
   year` before fitting Dixon-Coles.
3. Calibration multipliers are fitted on season S−1 actuals only.
4. Prices are `start_cost` of S (set at the S deadline); today's `player.price`,
   `status`, `news`, `chance` and the overlay are never read.

## Results

Fitted calibration multipliers — current: 2023/24 DEF 0.926 / GKP 1.001 /
MID 0.994 (no FWD group, n<6); 2024/25 DEF 0.969 / FWD 0.968 / GKP 0.984 /
MID 1.011.

```
--- target 2023/24   (trained on 2022/23) ---
variant         grp      n  Spearman  mean p/a  sum p/a  E[proj]  E[act]  >150 proj >150 act
current         ALL    163     0.452      3.36     1.22    101.9    83.6         15       18
current         GKP     12     0.552      1.69     1.42    115.8    81.4          2        1
current         DEF     54     0.228      1.91     1.30    95.2    73.0          1        2
current         MID     83     0.493      4.89     1.19    97.4    82.1          7        9
current         FWD     14     0.301      1.22     1.05   142.4   135.3          5        6
vol_lambda_0_56 ALL    163     0.463      3.39     1.22    102.1    83.6         15       18
vol_lambda_0_56 GKP     12     0.552      1.69     1.42    115.8    81.4          2        1
vol_lambda_0_56 DEF     54     0.204      1.93     1.31    95.4    73.0          1        2
vol_lambda_0_56 MID     83     0.516      4.94     1.19    97.8    82.1          8        9
vol_lambda_0_56 FWD     14     0.191      1.22     1.05   141.6   135.3          4        6

--- target 2024/25   (trained on 2022/23, 2023/24) ---
variant         grp      n  Spearman  mean p/a  sum p/a  E[proj]  E[act]  >150 proj >150 act
current         ALL    226     0.495      2.58     1.18    96.0    81.0         17       18
current         GKP     17     0.532      1.70     0.99    91.3    91.9          0        1
current         DEF     77     0.430      2.15     1.23    86.9    70.6          2        1
current         MID    109     0.517      2.42     1.14    95.7    83.7          9       11
current         FWD     23     0.562      5.41     1.38   131.4    95.3          6        5
vol_lambda_0_56 ALL    226     0.489      2.61     1.20    96.9    81.0         17       18
vol_lambda_0_56 GKP     17     0.532      1.70     0.99    91.3    91.9          0        1
vol_lambda_0_56 DEF     77     0.425      2.16     1.23    87.0    70.6          1        1
vol_lambda_0_56 MID    109     0.510      2.45     1.15    96.7    83.7          8       11
vol_lambda_0_56 FWD     23     0.550      5.51     1.42   135.6    95.3          8        5
```

## Reading

1. **Ordering survives, levels do not.** Spearman 0.45–0.50 overall (weakest:
   defenders in 2023/24, where one training season backs the shrinkage). But
   Σproj/Σact sits at 1.18–1.31 for outfield groups even AFTER calibration —
   the stack over-projects season totals by ~20% against what actually happened,
   most of it not removed by a calibrate() fitted on the previous season.
   Forwards are worst in 2024/25 (1.38x): trained on the high-scoring 2023/24
   (record ~3.3 goals/match), projecting into a low-scoring 2024/25 (~2.85).
   A single-season calibration target inherits the source season's scoring
   environment; this is the strongest evidence yet that calibrate()'s refit
   should use more than one anchor season, or a scoring-environment index.
2. **The attenuated variant is roughly order-neutral and slightly
   level-negative here.** Overall Spearman +0.011 / −0.006 across the two
   targets; Σp/a moves ≤0.02. It helps keepers' ordering not at all (no attack
   term) and mildly reshuffles mid/DEF. Nothing in this hold-out says λ=0.56
   fixes totals — consistent with volume_test's own caveat that half the
   multiplier is real; if anything the full-season test suggests keeping MORE
   attenuation for FORWARDS (their Σp/a worsens 1.38→1.42 when attenuated,
   because strong-club forwards were the most over-projected) while leaving
   DEF/MID alone. Small samples: 14–25 forwards per target.
3. **The >150 line tracks well** (15 vs 18, 17 vs 18 overall): the stack is not
   systematically crowning phantom 150-point seasons, just inflating everyone.

## Caveats

- **Club attribution**: `season_stat.team` is NULL for every historical row, so
  each player is placed against his CURRENT club's fixture list. In-league
  movers between S and 2026 get the wrong fixtures — undetectable noise that
  biases AGAINST the volume term (conservative for the variant comparison).
  Players whose current club was not in S at all (n=10 in 2023/24, n=9 in
  2024/25) cannot be placed and are excluded.
- No minutes filter on the target side means `mean p/a` is inflated by fringe
  players scoring 1–20 points; use `sum p/a` for level.
- The as-of minutes model keeps production's price-rank pecking-order blend but
  must drop the overlay and the in-season trust term (2026 facts); historical
  start rates are therefore slightly less informed than production's.
- Two targets only, one with a single training season; treat per-position
  numbers (especially FWD, n≤23) as indicative.
- Clean-sheet probabilities come from the as-of Dixon-Coles fit, not the
  market-blended view production uses for upcoming gameweeks.

## Addendum — `anchor_2season` variant (2026-08-23, later same day)

The two-season calibration anchor shipped to `player_model.calibrate()` after
the tables above were generated is now IN the harness (`calibrate(...,
two_season=True)`, third variant row `anchor_2season`), so the shipped change
is reproducible from the repo. It mirrors production exactly as-of each
target: outfield k fitted on the mean pts/38 across the last TWO completed
seasons (latest stint must clear 2,000 minutes; contributing stints clear
900), GKP keeps the single-season k, targets with one training season behind
them fall back to the single anchor — hence bit-identical rows on 2023/24.

Committed-harness numbers for the shipped variant on the 2024/25 target,
superseding the provisional /tmp-driver figures quoted in PREDICTIONS.md's
first draft (FWD 1.28 / Spearman 0.501): **ALL Spearman 0.495 -> 0.502, Σp/a
ALL 1.18 -> 1.15, DEF 1.23 -> 1.17, MID 1.14 -> 1.13, FWD 1.38 -> 1.31,
>150-point count 16 vs 18 actual**. Direction and materiality are unchanged;
where the two drivers differ slightly, these committed-harness numbers are the
source of truth.

