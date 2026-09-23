# Player model, phase 3: availability, club constraints, volume, calibration

Date: 2026-09-23 (GW5 finished; GW6 deadline 2026-10-10). Branch `model/phase3`.
Protocol as in [system-audit-2026-09-12.md](system-audit-2026-09-12.md): a
forecast-changing rule is promoted only if it improves a proper score on
chronologically held-out data (choose on earlier seasons, judge once on later
ones, start-of-season prices, no look-ahead). Bug fixes can ship on unit tests
alone when no backtest is possible, and say so.

| # | Change | Verdict |
|---|---|---|
| 1 | Undated injuries ramp back instead of clearing after one week; doubt decays | **Bug fix, shipped** (ramp fitted on this season's status series) |
| 2 | Start probabilities constrained to 11 per club fixture, keepers to 1 | **PROMOTED** |
| 3 | Attack scaled by fixture xG against the club's own level, `** 0.5` | **PROMOTED** |
| 4 | Calibration k on rate components only; re-fit after 2-3 | **SHADOW** (`FPL_CALIBRATION_SCOPE=rates`); no re-fit |
| 5a | Retro: 60-minute appearance / clean-sheet rules | Bug fix, shipped (no forecast change) |
| 5b | Pecking-order price ties | Bug fix, shipped; measured: start Brier 0.09641 → 0.09505 |
| 5c | the-odds-api books averaged as probabilities | Bug fix, shipped (unit test only) |
| 6 | DefCon stability 0.56 → 0.93 | **PROMOTED** (small hold-out); defender xG 0.29 **rejected** |

Reproduce: `python v2/backtest_inseason.py --club | --volume | --stability`,
`python v2/backtest_totals.py --asof-club`,
`python research/injury_ramp_20260923.py`,
`python research/calibration_scope_20260923.py`.

---

## 1. Undated injuries (bug fix)

**Defect.** `availability.status_for_gameweek()` turned `i`/`s` with no
parseable return date into `a` from the gameweek after the next deadline.
Saliba ("Back injury - Unknown return date") projected 0.84 to start in GW7,
Ekitiké (Achilles) 0.54. Doubtful (`d`) players were also fully fit from the
second gameweek.

**Evidence.** No historical FPL status exists before 2026/27, but every
refresh commit of `data/projections.json` carries status, news and chance:
47 snapshots from 6 Aug to 23 Sep, plus today's database. (`data/price_log`
has status but no news, so it cannot separate dated from undated flags.)
First-choice players (archived deadline `baseline_start` ≥ 0.5) flagged at
the deadline of GW n, followed into GW n+k, starts relative to equally rated
*available* players at the same horizon (which absorbs the baseline's own
drift: 0.92 / 0.86 / 0.81 / 0.78 at +1..+4):

| flag at deadline n | GW | n | started | baseline | relative |
|---|---|---|---|---|---|
| i, "Unknown return date" | n+1 | 41 | 0.02 | 0.72 | **0.04** |
| | n+2 | 31 | 0.10 | 0.71 | **0.16** |
| | n+3 | 18 | 0.17 | 0.72 | **0.28** |
| i, dated, after the stated date | n+1..3 | 9 (4 players) | 0.00 | 0.66 | 0.00 |
| d with a stated chance | n+1 | 9 | 0.56 | 0.80 | ~0.7 |

Maximum-likelihood fit of `P(fit) = 1 − exp(−max(0, days − LAG) / TAU)` on the
calendar gap between deadlines (90 player-weeks, 20 players): **LAG 5 d, TAU
50 d** (TAU profile 95% band 30–120 d); negative log-likelihood 22.7 against
88.6 for the old rule. Undated flags clear with a Kaplan–Meier median of 34
days (50 episodes, 17 cleared). The curve is measured to ~3 weeks and
extrapolated beyond; at +7/+14/+21/+28/+42 days it gives
0.04/0.16/0.27/0.37/0.52.

**Change.** `availability_for_gameweek()` returns (status, fit probability):
the flagged deadline uses FPL's chance as before; a dated absence holds to its
date and then clears (unchanged); an undated injury follows the ramp,
anchored at the flagged deadline (conservative: today is 17 days before the
GW6 deadline, but FPL's chance of 0 refers to GW6); an undated `s` is a
one-match ban; a doubtful flag's missing fitness halves every 14 days (a 75%
flag is 0.82 a week later — n = 9, a documented judgement, not a fit). The fit
probability scales starts and cameos together, as before.

