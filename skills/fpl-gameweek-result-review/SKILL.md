---
name: "fpl-gameweek-result-review"
description: "Review the user's actual FPL gameweek score — trigger on \"my FPL result\", \"this week's fantasy\", GW points breakdown requests"
---

# FPL Gameweek Result Review

Pull the user's actual gameweek score from the official FPL API using data already in the `fpl-2026` repo, then break down what drove it.

## Procedure

1. Find the entry ID in `v2/my_squad.txt` (header comment: `# FPL entry: <id>`); the same file gives the confirmed squad with captain/vice/bench order.
   - Done when you have the numeric entry id.
2. Fetch three endpoints with curl:
   - `https://fantasy.premierleague.com/api/entry/<id>/history/` → per-GW points, gw_rank, transfers, hit cost (`current[]`).
   - `https://fantasy.premierleague.com/api/entry/<id>/event/<gw>/picks/` → picks with multipliers and `automatic_subs`.
   - `https://fantasy.premierleague.com/api/bootstrap-static/` → `elements[].event_points`, `events[].average_entry_score`.
   - Done when all three JSON payloads are saved locally.
3. Join pick element ids to bootstrap elements; compute each player's points × multiplier, flag captain (×2) and vice (×3 only if auto-subbed in), list bench scores and any automatic subs.
   - Done when per-player table matches the history total.
4. Contextualise: compare total vs `average_entry_score`; use gw_rank (~11M+ managers) for percentile framing. Attribute the gap to projection by naming top contributors and blanks, especially the captain.
   - Done when the reply states total vs average, rough rank percentile, and 2–3 named drivers.

## Reference

- The confirmed pre-deadline squad and deadline reasoning live in `research/gw*-deadline*.md`; model projections per player are in `data/history/gw<N>.json` (`proj`, `start_rate`) for projection-vs-actual comparison.
- If `v2/scorecard.py` prints "GW<n>: not finished yet": the gate is the live `bootstrap-static` event's `finished` flag, not any local cache — check `events[<gw>].finished` there first before touching `v2/cache/fixtures.json` or rerunning fetch (the fixtures endpoint can report matches finished while the event flag is still false; refreshing caches then changes nothing).
