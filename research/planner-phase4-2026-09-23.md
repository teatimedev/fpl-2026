# Planner phase 4: terminal value, sampled act-or-hold, honest alternatives, path-based chips

2026-09-23, branch `planner/phase4` (off `revamp/weekly-freshness` @ 1c8c7b7).
GW5 finished, GW6 deadline 2026-10-10. Real run: entry 3415101, 1 FT, £0.4m.

**Evidence status, up front.** Five 2026/27 gameweeks exist and only GW4 has
graded policy benchmarks (every buffer held; 77 pts each). Nothing here is
validated on realised points. The changes are justified on structure (the old
objective ignored value after the window, the old buffer was applied to one
side only, the old tables used the wrong baseline), on published defaults of
the reference open-source solver, and on quantities this project can measure
from its own archive (forecast revisions, lead-time bias). Every tuned number
below says where it came from.

## 1. Terminal value and decay (`v2/planner.py`)

The path MILP now takes a `Valuation` (opt-in; `weekly.py` passes
`production_valuation()`; the default reproduces the old objective exactly,
so `policy_lab.py` and old tests are unchanged). It stays one linear MILP.

| term | value | source |
|---|---|---|
| week weight | 0.9^k for week gw+k (hits too) | FPL-Optimization-Tools `decay_base` 0.9; this model's own lead bias: actual/forecast ≈ 0.94, 0.86, 0.84 at leads 0, 1, 2 weeks (GW2–4), and residual MSE rises 9.9 → 10.3 → 11.2 |
| banked FTs at the horizon | 2nd 2.0, 3rd 1.6, 4th 1.3, 5th 1.1 (concave, undecayed) | FPL-Optimization-Tools `ft_value_list` {2: 2, 3: 1.6, 4: 1.3, 5: 1.1}, adopted as marginal values |
| money in the bank | 0.08 pts per £1m (tie-breaker) | FPL-Optimization-Tools `itb_value` 0.08 |
| squad tail | the horizon squad's next 6 weeks, decayed, as one aggregated block with its own XI, captain and bench weight | `projections_season.json` (same model; inside the window it equals `proj_by_gw` exactly — median ratio 1.000, IQR 1.000–1.000); falls back to the late-window rate if absent |

An attempt to measure FT values in-model (V(ft=k+1) − V(ft=k) of the GW6
squad) was inconclusive: the 120-player solves hit their time limits and the
differences were non-monotone (−0.3, +1.5, +2.4, −0.1). The reference values
were adopted instead; halving them did not change the GW6 decision.

The rescored result carries `objective` (what decisions compare) next to the
undiscounted `total` (what the digest shows), plus `terminal` =
{ft_end, ft_value, bank_end, bank_value, tail_value}.

Also in this item: `squad_evaluator.evaluate_week` now reuses the exhaustive
lineup search's own total instead of re-enumerating absence states in Python
(identical to 1e-9, `tests/test_evaluator_fast_path.py`). Under a profiler the transfer
engine spent ~70% of the old weekly run there; that saving pays for the sampler.
The solver's reported upper bound now includes the objective's constant term.

## 2. Act or hold (`v2/act_or_hold.py`)

