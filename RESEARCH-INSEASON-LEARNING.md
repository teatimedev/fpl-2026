# In-season learning — review and plan

Date: 2026-08-24 (GW1 finished; GW2 deadline Fri 28 Aug 17:30 UTC)
Scope: how the v2 pipeline turns each gameweek's actual player performance into
the next projection, whether that design is right, and what to change first.
Review only — no code changed. Evidence comes from reading the code and the
archived GW1 snapshot (`data/history/gw1.json`, generated 2026-08-21 16:36Z);
the sandbox blocked live FPL fetches, so GW1 outcomes below are the ones you
reported (Haaland started and blanked, Thiago 0 minutes, Foden 1 point).

## TL;DR

1. **There is a real bug in the one channel that is supposed to react fast.** A
   player with zero minutes this season has no `now` row (`load()` skips
   `minutes == 0`, `v2/player_model.py:134`), so the n/(n+4) start-rate update
   in `minutes_model()` never fires for him. Thiago's benching changes nothing;
   a 5-minute cameo by someone else is penalised. Fix before Thursday's rebuild.
2. **Attacking rates learn far more slowly than the code says.** The comment at
   `player_model.py:49-53` claims a regular's own 2026/27 rates are "~40% of his
   evidence by GW10 and half by GW19". For a four-season veteran the real
   figures are ~10% and ~17%; a full season only reaches ~29%. For a stable
   player that slowness is *correct* (xG/90 repeats at 0.90); for a player whose
   context changed (new manager, new club, new role, new pen duty) it is too
   slow, and the model has no way to tell the two apart.
3. **The minutes update is order-blind and availability-blind.** It reads
   `starts / team games played` from season aggregates. It cannot distinguish
   "benched the last three" from "rested once in six", and it punishes a player
   who was injured (Saka returning from a 10-game absence would sit at ~0.56
   start probability in December while starting every week).
4. **`calibrate()` re-fits every refresh against 2024/25–2025/26 actuals**, so
   any in-season level learning (a higher-scoring league, a better City under
   Maresca) is rescaled straight back out. Ordering learns; levels cannot.
5. **Nothing closes the loop — at either level.** `scorecard.py` will grade
   GW1 on Thursday's CI run, but nothing reads the grade, and the digest never
   mentions the gameweek just played. Model-level feedback is the right thing
   to defer at n=1; the plan says what to monitor now and when (GW8–10) a
   feedback term becomes justifiable. The reader-level loop is cheap and safe
   to close now: P3 classifies why each player diverged (availability /
   minutes / role / variance) and lets the digest say "hold" with the number
   attached.
6. **The highest-leverage single change is infrastructure, not a formula:**
   store the per-gameweek player rows the pipeline already downloads and throws
   away (`element-summary` → `history`, `v2/fetch.py:175-188`). Every fix in
   §3 that needs validating needs those rows, and the DefCon dispersion test
   the README promised needs them too.

---

## 0. Implementation log — 2026-08-25

Every plan item below was built in one pass on 25 Aug. The session that
built it could not execute Python or git (both were permission-gated), so
**nothing here has been run**: the test-suite, `validate.py`, the pipeline
smoke run and the per-item commits are all still to do. What was written was
hand-traced twice (author + four independent read-only reviews) against the
tests; treat the first real run as the acceptance test. The commands, in
order:

```bash
.venv/bin/python -m pytest -q                    # new: test_player_model, test_fetch_gw_stat,
                                                 #      test_retro, test_season_view_decay;
                                                 #      extended: test_weekly_coherence,
                                                 #      test_news_pipeline, test_availability
.venv/bin/python v2/fetch.py                     # fills gw_stat + data/gw_stats.csv (P1 checks print)
.venv/bin/python v2/teams_model.py && .venv/bin/python v2/season_view.py
.venv/bin/python v2/player_model.py              # first run FITS and stores v2/calibration.json (P4)
.venv/bin/python v2/to_csv.py && .venv/bin/python optimise.py --json && .venv/bin/python validate.py
.venv/bin/python v2/scorecard.py && .venv/bin/python v2/retro.py     # GW1 review (rates from current run)
.venv/bin/python v2/weekly.py --no-refresh --plan --chips --snapshot --json --push-file
.venv/bin/python v2/backtest_totals.py           # must be bit-identical to the 23 Aug run
.venv/bin/python v2/import_gw_history.py         # vaastav rows for 2022/23-2025/26 (network)
.venv/bin/python v2/backtest_inseason.py         # --minutes / --rates / --retro
.venv/bin/python v2/season_view.py --validate-decay
```

Suggested commits (one per item; files overlap, so stage with `git add -p`
where two items touch the same file):

