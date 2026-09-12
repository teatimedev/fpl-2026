# End-to-end FPL audit and improvement log

Started 12 September 2026, from commit `8848760`. This is an active implementation
and verification log, not a claim that forecasting can become perfect.

## Acceptance

Inspect every production stage and the research used to justify it. Reproduce
substantive defects, repair them, and test the behaviour that matters. Numerical
changes need chronological evidence or must remain explicitly experimental.
Preserve authentic deadline forecasts and distinguish production verification
from tests, simulations, retrospective proxies and unavailable account state.

Completion requires coverage of the areas below, no unresolved reproducible
correctness defect within the available scope, measured evidence for promoted
forecast changes, and documented limits where additional data is required.

## Coverage ledger

| Area | Status | Evidence / next investigation |
|---|---|---|
| Source ingestion, schema and historical missingness | Pending | FPL, football-data, imported fixture panel, freshness and joins |
| Deadline clock, scheduled execution and concurrent publishing | In progress | Stale `is_next` flag, gate windows, snapshot deadlines, code/data revision coherence |
| Team model and odds | Pending | Chronological fitting, scoring, source timing, promoted/manager priors |
| Player rates, roles and calibration | Pending | Historical clubs, shrinkage, attack scaling, penalty exposure, current season learning |
| Starts, cameos, P60 and availability | In progress | Reproduce conditional cameo and short-start scoring; measure against fixture panel |
| News and scouting | Pending | Article quality, source evidence, expiry, extraction, effect and cost controls |
| Account state, prices, FT and chips | In progress | User-reported Pedro move is not authenticated account access; unknown price pressure denominator |
| Legal XI, bench and captain evaluation | Pending | Exhaustive small cases and realistic squads |
| Transfer planner and policy | Pending | Exact versus proxy scoring, candidate coverage, costs, robustness and empirical evidence |
| Match and decision simulation | Pending | Means, event conservation, dependence, doubles, chips and hits |
| Scorecard, retrospective explanations and replay | Pending | Actual submitted results, immutable forecasts, common cohorts and no future leakage |
| Dashboard and public API | Pending | Runtime, desktop/mobile, stale/mismatched account and network failure states |
| Documentation, maintainability and deployment | In progress | Claims in README predate known limitations; end-to-end production verification required |

## Findings and changes

The repairs immediately preceding this audit are documented separately in
[repair-2026-09-12.md](repair-2026-09-12.md).

### Batch 1: reproduced correctness and operational defects

- The Python clock, news scan, scheduler and browser trusted a stale `is_next`
  flag. They now select the earliest future deadline, including at the exact
  deadline boundary. Missing calendars fail explicitly. A finished season does
  not fabricate GW1. Thursday catch-up now obeys the 45-minute deadline lock.
- CI used `git pull --rebase` after computing forecasts. A run using older code
  could publish its output on top of a newer implementation. Publishing now
  uses an ordinary fast-forward push: remote changes reject the outdated run.
- An FPL 403 or timeout was treated like unpublished picks, potentially walking
  through 37 earlier gameweeks and returning an empty squad. Only 404 permits
  that fallback now. Missing account history cannot imply a verified FT count.
- The public API proxy had no timeout and permitted ten minutes of stale data
  after its one-minute cache. It now limits requests to eight seconds, permits
  GET only, caches successful responses for one minute, and never caches errors.
  The app retries on focus, reconnection, five-minute intervals and rollover.
- Price pressure invented one owner when the manager count or rounded ownership
  was unknown. Those pressures are now null, rankings use net flow consistently,
  and seller/riser lists are sign-correct. No flow is not evidence of locked prices.
- Fitness flags previously reduced starting probability without reducing all
  cameo exposure. Fitness probability now scales both routes to appearing. This
  fixes, for example, a doubtful player at zero availability receiving a cameo.
- Fixed-duration 45-minute starting scenarios earned two appearance points and
  clean-sheet points. They now earn one appearance point and no clean sheet.
  This corrects scoring within the existing approximation; an empirical P60
  distribution still needs separate validation.