Replaces `choose_plan`'s "gain ≥ 2.0 × moves" rule (applied to acting but not
to the hold path's own later moves) with a sample-average approximation of the
two-stage problem: fix this week's squad `a` (one choice for every sample),
re-plan every later week in each sample `s`, and take
`argmax_a mean_s V_s(a)` with hold always one of the `a`, on common random
numbers. This is the FPL-Optimization-Tools community's "sensitivity analysis"
(re-solve under perturbed projections, tally this week's moves) turned into a
paired comparison.

**What a sample is.** The forecast the manager will have at the next
deadline — not the truth. Later weeks are re-planned and scored with it,
which is unbiased if forecasts are martingales; the coming gameweek is not
perturbed. The spread is therefore the one-week forecast revision, measured
from the committed deadline projections (same target GW, players ≥ 2 pts):

| revision | player-level relative sd | within-player sd | P(drop > 50%) |
|---|---|---|---|
| GW2 → GW3 | 0.24 | 0.11 | 4.5% |
| GW3 → GW4 | 0.20 | 0.11 | 1.2% |
| GW4 → GW5 | 0.16 | 0.14 | 2.1% |
| GW5 → GW6 | 0.14 | 0.11 | 0.0% |

Sampler: z ~ N(0, 0.15) per player, replaced by U(−100%, −50%) with
probability 0.02 (news), plus N(0, 0.10) per player-week, clipped at zero.
Revisions include model code changes between deadlines, so early weeks
overstate information. For comparison, the persistent part of forecast
*error* (within-player cross-week residual covariance, GW1–4) is ≈ 0.6
pts/GW (90% bootstrap CI 0.2–0.9), about 0.16–0.37 relative — the same order.

**Why no buffer.** A fixed path is linear in the forecast, so mean-one noise
leaves it unchanged; re-planning is a maximum and gains from noise. A banked
transfer is flexibility to use next week's news, so hold collects its option
value inside the model — and loses it at five FTs, where holding wastes one.

**Choosing.** Best mean, then the one-standard-error rule (Breiman et al.
1984; Hastie, Tibshirani & Friedman 2009, §7.10) on the paired difference:
among actions within one SE of the best, fewest transfers. It guards against
Monte Carlo noise and shrinks with more samples; it is not a points bar.

**Flow in `weekly.py`.** Nominal free and hold paths (120-player pool, refit);
then on one 80-player model: the nominal continuation of the unconstrained
first week and the single/pair shortlist; 6 discovery samples of the free
planner (anything chosen twice joins); hold plus the top 3 by nominal gain on
up to 24 common samples (8 s per solve, absolute MIP gap 0.1 pt, 240 s
wall-clock budget checked between samples, minimum 10). The chosen action's
full path is re-solved on the big model for display. If the sampler fails,
`choose_plan` falls back to the nominal objective with no buffer.

**The 5-FT bug.** The old `elif not unlimited: action_kind = 'hold'` at the
end of the plan block overrode the "you have five, use one" branch; it is
gone. At five FTs hold and act reach the next deadline with the same bank,
so any move with positive expected value wins
(`test_at_five_free_transfers_holding_wastes_one_so_a_small_gain_is_taken`).
On the real GW6 squad with `ft=5` forced (an earlier run, with FT values
1.5/1.0/0.6/0.3 — irrelevant at the cap, where both paths end on five), the
sampler took Sadiki → Gomez at +2.1 (ahead in 10 of 10 samples); at `ft=1`
the same candidates are all −1.2 to −2.5 and it holds.

**Output.** `plan.decision` = {samples, seed, rule, noise, runtime_s,
chosen {in_, out, gain, se, p_beats_hold}, best_move {…}};
`plan.candidates[]` gain = sampled mean vs hold, with `se`, `p_beats_hold`,
`p_best`, `nominal_gain`; `plan.valuation`, `plan.terminal`,
`plan.objective`/`hold_objective`/`nominal_diff`. `move_bar` is only emitted
for the pre-season rebuild. The app's `decisionReason()` now says, e.g.
"Saving the transfer beats every move tested: the best (Sadiki → Gomez) is
−1.4 pts against holding and comes out ahead in only 27% of 22 forecast
scenarios." `decision_version` → `2026-09-23.sampled-act-or-hold.1`.
`decision_replay.freeze` also archives `sampled_act_or_hold` and
`sampled_best_move` so the scorecard can start grading them.

## 3. Honest alternatives

`transfer_engine`'s `gain`/`net` are unchanged (move alone, then no further
transfers) but every tested single/pair now carries `vs_hold` (sampled) and
`vs_hold_nominal` (same continuation model, unsampled), with
`transfers.gain_basis`. The app table has a "vs saving" column (`*` =
unsampled) and "Gain" is relabelled "Gain alone"; the digest adds one
sentence restating the tested moves against holding. Duplicate pair rows
(same two players in either order) are removed.

GW6 example: Sadiki → Gomez shows **+5.5** alone, **+1.0** against holding
nominally, **−1.4** against holding once later weeks can react.

## 4. Chips (`v2/chips.py`)

- Bench Boost, Triple Captain and the Free Hit gap for a future week use the
  squad the selected plan fields that week (its last squad after the plan),
  not today's. The Free Hit budget stays today's.
- The wildcard is solved in every planned week where a copy is available
  (80-player model, one pass each) as the objective gain over the best normal
  path; it is recommended now only if it clears the (unchanged) 20-point
  heuristic and no planned week is clearly better (within 10%).