| item | status | files | deviations from the spec |
|---|---|---|---|
| P0 | DONE (ran 25 Aug) | `v2/player_model.py` load(); `tests/test_player_model.py` LoadKeepsCurrentSeasonZeroRow, MinutesModelAggregateRule | none. Per-90 rates of a zero row are 0.0 (no division). The "Thiago prints ~0.78" check needs the rebuild. |
| P1 | DONE (ran 25 Aug; vaastav import ran 27 Aug, §0.1) | `v2/fetch.py` (gw_stat, `gw_rows_for`, `check_gw_stats`, `export_gw_stats`), `v2/player_model.py` load()/team_fixtures(); `tests/test_fetch_gw_stat.py` | added a `pos` column (the historical import needs per-season position) and goals/assists/cs/gc/own goals/pens/cards + threat/creativity/influence so the retro's actual side and P5's context split are covered. The row-count check flags only *more* rows than club fixtures (a mid-season signing legitimately has fewer). `--skip-histories` (weekly.py's default local refresh) leaves gw_stat stale — CI always full-fetches. |
| P2 | **MEASURED 27 Aug (§0.1) — production switched to recency K=1, HL=3** | `v2/player_model.py` (minutes_prior/match_evidence/recency_update/aggregate_update/minutes_model, SNAPSHOT_STATUS, MINUTES_RULE), `v2/weekly.py` snapshot(), `v2/scorecard.py`, `v2/import_gw_history.py`, `v2/backtest_inseason.py --minutes`; tests in `test_player_model.py` | Production stays on the aggregate rule (`MINUTES_RULE='aggregate'`, env `FPL_MINUTES_RULE=recency` to switch); the recency rule is computed for everyone and archived as `p_start_recency` next to `p_start_aggregate`, both through the deadline/override layer, and the scorecard grades them side by side (`recency_vs_aggregate_lift`, `recency_wins`). K=4/HL=3 are placeholders: at those values three straight benchings take a 0.9 regular to ~0.56, **not** the <0.4 the plan asks for (K≈1.5 would) — the grid decides. `games_ago` counts all club fixtures, so evidence from before a long absence ages. **Bug found and fixed on the way:** `snapshot()` archived `baseline_start` from the availability-adjusted `start_rate`, so `start_brier_lift` was identically 0; it now archives the minutes model's own `baseline_start_rate`. The vaastav import needs network (`import_gw_history.py`), and availability at past deadlines is not in that data (every fixture counts — the known gap). |
| P3 | DONE (ran 25 Aug; backward replay 27 Aug, §0.1) | `v2/retro.py` (new), `v2/weekly.py` (review section, minutes warning, table notes, verdict clause, push line, `--no-retro`, `J['retro']`), `v2/scorecard.py` (`explain`/stats kept, `retro_class` grouping), `.github/workflows/weekly.yml` (scorecard → retro → weekly), `export_app_data.py`/`app/src/types.ts` (`retro`, `weekly.retro`); `tests/test_retro.py`, `tests/test_weekly_coherence.py` RetroCoherenceTests | `E[xG│minutes]` includes the position's calibration `k` (so the identity holds without a separate calibration bucket); the snapshot's rounding gap is folded into `other` and reported as `recon_gap`, with `unexplained` (should be 0) as the check. GW1's snapshot lacks the rate fields: the review falls back to the *current* run's shrunk rates (`rates_source: current`) — under the 200-minute gate they are unchanged for a regular. A haul whose chance-quality piece is ≥1 point in magnitude falls to `on_model` + `large_residual` under the plan's `│chance│ < 1` gate (not `variance`); worth revisiting after the replay. The xGI window uses a fixture-average volume (per-past-match fixture xG is not archived). The coherence test is at function level (pure functions over a synthetic squad; `eng`/`J['transfers']` deep-equal before and after) plus a check that no review line can be picked up as the verdict — `main()` itself needs the network. |
| P4 | DONE (ran 25 Aug; calibration.json frozen) | `v2/player_model.py` (fit_calibration/apply_calibration/calibrate, `CALIBRATION_MIN_AVAIL`, `--refit-calibration`), `.github/workflows/weekly.yml` (`v2/calibration.json` committed); regression test in `test_player_model.py` CalibrationTests | The stored `k` will be fitted on the **first run after this lands** (GW2 window), not at GW0 — the 23 Aug multipliers were never persisted. The +10% xG regression test runs on a synthetic one-player world with a frozen k=1 (a re-fitting k cannot be demonstrated with one player); it asserts a 3–10% rise. `backtest_totals.py` has its own calibrate() and calls only minutes_model/shrink/positional_priors, whose default behaviour is unchanged — verify bit-identity by re-running it. |
| P5 | **MEASURED 27 Aug (§0.1) — multiplier stays 1.0** | `v2/backtest_inseason.py --rates`, `v2/player_model.py` shrink(current_mult)/context_multiplier/`CONTEXT_CURRENT_MULT`, `v2/manager_changes.py` | The multiplier is a placeholder **1.0** (no behaviour change) until `--rates` has run; position-changed and pen-order-changed triggers are not detectable (the DB keeps neither for past seasons), so context = summer arrival or new-manager club. The historical manager table is a hand list from memory — verify it. `rate_mult` overlay entries are untouched. |
| P6 | DONE; **validated 27 Aug (§0.1)** | `v2/season_view.py` (promoted_prior_weight/manager_shrink/adjust_ratings/build_ratings(n), `--validate-decay`), `v2/teams_model.py` walk_forward_adjusted(); `tests/test_season_view_decay.py` | K_T=30, K_M=15 as specified; IPS's 0.6 decays as 0.6·K_T/(K_T+n). Validation is a CLI (`season_view.py --validate-decay`), scoring only fixtures involving promoted / new-manager clubs, fixed vs decaying. |
| P7 | monitor live since 25 Aug (level_ratio_cum 0.86 after GW1); feedback mechanism built, off | `v2/scorecard.py` (level_ratio per GW, `level_ratio_cum`, ep_next Spearman/MAE), `v2/weekly.py` snapshot() archives `ep_next`, `v2/player_model.py` feedback_blend()/`--feedback` | The digest's "calibration drift" line is not rendered yet (the scorecard summary carries `level_ratio_cum`; add one line to weekly.py once ≥2 gameweeks exist — the review section already prints the graded count). Feedback is opt-in (`--feedback`) and guarded: ≥8 gameweeks, │ratio−1│>10%, weight n/(n+8). |
| P8 | 8.1 DONE (review-only); 8.2 DONE (shadow-only); 8.3/8.4 no code by design | `v2/news_pipeline.py` build_predicted_lineup_overrides + `_semantic_generated` (review rows never trigger rebuilds), `v2/availability.py` `blend_weight`; `v2/player_props.py` (new), `v2/weekly.py` snapshot() `p_goal_model`/`p_goal_market`, `v2/scorecard.py` goals log-loss | Predicted rows are written with `status: review` (skipped by the loader) until `PROMOTE_PREDICTED_LINEUPS = True`; implied start 0.85 / bench 0.15 at blend 0.5. Props need `ODDS_API_KEY` **and** `ODDS_API_PLAYER_PROPS=1`; single-sided "Yes" prices are de-vigged by a fixed 8% margin (two-way when a "No" is quoted). Name matching is full-name then unique surname within the two clubs. |
| P9 | script DONE (unrun); waits for ≥6 rows per player | `v2/defcon_dispersion.py` | Also prints per-match xG sd for attackers → `retro.XGI_MATCH_SD`. |

Also changed: `v2/manager_changes.py` is the single source of `NEW_MANAGER`
(season_view imports it); `v2/README.md` pipeline list.

Found by the read-only reviews and fixed before this log was written:
`predict/components.py` parsed calibrate()'s stdout for `k` (now reads
`calibration_k` from the rows); `project()` never wrote `generation_rule`
into `availability_by_gw`, so the scorecard's `claim_type` group was always
`baseline` (now written — this is what P8.1's promotion test needs);
`teams_model.promoted_prior()` treated a partial season as complete (now
skips seasons under 300 matches, which matters from GW1 onwards because
fetch.py writes this season's results into `match`); the retro's set-piece
fallback compared "now" with "now" for every non-taker (now only for
snapshots that predate the fields); the vaastav import stamped the
season-end club on every row (look-ahead; now the row's own club); the
backtest's pecking-order peers excluded mid-season arrivals; the replay's
prefix sums crashed on mid-season arrivals; `defcon_dispersion.py` had its
id→code map reversed; `weekly.yml` guards the `calibration.json` add until
the file exists and passes the props opt-in env.

Two things the reviews flagged that are left as-is, on purpose:
`backtest_inseason.py --retro` still gates on whether a player has a row in
GW n+1..n+3 (whether he was still in the game — marginal post-n
information, noted in its docstring); and the digest's per-position
"calibration drift" line (P7) is not rendered until two gameweeks are
graded.

## 0.1 Measurements — 2026-08-27

Everything in §0 was run on 27 Aug (tests, rebuild, vaastav import,
the three backtests and the decay validation). The import gives four seasons
of per-GW rows — 113,582 in `gw_stat` — whose minutes reconcile with
`season_stat` for 1,181 of 1,182 players. The numbers below are what the
in-season constants now rest on; the commands are in §0.

### P2 — minutes rule: recency wins, decisively (`--minutes`)

107,801 "starts in GW n+1" predictions, 2022/23–2025/26, Brier:

| rule | all | GW2–8 | GW9–24 | GW25–37 | prior start ≥ 0.7 |
|---|---|---|---|---|---|
| prior only | 0.207 | 0.217 | 0.208 | 0.201 | 0.344 |
| aggregate (production until 27 Aug) | 0.118 | 0.110 | 0.120 | 0.120 | 0.174 |
| recency K=2 HL=3 | 0.101 | 0.097 | 0.103 | 0.101 | 0.147 |
| **recency K=1 HL=3 (production now)** | **0.095** | **0.085** | — | — | **0.135** |
| recency K=0.5 HL=2 (grid edge) | 0.091 | 0.080 | — | — | 0.127 |

Recency beats the aggregate rule in every phase and every prior-start band,
and keeps improving to the smallest K and shortest half-life in the grid: the
last one or two games are nearly all the evidence that matters. The harness
cannot see availability (every club fixture counts), so part of the edge is
the rule "predicting" a continued absence that production's availability
layer already handles — hence one step inside the optimum. Production is
`MINUTES_RULE='recency'`, `RECENCY_K=1`, `RECENCY_HALF_LIFE=3`, with minutes
per start on their own unmeasured trust (`RECENCY_MPS_K=4`). The aggregate
rule is still archived in every snapshot; `scorecard.recency_vs_aggregate_lift`
is the forward check, and K=0.5/HL=2 is the next step if it holds over ≥ 4
gameweeks.

### P5 — context multiplier: nothing to buy (`--rates`)

Rest-of-season xG/90 from the first n gameweeks, wMAE (Spearman):

| n | context | prior only | blend m=1 | blend m=2 | current only |
|---|---|---|---|---|---|
| 3 | stable | 0.0671 (0.445) | **0.0654** (0.453) | 0.0659 | 0.0882 |
| 3 | changed | 0.0667 (0.656) | 0.0612 (0.699) | **0.0610** (0.701) | 0.0764 |
| 8 | stable | 0.0730 | **0.0703** | 0.0705 | 0.0828 |
| 8 | changed | 0.0688 | **0.0592** (0.710) | 0.0600 | 0.0731 |

The plain blend already learns for movers (prior-only → blend is the whole
gain); m=2 adds 0.0002 at best and m ≥ 5 is worse everywhere. xA/90 says the
same. `CONTEXT_CURRENT_MULT` stays 1.0.

### P6 — decaying team adjustments: validated (`--validate-decay`)

526 fixtures involving promoted or new-manager clubs, 1X2 log-loss:
fixed 0.9723, **decaying 0.9615** (bookmaker 0.9463). K_T=30, K_M=15 stay.

### P3 — the classes, replayed on 2023/24–2025/26 (`--retro`)

| class | n | next-GW start | resid next 3 GW | 3-start xGI window err | prior err |
|---|---|---|---|---|---|
| on_model | 27,298 | 0.39 | −0.3 | 0.099 | 0.066 |
| variance/finishing | 1,788 | 0.81 | +1.4 | 0.124 | 0.084 |
| variance/team | 3,021 | 0.87 | +0.1 | 0.052 | 0.032 |
| minutes_loss/dnp | 3,026 | **0.23** | −4.1 | 0.113 | 0.062 |
| minutes_gain | 3,240 | 0.68 | +2.8 | 0.100 | 0.075 |
| role_change/xgi | 740 | 0.85 | +0.1 | **0.435** | 0.121 |

Hold-vs-swap after a `variance` week (n=4,959): holding beats the best
same-position, same-or-cheaper alternative minus the hit by **+2.0 points
over three gameweeks** (holding wins 58%). A fit player benched once starts
next week 23% of the time — the sell signal is real and it is the benching,
never the blank. The three-start xGI window is 3.6× worse than the prior
for `role_change` — it stays a flag, never an input. 2022/23 is skipped
(no team fit before it).

### Fixed on the way

- `backtest_inseason.py:170` — `NameError: peers` in `--minutes` (passed the
  undefined name instead of the players dict).
- `validate.py` priced squads against `data/bootstrap.json` (the 6 Aug v1
  dump) while the optimiser used this run's `v2/cache/bootstrap.json`; the
  first refresh after prices unlocked failed on a £0.1m mismatch. It now
  prefers the cache like `export_app_data.py`.
- `should_refresh.py` — GitHub dropped every hourly run between 23:46 and
  10:00 UTC on Thu 27 Aug, so the 06:00–09:00 weekly slot never ran. Any
  Thursday hour qualifies now, a missed T-24h is taken late, and done windows
  fall through to the news cadence.
- `weekly.py` — the transfer verdict speaks with one voice. It is judged on
  XI + captain gain ("on the pitch"; auto-sub cover is shown but does not
  decide), and when the planner ran and says acting now is worth less than
  the hold threshold, it supersedes a "transfer" verdict with a hold that
  names the queued move. GW2's digest had recommended Milenković → Gvardiol
  +3.7 (+1.3 XI, +2.5 cover) two paragraphs above a planner saying hold; on
  the recency rule the same move is +4.1 on the pitch over the window but
  acting now rather than next week is worth +1.0 — hold, Gvardiol queued.

## 1. How learning works today

### 1.1 The refresh cadence (what "each week" means)

`.github/workflows/weekly.yml` fires hourly; `v2/should_refresh.py:33-49`
promotes a run to a full rebuild at T-24h and T-2h before each deadline, plus a
Thursday 06:00–09:00 UTC safety refresh; between T-30h and T-45m it runs cheap
news scans that only rebuild if an owned player's availability changes. A full
rebuild is `fetch.py → teams_model.py → season_view.py → player_model.py →
to_csv.py → optimise.py → weekly.py --snapshot --price-log → movers.py →
scorecard.py`.

So the first rebuild that sees GW1 is Thursday 27 Aug (weekly window or T-24h,
whichever the cron hits first). Nothing in the repo has learned from GW1 yet:
`v2/projections_v2.json` was built on 23 Aug from a database fetched on
21 Aug 12:48, before the deadline.

### 1.2 What arrives each refresh (`v2/fetch.py`)

| source | what it writes | in-season learning role |
|---|---|---|
| `bootstrap-static` | `player` (price, status, news, chance, pen order) and, once a GW is live, **one `season_stat` row per player for 2026/27** holding the API's running season totals (`load_current_season`, `fetch.py:229-295`) | the only player-level in-season evidence the model sees: season aggregates, no per-match rows |
| `fixtures` | finished 2026/27 scores → `match` rows (`fetch.py:279-292`) | the team model refits on them |
| `element-summary` × 572 | `history_past` → four seasons of aggregates (`fetch.py:175-188`) | **the per-round `history` array in the same response is discarded** |
| football-data / the-odds-api | forward 1X2 and O/U 2.5 odds → `market` | blended into fixture xG at 80% when posted (`season_view.py:110-117`, `teams_model.market_view`) |

Aggregates only: after GW5 the model knows Foden has 3 starts and 210 minutes,
not which three, nor whether the two non-starts were benchings or absences.

### 1.3 Attacking and defensive rates: `shrink()` (`player_model.py:213-244`)

For each of `xg90, xa90, dc90, bonus90, saves90, yellow90`:

```
own    = Σ_seasons SEASON_WEIGHT[s] · mins_s · rate_s  /  Σ SEASON_WEIGHT[s] · mins_s     (seasons with ≥200 min)
n_eff  = Σ SEASON_WEIGHT[s] · mins_s / 2200
k      = (1 − stability) / stability          (xG: 0.111; xA: 0.19; DefCon: 0.79; DEF bonus: 6.1)
est    = w·own + (1−w)·positional prior,  w = n_eff / (n_eff + k)
```

`SEASON_WEIGHT = {22/23: 0.30, 23/24: 0.50, 24/25: 0.75, 25/26: 1.0, 26/27: 1.0}`.
The current season is one more row, weighted by minutes like the rest, and it
does not enter at all until the player has **200 minutes** this season
(`player_model.py:227`) — roughly GW3 for a starter.

How much of `own` the current season is, at ~75 minutes a game:

| player | prior weighted minutes | GW5 | GW10 | GW19 | GW38 |
|---|---|---|---|---|---|
| Haaland (4 seasons; 2,953 in 25/26, earlier seasons assumed ~2,500–2,800) | ≈7,100 | 5% | 9.5% | 17% | 29% |
| Thiago (1 season, 3,282 — exact) | 3,282 | 10% | 21% | 30% | 46% |
| one prior season of 1,000 min | 1,000 | 27% | 43% | 59% | 74% |

The code comment's "40% by GW10, half by GW19" describes the third row, not a
regular. Because `w` is already ≈0.96 for any established player, the
positional prior plays no part in-season; the current season's influence is
exactly its minutes share above.

`evidence` (the `w` for xG) also sets the DefCon negative-binomial dispersion
(`defcon_hit_prob`), so it tightens marginally as minutes accrue — a small,
sensible effect.

`positional_priors()` includes 2026/27 rows once they pass 450 minutes,
minutes-weighted, so the prior moves negligibly in-season. Fine.

### 1.4 Minutes: `minutes_model()` (`player_model.py:248-319`)

In order:

1. Past seasons: `starts/38` per season, `SEASON_WEIGHT`-averaged; minutes per
   start likewise (current season excluded, `:257`).
2. Blend with a price-rank pecking-order table for the club/position; trust on
   the observed rate is capped at 0.30 for summer arrivals and 0.15 for
   non-first-choice keepers.
3. `overlay.py` `mins` entries replace the result outright (pre-season
   research, keyed by element id, static since 6 Aug).
4. **This season overrides everything above with weight n/(n+4)**
   (`CURRENT_TRUST_K = 4.0`, `:310-317`): `rate_now = starts_now / team games
   played`, `start_rate = trust·rate_now + (1−trust)·start_rate`; minutes per
   start blended the same way when `starts_now > 0`.

   trust after n team games: 1 → 0.20, 2 → 0.33, 4 → 0.50, 8 → 0.67, 12 → 0.75.

   `K = 4` is a judgement — nothing in `stability.py` or the backtests measures
   it. The 0.46 year-over-year stability of starts justifies shrinking *last
   season's* rate; it says nothing about how fast *this season's* first few
   starts should be believed.

Then per gameweek (`project()`, `:430-441`): the FPL status/news/chance flag is
applied to the deadline gameweek only (`availability.deadline_start_probability`;
dated returns extend it), and `availability.json` / `availability.generated.json`
overrides replace `p_start`, `p_cameo` and minutes for explicit gameweek
ranges. This override layer — manual research plus the news pipeline's
`explicit_absence_v1` rule — is the genuinely fast channel, and it is
human/rule-driven, not learned.

### 1.5 The team layer

`teams_model.py` refits Dixon-Coles on every refresh with a 280-day half-life
(`DEFAULT_HALF_LIFE`, `:318`), so each 2026/27 result enters at weight 1.0
against decayed history. This *is* a working closed loop: after GW1 City's
attack rating already reflects the Bournemouth result. Bookmaker odds override
80% of the fixture xG when posted.

Two adjustments in `season_view.py:34-37` do **not** learn:

- `MANAGER_SHRINK = 0.80` pulls ten clubs 20% to the mean for the whole season,
  even after 20 matches under the new manager are in the fit.
- `PROMOTED_BLEND = {COV: 1.0, HUL: 1.0, IPS: 0.6}` — at 1.0 the fitted rating
  is **discarded entirely** (`build_ratings`, `:71-74`), so Coventry and Hull
  sit on the generic promoted prior all season regardless of results.

### 1.6 Calibration: `calibrate()` (`player_model.py:537-606`)

Every run, per position: `k = mean(actual pts/38 over 2024/25–2025/26 for
players with 2,000+ min last season) / mean(proj_gw for those same players)`,
clipped to [0.7, 1.45], applied to everyone. Two in-season consequences:

- **It pins the level of the established cohort to history.** If the team model
  learns the league is scoring 10% more, or the minutes model learns City's
  starters are playing more, `proj_gw` rises, `k` falls, and the level is
  restored. Only within-position ordering survives.
- **The denominator includes availability.** An established player out for the
  whole window (dated return) projects ~0, dragging the cohort's `proj_gw`
  down and inflating everyone else in the position. Three long-term absences
  in a 60-player defender cohort is ~5% on every defender.

### 1.7 Scorecard: `scorecard.py`

`weekly.py --snapshot` archives per-player `proj`, `p_start`, `p_play`,
`expected_minutes`, `baseline_start` and the availability source/confidence
before each deadline; `scorecard.py` grades finished gameweeks: Spearman over
likely starters and the pool, MAE/bias, deciles, captain/XI vs yours vs
hindsight, clean-sheet Brier, start/appearance Brier, minutes MAE/bias, and
Brier by availability source tier and claim type. `data/scorecard.json` is
empty because no graded run has happened yet; the docstring is explicit that
"nothing here changes the model" (`:21-23`), and nothing in `weekly.py` or
`player_model.py` reads it. Nor does the digest look backward at all: it is
built from `proj_by_gw[gw-1:]` onward, so the gameweek just played appears
nowhere in it (P3).

### 1.8 Worked trace: what Thursday's rebuild will do with GW1

Pre-deadline state from the snapshot and `projections_v2.json`:

| | p_start GW1 (source) | baseline start | start min | xG/90 | xA/90 | evidence | seasons | GW1 proj |
|---|---|---|---|---|---|---|---|---|
| Haaland | 0.92 (override) | 0.839 | 87.1 | 0.787 | 0.076 | 0.96 | 4 | 7.26 |
| Thiago | 0.97 (baseline, capped) | 0.97 | 88.7 | 0.557 | 0.053 | 0.91 | 1 | 5.24 |
| Foden | 0.80 (override) | 0.771 | 88.2 | 0.286 | 0.232 | 0.94 | 4 | 5.06 |

**Haaland — started, blanked (assume 90 min).** `now` row exists. Minutes:
`rate_now = 1/1`, trust 0.2 → `start_rate = 0.2·1 + 0.8·0.839 = 0.871`;
`mps = 0.2·90 + 0.8·87.1 = 87.7`. The GW1 override expires. Attack: 90 minutes
is under the 200-minute gate, so `xg90` is **unchanged**; once he passes 200
minutes his GW1–3 xG enters at ~3% weight. Team: the City result is in the DC
fit. Net: his GW2 projection goes *up* ~3% because he started. That is the
correct response to one blank — finishing variance should not move a 0.79
xG/90 estimate backed by 7,000 weighted minutes.

**Thiago — 0 minutes.** `load()` drops the zero-minute row (`:134`), so
`p['now'] is None` and the this-season block at `:311` is skipped. His start
rate stays **0.97**, projection unchanged at ~5.0/GW. The intended mechanism
would have given `0.2·0 + 0.8·0.97 = 0.78`; after two benchings 0.65, three
0.55, four 0.49. If FPL flags him (`status` i/d with `chance`), the deadline
path handles GW2 only; an unflagged, healthy, unused striker is invisible to the
model until he plays a minute. This is the case that matters most for transfer
decisions and it is exactly the one the code ignores.

**Foden — 1 point.** Two readings, and the model treats them very differently:

- cameo (say 15 min, no start): `rate_now = 0`, → `0.8·0.771 = 0.617`, minutes
  per start unchanged; GW2 projection falls ~20%.
- start subbed before 60': `rate_now = 1` → `0.2 + 0.8·0.771 = 0.817`;
  `mps = 0.2·55 + 0.8·88.2 = 81.6`; projection rises slightly.

So a substitute appearance is penalised harder than a total non-appearance
(Thiago). The asymmetry is the bug in §2 W1, not a design choice.

**Your GW1 (46 vs 36 average).** The model's captain (Haaland) and XI match the
snapshot's `model` block, so Thursday's scorecard will grade captain model =
yours = Haaland's score, and `xi.best` will show the hindsight ceiling. That
grade is one observation; treat it as such.

---

## 2. Weaknesses, ranked by expected impact on weekly transfer decisions

Impact is judged by: how often the case occurs per week across a 15-man squad
and the transfer pool, and how many points a wrong projection costs before the
model catches up.

### W1 — Zero-minute non-appearances never reach the minutes model (bug) — HIGH

`player_model.py:134` (`if not mins: continue`) → `:147` (`p['now']` is None)
→ `:311` (block skipped). Affects every benched or unused player until his
first minute. This is the "lost his place" case, which is the single most
valuable thing an in-season model can detect a week early. It also silently
breaks the intended behaviour for new signings who are not yet playing (their
pecking-order guess persists untouched). Until P0 lands, P3's review is the
only place a healthy non-appearance shows up at all; after it lands, the
review is what makes any recurrence visible as a disagreement between the
retrospective and the transfer table.

### W2 — The start-rate update is order-blind and availability-blind — HIGH

`rate_now = starts / team games` (`:312`) treats all games equally and counts
games the player was injured, suspended or not yet signed as non-starts.

- Lost place: after three consecutive benchings a nailed starter still sits at
  0.55; the truth is ~0.1–0.2. Eight-plus games to fall below 0.4.
- Returning from injury: out GW1–10, starts GW11–20 → `10/20 = 0.5`, trust
  0.83 → **0.56** for a player starting every week. Applies to Saliba,
  Ekitiké, Kulusevski, Mitoma, Ferguson and every mid-season injury.
- Mid-season signing: games before `joined` count as non-starts.
- `K = 4` is unvalidated, and one number cannot be right for both "rested once"
  (should barely move) and "dropped" (should move a lot). Sequence carries the
  information; the aggregate throws it away. P2 puts the sequence into the
  number; P3 puts it into the digest ("benched 2 of the last 3") from the
  first week.

### W3 — Per-gameweek rows are downloaded and discarded (infrastructure) — HIGH

`fetch.load_histories` reads `history_past` only. The `history` array in the
same 572 responses has, per round: minutes, starts, points, xG, xA, xGC,
DefCon, BPS, bonus, saves, cards, opponent, home/away, kickoff. Without it:
W1/W2 cannot be fixed properly, in-season learning cannot be backtested on
this season as it accumulates, the README's DefCon dispersion test
(`v2/README.md:127-129`) cannot run, and the code comment "can be, after a few
gameweeks of 2026/27" stays false. Zero extra requests to fix.

### W4 — `calibrate()` re-anchors levels to the past on every run — MEDIUM-HIGH

§1.6. Two effects: in-season level learning is cancelled (only ordering
survives), and long-term absences among the anchor cohort inflate their
position. The second is a slow drift that gets worse as injuries accumulate
through autumn and the fix is cheap. The first matters most for captaincy and
"is a 4-point hit worth it" decisions, which are level questions.

### W5 — No context-aware attacking-rate update — MEDIUM

§1.3. For stable players slow learning is right and any "form" term would be
worse (goals/90 stability 0.82 vs xG/90 0.90; assists 0.59 vs xA 0.84). But
the model applies the same near-zero in-season weight to players whose rates
were earned in a different context: Semenyo (moved wide), Foden (Maresca's
central pillar), Anderson (Forest → City DefCon), Rogers (Villa → Chelsea),
Thiago (one season, new-ish), every promoted-club player. The overlay's
`rate_mult` is a static August guess for these; the data that would correct it
arrives weekly and is used at ~1–3% weight. Penalty duty is the sharpest
example: `penalties_order` is refreshed every fetch, stored, exported — and
never used in the projection.

Honest limit: whether first-n-GW xG/90 improves rest-of-season prediction
over a multi-season prior, and by how much for movers vs stayers, is an
empirical question the repo cannot currently answer. Plan item P5 answers it
before anything ships; until then P3 surfaces a change of penalty or set-piece
duty, and a three-start xGI shift, as "reassess" without moving a rate.

### W6 — Team-layer adjustments that never relax — MEDIUM

`PROMOTED_BLEND = 1.0` discards Coventry's and Hull's fitted ratings all
season; `MANAGER_SHRINK = 0.8` is constant. Thirteen clubs' clean-sheet and
volume terms are affected — that is most defenders and keepers in the game.
The DC refit itself learns correctly; the post-fit adjustments then partly
undo it.

### W7 — Open loop from the scorecard — MEDIUM (model feedback deliberately deferred; digest feedback simply missing)

Right default today. Per-position level bias is the one thing worth feeding
back eventually; the noise floor says not before ~GW8–10 (§4). Cheap monitoring
should start now so the decision at GW8 is made on data.

There is a second loop, cheaper and not deferred: the reader's. The digest
rates transfers on forward projections and says nothing about the gameweek
just played, so the reader supplies the explanation — and the explanation a
reader supplies for a blank is rarely "finishing variance, hold". P3 closes
that loop without touching the model: classify why each player diverged, put
selection and duty changes first, and label variance as variance.

### W8 — Fast external signals unused — LOW-MEDIUM

- Predicted line-ups: strongest weekly minutes signal; used only through
  manual `availability.json` entries (five GW1 rows) — `news_extract.py:25`
  already extracts a `predicted_start` claim type, but `news_pipeline.py`
  only applies explicit absences (`explicit_absence_v1`), so it stays
  review-only.
- Player anytime-goalscorer odds: a per-player market estimate of P(goal) that
  folds in team news, role and pens. Not fetched.
- FPL's own `ep_next`: a free public benchmark projection; not archived or
  graded.
- Price and ownership: reactive to points and news, not predictive of them.
  Useful for effective-ownership/captaincy risk and for the price-change model,
  not as a projection input. No action recommended beyond continuing to log.

### W9 — Small correctness issues — LOW

- `mps_now = mins/starts` (`:316`) includes cameo minutes in the numerator.
- `games_played()` counts fixtures finished with a home score; a postponed
  fixture is fine, but a player whose team has played fewer games gets a lower
  `trust`, which is correct only by accident.
- `SEASON_WEIGHT['2026/27'] = 1.0` equals last season; a slight recency
  preference (1.0 vs 0.85) is defensible but is not the lever — minutes share is.

---

## 3. Plan

Ordered by (impact ÷ effort) with the dependency that P1 unlocks most of the
rest. Each item names the file/function, the validation that must pass, and
rough effort for one person.

### P0 — Fix W1 before Thursday's rebuild

**Change.** `player_model.load()` (`:128-148`): stop skipping the current
season's zero-minute row — keep it when `season == CURRENT` (past-season
zero rows can still be dropped). Guard the two consumers: `shrink()` already
requires ≥200 minutes (`:227`) and `positional_priors()` ≥450 (`:184`), so
neither is affected; `minutes_model()` (`:311`) needs `now['mins'] == 0` to be
treated as `starts = 0`, which it already does via `now['starts'] or 0`.
Confirm `fetch.load_current_season` writes the row with `starts = 0`, not
`None`, for unused players (it writes `p.get('starts')`; check the API returns
0 rather than null for a player with no appearance).

**Validate.** Unit test in `tests/`: a player with a 2026/27 row of 0 minutes /
0 starts after 1 team game gets `start_rate = 0.8 × history` — and a player
with no row at all is unchanged. Then rebuild and check Thiago's `start_rate`
prints ~0.78 and his GW2 projection falls accordingly.

**Effort.** 1 hour. **Risk.** None; it restores documented behaviour.

### P1 — Persist per-gameweek player rows (unlocks P2, P3, P5, P7, P9)

**Change.** `fetch.py`: new table `gw_stat(code, season, round, fixture_id,
opponent, was_home, kickoff, minutes, starts, points, xg, xa, xgc, defcon,
bps, bonus, saves, yellow, red, PRIMARY KEY (code, season, fixture_id))`
filled from `element-summary.history` inside `load_histories()` (same
responses, no new requests). CI's database is rebuilt from scratch each run
but `history` returns every round played so far, so nothing is lost between
runs; optionally export `data/gw_stats.csv` so the app and offline analysis
have it without the DB.

Also: keep `data/history/gw{n}.json` snapshots as they are — they already
record each player's deadline `status`, which is the "was he available"
signal P2 needs.

**Validate.** Row counts = Σ players × rounds played; spot-check Haaland's GW1
row against the FPL site; sum of `gw_stat.minutes` equals the bootstrap
season total for every player.

**Effort.** Half a day.

### P2 — Rebuild the in-season minutes update on per-match evidence (W2)

**Change.** `minutes_model()`: replace the `starts / n_games` block with a
recency-weighted, availability-conditioned start estimate:

```
for each 2026/27 team fixture i (most recent first):
    available_i = played_i  or  (snapshot status at that deadline was 'a' and not overridden to 0)
    if not available_i: skip                      # injury/suspension/pre-join games are not evidence
    w_i = 0.5 ** (games_ago_i / HALF_LIFE)         # HALF_LIFE ≈ 3 games to start
    s_i = 1 if started_i else 0
rate_now = Σ w_i s_i / Σ w_i;   n_eff = Σ w_i
start_rate = trust · rate_now + (1 − trust) · prior,   trust = n_eff / (n_eff + K)
```

Keep `K` and `HALF_LIFE` as module constants next to `CURRENT_TRUST_K` with the
validation result quoted in the comment. Minutes per start from `gw_stat`
rows where `starts = 1` only (fixes W9's cameo contamination). Behaviour to
check by hand: three straight benchings for a 0.9 starter should land below
0.4; one rest in six should stay above 0.8; a returning long-term absentee
should be judged only on games since return.

**Validate.**
- *Forward, free:* `scorecard.py` already reports `start_brier` against
  `baseline_start_brier` per gameweek. Add the new rule's probability to the
  snapshot as a third column (`p_start_recency`) so both rules are graded
  side by side from GW2 onward without switching production until the new
  one wins over ≥4 gameweeks.
- *Backward, needs external data:* the repo has no historical per-GW rows.
  The public vaastav `Fantasy-Premier-League` dataset has per-GW rows
  (minutes, starts, xG, xA, points, bps) for 2022/23–2025/26, keyed by
  per-season element ids with `players_raw.csv` carrying `code`. Import into
  `gw_stat` for past seasons (verify the `starts` and `expected_*` columns
  exist per season on import; treat any season without them as minutes-only)
  and write `v2/backtest_inseason.py`: for every GW n ≥ 2 and every player,
  predict "starts in GW n+1" from (a) the current rule, (b) the recency rule
  over a grid of K ∈ {2,4,6,8} × HALF_LIFE ∈ {2,3,5,∞}; score Brier and
  log-loss; report by n (early vs late season) and by prior-season start band.
  Availability at each historical deadline is not in that dataset, so the
  backward test can only condition on "played" — note it as a known gap and
  let the forward scorecard settle the availability-conditioned version.

**Effort.** 1–2 days including the data import. **Small-sample note.** Four
seasons × ~300 regular players × 36 predictions ≈ 40k observations — enough
to fix K and the half-life to within a factor of two, which is all that is
needed.

### P3 — GW-Retrospective Module (W1, W2, W5, W7)

**What it is.** A post-gameweek review pass that runs once per finished
gameweek, before the next digest is written, and answers one question for
every player in the pool: *why* did his actual score differ from the archived
projection — and does that reason carry information for next week. It never
changes a projection or a transfer number. It changes what the digest shows
first and what it says next to each name, so that the reader reacts to
minutes and role changes and does not react to finishing.

Today the loop is open at two levels. The model-level loop (feeding grades
back into `k`) is deferred on purpose (P7). The reader-level loop is simply
missing: `weekly.py` rates transfers against forward projections only,
`scorecard.py` grades the past but nothing reads the grade, and the digest
that arrives on Thursday does not mention that Haaland blanked or that Thiago
did not play. The reader fills that gap from memory and from social media,
which is the worst available source. This item fills it from the data.

#### 3.1 The review pass

Inputs, all of which exist or are already scheduled:

| input | where | what it supplies |
|---|---|---|
| the belief | `data/history/gw{n}.json` (`weekly.py snapshot()`) | per player: `proj`, `p_start`, `p_play`, `expected_minutes`, `baseline_start`, deadline `status`, availability source/confidence; per team: the fixture's `xg`, `xgc`, `cs` |
| the outcome | `gw_stat` rows for round n (P1); `event/{n}/live/` `explain` as the fallback until P1 lands | minutes, starts, points, xG, xA, xGC, DefCon, BPS, bonus, goals, assists, cards, opponent, home/away; `explain` gives the exact points-by-stat breakdown |
| the world now | `bootstrap-static` at run time (already fetched) | current `status`, `news`, `news_added`, `chance`, `penalties_order`, corners and free-kick order |
| the new belief | `v2/projections_v2.json` after this run's refit | next week's `proj_by_gw`, `start_rate`, `xg90`, `xa90` — so the digest can say which way the projection moved, and why |

Two groups of snapshot fields have to be added for the expected side to be
reconstructible: the shrunk rates (`xg90`, `xa90`, `dc90`, `bonus90`,
`evidence`) with the per-position calibration multiplier `k`, and the
set-piece orders (`pens`, `corners`, `fk`) so a change can be detected.
`snapshot()` has all of them in hand (`weekly.py:439-457`); it just does not
write them. GW1's snapshot lacks them, but the 23 Aug `projections_v2.json` is
committed and pre-deadline, so GW1 can be backfilled once.

Scope is the whole pool, not the squad: every snapshot row with
`p_play ≥ 0.3` or `proj ≥ 0.5` (about 350 players). The squad's residuals
only mean something next to the pool's (3.5), and the transfer targets are in
the pool.

For each player the pass reconstructs the projection's components with the
formula in `project()` (`player_model.py:467-490`) — attack, clean sheet,
goals conceded, saves, DefCon, appearance, bonus, cards, all × `k` — and sets
them against the actual points by stat. The residual is then split so that
each piece maps to a cause:

| component | definition | what it is evidence of |
|---|---|---|
| minutes | projection re-evaluated at the actual start/minutes, minus the projection | selection and fitness — the fast signal |
| chance quality | `(xG − E[xG │ actual minutes]) · goal pts + (xA − E[xA │ minutes]) · 3` | role and volume — slow |
| finishing | `(goals − xG) · goal pts + (assists − xA) · 3` | noise (goals/90 0.82 vs xG/90 0.90; assists 0.59 vs xA 0.84) |
| team | clean-sheet and goals-conceded points against the fixture's `cs` / `xgc` | team-level noise; the DC refit already reads the result |
| DefCon | 2 × (hit − P(hit │ actual minutes)) | noise at n = 1 — a count either side of a threshold |
| bonus | bonus − `bonus90 · share · 0.85` | noise (DEF 0.14) |
| other | remainder: saves, cards, own goals, penalties | usually small; if it is not, the decomposition is wrong |

The pieces sum to `actual − proj` by construction; that identity is the first
test. `E[xG │ actual minutes]` is `xg90 · minutes/90 · fixture xg / 1.45` — the
fixture is already inside the expectation, so a low xG at Anfield is not
"chance quality down" unless it is low *for that fixture*.

#### 3.2 Classification — diagnose before recommending

Each player gets exactly one class, evaluated in this order. The order is the
point: a benched player's zero xG is not a finishing signal, and an injured
player's non-appearance is not a selection signal.

| class | trigger | evidence in one gameweek | digest action |
|---|---|---|---|
| `unavailable` | `status ≠ 'a'` at the deadline, or an override with `p_start < 0.2`; or `status ∈ {i, s, d, u}` or `news_added` after the deadline now; or a red card | none needed — the availability layer owns this | nothing new: the availability warnings and "check before the deadline" already carry it |
| `minutes_loss` | `p_start ≥ 0.6` at the deadline, `starts = 0`, healthy then and now. Sub-types `dnp` (0 minutes) and `cameo` (> 0) | high — the within-season start sequence is the fastest real signal there is; P2's rule will take a 0.9 starter to ~0.75 after one and ~0.55 after two | **top of the digest**. One occurrence: "check" (a regular is rested about one game in eight). Two consecutive: "sell-grade" |
| `minutes_watch` | started but ≤ 60 minutes, healthy; or `0.35 ≤ p_start < 0.6` and did not start | low — early hooks are usually tactical; two consecutive → treat as `minutes_loss` | one line under "check before the deadline" |
| `minutes_gain` | `p_start ≤ 0.4` at the deadline, `starts = 1`, healthy | moderate — the same signal in reverse | the pool's "breakout minutes" list |
| `role_change` | played ≥ 60 and either: first-choice penalty, corner or free-kick taker changed against the snapshot; or the last three starts (≥ 60' each) have xGI/90 outside the player's own 80% band | pens: a discrete fact, act on it at n = 1 (surface, do not re-rate). xGI drift: **nothing at n < 3**; "reassess" at n = 3; the rate itself moves only through P5's measured multiplier | "reassess" list; a pen change ranks next to `minutes_loss` |
| `variance` | played ≥ 60 (or ≥ expected minutes − 15), none of the above, `│chance quality│ < 1.0` pt and `│finishing + team + bonus + DefCon│ ≥ 2` | ~none — at 0.90 stability one match moves a regular's xG/90 by 1–3% (§1.3) | a "held despite blank" or "hauled on low xG" note; explicitly no action |
| `on_model` | residual within ±2 and none of the above | — | none |

Where the thresholds come from. `p_start ≥ 0.6` is the scorecard's own
definition of a likely starter (`scorecard.py:138-139`). 60 minutes is FPL's
appearance-points line and `started_outcome()`'s fallback (`:95`). ±2 points
is about two-thirds of a starter's weekly standard deviation (≈ 3; the
arithmetic is in P7). The 80% band for xGI/90 needs the per-match xG
dispersion, which the repo cannot measure until P1 has a few weeks of rows —
a regular attacker's per-match xG standard deviation is roughly 0.4 from
public data, so over three starts a 0.5 xGI/90 player sits at about 1.5 ± 0.9;
treat the band as a placeholder to be replaced by the measured figure. Every
threshold above is a starting point for the backtest in 3.4 to move, not a
finding.

Sample-size limits, stated once so the digest can quote them. Under the
model's own numbers a 0.5 xG/90 forward scores nothing in about 61% of his
starts (e^−0.5), and blanks three in a row 22% of the time while nothing
whatsoever is wrong. Haaland at 0.79 xG/90 and ~87 minutes blanks in about
46% of starts: GW1 was the median outcome, not a surprise. One gameweek of
finishing is therefore never a class trigger, and three are not either; the
only attacking quantity the classifier reads at n = 1 is a discrete change of
duty. Minutes are different in kind: whether a healthy regular started is a
fact, not a sample, and starts have 0.46 year-to-year stability precisely
because managers change their minds mid-season. That asymmetry — react to
selection, ignore finishing — is the whole classifier.

Sequence, not snapshots. The pass keeps a rolling class history per player
(`gw{n}_retro.json` carries the last six), so the digest can say "benched 2 of
the last 3" rather than "benched". That is the qualitative twin of P2's
recency-weighted rate; once P2 ships the digest prints both.

#### 3.3 How the classes reach the digest

The rule is that the retrospective changes ordering and wording, never
numbers. `transfer_engine()` and `evaluate_squad()` are untouched;
`J['transfers']` and `J['decision']` must be bit-identical with and without
the retrospective (a coherence test, 3.4). If a `minutes_loss` player's
projection has barely moved, the digest says so — that is a signal that W1/W2
are still open, and hiding it would be worse than showing it.

New section, placed directly under the deadline line, before the captain:

```
## GW1 in review  — what happened, and what it does and does not change

Act on
- **Thiago** — 0 minutes, healthy (status a; deadline start estimate 97%).
  First non-start of a regular: check Friday's presser. Two in a row is
  sell-grade. Projection GW2: 5.2 → 5.0 (W1: the model has not registered
  the benching). Best same-position move if it comes to that: X (+1.8).

Hold — variance, no action
- **Haaland** — 90', 0.8 xG, 0 goals, 2 pts (proj 7.3). Finishing.
  Projection GW2: 7.3 → 7.5, up because he started.

| player  | proj | actual | mins |   Δ  | minutes / chance / finishing / team / bonus | class                |
|---------|------|--------|------|------|---------------------------------------------|----------------------|
| Haaland |  7.3 |      2 |   90 | −5.3 | +0.1 / +0.2 / −4.2 / −0.9 / −0.5            | variance             |
| Thiago  |  5.2 |      0 |    0 | −5.2 | −5.2 /  —   /  —   /  —   /  —              | minutes loss (dnp)   |
| Foden   |  5.1 |      1 |   15 | −4.1 | −3.9 / −0.1 /  0.0 /  0.0 / −0.1            | minutes loss (cameo) |

Pool
- breakout minutes (started at ≤ 40%): …
- set-piece duty changed: …
- hauled on low xG — do not chase: …
- blanked on good xG — unchanged as targets: …
```

(Illustrative: the GW1 xG figures are not in the repo and the decomposition
numbers are placeholders.)

Then three annotations elsewhere in the digest:

- **Transfers.** A `minutes_loss` owned player gets a **Minutes warning**
  line ahead of the singles table, in the same shape as the existing
  availability warning, naming the best same-position replacement and its
  gain — *even when the gain is under `HOLD_THRESHOLD`*. The verdict line is
  not overridden: "Recommended: hold" stays if that is what the numbers say,
  followed by "— but Thiago is a check-first case (above)". Any player in the
  out or in column whose class is not `on_model` gets a one-line note under
  the table: "Milenković — variance (90', conceded 2 against a 31%
  clean-sheet expectation)". An incoming player who hauled gets "hauled on
  low xG (2 goals from 0.4); projection unchanged".
- **Check before the deadline.** `role_change` and `minutes_watch` join the
  list with what changed ("now first on penalties, was second"; "hooked on
  58' and 61' in consecutive starts").
- **Push summary.** One line: `Last GW: Thiago benched (healthy) — check ·
  Haaland blank = variance, hold`.

Pool highlights are capped at five per list and ordered by projected points
from GW n+1 to the horizon, not by the size of last week's residual — the
lists exist for transfer relevance, and ordering by residual would be the
very loop this item is built to prevent. `J['retro']` carries the whole
thing for the app.

Wording rules, because the wording is the product: a blank is always printed
with its xG; a haul is always printed with its xG; the next-week projection is
always printed with its direction and a one-clause reason; "check" and "sell"
are distinct words tied to one and two consecutive non-starts; the word
"form" does not appear.

#### 3.4 Where it runs, what it touches, how it is validated

**Placement.** `v2/retro.py --gw n` runs after `player_model.py` / `to_csv.py`
and before `weekly.py`, so the digest for GW n+1 can read GW n's review. That
means moving `scorecard.py` earlier too: it runs last today
(`weekly.yml:109-111`) but only needs last week's snapshot and the API, so the
chain becomes `fetch → teams_model → season_view → player_model → to_csv →
optimise → scorecard → retro → weekly --snapshot → movers`. It fires on the
first full rebuild after FPL marks GW n `finished` — Thursday's window in a
normal week — and re-runs idempotently at T-24h and T-2h. Classification needs
minutes, starts, xG and xA, all final at full time, so it does not wait for
`data_checked`; bonus does, and is re-scored when the actuals cache flips to
`checked`, exactly as the scorecard does.

**Files.**

| file | change |
|---|---|
| `v2/retro.py` (new) | the pass: load snapshot + outcome + bootstrap + new projections; decompose; classify; write `data/history/gw{n}_retro.json` (per player: components, class, sub-type, note, six-week class history) and roll `data/retro.json` (per-class counts and, once there is data, per-class next-week residual) |
| `v2/weekly.py` | `snapshot()` also archives `xg90`, `xa90`, `dc90`, `bonus90`, `evidence`, `k`, `pens`, `corners`, `fk`; `main()` renders the review section, the minutes warning, the table notes and the push line from `gw{gw-1}_retro.json` when present; `--no-retro` |
| `v2/scorecard.py` | `actuals_for()` keeps the `explain` breakdown and the stat fields it already downloads, so the actual side of the decomposition is exact and cached; a fourth `availability_groups` dimension, `retro_class`, grouping next-week residuals by the previous week's class — the forward validation, reusing the grouping code that already exists (`:197-218`) |
| `v2/fetch.py` | with P1's `gw_stat`, also keep `threat`, `creativity` and `influence` from the same `history` array — the cheapest available proxy for *where* the chances came from, if the three-start xGI window turns out to need one |
| `.github/workflows/weekly.yml` | the reorder above; `retro.py` between scorecard and weekly |
| `export_app_data.py` / `app/` | expose `data/retro.json` and `J['retro']`; rendering is a later job |
| `tests/test_retro.py` (new) | fixture-driven classification: the three §1.8 players (Haaland → `variance`; Thiago → `minutes_loss/dnp`; Foden at 15' → `minutes_loss/cameo`, at 55' as a starter → `minutes_watch`); an injured DNP → `unavailable`, not `minutes_loss`; a pen-order change → `role_change`; precedence when two triggers fire; the decomposition identity |
| `tests/test_weekly_coherence.py` | `J['transfers']`, `J['decision']` and the model XI/captain are identical with and without a retro file present |

**Validation.** Three layers, cheapest first.

1. *Identity and agreement with the scorecard (free, from GW2).* Components
   sum to `actual − proj` for every player; the pool sum of residuals over
   likely starters equals `bias_starters × n_starters` in
   `data/scorecard.json` for the same gameweek. Two graders that disagree
   mean one is wrong.
2. *Forward, shadow (free, GW3 onward).* The `retro_class` grouping in the
   scorecard answers, week by week: after `minutes_loss`, what fraction
   started next week (the number that should set the "check" / "sell"
   wording — expect 0.4–0.6 after one, well under 0.3 after two); after
   `variance`, is the next-week residual ≈ 0 (if it is consistently negative
   the class is hiding a real signal and the thresholds are too loose); after
   "hauled on low xG", is the next-week residual ≈ 0 (the empirical basis for
   "do not chase"). By GW8 there are ~2,000 classified player-weeks, enough to
   see the sign of each; the digest prints "retrospective classes: n weeks
   graded" so the reader knows how far to trust the wording. It ships as
   annotation from the first week because it moves no numbers — the same
   standard the workflow notes set for generated availability
   (`WORKFLOW-NOTES.md:58-62`): observe first, promote on measured Brier.
3. *Backward, on the P2 import (September).* `backtest_inseason.py --retro`:
   replay the classifier over 2022/23–2025/26 with availability approximated
   by "played" (the known gap from P2) and report per class: next-GW start
   rate, next-3-GW points residual, and the rest-of-season xGI/90 error of the
   three-start window versus the multi-season prior (shared with P5's
   measurement — same rows, same question). Then the one policy simulation
   that quantifies 3.5: for every `variance` player-week, the next-3-GW points
   of holding him against the best same-position, same-price-or-cheaper
   alternative by as-of projection, minus 4. The expectation from the
   stability numbers is that holding wins by roughly the hit; the backtest
   turns "roughly" into a number the digest can quote.

Any future use of a class to change a projection — feeding `role_change` into
P5's `current_mult`, say — waits for layer 3.

**Effort.** `retro.py` one day; snapshot fields, digest rendering, CI reorder
and tests one day; the backtest mode one day on top of P2's import. Depends on
P1 for the rolling windows; the `explain` fallback covers a single gameweek
without it. The first digest that can carry it is GW3's (the first rebuild
after GW2 finishes, Thursday 3 Sep).

#### 3.5 Guardrails — why the naive loop loses points

The obvious design — "my players underperformed, re-review them" — is the one
this item is built to prevent, and it is worth being explicit about why,
because it is also the instinct of every reader on a Sunday evening.

- *Selling on a blank has no positive expected term.* At 0.90 stability a
  blank moves a regular's expected xG/90 by a percent or two; his expected
  points next week are essentially what they were. The transfer costs the hit
  (−4 unless free), the replacement's own regression if he was chosen because
  he just hauled, and the transfer that could have gone on a real minutes
  case. Two blanks in a row happen to a correctly projected 0.5 xG/90 forward
  37% of the time; a reader who sells at two blanks makes that mistake several
  times a season at −4 each, before counting what the replacement does. The
  README's finding that goals/90 (0.82) predicts next season's goals worse
  than xG/90 (0.90) does, and assists (0.59) far worse than xA (0.84), is the
  measured version of this.
- *Buying on a haul is the same mistake with the sign flipped.* A player who
  scored twice from 0.4 xG is bought at the top of his noise. The pool review
  prints the xG next to the haul so the digest is symmetric: bad luck is not a
  sell, good luck is not a buy.
- *Reviewing only your own squad is a confirmation-bias machine.* Fifteen
  players produce fifteen residuals a week and some will be large; without
  the pool as the control group every large one looks like a pattern.
  Classifying the pool with the same rules is what makes "your variance
  residuals are indistinguishable from everyone's" a checkable statement.
- *"Re-review" is not a neutral act.* It is a prompt to find a reason, and one
  can always be found — a tough fixture, a knock, a tactical tweak. The
  classifier fixes the list of admissible reasons in advance (availability,
  selection, duty, a three-start xGI shift) and the order in which they are
  checked, so a divergence can only be called variance after the real causes
  have been excluded — and cannot be called anything else before they are.

How the design enforces it: precedence, so nothing real hides under
`variance`; the projection is never touched, so the loop has no path to the
numbers; the "check" / "sell" split ties action to a count of facts rather
than a feeling; every note carries the number that argues against reacting
(the xG, the projection's direction); and the pool lists are ordered by next
week's projection, not last week's residual.

What it does not protect against, said plainly: a genuine role change that
shows up only as a slow xG drift will be called variance for two more weeks —
that delay is the price of not reacting to noise, and at 0.90 stability it is
the right trade. And a real minutes loss that the projection fails to register
(W1 today) will appear as a disagreement between the review and the transfer
table; the review is right to show it, and the fix belongs in the model, not
the digest.

### P4 — Freeze calibration at its pre-season value; clean the anchor cohort (W4)

**Change.** `calibrate()`: compute `k` per position once from the pre-season
(GW0) projection, store in `v2/calibration.json` alongside the anchor seasons
and cohort size; on later runs apply the stored `k`. Add `--refit-calibration`
to `player_model.py` for deliberate re-fits. When fitting, exclude players
whose window `proj_gw` is depressed by status/overrides (use the
availability-free projection for the ratio, or drop anyone with
`start_by_gw` mean < 0.6 of `baseline`).

**Validate.** `backtest_totals.py` must be bit-identical (it computes `k`
as-of and does not touch the stored file). Add a regression test: injecting a
+10% multiplier into every fixture xG in `season_view.json` must raise every
projection ~+5–8% (attack share) instead of ~0%. Then watch the scorecard's
`bias_starters` over GW2–8: with a frozen `k` it becomes a meaningful drift
measure; with the re-fitting `k` it was partly measuring the anchor.

**Effort.** Half a day.

### P5 — Measure, then add, a context-aware in-season attacking-rate weight (W5)

**Measure first** (`v2/backtest_inseason.py`, on the P2 import): for each past
season and each n ∈ {3, 5, 8, 12} gameweeks, predict rest-of-season xG/90 and
xA/90 with (a) the current multi-season blend, (b) the blend with the current
season's minutes weight multiplied by m ∈ {1, 2, 3, 5, 10}, (c) current season
only. Score MAE and Spearman, split by *context changed* (club changed since
the prior season — vaastav rows carry the team — or club in the new-manager
set; a hand table of manager changes for 2022–25 is a 20-minute job) versus
*stable*. Expected outcome given the 0.90 stability: m ≈ 1 wins for stable
players and m ≫ 1 wins for changed context, but the size is unknown and that
size is the whole decision.

**Then change** `shrink()`: accept a per-player `current_mult` (1.0 default;
the measured value when `joined ≥ 2026-05-01`, `team ∈ NEW_MANAGER`, position
changed, or `pens` changed since last season) applied to the 2026/27 row's
weight. Retire the manual `rate_mult` overlay entries the data now covers.

Penalties: do not build a pen model yet — the API does not split xG or goals
by penalty. Log `penalties_order` per gameweek (P3's snapshot fields do this)
so a mid-season change of taker is surfaced by the retrospective's
`role_change` class, and revisit once the P5 measurement shows whether role
changes are worth chasing at all.

**Effort.** 2 days (1 measure, 1 implement). Do not ship without the
measurement — this is the item most likely to be "vibes" if rushed.

### P6 — Let the promoted blend and new-manager shrink decay with evidence (W6)

**Change.** `season_view.build_ratings()`: `w_prior = K_T / (K_T + n_matches)`
for promoted clubs and `shrink = 0.8 + 0.2 · n / (n + K_M)` for new-manager
clubs, `n` = 2026/27 matches in the fit. Starting values from precision
arithmetic rather than taste: the promoted prior's measured spread is 0.158
(attack, `predict/promoted.py`) → prior precision ≈ 40; each match adds ≈ 1.2
units of Fisher information on a log-rate, so the data reaches half weight at
≈ 33 matches — `K_T ≈ 30`, and the current 1.0-forever is wrong only in the
limit, which is why nobody has noticed. `K_M` has no measurement behind it;
start at 15 and let the validation move it.

**Validate.** `teams_model.walk_forward()` already exists; add a variant that
applies the post-fit adjustments as production does and scores only matches
involving promoted / new-manager clubs, for fixed vs decaying blends, over
2022/23–2025/26 (manager-change table needed, same one as P5). Log-loss
against the Pinnacle line is the yardstick, as it is for the base model.

**Effort.** 1 day.

### P7 — Close the loop carefully: monitor now, feed back at GW8+ (W7)

**Now.** `scorecard.py`: add per-position `sum(actual)/sum(proj)` over likely
starters per gameweek and cumulatively, and archive FPL's `ep_next` in the
snapshot so its Spearman is graded next to the model's (the in-season
equivalent of `naive_price` — if the model cannot beat it, that is the
finding). `weekly.py`: one "calibration drift" line in the digest from the
cumulative ratio, with the gameweek count, no action attached.

**Later (GW8–10).** If the cumulative per-position ratio sits outside ±10%
with ≥8 gameweeks, blend the frozen `k` (P4) with the observed ratio at
weight `n_gw / (n_gw + K_C)`, `K_C ≈ 8`. Why 8: a starter's weekly points have
sd ≈ 3 and a position cohort has ≈ 60 starters, so one gameweek's cohort mean
has sd ≈ 0.4 on a mean of ≈ 4 — a 10% level error is 1σ per gameweek, 2σ at
four gameweeks, 3σ at nine, and within-gameweek fixture correlation makes it
worse. Anything faster than K_C ≈ 8 is reacting to fixture luck. Validate the
constant on the P2 import (weekly cohort ratios across four seasons give the
actual sd) before relying on it.

**Effort.** Half a day now; half a day at GW8.

### P8 — Fast signals worth adding, and the ones to leave alone (W8)

1. **Predicted line-ups as low-confidence generated overrides.** The news
   pipeline already stores `predicted_start` claims as review-only
   (`WORKFLOW-NOTES.md`). Promote them to a generated tier that sets `p_start`
   to a blend of the model's own rate and the source's implied rate (e.g.
   0.5/0.5), never overriding an explicit absence, expiring at the deadline.
   Validation is already built: `scorecard.availability_groups.claim_type`
   Briers by rule; keep it review-only until two graded deadlines show
   positive `start_brier_lift`, exactly as the workflow notes prescribe for
   nuanced claims. Effort: 1 day.
2. **Anytime-goalscorer odds.** the-odds-api exposes per-event player props
   (`player_goal_scorer_anytime`) for `soccer_epl`; ~10 requests per
   gameweek, within the free tier if the existing key's plan includes props
   (check first). Convert to de-vigged P(goal), compare to the model's
   `1 − exp(−xg90 · minutes/90 · vol)` and blend in log-odds at a weight fit
   to the season's goal-scored log-loss. This is the only way to get a
   market view on a *player* rather than a fixture, and it prices in exactly
   the role/pen/line-up news the model lacks. Pure forward validation — no
   history exists — so run it shadow-only for a month. Effort: 1–2 days.
3. **Price / ownership.** Leave as is. `--price-log` and `movers.py` are the
   right scope: effective-ownership for captaincy risk and the price-change
   model. Transfer flow is a lagging summary of news and points, not
   information the projections lack.
4. **FPL `form`.** Do not add it. It is a 30-day points average — finishing
   plus fixtures — and the stability study is the reason not to.

### P9 — The DefCon dispersion test (README promise) — once P1 has ≥6 gameweeks

Compare Poisson vs negative-binomial fit of per-match DefCon counts around each
player's shrunk mean (`gw_stat.defcon`), and re-derive the `r = 4 + 11·evidence`
line in `defcon_hit_prob` from the measured over-dispersion. Effort: half a
day once the data exists.

### Sequence

Week of 24 Aug: P0 (today), P1, P4, P7-monitor. Week of 31 Aug: P2 (forward
shadow column first, backward harness alongside) and P3 (annotation-only,
first carried by the GW3 digest). September: P5 measurement, P6, P8.1, P3's
backtest mode on the P2 import. October: P5 decision, P7 feedback decision at
GW8, P8.2, P9.

**Status 2026-08-25:** all of the above is written (see §0); what remains is
running it — tests, the rebuild, the vaastav import and the three backtests —
and then the decisions the measurements are for: K/HALF_LIFE, the context
multiplier, K_M, the retro thresholds.

---

## 4. What should and should not move after one or two gameweeks

| signal | move now? | mechanism | status |
|---|---|---|---|
| started / benched while available | **yes, strongly, with recency** | P0 + P2; surfaced by P3 | broken (W1), then order-blind (W2); invisible in the digest |
| sub timing, minutes per start | yes, moderately | P2; P3 `minutes_watch` | aggregate only, cameo-contaminated |
| FPL flag, dated return, explicit club absence | yes, for the flagged deadline | availability layer | working |
| penalty / set-piece order change | yes (surface), model later | P3 `role_change`; P5 note | ignored |
| team result | yes, small | DC refit | working; post-fit adjustments do not learn (P6) |
| xG/xA per 90, stable context | **no** — one game is noise against a 0.90-stability prior | shrink() | correct |
| xG/xA per 90, changed context | a little, measured | P5; P3 "reassess" at three starts | not distinguished |
| goals vs xG (finishing), bonus, clean sheets | **never directly** | P3 labels it `variance` | correct: these are the noise the design was built to ignore — and the digest should say so |
| positional level bias | not before GW8 | P7 | open loop, and currently cancelled by calibrate() (P4) |
| price, ownership, transfers | no | — | correctly ignored |

Haaland's blank belongs in the "never" row; Thiago's benching belongs in the
first row and currently lands nowhere. That is the whole review in one line —
and P3 is the item that makes the digest say exactly that, every week.

## 5. Validation infrastructure: what exists, what is missing

- `v2/backtest.py` — season-level pts/90 rate estimators, ≥900-minute survivors.
  Cannot see in-season behaviour.
- `v2/backtest_totals.py` — as-of season totals; the right harness for
  calibration levels (P4 must leave it bit-identical).
- `teams_model.walk_forward()` — rolling-origin 1X2 vs Pinnacle; extend for P6.
- `v2/scorecard.py` — the only per-gameweek, forward, no-look-ahead grader; it
  already scores minutes and start probabilities by source tier. Everything
  in-season should be shadow-graded here before it ships.
- `v2/retro.py` (P3, planned) — per-player residual decomposition and class
  per gameweek. The scorecard's `retro_class` grouping is the forward test of
  the digest's own advice ("hold" after variance, "check" after a benching);
  `backtest_inseason.py --retro` is the backward one, including the
  sell-on-blank policy simulation that puts a number on the guardrails in
  P3 (3.5).
- **Missing:** a per-gameweek walk-forward harness (`v2/backtest_inseason.py`)
  and the historical per-GW rows to run it on (P1 + external import). Until
  it exists, every in-season constant (`CURRENT_TRUST_K`, half-lives, context
  multipliers, feedback weights) is a guess, and the honest expectation for
  any of them from one 38-game season is "directionally right, magnitude
  unknown".
