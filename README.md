# FPL 2026/27

A weekly FPL decision app with player projections, legal squad and transfer
planning, sourced scouting, and archived forecasts that can be checked against
actual results.

**Production:** [fpl-2026.vercel.app](https://fpl-2026.vercel.app/)

## Using the app

Start with **This week**. It gives four plain instructions: transfer or hold,
captain and vice, starting team, and checks before the deadline. The pitch and
bench order show the recommended team. Transfer instructions include the chosen
moves, any points cost and next week's free-transfer balance. A hold applies to
this week; future plans are conditional scenarios.

Expand **Why this recommendation?** for the numbers, **Review a player: keep or
sell?** for replacements and evidence, or **Club news and sources** for the
collection record. My squad, Season and Scorecard provide the other views.

Public FPL data shows the last published lineup, not unpublished changes made
since a deadline. Weekly instructions are withheld when the deadline, forecast,
account, prices or squad news do not match. Missing chip advice is stated.

## Run locally

```sh
cd app
npm ci
npm run dev -- --host 127.0.0.1
```

Open http://127.0.0.1:5173/. The app uses the committed data bundle plus a live
FPL proxy. Node 24 matches the production runtime. Python is only needed to
rebuild projections or run model tests:

```sh
python3 -m venv .venv
.venv/bin/pip install numpy scipy pulp highspy pytest
.venv/bin/python -m pytest tests/ -q
```

From `app/`, run `npm test`, `npm run lint` and `npm run build`. Use `tests/`
explicitly for Python: the older `v2/predict/volume_test.py` research script
requires ignored local artifacts and is not part of the unit suite.

## Refreshing advice

The production workflow is [.github/workflows/weekly.yml](.github/workflows/weekly.yml).
It scans news, refreshes the numerical model when due, grades finished weeks,
builds the weekly decision and chip advice, runs bounded scouting, freezes the
deadline evidence, exports the app bundle and commits the results to `master`.
The hourly gate selects useful deadline/news windows; it does not rebuild every
hour. Existing notification steps run separately from the deployment.

For a local full model and weekly refresh, with no phone notification:

```sh
.venv/bin/python v2/weekly.py --full --plan --chips --scout --snapshot --json --team 3415101
.venv/bin/python export_app_data.py
```

`--full` fetches player histories needed by a fresh database. Use `--no-refresh`
instead only when the local database and numerical projections are already
current. Exporting alone does not refresh a forecast. The full scoring and news
pipeline, including `scorecard.py` and `retro.py`, is documented in the workflow
and [v2/README.md](v2/README.md).

Scouting uses `DEEPSEEK_API_KEY` from an ignored root `.env.local` or the process
environment. GitHub Actions uses the secret of the same name. The app bundle
contains evidence, never the key. See [SCOUTING.md](SCOUTING.md) for configuration,
bounded costs, source requirements and experiments. A manual cloud workflow run
can send an existing ntfy notification; local commands above do not.

## How the pieces fit

| Area | Main files |
|---|---|
| Team and player forecasts | `v2/teams_model.py`, `v2/season_view.py`, `v2/player_model.py` |
| Shared legal lineup scoring | `v2/squad_evaluator.py` |
| Weekly action and transfer balance | `v2/weekly.py`, `v2/planner.py`, `v2/decision_state.py` |
| Sourced observations | `v2/scouting.py`, `v2/scouting_collect.py` |
| Transfer comparisons and experiments | `v2/transfer_review.py`, `v2/policy_lab.py` |
| Frozen forecasts and actual picks | `data/history/forecasts/`, `data/history/submitted/` |
| Outcome grading | `v2/scorecard.py`, `v2/decision_replay.py` |
| Weekly interface and freshness checks | `app/src/WeeklyBrief.tsx`, `app/src/weeklyActions.ts`, `app/src/coherence.ts` |

The current system uses v2 forecasts. The [v1 overview](README-v1.md),
[preseason research](RESEARCH.md) and [initial squads](SQUADS.md) are historical
references, not current recommendations.

## Deployment and handover

Vercel deploys `master` to production with **root directory `app`**; other branches
produce previews. The Python refresh runs on GitHub Actions, not Vercel. Run the
tests and app build before pushing; verify the actual production page, linked
team, disclosures and player drawer after deployment.

[RESUME.md](RESUME.md) records current operating state and remaining work.
[IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) records the September release,
experiments and validation. [app/README.md](app/README.md) covers the frontend.

The model estimates outcomes. Sourced AI observations and more simulations do
not establish better FPL performance. The current transfer threshold, price
timing and scouting sensitivity assumptions still need independent deadline
results; experimental P60 and policy variants remain in shadow.
