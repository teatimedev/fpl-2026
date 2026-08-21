# FPL workflow notes

## Current reality

- `.github/workflows/weekly.yml` is the production runner. It checks hourly and
  performs full rebuilds at T-24h, T-2h, Thursday morning, or on manual dispatch.
- `v2/fetch.py` automatically retrieves the official FPL `status`, `news`, and
  `chance_of_playing_next_round` fields. It does not retrieve press conferences
  or predicted lineups.
- `v2/availability.json` contains short-lived, sourced manual judgements. The
  model distinguishes starting, cameo, and no-appearance probability and
  expires overrides by gameweek.
- `v2/player_model.py` consumes availability forecasts. `v2/weekly.py`, the
  optimiser, planner, simulation, and app consume the resulting projections.
- A successful refresh commits generated data to `master`; Vercel deploys the
  app from `app/`; deadline-window runs publish `v2/push.txt` through ntfy.
- `v2/news_pipeline.py` scans the twenty public official club-news pages,
  stores short auditable evidence, and writes safe generated availability
  inputs. Explicit recent absences can apply automatically; nuanced wording is
  review-only until two deadlines have been scored.
- `v2/should_refresh.py` schedules cheap news scans every three hours from
  T-30h to T-6h and hourly until T-45m. An owned-player change promotes that
  run to the existing full model/export/deploy/ntfy path.

## Terms

- **Evidence:** a dated source claim about a player's availability or likely
  role. Evidence is not itself a probability.
- **Claim:** a structured fact extracted from evidence, such as `explicit_out`,
  `suspended_until`, `available`, `late_test`, or `predicted_start`.
- **Candidate:** an ambiguous claim shown for review but not applied to the
  model.
- **Generated override:** an automatically applied, bounded availability input.
- **Manual override:** a human-researched input in `v2/availability.json`; it
  always wins over generated evidence for the same player and gameweek.
- **Material change:** one that affects Jordan's squad, captain/vice, first
  bench player, a recommended transfer, or moves expected minutes by at least
  ten minutes.

## Constraints and known failure cases

- GitHub scheduled workflows can be delayed or dropped. Deadline windows must
  be wide, idempotent, and manually runnable.
- The GitHub runner starts without the uncommitted SQLite database, so a full
  projection rebuild requires the existing full fetch. Cheap news scans should
  trigger that rebuild only when evidence materially changes.
- Official FPL news is structured enough to apply automatically. Press quotes
  and predicted lineups are not.
- `available` means fit enough to be selected, not certain to start.
- A predicted XI is an opinion and must never overrule an explicit first-party
  absence.
- Public and subscription content have different access rights. The first
  release uses public first-party pages only and stores metadata plus a short
  evidence excerpt, never a full article.
- The static Vercel app has no authenticated write-back path. Ambiguous claims
  can be displayed and notified, but approval UI is a later project.

## Recommended operating principle

Automate collection, provenance, expiry, and unambiguous absences first. Keep
nuanced start/cameo adjustments in observation mode until their Brier score and
minutes error have been measured over at least two deadlines.
