# FPL decision system: implementation plan

Started 6 September 2026. Objective: improve repeatable FPL decisions, including
when to sell an underperforming player. Success is better decisions on unseen
deadlines, not more simulations or a promise of an unbeatable team.

## 1. Make the recommendation trustworthy

- Enforce matching gameweek, squad, bank, transfers and forecast identity in the app.
- Incorporate completed provisional fixtures in minutes evidence.
- Carry actual selling values into single moves, packages and the planner.
- Preserve pre-deadline forecasts; grade the submitted picks and captain multiplier.
- Expose approximation and stale-data limitations beside the affected advice.

## 2. Build sourced scouting

- Server-side DeepSeek client, pinned to `deepseek-v4-flash` (updated to the user's latest choice).
- Bounded calls, token budget, caching and recorded usage; credentials never exported.
- Extract observations only from fetched articles. Require player identity, a short
  matching quotation, source URL, publication time, mechanism and direction.
- Track independent sources, contradictions, expiry and missing coverage.
- Show squad and shortlisted targets together. AI extraction confidence is not a
  calibrated probability of starting or scoring.

## 3. Make transfer decisions inspectable

- Show the strongest affordable replacement for every owned player, including
  Thiago and Kluivert even when its estimated gain is negative.
- Compare packages and acting now versus waiting using the same squad evaluator.
- Show the current policy threshold as an unvalidated assumption.
- Run explicit, labelled minutes/attacking-output stress tests; explain what would
  have to change for selling to beat holding. Do not silently apply a sentiment tax.

## 4. Measure the next improvements

- Add probability of reaching 60 minutes as a separate shadow forecast, using
  completed-match evidence; grade it before changing production point estimates.
- Archive the scouting evidence and scenario assumptions with the forecast.
- Add replay/evaluation contracts and compare against simple baselines.
- Retain adaptive rates and dynamic transfer-value experiments in shadow until
  deadline-safe evidence demonstrates improvement.

## Acceptance for this build

Meaningful Python and app tests; build and lint; a real bounded DeepSeek call
when the authorised credential is available; a fresh local recommendation build;
desktop and mobile inspection of the rendered workflow. Record what is implemented,
what was verified, and what still requires future results. Publishing or submitting
FPL account changes is a separate action.

## Status

First implementation completed locally on 6 September 2026. Existing audit fixes
and research artifacts are retained. The refreshed GW4 forecast, transfer review,
scouting observations and deadline archive are connected to the app.

Implemented and exercised:

- Account/forecast freshness checks, correct sale-price treatment and completed
  provisional-match handling. Export time is separate from model-build time;
  exporting news cannot make old projections appear newly generated.
- Per-player affordable alternatives, attacking-output and minutes stress tests,
  and visible assumptions behind the current transfer policy.
- DeepSeek V4 Flash through the direct DeepSeek API, thinking enabled,
  `reasoning_effort=max`, `max_tokens=393216`. Live maximum-setting calls succeeded.
  The supplied key is stored locally outside version control and configured as
  the repository Actions secret. The edited workflow has not been published.
- Validated source quotations, date/identity checks, cache and cost limits,
  contradictions, explicit coverage gaps, and immutable scouting run records.
- Shadow P60 predictions and frozen policy baselines for future scoring; actual
  submitted-pick grading preserves the Haaland Triple Captain multiplier.

Verification: 246 Python tests and six app tests passed; app build and lint passed.
Desktop and 480-CSS-pixel mobile views were inspected, including player selection,
source expansion and opening/closing a player detail. The live account comparison
resolved correctly, and the final fresh browser load had no console errors.
Existing PuLP deprecation and bundle-size warnings remain.

This is a first measured implementation, not evidence of improved realised points.
The initial scouting run supports observations for only three of the 15 holdings.
Thirteen club feeds returned no usable dated articles; that collection gap is
visible in source health. The AI extracts written reports and has not watched video.

## Second milestone: sourced transfer review

Completed on 6 September 2026 and published on the preview branch
`agent/scouting-policy-review`:

- Expanded collection now produces 34 validated observations covering seven of
  the 15 holdings, plus both named replacements. Publication headers, embedded
  report cards, accented names and first-team link discovery are handled. Eleven
  source indexes still have no recent usable articles. Extraction failures and
  exhausted call budgets remain recorded; coverage is not inferred from HTTP success.
- Tested six transfer buffers on identical inputs. The flexible plan gains only
  0.389 points across three immediate moves against waiting. Buffers of zero or
  0.10 permit it; 0.25 and above hold. The current two-point policy is exposed as
  an unvalidated uncertainty assumption, rather than silently replaced.
- Forced Thiago → João Pedro and Kluivert → Barnes comparisons allow future
  transfers on both paths. Baseline sale values are −1.89 and −1.05 points versus
  waiting. Symmetric attacking downgrades test whether a one-sided concern gives
  the replacement an unfair advantage. Solver limitations are shown explicitly.
- Fresh completed-match case studies separate chance generation from returns,
  including Thiago's missed penalty and the unfinished João Pedro fixture.
- Eleven GW4 policies and the current scouting evidence are frozen in an
  immutable pre-deadline revision. Historical submitted picks retain Haaland's
  Triple Captain multiplier. Future scorecards can grade the policy variants.

The expanded collection used 18 paid maximum-reasoning DeepSeek calls across two
bounded runs, followed by cache-only validation. No football-point gains have yet
been demonstrated on unseen deadlines. Details and reproducible experiment
commands are in `research/hold-sell-cases-2026-09-06.md` and `SCOUTING.md`.

Final checks for this milestone: 260 Python tests and six app tests pass; app
lint and production build pass. A credential scan found no supplied API key in
the staged review files. Use `python -m pytest tests/ -q` in a fresh checkout:
the older `v2/predict/volume_test.py` research script requires ignored local data
and is not part of the unit suite. Existing PuLP deprecation and bundle-size
warnings remain.

## Next milestones

Transfer-bank follow-up, 6 September: the planner's displayed FT balance is
reconstructed from actual moves rather than loose solver variables. A hold
instruction now displays the path constrained to hold this week, instead of
showing a competing path that already spent transfers. The app explains the cap
and labels when an additional weekly transfer is forfeited. With three FT in
GW4, making no transfers gives 3, 4, 5, 5 at the GW4–7 deadlines. This corrects
accounting and presentation; the existing two-point policy remains unvalidated.
Follow-up validation: 262 Python tests, seven app tests, lint and build pass.

1. Improve dated article extraction and add reliable independent sources until
   owned players and serious transfer targets have useful coverage. Audit a sample
   of extracted claims against the articles before allowing wider influence.
2. Collect deadline-frozen forecasts and outcomes. Compare P60 calibration, points,
   captain selection and transfer policies against simple baselines, including
   uncertainty and transfer costs. Historical forecasts cannot be reconstructed
   using later news and then presented as a valid backtest.
3. Test evidence-driven changes to minutes, role and attacking rates in shadow.
   Promote a change only after out-of-sample improvement; the current sentiment
   scenarios and two-point-per-move threshold are not calibrated decisions.
4. Address the remaining model audit priorities: expiring preseason overlays,
   coherent team/player attacking shares, joint lineup probabilities and measured
   free-transfer option value.

The local preview is http://127.0.0.1:5173/. The hosted branch preview is
https://fpl-2026-git-agent-scouting-p-a951e9-jordanrippon2020s-projects.vercel.app.
The production site and its scheduled workflow still use master. No FPL account
selections or transfers have been submitted.

### Plain weekly instructions (6 September follow-up)

The weekly page now leads with four actions: transfers, captain and vice,
starting eleven, and deadline checks. The pitch shows player names and armbands;
bench priority is stated separately. Detailed comparisons, future paths and news
sources are folded below. Public picks are identified as the last published team,
so an unpublished change is never claimed to be confirmed in FPL.

The weekly exporter publishes selected moves and the lineup after those moves as
structured data. A hold discards rejected moves; a recommended transfer cannot
show the old squad as its resulting lineup. Transfer counts, point costs and the
five-transfer cap are explicit. Vice-captain-only changes are now called out.
Missing or mismatched data suppresses the weekly instruction; unavailable chip
advice is disclosed instead of being guessed. This changes clarity, not the
transfer threshold or forecast assumptions.

Validation: 265 Python tests and 13 app checks, lint and production build pass.