- Blank gameweeks previously retained nonzero appearance probabilities and
  minutes despite zero fixtures. They now have zero starts, appearances and minutes.

### Historical data repair

Direct SQL reproduced zero starts, xG, xA and xGC throughout 2022/23 GW1–15,
including 2,818 rows with at least 60 minutes. The importer now marks these
source-specific values unknown. Later observed zeros remain zero. Start
benchmarks exclude unknown target labels and disclose the earlier minutes-based
evidence proxy; rate benchmarks reject partially unmeasured exposure windows.

Fixture-row positions now take precedence over end-of-season metadata; assistant
manager rows are excluded from player datasets. Historical club names cannot
silently fall back to a player's end-of-season club. Exact duplicate rows are
deduplicated, and conflicting duplicates fail rather than silently replacing data.

An offline reimport retained 26,505 / 29,725 / 27,283 / 29,747 player-fixture rows
in 2022/23 through 2025/26. The 2024/25 reduction removes 322 assistant-manager
rows. All available season aggregate minutes reconcile except one existing
2024/25 discrepancy: code 487117 has 385 aggregate versus 368 fixture minutes.
Do not quietly repair that source discrepancy by inventing minutes.

### Verification so far

Focused deadline, data, probability and API regressions pass. The app's test,
lint and build commands pass. In the running browser, a blocked FPL feed removes
live advice and exposes only the labelled dated review; restoring the feed
restores verified inputs. A 390px viewport has no outer horizontal overflow.
The browser's unsupported clock injection was not used; exact rollover is
currently verified through the shared Python and browser-function tests.

Research and numerical model validation remain in progress. Batch 1 was committed
as `35cffc1` and deployed successfully to Vercel. The production browser shows
GW4, the user-reported Pedro transfer, two remaining free transfers, and the
explicitly unvalidated hold-policy wording. Python: 298 passing tests; app: 28,
with lint/build passing. These are implementation checks, not predictive proof.

### Batch 2: captain pairs and incomplete account inputs

Captain selection used the highest individual mean before calculating fallback.
It now compares each captain with their best vice, using the existing independent
appearance assumption. A 5-point player with P(play)=.5 plus a certain 6-point
vice yields 8 extra expected points, versus 6 in the opposite order. Exhaustive
appearance-state tests verify the pair selection. Python, simulator, dashboard,
lineup differences and digest use the same calculation. A goalkeeper is no
longer dismissed as vice solely because of position.

The weekly backend could walk backwards after any API error, load the saved
preseason squad, or assume one FT when history failed. It now falls back only
on an unpublished-picks 404, validates complete picks/bank/history, and stops
if the requested entry cannot be resolved. Unknown selling values cannot fund
a recommendation. The app checks incomplete player feeds, changed availability
percentages, non-finite bank values, invalid deadlines and future build dates.

Forecast price/status/news/availability inputs are compared with fresh FPL data
before the solve and again before publication. A deadline crossed during a long
run cannot publish the old analysis. The rebuilt GW4 report passed both input
checks and was archived at 11:26:23 UTC. Haaland/Saka remains the preferred pair,
with a 0.4-point advantage including fallback. No FPL account changes were made.

This batch passes 308 Python tests and 34 app tests, plus lint/build. A clean
checkout without the ignored database/cache passed 305 tests before the three
market-selector tests were added. Model helper imports no longer fetch the
calendar; the production entry point still requires a future deadline. Pinned
Python dependencies and a read-only GitHub code-check workflow cover later edits.

The browser review caught and fixed a 19px overflow from the new captain table
and a false Mbeumo playing-time warning derived from positive attacking evidence.
The 390px render now has a 390px document width; temporary emulation was cleared.

### Simulator reproductions pending repair