- **Not in the MILP, deliberately.** BB and TC are easy linear additions, but
  a Free Hit needs a second squad for its week, a wildcard week as a variable
  needs big-M rollover logic, the chip windows run to GW19 (far past a 6-week
  horizon, so an unused chip needs its own terminal value), and the sampler
  would then have to hold chip choices fixed across samples. Runtime and
  correctness risk outweigh the gain while chips are months away on this
  squad; timing stays a separate, labelled heuristic.

## 5. Horizon

Eight weeks was tested by extending projections with the season outlook
(GW12–13): each solve took ~1.7× as long (15 s vs 9 s), the first-week action
was identical (Szoboszlai → Barnes) and the nominal act-vs-hold gain moved
from +0.65 to +0.70. `player_model.py` (not editable here) produces
`gwclock.WINDOW` weeks and the sampler's cost scales with the horizon, so
`WINDOW` stays 6; the terminal tail does the job of the longer view.

## GW6 before and after (entry 3415101)

| | before (HEAD 1c8c7b7) | after |
|---|---|---|
| recommendation | hold ("no path clears the 2.0-point-per-move bar") | hold ("best move −1.4 vs saving, ahead in 27% of 22 scenarios") |
| window points, selected (hold) path | 407.4 | 405.5 (different later moves; objective 485.6 incl. 3.6 FT value and 165.4 tail) |
| best tested moves vs hold | Szoboszlai → Enzo +0.37, Szoboszlai → Barnes +0.18, Sadiki → Gomez +0.14 (window points) | Sadiki → Gomez −1.43 ± 0.42 (nominal +1.02); Szoboszlai → Barnes −2.54 ± 0.39 (nominal +0.31); Raya + Obi → Petrović + Gonzalo −5.4 (sampled planner, with a hit) |
| singles table top line | Sadiki → Gomez +5.5 | +5.5 alone, −1.4 vs saving |
| Obi | kept by the unconstrained path all window; hold path sells him GW8 | hold path sells him GW9 (Obi → Kostoulas, with Szoboszlai → Tavernier on the 2 banked FTs); without the tail term the planner banks to 5 FTs and never sells him |
| last-week moves in the unconstrained path | 2 (GW11) | 2 (GW11: Tarkowski, Obi out) — both now justified by the tail, not one week of points |
| chips | BB hold (GW8 9.4); TC hold (GW36 Haaland 8.8); FH hold (GW16 7.3); WC now +4.8 | BB hold (GW13 12.7 with the planned squad); TC unchanged; FH hold (GW12 4.6); WC by week +6.0, +5.1, +2.0, +1.9, +2.8, +3.6 — hold |
| runtime (M-series laptop, `--no-refresh --plan --chips --json`) | 446 s | 395 s (sampler 252 s of it) |

Seed stability (hold + 4 tested moves, three fresh seeds, 16–21 samples in
the 240 s budget): hold every time. Every move stayed negative
(Szoboszlai → Barnes −1.22/−1.61/−1.31, Sadiki → Gomez −1.28/−1.34/−1.92,
Szoboszlai → Enzo −1.83/−2.13/−1.44, Tarkowski → Guéhi −4.6/−3.9/−4.0; SEs
0.3–0.7), but the ORDER of the near-tied moves changes with the seed, so the
"best move" named in a hold explanation is not stable at this sample size.

Sensitivity of the nominal plan (act-vs-hold gain, unconstrained path):
legacy +0.18; decay only −0.14; production +0.65; decay 0.85 +2.44;
no tail +0.65 (but Obi never sold); FT values halved +0.65. The decay rate
matters most and is the least identified parameter.

## Runtime

Per sample solve ≈ 3 s (80 players, first week fixed, 5 free weeks + tail).
The sampler is wall-clock bounded (240 s + at most one sample), so a slower
GitHub runner gets fewer samples (minimum 10) rather than a longer job. The
evaluator speed-up cut the rest of the run by more than the sampler adds;
end to end the weekly run is ~50 s faster than before locally.

## Open risks

- No realised-points validation; the scorecard now archives the sampled
  decision and the rejected best move to build it.
- Decay and FT values are reference defaults, not fitted here; decay 0.85
  would have flipped the nominal comparison sharply.
- The revision noise model is fitted to four weekly revisions that include
  model code changes; later weeks learn nothing further in a sample.
- Samples use an 80-player pool, no bench-weight refit and a 0.1-pt MIP gap;
  shared by all actions but adds noise. With ~20 samples, SEs are 0.4–0.6.
- Chip timing remains a separate heuristic with unchanged thresholds (now in
  objective units for the wildcard).
