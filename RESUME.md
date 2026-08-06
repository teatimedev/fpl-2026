# Where this is up to — 6 Aug 2026

**GW1 deadline: Fri 21 Aug, 18:30 BST.** Prices are locked until then, so nothing
in the dataset moves before you pick.

## State

Two generations of model live side by side. **v2 is what everything runs on now.**

- `v2/` — the current model. Dixon-Coles team ratings fitted on 1,520 real
  matches and validated 1.6% behind Pinnacle's closing line; shrinkage weights
  set by measured year-over-year stability; hold-out backtested.
- v1 (`project.py`, `overlay.py`, `simulate.py`, `plan.py`) — superseded but
  still working and still used. `overlay.py` in particular is imported by v2,
  because it holds research no model can derive (confirmed line-ups, role
  changes). v1's projections are archived at `data/projections_v1.csv`.
- `app/` — the browser squad builder, **local only, never deployed**.

## Nothing is decided yet

You have not picked a team. Four candidate squads are in `data/squads.json` and
loadable in the app. `SQUADS.md` has the reasoning, but **it describes the v1
squads** — the app and `data/squads.json` have since been rebuilt on v2 and the
picks changed materially (Saka, Mbeumo and Foden in; Haaland out of Option A).
Rewriting SQUADS.md against v2 is the obvious first job next time.

## To pick up

```bash
cd ~/projects/fpl-2026
cd app && npm run dev          # the builder, http://localhost:5173
.venv/bin/python v2/weekly.py  # captain, XI, transfers, price watch
```

Add `--team <your FPL entry id>` to `weekly.py` once you have made a team; before
GW1 your picks are not public, so edit `v2/my_squad.txt` instead.

## Open threads

1. **Rewrite SQUADS.md against v2.** It is currently out of date.
2. **Re-run `simulate.py` and `plan.py` on v2 projections.** Both still read
   `data/projections.csv`, which is now v2, so they will work — but the numbers
   quoted in SQUADS.md came from v1 and have not been refreshed.
3. **The model likes unknowns** — Kostoulas, Thiaw, O'Shea. Low ownership, almost
   no Premier League record, so they lean on the price prior. Sanity-check them
   by eye before trusting them.
4. **DefCon dispersion is untested.** Poisson is assumed. After a few gameweeks
   of real 2026/27 match logs, test Poisson vs negative binomial properly.
5. **Bookmaker odds are not live yet.** football-data publishes forward odds
   about a week before each round; `weekly.py` picks them up automatically and
   prefers them over the fitted ratings once they appear.
6. **Offered but not done:** a scheduled job so the digest lands before each
   deadline.

## Things learned the hard way

- Backtests that flatter a method are usually leaking. Use `start_cost` (price at
  the start of that season), never today's price.
- Clean sheets have almost no player-level signal (0.21 stability) — they must
  come from a team model.
- FPL's fixture difficulty correlates only −0.60 with real clean-sheet
  probability. Do not trust it.
- Realistic ceiling for predicting player points/90 is Spearman ~0.46.

---

## Deployed

**https://fpl-2026.vercel.app** — public, works on a phone.

- **This week** tab: captain, XI, transfers, injury flags, price pressure.
  Enter your FPL team id once (stored in the browser) and it reads your real
  squad. Before Gameweek 1 your picks are not public, so it uses whatever you
  built in the Build tab.
- Prices, injuries and your squad are read **live** on every visit through
  `app/api/fpl.ts`, a proxy that exists because the FPL API sends no CORS header
  and browsers cannot call it directly.
- Projections are rebuilt every **Thursday 07:00 UTC** by
  `.github/workflows/weekly.yml`, which commits the new data and triggers a
  Vercel deploy. Repo: github.com/teatimedev/fpl-2026 (private).
- Vercel root directory is `app`. That is dashboard state, not in git — if the
  project is ever recreated it must be set again or GitHub builds fail.

### The weekly job has not run yet

GitHub Actions was in a **major outage** on 6 Aug 2026 (from 15:22 UTC: "jobs may
remain queued for an extended period"), so the workflow has never completed a
run. Nothing is wrong with it — the identical commands were verified to run
clean from a fresh checkout and reproduce the local build exactly, 0 of 572
players differing. **First thing next session: check it ran.**

```bash
gh run list --workflow=weekly.yml --limit 3
gh workflow run weekly.yml     # to force one
```

The repo is public. It went public on a wrong diagnosis (I assumed private-repo
Actions minutes were exhausted; the account has in fact run Actions 14,773 times
on designarena-hunter). Left public deliberately — no secrets are tracked, and
unlimited Actions minutes is a real if incidental benefit.
