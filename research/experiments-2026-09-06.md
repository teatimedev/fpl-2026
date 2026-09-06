# FPL model experiments — 6 September 2026

The concern about repeated hold recommendations is justified. The experiment previously used to support the transfer threshold contains an accounting error, and Kluivert's forecast still includes a permanent preseason boost. However, the live evidence does not support treating Thiago's and Kluivert's blanks as the same problem. There is also a promising affordable package involving Foden and Sadiki.

This extends the [original audit](audit-2026-09-06.md). The audit findings and the request to continue with experiments were saved in memory. This round adds reproducible experiments, local planner/rules fixes and 230 passing tests. Forecast challengers remain experimental; the website and FPL account have not been changed.

## 1. What the live evidence says

Refreshed all **653 player histories**, official bootstrap, fixtures and entry 3415101 at **01:31 BST on 6 September**. The research database is isolated from the production database. The player input snapshot is fresh; the team-strength/fixture projections are deliberately frozen at the September 4 production version to isolate player-model changes.

| Player | First three GWs | Minutes | xG | xA | Interpretation |
|---|---:|---:|---:|---:|---|
| Thiago | 0, 2, 2 points | 262 | 2.30 | 0.10 | Poor returns, strong chance volume; one missed penalty is included in xG |
| Kluivert | 3, 3, 1 points | 229 | 0.78 | 0.06 | Modest underlying attack; limited creativity so far |
| Foden | 1, 9, 1 points | 195 | 1.61 | 0.86 | Strong attacking involvement, but latest match was a 24-minute substitute appearance |

Thiago has approximately **0.79 xG/90**, versus Kluivert's **0.31**. Those rates are descriptive small samples, not independent forecasts. Thiago's penalty miss accounts for part of his shortfall; it is not evidence that goals are now owed to him. His continued minutes and chances are the positive evidence. Brentford's official pre-match report also recorded seven Thiago shots across the opening two fixtures.

Kluivert's production attacking rates receive an **18% multiplier** because of a preseason hat-trick including two penalties. It has no automatic expiry or evidence-driven refresh. Removing that multiplier reduces his six-week projection by approximately **1.88 points** in the recency-minutes scenario. We should model penalty opportunities and taker probability directly, rather than boost all goals and assists forever.

The public record still shows no transfers. On that evidence, **GW4 should have four free transfers and £0.0m bank**, unless there are unpublished upcoming transfers. Haaland's GW3 Triple Captain is confirmed in the official picks: nine player points, tripled to 27. These experiments start at GW4 and do not reuse the spent first-half chip.