Not changed, but noted: the 4 players with a dated return all failed to start
in the 1–2 weeks after their date (0/9). Too few players to fit a post-date
ramp; the scorecard should watch it.

## 2. Club starts sum to 11 (PROMOTED)

Per-player probabilities do not know that eleven start. Walk-forward panel,
raw Σ P(start) per club fixture (production recency rule, no availability):
mean 12.1 in 2023/24–2025/26 with 100% of fixtures above 11, keepers 1.09
(the part caused by the pecking-order tie bug, item 5b, is shown there).
Production GW6 before: max Hull 12.15 per fixture; keeper sums above 1.02 at
ten clubs, Bournemouth 1.22.

`normalise_club_starts()` holds flag/override rows fixed and shifts every
model-baseline player's log-odds by one constant per club and gameweek so
keepers sum to 1 and outfielders to 10 (a 0.97 starter barely moves, a 0.5
rotation option most). `backtest_inseason.py --club`, next-GW starts, variants
chosen on 2022/23–2023/24, judged once on 2024/25–2025/26 (54,392
player-fixtures):

| variant | Brier | log-loss | minutes MAE |
|---|---|---|---|
| none (before the 5b fix) | 0.09641 | 0.31244 | 17.74 |
| **logit, two-sided, keeper split** (before 5b) | **0.09548** | **0.30733** | **17.38** |
| proportional, capped, split (before 5b) | 0.09629 | 0.31055 | 18.14 |
| none (after 5b) | 0.09505 | 0.30609 | 17.26 |
| **logit, two-sided, keeper split** (after 5b) | **0.09494** | **0.30503** | **17.17** |

Gameweek-block 95% interval of the Brier difference: −0.00123 to −0.00067
before 5b, −0.00021 to −0.00004 after it (the tie fix removed most of the
over-sum, 12.1 → 11.4). Same variant chosen both times. On this season's
archived, availability-aware deadline forecasts (GW1–5, 3,183
player-fixtures; flagged rows held fixed): Brier 0.1021 → 0.0979, log-loss
0.3236 → 0.3144 (capped 0.0983, total-only 0.0987, proportional 0.0993).
Two-sided lifts clubs whose injuries leave them short (Newcastle 10.0 in
GW6), since somebody still starts. `FPL_CLUB_NORMALISE=off` reverts; the
recency/aggregate shadows stay unconstrained so the scorecard's rule
comparison is untouched.

## 3. Team goals counted twice (PROMOTED)

A player's xG/90 already carries his club's level; `× fixture xG / 1.45`
applied it again. `backtest_inseason.py --volume`: per player-fixture xG and xA
**given actual minutes** (so only the attack formula is tested), rates as of
the previous gameweek, fixture xG from the as-of Dixon–Coles fit. Every
variant gets one level constant per metric fitted on 2023/24; λ and the
winner were chosen on 2023/24 (xG deviance), judged once on 2024/25–2025/26
(22,452 player-fixtures):

