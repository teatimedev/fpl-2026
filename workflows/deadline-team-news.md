# Deadline team-news ingestion

- Status: implemented; production release verification pending; nuanced-claim calibration 0/2 deadlines
- Owner: FPL bot; Jordan owns any optional manual override
- Primary runner: GitHub Actions
- Consumers: availability model, weekly digest, Vercel app, ntfy

## Purpose

Collect recent first-party team news before each FPL deadline, convert it into
traceable availability evidence, and rebuild recommendations when the evidence
materially changes. The workflow must improve freshness without treating an
ambiguous quote or third-party predicted XI as confirmed team news.

## Scope

The first release covers:

1. official FPL status/news/chance fields;
2. public official Premier League and club news or press-conference pages;
3. deterministic claims for explicit absence, suspension, return date, late
   fitness test, fit/available, and predicted start/bench;
4. safe generated overrides only for unambiguous absence information;
5. observation-only candidates for nuanced starting and cameo judgements;
6. website freshness/provenance and material-change phone alerts;
7. scoring predicted availability against actual starts and minutes.

It does not initially scrape logged-in subscription pages, reproduce full
articles, publish an authenticated approval UI, or let an LLM directly choose
probabilities in production.

## Trigger

Keep one production workflow and extend `v2/should_refresh.py` to emit a mode:

- `full`: existing T-24h, T-2h, Thursday, and manual rebuilds;
- `news`: a cheap scan every three hours from T-30h to T-6h, then on each
  hourly tick through T-45m;
- `noop`: outside the deadline window or a completed idempotency key.

Continue scheduling away from minute zero. Use wide deadline windows because a
GitHub cron is not a precision timer. `workflow_dispatch` remains the recovery
path and accepts `mode=news|full`.

A `news` run performs a full rebuild only when it finds a material new claim or
the official FPL availability fields changed. Otherwise it records health and
exits without making a commit or sending an alert.

## Required inputs and access

- Public official FPL bootstrap and fixtures endpoints.
- `v2/news_sources.json`: a reviewed registry of public first-party URLs for all
  twenty clubs and relevant Premier League pages, including source type,
  expected club, parser strategy, and enabled flag.
- `v2/player_aliases.json`: FPL player id, canonical name, known public-name
  variants, and club. Ambiguous surname-only matches are rejected.
- Existing `v2/availability.json` manual overrides.
- Existing cached projections and FPL entry id for materiality ranking.
- `NTFY_TOPIC` for alerts. No new credential is needed for the first release.

Optional later input: a licensed/public predicted-lineup feed. A subscription
website is not fetched from CI unless its terms and access method explicitly
permit automation.

## Data contract

Write evidence separately from model overrides.

### Evidence record

Each `data/news/evidence.json` row contains:

- stable `evidence_id`;
- `source_url`, source tier, publisher, and club;
- `retrieved_at` and published time when available;
- canonical content hash and parser version;
- matched `player_id` and match confidence;
- structured claim type and effective deadline/gameweek;
- a short evidence excerpt or paraphrase, capped at 280 characters;
- extraction confidence and any rejection reason.

### Generated override

`v2/availability.generated.json` uses the existing availability fields plus:

- `evidence_ids`;
- `generation_rule` and rule version;
- `generated_at` and explicit expiry;
- `status=applied|candidate|rejected`.

`load_overrides()` merges manual and generated files. An active manual override
wins. Conflicting generated evidence becomes a candidate; it never creates two
active overrides for the same player/gameweek.

## Source and confidence policy

Apply sources in this order:

1. official FPL structured status, suspension, and chance fields;
2. explicit first-party club/manager statements;
3. official recent match squad or participation evidence;
4. public third-party predicted lineups, if later licensed/approved.

Only these claims alter the model automatically in the first release:

- explicitly out or unavailable for the relevant fixture: start and cameo zero;
- suspended through the deadline: start and cameo zero;
- explicit return date after the deadline: unavailable through that deadline;
- a changed official FPL status/chance field: existing FPL adjustment rules.