Sources: [official bootstrap](https://fantasy.premierleague.com/api/bootstrap-static/), [Thiago history](https://fantasy.premierleague.com/api/element-summary/106/), [Kluivert history](https://fantasy.premierleague.com/api/element-summary/70/), [Foden history](https://fantasy.premierleague.com/api/element-summary/398/), [entry history](https://fantasy.premierleague.com/api/entry/3415101/history/), [Brentford preview](https://www.brentfordfc.com/en/news/article/match-previews-brentford-v-sunderland-premier-league-05-09-2026). Snapshot: [live-squad.json](experiments-2026-09-06/live-squad.json).

## 2. Confirmed error in the transfer-threshold experiment

The old `threshold_sweep.py` spent one FT after a transfer but awarded the next week's FT only after a hold. It incorrectly treated one transfer every week as repeated paid transfers.

Correct ordinary-week transition:

```
FT_next = min(5, max(FT_now - transfers, 0) + 1)
hit_cost = 4 * max(transfers - FT_now, 0)
```

Starting with one FT and transferring once in each of three weeks leaves one FT at each following boundary. It does not cause hits in weeks two and three. I corrected both the scalar and vectorised implementations and replaced tests that asserted the faulty behaviour.

I compared the old and corrected rules on **20,000 paired synthetic seasons**, each 38 weeks, at five noise levels. The old zero-noise optimum was 1.25 points; the corrected optimum was zero. At the original hypothetical noise level of 5.16, the old sampled optimum was 2.25 and the corrected one was zero. These results withdraw the claimed experimental validation of a two-point threshold.

They do **not** establish that the live threshold should become zero: with one candidate per week, the corrected toy never needs a hit and cannot model saving transfers for a multi-player package. Also, a player rank correlation does not identify the error distribution of transfer gains.

[Results](experiments-2026-09-06/threshold.json), [experiment](experiments_20260906.py), [official transfer rules](https://www.premierleague.com/en/news/4661029).

## 3. A stronger decision formula, implemented and tested

I built a second experiment with **three opportunities per week**, noisy gain estimates, correct free-transfer accounting and a 38-week horizon. It compares four decision policies using the same 20,000 independently generated evaluation seasons. The Bayesian policies know the synthetic gain/noise distribution; the value function uses a separate 100,000-sample training draw.

The useful replacement for a universal transfer barrier is:

```
Q(action, state) = E[points gained | evidence]
                  - actual hit cost
                  + E[V(next state) | action]

Choose the legal action with greatest Q, including holding.
```

Here, state includes squad, free transfers, money, chips and remaining fixtures. In the implemented synthetic experiment it includes the free-transfer bank and weeks remaining; real squads and chips are future extensions. The future value prices flexibility once. A second fixed charge per move is not a substitute for estimating forecast uncertainty.

| Synthetic policy | Mean simulated gain | Mean transfers | Mean paid transfers |
|---|---:|---:|---:|
| Fixed two-point charge per move | 137.09 | 42.65 | 5.00 |
| Remove the charge, use noisy forecasts directly | 123.13 | 50.15 | 12.23 |
| Posterior expected gains, ignore future flexibility | 134.80 | 39.59 | 1.59 |
| Posterior expected gains + dynamic future value | **145.88** | **38.34** | **0.34** |

The dynamic policy gains 8.79 simulated points over the fixed charge, paired 95% interval **8.66–8.93**. Removing the barrier alone loses 13.96. These are controlled synthetic results, **not projected extra points for Jordan or a historical FPL strategy return**.

The experiment demonstrates why more transfers is not the right objective. Act more selectively, assess packages, and value the FT bank according to circumstances. For six weeks remaining, the synthetic marginal value of successive FTs declines from about 3.15 to 1.56; with one week left, unused fourth/fifth FTs have no value when only three actions are possible.

This is an application of established Bayesian decision theory and dynamic programming, not a claim to have invented those methods. The project-specific opportunity is learning its forecast-error distributions and future squad value from genuine deadline records.

[Results](experiments-2026-09-06/decision.json), [implementation](decision_experiment_20260906.py).

## 4. An adaptive attacking-form formula

I designed and tested a model that accelerates learning when **chance creation** deviates from a player's established level. It avoids using blanks themselves as the trigger.

For each of xG and xA:

```
recent_counts = sum(0.5 ** (matches_ago / half_life) * observed_count)
exposure      = sum(the same weight * minutes / 90)
fast_rate     = (recent_counts + k * slow_rate) / (exposure + k)

surprise      = sum((recent_counts - exposure * slow_rate)^2
                    / (exposure * max(slow_rate, 0.05) + 0.25))
adapt_weight  = 1 - exp(-sensitivity * surprise / 2)
new_rate      = (1 - adapt_weight) * slow_rate + adapt_weight * fast_rate
```

The adaptation weight is a regularised heuristic, not a calibrated probability of a role change. A true regime model would estimate both that probability and its observation noise. The formula is a new project challenger assembled from known shrinkage and change-detection ideas; there is no claim of research novelty or guaranteed superiority.

**Protocol:** train mean calibration on 2023/24, select among 33 candidates on 2024/25, evaluate the selected configuration on 2025/26. Predict actual goal/assist FPL points over the next three gameweeks. Eligible players had at least 90 minutes in the preceding three GWs, with no future-minutes or current-roster survivor filter. Include zeros in the target. Exclude the unrecorded xG period in 2022/23 GW1–15. All candidates share the same lagged minutes forecast.

Validation selected **half-life 12 matches, k=3 equivalent 90-minute matches, sensitivity 1**. On **2,680 held-out player/windows**:

| Attacking component | RMSE, lower better | MAE | Rank correlation |
|---|---:|---:|---:|
| Slow historical blend | 3.2127 | 2.3557 | 0.3503 |
| Selected adaptive formula | **3.2043** | **2.3462** | **0.3506** |
| Fourfold current-season weighting, prespecified comparison | 3.2060 | 2.3272 | 0.3475 |
| Recent realised goals/assists | 4.1769 | 2.5694 | 0.1766 |

The selected formula improves RMSE by **0.26%**, not a dramatic leap. Its paired gameweek-block bootstrap MSE difference interval is **−0.100 to −0.011**. On 420 poor-return windows, RMSE improves from **2.8642 to 2.8503**. Defenders slightly worsen, so a uniform production rollout is not justified. The fourfold blend has a lower MAE but was not the validation-selected RMSE winner; choosing it afterwards on test MAE would undermine the holdout.

Recent realised returns are approximately **30% worse on RMSE** overall. In the poor-return subset, their deceptively low MAE comes with severe downward bias and worse RMSE: predicting nearly zero often looks good on absolute error while missing the rebounds that matter for expected points.

Limits: this is an attacking-component experiment, not the full production pipeline. It omits opposition, market odds, total scoring, injuries, transfers, captaincy and chips. Historical fixture allocation is treated as known, so postponed-fixture timing could leak schedule knowledge. There are only 11 test target blocks, and players repeat across blocks; the bootstrap does not resolve every dependence. Its gain warrants a frozen shadow forecast and broader chronological validation, not promotion as a game-winning model.

[Detailed results](experiments-2026-09-06/form.json), [reproducible experiment](form_experiment_20260906.py), compressed row-level predictions in the accompanying evidence directory.

## 5. Current transfers: individual swaps and packages

I generated six player-model variants and scored single replacements using the actual squad and selling prices. **Production already uses recency minutes**, confirmed both in the source default and the September 4 deadline archive. Aggregate minutes are a control experiment, not the production default. Four decision variants compare: refreshed aggregate minutes; refreshed recency minutes; recency with preseason attacking multipliers removed; and that last configuration plus the adaptive formula. Two further variants isolate incomplete fixture status. These variants test sensitivity; their equal consideration is not a learned ensemble.

**Thiago:** the refreshed aggregate model rates every affordable one-for-one replacement below him over GW4–9. João Pedro (£7.7m) ranges from approximately **−1.50 to +1.05 squad points** across the tested variants. That is a plausible funding alternative, not strong evidence to sell Thiago because he blanked.

**Kluivert:** Barnes (£6.0m) is approximately **+0.66** under the refreshed production recency assumptions and **+2.54** with the old attacking multipliers removed. The aggregate control instead gives **−0.36**. The adaptive version prefers Tavernier (£6.0m), approximately **+2.64**. His place deserves scrutiny, and the old preseason story materially affects the answer.

**Foden:** using only `finished` fixtures misses Saturday's provisionally finished match. Under production recency rules, his start estimate is **91.8%** with that omission and **64.1%** when Saturday's match is included. The corresponding six-week projection changes from **34.74 to 25.05**. The aggregate control gives 84.7% versus 72.6%, a smaller response. The API also supplies zero history rows for Sunday's unplayed fixtures; those were explicitly excluded from the experiment. A production freshness fix must handle both facts together. The recency algorithm itself is already deployed; its access to current evidence is the issue isolated here.

I evaluated **1,969 affordable Thiago/Kluivert replacement pairs** under aggregate assumptions and **1,720** in each of the other two package-search variants. Pools exclude unavailable players and require estimated start probability of at least 40%; these are not exhaustive searches over every registered player.

| Package | Cash remaining | Six-GW squad gain across four variants | GW4 gain across variants |
|---|---:|---:|---:|
| **Foden + Sadiki → Barnes + Tavernier** | £0.0m | **+2.66 to +7.71** | **+0.85 to +1.65** |
| Milenković + Obi → Giles + Evanilson | £0.0m | +2.97 to +3.48 | +0.94 to +1.01 |
| Kluivert + Thiago → Tavernier + João Pedro | £0.3m | −2.38 to +3.68 | −0.17 to +0.89 |
| Kluivert + Thiago → Barnes + João Pedro | £0.3m | −1.93 to +2.63 | −0.47 to +0.29 |

These are legal, affordable static-squad comparisons, assuming four available FTs, with no hits. They include XI, captain fallback and autosub expectations under the existing evaluator. They do not deduct the value of future flexibility or account for future moves.

The Foden/Sadiki package is the strongest shortlist result here because it improves all four variants and upgrades a weak bench slot while addressing rotation risk. It should be assessed against the next team news and remaining GW3 matches before acting. Selling both Thiago and Kluivert is much less stable across assumptions.

The adaptive model also generated an aggressive four-player Haaland-sale package: its static gain ranged from **−10.02 to +9.76** across variants. That instability is a reason to reject that package as a robust recommendation and investigate how the attacking changes interact with the existing team-strength multiplier.

Multiweek planning further tempers the apparent gains. A diagnostic recency-model solve finds **404.1 points when action is allowed versus 403.3 when GW4 is frozen**, a **0.8-point** difference, because the holding path can still transfer next week. Both HiGHS solves reached their optimality tolerance (roughly **0.01% or less** gap on each linear bench objective). Production then applies an additional **four-point barrier for two moves**. This is a clear example of the barrier suppressing a positive modelled path advantage, but the advantage is small enough that model error matters. Linear bench objectives differ after refitting; their bounds are not confidence intervals for the exact rescored gain.

[Single moves and paths](experiments-2026-09-06/transfers.json), [packages and solver diagnostics](experiments-2026-09-06/packages.json), [transfer experiment](transfer_experiment_20260906.py), [package experiment](package_experiment_20260906.py).

## 6. Changes made and next promotion criteria

Local verified fixes:

- Correct FT accrual in the synthetic threshold harness and withdraw its invalid threshold-validation claims.
- Correct wildcard rollover: retain the FT bank unchanged, rather than add an extra FT.
- Add optional original-lot selling prices to the planner. Holding remains feasible, selling loses only the appropriate profit, repurchasing costs full price, and repeated sales do not repeatedly charge the original discount. The research callers supply these prices; ordinary weekly callers still require account-state integration.
- Expose HiGHS termination, objective, bound and gap so small recommendation differences can be assessed honestly. Bounds concern the linear objective, not the exact nonlinear squad rescore.
- Add five selling-price regression tests and correct the existing FT/wildcard assertions. **230 Python tests pass.** Existing SQLite resource/deprecation warnings remain.

Before promoting the forecast challenger: freeze its configuration, fix the fixture/minutes information boundary, test position-specific behaviour and run the full production deadline replay against a simple baseline. Before replacing the hold barrier: learn transfer-gain error, include the terminal value of saved FTs, use common scenario draws for act/hold and evaluate the complete season policy. The dynamic synthetic experiment supports this architecture, but does not supply those missing empirical estimates.

The highest-value AI addition remains a source-linked, timestamped extractor of injuries, selection, penalty duties and role changes that feeds calibrated probabilities. A language model's confident football opinion should not become an unexplained multiplier. Later, competition-specific value functions can include rival ownership, captaincy and chip state to maximise mini-league win probability rather than raw expected points alone.

Reproduction order: `experiments_20260906.py snapshot`, `experiments_20260906.py threshold`, `form_experiment_20260906.py`, `decision_experiment_20260906.py`, `transfer_experiment_20260906.py`, `package_experiment_20260906.py`, using the repository `.venv/bin/python`. Snapshot retrieval is live and will change with time; the frozen forecast artifacts and [hash manifest](experiments-2026-09-06/provenance.json) identify the inputs used for this report. Compressed copies of the team view, bootstrap, fixture list, current per-fixture statistics and calibration are saved in the evidence directory's `inputs` subdirectory. Historical experiments also need the repository's ingested historical database. Re-fetching live data later is not identical to replaying these frozen inputs.