| variant | xG deviance | xG MAE | xG ρ | xA deviance | att-pts MAE | Σp/Σa |
|---|---|---|---|---|---|---|
| current `xG_f/1.45` | 0.17196 | 0.10125 | 0.541 | 0.09722 | 1.0319 | 1.166 |
| `(xG_f/1.45)^0.25` (best λ) | 0.16587 | 0.09782 | 0.545 | 0.09373 | 1.0217 | 1.097 |
| `(xG_f/1.45)^0.56` (volume_test's λ) | 0.16686 | 0.09889 | 0.546 | 0.09420 | 1.0259 | 1.129 |
| no fixture adjustment | 0.16673 | 0.09732 | 0.540 | 0.09442 | 1.0181 | 1.069 |
| **relative `(xG_f / club mean)^0.5`** | **0.16506** | **0.09661** | **0.546** | **0.09291** | **1.0139** | 1.070 |
| relative `^1` | 0.16600 | 0.09652 | 0.547 | 0.09308 | 1.0096 | 1.069 |
| share of team xG (shrunk) | 0.16840 | 0.10197 | 0.551 | 0.09398 | 1.0428 | 1.202 |
| share, reconciled to club total | 0.16917 | 0.10277 | 0.549 | 0.09419 | 1.0488 | 1.215 |
| rate-share (rates reconciled to club total) | 0.17068 | 0.10307 | 0.544 | 0.09503 | 1.0504 | 1.215 |

Relative `^0.5` also wins each hold-out season separately and on the level-free
("oracle level") deviance. Between players (per-player window totals, ≥ 450
minutes): xG Spearman 0.920 → 0.934, attacking-points Spearman 0.831 → 0.853
over GW2–38; 0.626 → 0.666 over GW2–8.

Club conservation (Σ player xG / fixture xG on the hold-out, before level
constants), weak / mid / strong fixtures: current 0.94 / 1.02 / 1.17;
relative `^1` 1.13 / 1.02 / 0.92; reconciled rules 1.00 by construction.
**Forcing conservation made predictions worse**: the positional shrinkage
lifts weak clubs' players and lowers strong clubs', and reconciling undoes
shrinkage that is right player by player.

Season totals (`backtest_totals.py`). The harness placed every historical
player at his **2026** club. That is look-ahead, not just noise: a player's
2026 club reflects what he did in S. With the default attribution the relative
rule *loses* (Spearman 0.451 → 0.441, 0.479 → 0.437). With `--asof-club` (club
of his first S fixture, from gw_stat) it wins: final stack, all changes in,

| target | anchor_2season (baseline) | + relative volume + club constraint |
|---|---|---|
| 2023/24 ALL Spearman | 0.496 | 0.524 |
| 2024/25 ALL Spearman | 0.452 | 0.474 |
| 2024/25 FWD / MID / DEF | 0.551 / 0.449 / 0.383 | 0.580 / 0.490 / 0.418 |
| >150-point count (actual 18) | 16 / 15 | 12 / 13 |

Σp/a is unchanged (1.19–1.20 / 1.15). **Caveat:** the projected top tail
thins (fewer >150 seasons): premium players at strong clubs no longer get the
second club multiplier that partly offset their shrinkage. Ordering improves;
the absolute level of the very top is lower.

Production: `attack_volume.py`; club mean = mean fixture xG over the whole
season view (every opponent home and away). Rows carry `club_xg` so
`retro.py`, `player_props.py` and `transfer_review.py` rebuild the same attack
term. `FPL_ATTACK_VOLUME=league` reverts.

## 4. Calibration multiplier scope (SHADOW)

k (DEF 1.006, FWD 1.103, GKP 1.102, MID 1.142; frozen at GW2) multiplies the
whole projection, so it also scales appearance points, clean sheets and goals
conceded. The GW1–4 retro shows it: for likely starters the `other` bucket
(appearance/saves/cards at the minutes actually played, × k) is −0.33 a week
for MID and FWD — most of MID's total over-projection (3.44 projected vs 3.13).

`FPL_CALIBRATION_SCOPE=rates` applies k to attack, saves, DefCon and bonus only
(rows now carry `rate_by_gw`). Evidence:

*Season totals, `--asof-club`, k re-fitted per scope on the two-season anchor:*
ALL Spearman 0.524 vs 0.525 (2023/24), 0.474 vs 0.478 (2024/25); Σp/a
identical. *This season's archived forecasts, GW2–4 (GW1 snapshots lack
rates), 687 likely-starter player-weeks, same stored k:*

| pos | k on all: act/proj | k on rates | no k | Δ squared error, rates vs all (±1 s.e.) |
|---|---|---|---|---|
| GKP | 1.002 | 1.079 | 1.104 | +0.11 ± 0.21 |
| DEF | 1.003 | 1.007 | 1.009 | +0.00 ± 0.01 |
| MID | 0.904 | 0.972 | 1.033 | −0.08 ± 0.09 |
| FWD | 0.862 | 0.899 | 0.951 | −0.14 ± 0.14 |

Levels improve for MID/FWD but every squared-error difference is inside one
standard error and keepers get worse: not a clear improvement, so it stays a
flag. **Re-fit:** `--refit-calibration` after items 2–3 would move k to DEF
1.112 / FWD 1.232 / GKP 1.158 / MID 1.134 (the old stack re-fitted today:
1.070 / 1.151 / 1.127 / 1.081). The anchor pulls FWD *up* while this season's
forwards are already over-projected (0.86), so the frozen k is kept. With it,
items 1–3/5/6 raise GW6 likely starters' mean projection by +0.6% GKP, +1.0%
DEF, +1.6% MID, +2.4% FWD.

Harness fix found on the way: `backtest_totals.calibrate(two_season=True)`
returned before applying k when only one training season existed, so every
2023/24 `anchor_2season` row in research/totals-holdout.md was uncalibrated.

## 5. Smaller bugs

- **retro 60-minute rules.** `expected_components()` credited 2 appearance
  points and a clean sheet to every start; `project()` gives a sub-60 start 1
  and none. Fixed for starts and cameos; the forecast mixture (≥ 60-minute
  starts, 25-minute cameos) is unchanged, so decompositions of normal weeks
  are identical. Unit test.