These remain observation-only candidates:

- `available`, `fit`, `trained`, or `ready to be involved`;
- late fitness tests and ambiguous manager language;
- predicted start, predicted bench, or omission from a predicted XI;
- recent friendly or competitive lineup evidence.

`available` may remove an injury restriction only when it unambiguously reverses
an earlier absence; it must not set a high start probability by itself.

## Ordered actions

1. Determine next gameweek, deadline, mode, and idempotency key.
2. Fetch enabled sources concurrently with conditional HTTP requests, per-host
   rate limits, a descriptive user agent, two bounded retries, and timeouts.
3. Save source health and canonical hashes; do not reparse unchanged content.
4. Extract dated claims, including negation and quoted-speaker context.
5. Resolve players through FPL ids and the alias registry; reject ambiguity.
6. Deduplicate claims and resolve precedence/conflicts.
7. Produce generated overrides and observation-only candidates with expiry.
8. Compare against the previous accepted state and calculate materiality using
   Jordan's squad and current recommendations.
9. If material, run the existing full fetch/model/optimiser/digest/export path.
10. Commit evidence, health, generated overrides, and rebuilt outputs in one
    commit; Vercel deploys from that commit.
11. Send one deduplicated ntfy message only for a material squad/recommendation
    change or a deadline-critical collection failure.
12. After fixtures, score start, appearance, and expected-minutes forecasts.

## Human checkpoint

No human action is required for the safe auto-applied claims above.

Candidates appear on the website under **Team news needs review** and in the
phone digest only when they affect the owned XI, captain/vice, first substitute,
or leading transfer. The exact human decision is: **apply a manual override,
leave the baseline unchanged, or investigate another source**. Until Jordan or
Codex writes a manual override, a candidate does not change projected points.

An authenticated Apply/Ignore interface is explicitly deferred; the current
static app cannot safely write to the repository.

## Outputs

- `data/news/evidence.json`: current deduplicated evidence.
- `data/news/source_health.json`: last fetch, HTTP/result state, age, and hash by
  source.
- `data/news/latest_run.json`: run mode and counts.
- `data/news/history/gw<N>.json`: frozen deadline snapshot for scoring.
- `v2/availability.generated.json`: applied generated overrides and candidates.
- Existing projections, weekly digest, app export, and `v2/push.txt` when a
  material rebuild occurs.
- Website freshness: last successful official-FPL fetch, first-party source
  coverage, stale/failed source count, and evidence provenance per flagged
  player.

## Idempotency and retry

- Source hash plus parser version prevents duplicate extraction.
- Evidence identity is player, claim, effective gameweek, source, and published
  time.
- The same material change sends at most one ntfy alert per gameweek.
- Failed sources retry twice with bounded backoff; successful sources are not
  refetched inside the same run.
- A rerun with unchanged inputs creates no commit, deployment, or notification.
- Previous evidence is retained only until its recorded expiry; a fetch failure
  never extends evidence life.

## Failure and escalation

- Official FPL unavailable: fail the model rebuild, retain the last good public
  site, and send an urgent stale-data alert inside T-3h.
- One first-party source unavailable: continue, mark it stale, and do not infer
  that silence means availability.
- Coverage below 80% of enabled first-party club sources: mark the scan degraded.
- An owned captain/vice club source failing inside T-3h: send a targeted warning
  even if no claim changed.
- Parser or player-match ambiguity: create a rejected/candidate record; never
  guess.
- Conflicting first-party claims: retain both, apply neither automatically, and
  surface the conflict.
- Model, validation, export, deployment, or ntfy failure: the workflow fails
  visibly and does not mark the deadline window complete.

## Privacy, credentials, and content rights

- Fetch public pages only in the first release.
- Do not commit cookies, subscription credentials, API tokens, or the ntfy
  topic.
- Treat the ntfy topic as a password-like secret even though the current repo
  stores it as a variable; migrate it to an Actions secret during this phase.
