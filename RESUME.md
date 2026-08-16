# Where this is up to — 16 Aug 2026 (evening)

**Since the morning entry below:** chips are modelled (`v2/chips.py`, two copies
per chip from the API's own windows, valued from a coarse full-season projection
`v2/projections_season.json`; `weekly.py --chips`); **the model now learns
in-season** — fetch.py writes 2026/27 season_stat rows from the bootstrap and
match rows from finished fixtures (guarded pre-season: the API shows last
season's totals until GW1 ends), the player panel includes 2026/27 at weight
1.0, and the minutes model trusts this season's start rate n/(n+4) after n team
games; `weekly.py --json` writes the digest as data (`data/weekly.json`),
`v2/movers.py` turns the daily price log into ownership/transfer momentum, and
the app bundle now carries weekly, movers, a model fixture ticker and player
detail fields. **The app now has four tabs:** This week (renders the CI digest
when the loaded squad matches — fixture chips, lineup diff, checks, XI-aware
transfers and two-move combos, six-week plan, price watch; a "load that squad"
link otherwise), Season (chips with per-week bars, model fixture ticker with
attack/defence toggle, movers), **My squad** (replaced Build: your real lineup on
the pitch with the model's diff overlaid, a transfer sandbox with XI-aware gain
and hit cost, "Find a replacement", squad health, context panels; header stats
from `entry/{id}/` once linked; Build survives as a "Draft a squad from scratch"
mode for wildcard/free-hit weeks — `app/src/SquadBuilder.tsx`), Scorecard; and a player drawer from any
name (window bars + season curve, xG/xA/DC per 90, evidence, start rate, news,
momentum). Header is two rows on a phone; the pitch fits four across at 375px.

**GW1 deadline: Fri 21 Aug, 18:30 BST (17:30 UTC).** Prices are locked until then.

## The team is picked

Jordan's real 15 is **Option B ("Haaland Build") from the app, verbatim**, and it
is in `v2/my_squad.txt` with his captain (Haaland), vice (Raya) and bench order.
Reviewed 16 Aug against the refreshed model:

- No single transfer improves it; the best XI-aware two-move combination is
  +0.8 pts over six gameweeks (noise). It sits 1.3 pts behind the model's
  unconstrained optimum on the same metric while owning the 73%-owned captain.
- Monte Carlo (`simulate.py`, now on the v2 team layer): mean 403 over GW1–6,
  beats the most-owned template 88% of the time. Option A beats it 56/44, and
  nearly all of that gap is **bench cover** (auto-subs 29.9 vs 19.8 — Reed and
  Destan are £4.5m floor players who don't play), not the 3 ARS + 3 MCI
  concentration (sd 50.7 vs 49.2).
- What the model would change on the pitch: captain **Saka over Haaland for
  GW1** (7.2 vs 6.7 — Arsenal home to promoted Coventry); **vice on Raya is a
  mistake** (should be Saka/Haaland — a keeper doubled if the captain misses);
  5-3-2 with O'Shea over 4-4-2 with Kluivert is a coin flip (+0.3).
- Recheck Friday afternoon after the pressers — the T-2h refresh (below) picks
  up late news and the opening odds, and that is when the captain call is real.

## What runs, and when

**https://fpl-2026.vercel.app** — public, works on a phone. Tabs: *This week*
(captain, XI, your lineup vs the model, transfers, checks, price watch), *Season*, *My squad*,
*Scorecard*.

`.github/workflows/weekly.yml` ("Refresh") fires **hourly**; `v2/should_refresh.py`
lets it through only at **T-24h** and **T-2h** before the next deadline, plus a
guaranteed **Thursday 06:00–09:00 UTC** slot, plus manual dispatch. Each real run:
fetch → team ratings → season view (bookmaker odds where posted) → player
projections → csv/json → re-optimise squads → `weekly.py --plan --snapshot
--price-log --push-file` → `scorecard.py` → export → commit → push (Vercel
deploys) → **ntfy push** to the phone (deadline windows and manual runs only).

- **Repository variables** (Settings → Secrets and variables → Actions → Variables):
  `NTFY_TOPIC` (set; subscribe to that topic in the ntfy app), and `FPL_ENTRY_ID`
  — **set this from Fri 21 Aug 18:30**, after which the workflow and the app read
  Jordan's real squad, bank, free transfers and lineup from the picks endpoint.
  Until then everything uses `v2/my_squad.txt`.
- **Optional secret** `ODDS_API_KEY` (the-odds-api.com, free tier): live match
  odds from many books, Pinnacle preferred. Without it, only football-data's
  week-ahead lines are used. As of 16 Aug no GW1 odds were posted by either.
- Vercel root directory is `app`. That is dashboard state, not in git — if the
  project is ever recreated it must be set again or GitHub builds fail.

## The model (v2)

Dixon-Coles team ratings on 1,520 matches (1.6% behind Pinnacle's closing line),
shrinkage weights from measured year-over-year stability, hold-out backtested
(rank correlation ~0.46 on points/90 among established players).

- **The window rolls.** `v2/gwclock.py` decides the next gameweek from the
  cached bootstrap; `season_view.py` and `player_model.py` project
  `next_gw … next_gw+5`. `proj_by_gw` stays indexed by absolute gameweek (entry
  0 = GW1) with zeros for weeks already played, so every consumer keeps asking
  for `proj_by_gw[gw-1]`; `horizon` is the last GW covered, `start_gw` the first.
  `FPL_GW_OVERRIDE=7 python v2/player_model.py` simulates the run-up to GW7.
- **Bookmaker odds are now actually used.** `teams_model.market_view()` backs out
  the (home xG, away xG) that reproduces the de-vigged 1X2 and O/U 2.5 prices and
  blends 80/20 with the model in log space; `season_view.py` applies it to any
  fixture with a `market` row and prints how many were priced. Before 16 Aug the
  odds were fetched and never read.
- `weekly.py` now: XI-aware transfer gains (lift to best XI + captain over the
  window, not player-vs-player), best two-move combos net of hits, hold-vs-use
  advice (a free move worth < 2.0 is banked; per move for multi-move plans), free
  transfer count inferred from public history (`infer_free_transfers`), a lineup
  checker (captain, vice — flags a keeper vice — bench→start, bench order,
  formation), "Check before the deadline" (news, chance of playing, start rate <
  80%, new signings), and price pressure = net transfers / owners (uncalibrated).
- `v2/planner.py` — plan.py ported to v2: multi-week integer program from the
  real squad/bank/FTs; `--plan` in weekly. Point-estimate plans churn; it is
  read for direction.
- `v2/scorecard.py` — grades each archived `data/history/gw{n}.json` once the
  round is finished: rank corr (starters/pool/played), MAE/bias, decile
  calibration, captain model/yours/best, XI model/yours/best, clean-sheet Brier.
  Writes `data/scorecard.json`; the app's Scorecard tab renders it. Empty until
  GW1 has been played.
- `data/price_log/{date}.csv` — every player's price and transfer flow, daily,
  for calibrating a price-change model later.

## Open threads

1. **After GW1 (Fri 18:30):** set `FPL_ENTRY_ID`. Confirm the T-2h run fired and
   the ntfy push arrived. Check the Scorecard grades GW1 after Monday's
   data-check.
2. **By ~GW6, with real data:** test DefCon dispersion (Poisson vs negative
   binomial), re-tune shrinkage/priors on 26/27, calibrate the price model
   from `data/price_log`, and look at `scorecard.json` to see whether the market
   blend weight (0.8) should move.
3. **The model over-likes thin-record players** (Kostoulas, O'Shea; Thiaw
   turned out to have a real 126-pt season). Sanity-check by eye.
4. `simulate.py`, `plan.py`, `optimise.py` (v1 shells) still assume GW1–6 in
   places (`P.HORIZON`, labels). They are pre-season tools; not urgent.
5. `SQUADS.md` describes the *v1* squads and is historical. Not worth rewriting
   now the team is picked; a "why Option B" note is at its top.
6. App follow-ups from the review: This Week still renders its digest and
   live paths as two JSX trees (~350 duplicated lines) — unify by normalising
   the digest into the live shape; move `Digest` into its own file. Sell prices
   in the sandbox are current prices, not FPL sell values.
7. Chips are modelled (v2/chips.py). What is still missing: Bench Boost / Triple Captain / Free Hit /
   Wildcard timing around double and blank gameweeks is the biggest lever in
   the second half of the season — the planner is the place to add it.

## To pick up

```bash
cd ~/projects/fpl-2026
.venv/bin/python v2/weekly.py --plan            # full refresh + digest (~2 min)
.venv/bin/python v2/weekly.py --no-refresh      # digest only, seconds
.venv/bin/python v2/scorecard.py                # grade finished gameweeks
.venv/bin/python simulate.py                    # Monte Carlo of the candidate squads
cd app && npm run dev                           # http://localhost:5173
gh run list --workflow=weekly.yml --limit 5     # did the refresh fire?
gh workflow run weekly.yml                      # force one (pushes to ntfy)
```

## Things learned the hard way

- Backtests that flatter a method are usually leaking. Use `start_cost` (price at
  the start of that season), never today's price.
- Clean sheets have almost no player-level signal (0.21 stability) — they must
  come from a team model.
- FPL's fixture difficulty correlates only −0.60 with real clean-sheet
  probability. Do not trust it.
- Realistic ceiling for predicting player points/90 is Spearman ~0.46.
- A per-player transfer score ("A projects more than B") is not a transfer
  score. Only the lift to the best XI counts, and a bench-fodder swap that
  frees money is invisible to it.
- The daily Chrome can squat 127.0.0.1:9222 and answer 404, pushing the CDP
  automation Chrome onto `[::1]:9222` where the MCP can't reach it. Check
  `lsof -nP -iTCP:9222` before assuming it's broken. Neither browser profile is
  logged in to FPL.