- **Pecking-order ties.** `peers.index(price)` gave every tied player the
  higher slot (two 5.0 keepers were both #1 at 0.92). Tied players now average
  the slots they occupy. Measured on the start harness: hold-out Brier
  0.09641 → 0.09505, log-loss 0.31244 → 0.30609, mean club sum 12.1 → 11.4.
- **the-odds-api.** Books without Pinnacle were averaged as decimal odds (1.5
  and 3.0 → 2.25 = 44% where the books say 50%). Now averaged as implied
  probabilities. Unit test; the provider's quotes are not archived, so no
  backtest.

## 6. Stability constants

`--stability`: rest-of-season rates from prior seasons plus the first n
gameweeks under shrink()'s formula, constant on a grid.

| DefCon/90, outfield | 0.56 (was) | 0.75 | 0.85 | 0.93 |
|---|---|---|---|---|
| select 2025/26, n = 3/5/8/12 pooled wMAE | 1.309 | 1.210 | 1.184 | **1.182** |
| hold-out 2026/27 GW1–2 → GW3–5 (169), wMAE | 1.929 | 1.848 | 1.818 | **1.814** |
| hold-out Spearman | 0.669 | 0.693 | 0.705 | **0.706** |

2025/26 is the only imported season with per-fixture DefCon (the 2024/25 rows
carry none), so the hold-out is this season's first five weeks — small, but
chronological and consistent at every n. **PROMOTED: 0.93** (the k floor of
0.15 binds from 0.87, so 0.87–0.93 are one setting). Not re-validated: the
DefCon hit-probability dispersion that reads the evidence weight.

| Defender xG/90 | 0.29 (audit) | 0.5 | 0.75 | 0.90 (production) |
|---|---|---|---|---|
| select 2023/24 | **best** | | | |
| hold-out 2024/25–25/26 wMAE | 0.0276 | 0.0268 | 0.0269 | 0.0276 |
| hold-out Spearman | 0.263 | 0.282 | 0.305 | 0.316 |

The selected 0.29 does not beat production on the hold-out: **rejected**.

## GW6 before / after (production pipeline, 23 Sep data)

| | before | after |
|---|---|---|
| max club Σ P(start) per fixture | HUL 12.15 (SUN 11.70, FUL 11.67) | 11.00 every club |
| keeper Σ above 1.02 | 10 clubs, max BOU 1.22 | none (max 1.00) |
| Saliba start GW6/7/8 | 0 / 0.84 / 0.84 | 0 / 0.03 / 0.10 |
| Ekitiké start GW6/7/8 | 0 / 0.54 / 0.54 | 0 / 0.02 / 0.07 |
| Σ player xG / team xG, GW6 | MCI 1.34 … COV 0.70 | LEE 1.39, COV 1.39 … AVL 0.82 |

Top 10 by GW6 projection, before: B.Fernandes 6.90, Haaland 6.75, Saka 6.70,
Mbeumo 6.62, Cunha 5.52, Barnes 5.26, Gabriel 5.25, Isak 5.15, Thiaw 5.09,
Ødegaard 5.07. After: B.Fernandes 6.30, Haaland 6.10, Saka 5.92, Mbeumo 5.91,
Thiaw 5.07, Gabriel 5.05, Cunha 5.02, Isak 5.01, Barnes 5.00, Tarkowski 4.94.
Six-week top 5 before: Haaland 45.9, Saka 39.4, B.Fernandes 37.0, Palmer 35.8,
Mbeumo 35.0; after: Haaland 38.3, B.Fernandes 35.4, Saka 34.8, Mbeumo 32.9,
Isak 30.5 (Palmer 30.1: his 75% flag now carries into GW7).

The xG conservation row is the honest limit of item 3: removing the double
count removes the strong-club excess, but the club-agnostic positional prior
now over-sums weak clubs. The harness says that is still the better forecast;
a club-aware shrinkage target is the natural next experiment.

## Open items

- `weekly.py --snapshot` should archive `club_xg` (and, for the calibration
  shadow, `rate_by_gw[gw]`) on each player row. Until it does, `retro.py`
  fills `club_xg` from the current projection for GW ≥ 6.
- `backtest_inseason.py --retro` still replays with the league volume rule and
  the old DefCon evidence constant (it is a classifier replay, not a forecast).
- Dated returns: 0 of 4 players started within two weeks of their date.
- MID/FWD absolute levels remain ~10% high on this season's evidence; the
  calibration question should be re-asked at GW8+ under the P7 feedback guards.