A deterministic defender with 90 minutes and two clean sheets receives 6 rather
than 12 points because fixtures are collapsed before player scoring. Calibration
uses one whole-window residual: targets [3, 9] become means [6, 6]. Additional
open issues include unconserved attacking events, independent player selection,
and unvalidated distribution shapes. Paired simulations do not supply evidence
independent of the forecast means to which they are calibrated.

### Team model and market source reproductions pending repair

`dc_matrix(6, 1, -.2)` and `(3, 3, .2)` produce negative cell probabilities.
The fixed 0–10 score grid gives a home mean of 8.38 when the supplied rate is
12. Optimiser success and finite/valid market odds are not checked consistently.

Historical ingestion selected `PSH/PSD/PSA`, although the documentation called
the benchmark a closing line. Football-data's [column notes](https://www.football-data.co.uk/notes.txt)
confirm these are pre-closing; closing headings add C. The provider also
[warns](https://www.football-data.co.uk/data.php) that its Pinnacle feed has been
unreliable since 23 July 2025. Current code prefers that feed over its market
average and can mix outcomes from different books when individual fields are
missing. A validated line selector and regressions are prepared; ingestion and
benchmark reproduction still need updating. No claim of improved predictive
skill follows from correcting the source labels.

### Playing-time experiment: selected before testing later seasons

Protocol: [playing-time-protocol-2026-09-12.md](playing-time-protocol-2026-09-12.md).
The selected strength was four prior observations with a six-fixture half-life,
chosen on 2023/24 after fitting positional priors on recorded 2022/23 rows.

| Active player component | 2024/25 fixed | 2024/25 candidate | 2025/26 fixed | 2025/26 candidate |
|---|---:|---:|---:|---:|
| Cameo Brier, lower better | .26574 | .20864 | .27061 | .21564 |
| Starter P60 Brier | .06923 | .06377 | .07064 | .06450 |
| Conditional start minutes RMSE | 12.44 | 11.98 | 12.62 | 12.06 |
| Conditional cameo minutes RMSE | 15.19 | 13.49 | 15.69 | 14.17 |

The cameo comparisons contain 7,358 and 7,109 non-starts respectively; P60
contains 8,017 and 8,013 starts. Paired gameweek-block intervals favour the
candidate over the fixed cameo/P60 assumptions in each season. The individual
P60 estimates do **not** show a clear advantage over a positional P60 prior.
Candidate cameo rates remain under-calibrated in the active cohort (.279/.282
predicted versus .379/.386 observed). The minutes comparator is a positional
start mean and fixed 25-minute cameo, **not the full production minutes model**.

This is evidence to develop a forward experiment, not to assert total-points
improvement. Historical availability is unknown, and the earliest recorded
kickoff is a calendar proxy. The selected grid boundary is not proof of an
optimal setting. Frozen selection and later-season evaluation are saved in
`system-audit-2026-09-12/playing-time-*.json`.

### Evaluation and documentation repairs

The two legacy backtests broke ties using arbitrary input order. They now use
average ranks, with tie/permutation regressions. Reproduction gives 2025/26
points-per-90 MAE .728 for the historical model versus .721 for price; rank
correlation .467 versus .444. This remains a survivor-cohort proxy.

The stability study now excludes the unrecorded aggregate DefCon seasons and
uses historical positions when available. Pooled DefCon correlation is .93 on
171 recorded pairs; defender xG correlation is .29 versus .90 pooled. These
correlations do not directly validate new shrinkage constants. Old numeric
production constants remain visible for a separate predictive comparison.

The app footer no longer presents 4,000 draws as independent validation or the
legacy .46 correlation as evidence of the complete live model's skill.

## Evidence boundaries

- GW4 closes at 12:30 UTC on 12 September. Subsequent observations cannot become
  inputs to a GW4 pre-deadline evaluation.
- Public FPL data cannot verify upcoming private transfers or lineup. The user
  reported Thiago to João Pedro; further previously discussed moves have not
  been submitted by this assistant.
- A passing test suite establishes its assertions, not a forecasting advantage.
- Full optimality and an absence of possible future improvements are unprovable.