- Store source metadata, structured facts, content hashes, and short evidence
  only. Do not archive full copyrighted articles.
- Respect robots, rate limits, and published access terms. Disable a source that
  does not permit automated retrieval.

## Observability

Every run reports:

- deadline, mode, start/end time, and commit SHA;
- enabled/fetched/failed/stale source counts and coverage percentage;
- changed pages, extracted claims, applied overrides, candidates, conflicts,
  and rejected matches;
- affected owned players and before/after start, play, and expected-minute
  values;
- whether a rebuild, deployment, and ntfy publication completed;
- age of the newest official FPL and first-party evidence.

The website shows a simple green/amber/red **News freshness** status. GitHub
Actions is the detailed audit trail. A run is successful only when the scan
health is recorded and any required downstream rebuild, deployment, and alert
also succeed.

## Forecast scoring

Extend `v2/scorecard.py` with:

- start Brier score;
- appearance Brier score;
- expected-minutes MAE and bias;
- results by source tier, claim type, and confidence;
- comparison of generated availability versus the historical-minutes baseline.

Do not auto-apply nuanced start/cameo claims until observation mode shows an
improvement over baseline across at least two deadlines and there is no severe
calibration failure for owned-player alerts.

## Implementation sequence

The collector, safety rules, model handoff, scheduler, website surface, alerts,
and scoring are implemented. Steps 1–4 are release candidates. Step 5 is a real
time-based observation gate: nuanced start/cameo claims remain review-only
until two completed deadlines have been scored.

### 1. Contracts and fixtures — implemented

- Add source, alias, evidence, run-health, and generated-override schemas.
- Add stored test snippets for absence, negation, return date, ambiguity, and
  conflicting evidence.
- Extend availability loading to merge manual and generated inputs safely.

### 2. Observation-only collector — implemented

- Implement `v2/news_fetch.py` and `v2/news_extract.py`.
- Build and validate the twenty-club first-party source registry.
- Commit health/evidence/candidates, but make no new probability changes.

### 3. Workflow and product surface — implemented

- Add `news` mode to the existing gate and workflow.
- Add materiality/dedup logic and conditional full rebuild.
- Add website freshness, provenance, candidates, and degraded-state messaging.
- Add targeted ntfy messages with a click through to the affected player.

### 4. Safe automatic absences — implemented; production verification pending

- Enable only explicit out/suspended/dated-return rules.
- Verify manual override precedence and expiry.
- Run end-to-end deployment and notification failure tests.

### 5. Calibration gate — active, 0/2 completed deadlines

- Freeze each deadline's predictions and score actual starts/minutes.
- Review after two deadlines; adjust or disable weak claim rules.
- Make a separate decision on licensed predicted-lineup data or optional
  structured LLM extraction. Neither is required for Phase 3 completion.

## Acceptance criteria

Phase 3 is complete when:

1. all twenty clubs have an enabled or explicitly unsupported first-party entry;
2. unchanged news scans are idempotent and do not rebuild or notify;
3. explicit absence/suspension/return-date fixtures produce correct expiring
   overrides, while ambiguous quotes remain candidates;
4. manual overrides always win and conflicts fail safe;
5. a material owned-player change triggers one valid full rebuild, production
   deployment, and ntfy alert;
6. source degradation is visible on GitHub and the website and becomes an
   urgent alert for captain/vice clubs inside T-3h;
7. no logged-in source or full article content is stored;
8. tests cover parsing, matching, precedence, expiry, idempotency, materiality,
   source failures, and the end-to-end workflow path;
9. deadline snapshots are scored for starts, appearances, and minutes;
10. two observation deadlines complete before nuanced probabilities can be
    automatically applied.

## Deferred decisions

These are deliberately outside the first release and require a separate choice:

- purchasing/licensing a predicted-lineup feed;
- using an OpenAI API key for structured extraction in GitHub Actions;
- building authenticated Apply/Ignore controls in the website;
- replacing GitHub cron with a stronger scheduling service.
