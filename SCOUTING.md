# Sourced scouting

The numerical model remains the point forecast. This layer uses DeepSeek to
extract concrete observations from dated public football reports, then shows those
observations next to affordable transfers and explicit sensitivity scenarios.
It does not treat social sentiment as a scoring probability or claim to watch games.

## Configuration

`DEEPSEEK_API_KEY` is read from the environment, then the ignored root `.env.local`
or `.env`. Use `.env.example` as the name reference. Keep credentials server-side.
The same key name is configured as a GitHub Actions secret; rotating a key requires
updating the local value and that secret.

The client is pinned to `deepseek-v4-flash`, thinking enabled, reasoning effort
`max`, and the maximum response allowance of 393,216 tokens. This is an allowance,
not a request to fill the response. Only structured evidence and token usage are
persisted; the model's reasoning text and full articles are not exported.

Each run defaults to eight calls and a $5 ceiling, reserving the maximum potential
response cost before every call. Failed calls consume their reservation. Cached
extractions do not spend another call. Prices are recorded assumptions and need
updating if the provider changes them. Current estimates use peak list prices.

## Running locally

After refreshing the numerical model and exporting its CSV/JSON projection:

```sh
.venv/bin/python v2/weekly.py --no-refresh --plan --scout --json --snapshot --team 3415101
.venv/bin/python export_app_data.py
```

To rerun only scouting against the current weekly shortlist:

```sh
.venv/bin/python -m v2.scouting --max-calls 8 --budget-usd 5
.venv/bin/python export_app_data.py
```

The second command sequence updates the app evidence; run the weekly snapshot
again before the deadline to archive that evidence with a new forecast revision.
Scouting refuses a closed deadline. These commands do not submit FPL actions or
send a phone notification. The existing cloud workflow has separate push steps.

## Evidence and evaluation

`data/scouting/latest.json` contains accepted claims, rejected-claim reasons,
source health, expiry and usage. Immutable run files live in `data/scouting/runs/`;
the ignored cache lives in `data/scouting/cache/`. Curated report URLs can be added
to `v2/scouting_sources.json`; articles without a usable publication time fail the
freshness check. An HTTP success alone does not count as scouting coverage.

For each accepted claim, the player must be identified in the fetched text and
the short quotation must match it. Source independence and conflicting directions
are reported. Missing observations mean unknown, not good form. Read the source
context before interpreting a single-match observation as an ongoing role or
ability change. Cached claims are revalidated against the current article, dates,
players and per-article quotation and summary budgets.

`v2/scouting_collect.py` handles publisher article headers, canonical article
metadata and dated first-team reports separately from the injury-news pipeline.
Fixture kickoff times and related-story dates cannot date the main article. The
collector follows a bounded number of relevant links and the call scheduler
prioritises uncovered squad members and their shortlisted replacements.
`v2/scouting_sources.json` supplies additional public report indexes and curated
articles. Several club indexes still return stale or unusable content; the app
shows those failures even when other sources cover their players.

## Transfer-policy experiments

```sh
.venv/bin/python -m v2.policy_lab
.venv/bin/python -m v2.policy_lab --stress
.venv/bin/python export_app_data.py
```

The first experiment compares acting now, waiting with future transfers, forced
Thiago/Kluivert sales and an extra free transfer using the same forecast and
account state. The second re-optimises both paths after hypothetical attacking
downgrades to the holding and replacement. Results and solver diagnostics are in
`data/policy_lab.json` and immutable `data/history/policy_lab/` records.
The app only shows this experiment beside a matching forecast and account.
This research command is not an automatic change to the live transfer threshold.

Deadline forecasts live in `data/history/forecasts/gwN/`; `gwN.json` points to the
latest revision saved before that deadline. Submitted picks are archived separately
under `data/history/submitted/ENTRY/`. Future scorecards compare shadow P60 and
frozen policy baselines with outcomes without rewriting past forecasts.
