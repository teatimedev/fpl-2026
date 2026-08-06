# Three squads for Gameweek 1

*Updated 6 Aug 2026 after a fix to the minutes model — see "Why the big names
are missing" at the bottom, and the changelog in README.md.*

Every squad below is **£100.0m or under, 15 players (2/5/5/3), max 3 per club**,
with a legal starting XI — checked independently by `validate.py` against the
raw API prices, not just asserted by the optimiser.

"Proj" is this project's own estimate of points over GW1–6. Treat it as a way to
compare players, not as a prediction. **It does not include captaincy** — that
matters, and it's the main reason the model looks harsh on premiums.

---

## Option A — Model Optimum
**£100.0m · 4-5-1 · 341.9 projected · captain Bruno Fernandes**

The highest-scoring legal XI the model can build.

| | Player | Club | Price | Proj | Owned |
|---|---|---|---|---|---|
| GKP | Raya | ARS | £6.0m | 27.1 | 30.9% |
| DEF | **Gabriel** (V) | ARS | £8.0m | 35.3 | 26.1% |
| DEF | Guéhi | MCI | £6.0m | 28.6 | 23.8% |
| DEF | Tarkowski | EVE | £6.0m | 28.2 | 9.9% |
| DEF | Senesi | TOT | £6.0m | 28.0 | 10.8% |
| MID | **Bruno Fernandes** (C) | MUN | £12.0m | 39.0 | 48.7% |
| MID | Rogers | CHE | £7.5m | 34.1 | 30.0% |
| MID | Anderson | MCI | £6.5m | 33.3 | 11.9% |
| MID | Rice | ARS | £7.5m | 29.8 | 22.4% |
| MID | Wilson | LEE | £6.5m | 29.2 | 8.5% |
| FWD | Thiago | BRE | £8.0m | 29.3 | 16.0% |
| *Bench* | Verbruggen | BHA | £4.5m | 21.6 | 16.8% |
| *Bench* | Mitchell | CRY | £4.5m | 21.4 | 7.0% |
| *Bench* | Beto | EVE | £5.5m | 23.4 | 3.2% |
| *Bench* | Barry | EVE | £5.5m | 19.5 | 0.9% |

Club spread: ARS ×3, EVE ×3, MCI ×2.

**The case for it.** Bruno at £12.0m with Hull away then Ipswich at home is the
best opening fixture in the game, and he takes penalties, corners and free-kicks
after 24 assists last season. The bench is the strongest of the three — Beto and
Mitchell both project 21+, so a blank from a starter isn't fatal.

**The risk.** No Haaland, and only one forward starting. You're relying on
Bruno's captaincy paying off against the field's Haaland captaincy.

---

## Option B — Haaland Build
**£100.0m · 5-4-1 · 338.7 projected · captain Haaland**

The same engine, forced to include Haaland. It costs 3.2 projected points before
captaincy is considered — and captaincy is exactly what closes that gap.

| | Player | Club | Price | Proj | Owned |
|---|---|---|---|---|---|
| GKP | Raya | ARS | £6.0m | 27.1 | 30.9% |
| DEF | **Gabriel** (V) | ARS | £8.0m | 35.3 | 26.1% |
| DEF | Guéhi | MCI | £6.0m | 28.6 | 23.8% |
| DEF | Tarkowski | EVE | £6.0m | 28.2 | 9.9% |
| DEF | Senesi | TOT | £6.0m | 28.0 | 10.8% |
| DEF | Lacroix | CHE | £6.0m | 27.1 | 12.0% |
| MID | Rogers | CHE | £7.5m | 34.1 | 30.0% |
| MID | Anderson | MCI | £6.5m | 33.3 | 11.9% |
| MID | Rice | ARS | £7.5m | 29.8 | 22.4% |
| MID | Wilson | LEE | £6.5m | 29.2 | 8.5% |
| FWD | **Haaland** (C) | MCI | £15.5m | 38.0 | 74.8% |
| *Bench* | Verbruggen | BHA | £4.5m | 21.6 | 16.8% |
| *Bench* | Millar | HUL | £5.0m | 14.8 | 0.1% |
| *Bench* | Destan | HUL | £4.5m | 10.4 | 2.6% |
| *Bench* | Obi | MUN | £4.5m | 7.9 | 1.2% |

Club spread: MCI ×3, ARS ×3, CHE ×2, HUL ×2.

**The case for it.** Two Chelsea assets (Rogers and Lacroix), the best defence of
the three, and the captain that three quarters of the game owns. Because the
projections exclude captaincy, this squad is *understated* relative to Option A:
captaining Haaland roughly doubles his 38 to 76, which more than covers the 3.2
point gap.

**The trade-off.** The bench is fodder — two Hull players and a £4.5m United
forward. If two starters miss, you're exposed. It also has no Bruno Fernandes.

**Verdict after simulating**: solid, but Option A beats it 56–44 with a better
floor, a better ceiling and a much better bench. The captaincy hedge this squad
buys turns out to be worth almost nothing (see the simulation section) — Option A
captains Bruno for the same 41 points.

---

## Option C — Differential
**£99.5m · 4-5-1 · 318.8 projected · captain Elliot Anderson · £0.5m banked**

Nothing owned by more than 25% of managers.

| | Player | Club | Price | Proj | Owned |
|---|---|---|---|---|---|
| GKP | Kelleher | BRE | £5.0m | 23.4 | 5.6% |
| DEF | Guéhi | MCI | £6.0m | 28.6 | 23.8% |
| DEF | Virgil van Dijk | LIV | £6.5m | 28.3 | 16.2% |
| DEF | Tarkowski | EVE | £6.0m | 28.2 | 9.9% |
| DEF | Senesi | TOT | £6.0m | 28.0 | 10.8% |
| MID | **Anderson** (C) | MCI | £6.5m | 33.3 | 11.9% |
| MID | **Semenyo** (V) | MCI | £8.5m | 30.7 | 22.4% |
| MID | Gibbs-White | NFO | £8.0m | 30.0 | 11.6% |
| MID | Rice | ARS | £7.5m | 29.8 | 22.4% |
| MID | Wilson | LEE | £6.5m | 29.2 | 8.5% |
| FWD | Thiago | BRE | £8.0m | 29.3 | 16.0% |
| *Bench* | Pickford | EVE | £5.5m | 22.9 | 9.0% |
| *Bench* | Lacroix | CHE | £6.0m | 27.1 | 12.0% |
| *Bench* | Watkins | AVL | £8.0m | 27.3 | 12.5% |
| *Bench* | Beto | EVE | £5.5m | 23.4 | 3.2% |

Club spread: MCI ×3, EVE ×3, BRE ×2.

**The case for it.** By far the best bench — Watkins and Lacroix as subs is close
to a second XI, and every sub projects 22+.

**The risk.** 23 points behind Option A before captaincy, no Haaland *and* no
Bruno, and the captain is a £6.5m defensive midfielder whose biggest scoring
source (DefCon) should shrink at Man City. Captaincy is where FPL is won, and
this squad is weakest exactly there.

---

## Option D — Conventional
**£100.0m · 3-5-2 · 331.2 projected · captain Bruno Fernandes, Haaland vice**

The standard FPL heuristic, imposed as a constraint: keep all seven goalkeepers
and defenders under £34.0m and push the money up front. This is what "cheap
defence, expensive attack" actually produces with this season's prices.

| | Player | Club | Price | Proj | Owned |
|---|---|---|---|---|---|
| GKP | Verbruggen | BHA | £4.5m | 21.6 | 16.8% |
| DEF | Guéhi | MCI | £6.0m | 28.6 | 23.8% |
| DEF | Tarkowski | EVE | £6.0m | 28.2 | 9.9% |
| DEF | Calafiori | ARS | £5.5m | 25.8 | 16.0% |
| MID | **Bruno Fernandes** (C) | MUN | £12.0m | 39.0 | 48.7% |
| MID | Rogers | CHE | £7.5m | 34.1 | 30.0% |
| MID | Anderson | MCI | £6.5m | 33.3 | 11.9% |
| MID | Gibbs-White | NFO | £8.0m | 30.0 | 11.6% |
| MID | Wilson | LEE | £6.5m | 29.2 | 8.5% |
| FWD | **Haaland** (V) | MCI | £15.5m | 38.0 | 74.8% |
| FWD | Beto | EVE | £5.5m | 23.4 | 3.2% |
| *Bench* | Steele | BHA | £4.0m | 1.4 | 4.6% |
| *Bench* | Brau | COV | £4.0m | 11.8 | 0.1% |
| *Bench* | Amenda | COV | £4.0m | 11.8 | 0.5% |
| *Bench* | Destan | HUL | £4.5m | 10.4 | 2.6% |

**What it costs: 10.7 projected points over six gameweeks, about 1.8 per week.**

**What it buys:** the only squad here holding **both Haaland and Bruno**, which
is the best captaincy position in the game — you pick whichever has the better
fixture each week rather than being locked into one. It also has by far the
highest ceiling.

**What it gives up:** the bench is genuinely dead (Steele projects 1.4), so an
injury to a starter costs you a blank rather than a sub.

---

## The simulation — which squad actually wins

Run with `.venv/bin/python simulate.py`. 40,000 Monte Carlo runs of GW1–6 that
model what a projection cannot: **team-level clean-sheet correlation** (three
Arsenal defenders blank together, they are one bet not three), **auto-subs**, and
**captaincy**. Calibrated so each player's simulated mean matches the projection
model to within 0.16 points a gameweek.

| Squad | Mean | p10 | p90 | SD | Captain | Auto-subs | Beats template |
|---|---|---|---|---|---|---|---|
| **A — Model Optimum** | **390** | **337** | **445** | **42.1** | 41 | **16.4** | **80%** |
| B — Haaland Build | 381 | 326 | 438 | 43.4 | 41 | 9.8 | 73% |
| D — Conventional | 378 | 324 | 435 | 43.4 | 42 | 11.4 | 72% |
| C — Differential | 358 | 312 | 406 | 36.6 | 33 | 13.7 | 58% |
| The template (£100m, most-owned) | 348 | 296 | 402 | 41.2 | 41 | 10.3 | — |

Head to head, P(row beats column):

| | A | B | C | D | Template |
|---|---|---|---|---|---|
| **A** | — | 56% | 73% | 58% | 80% |
| B | 44% | — | 68% | 52% | 73% |
| C | 27% | 32% | — | 34% | 58% |
| D | 42% | 48% | 65% | — | 72% |

### What this changes

**1. Option A wins on floor *and* ceiling.** It has the highest mean (390), the
highest 10th percentile (337) and the highest 90th percentile (445), with the
lowest spread. That combination is unusual and it makes A the clear pick.

**2. The captaincy argument for the conventional shape doesn't survive contact.**
I said last time that holding both Haaland and Bruno was worth the 1.8 points a
week Option D gives up. The simulation says it isn't: captaincy contributes 41,
41 and 42 points to A, B and D respectively — **effectively identical**. You only
captain one player a week, and A's Bruno is as good an armband as D's choice
between two. The optionality is worth roughly nothing.

**3. Bench strength is the sleeper, and it's worth more than the shape debate.**
Auto-subs are worth **16.4 points** to Option A over six gameweeks against 9.8
for Option B. That 6.6-point gap is most of A's 9-point lead. A strong bench
never appears in a projected XI total, and it is the single most underrated thing
in these squads.

**4. Option C fails at its own job.** A differential squad is supposed to buy
*variance* — the chance of a big swing. Option C has the **lowest** standard
deviation of the five (36.6) and the lowest ceiling (p90 of 406). It isn't a
high-variance play, it's just a worse squad. Drop it.

**5. All four comfortably beat the template.** Even the weakest beats the
most-owned legal £100m squad 58% of the time. Note that the naive "15 most-owned
players" squad costs **£111.5m** and is not a team anyone can actually field —
comparing against it was flattering it badly, and the numbers above use the
best legal approximation instead.

### What the simulation does not model

- **New injuries** beyond the flags showing today, and any transfers before the
  1 September deadline.
- **The actual field.** The template is a proxy for "the average manager", not a
  distribution of ten million real squads.
- Fixture difficulty uses FPL's own FDR, which is coarse.

---

## Playing GW1–6 with transfers

`plan.py` solves the whole horizon as one integer program: which 15 to own in
*every* gameweek, who starts, who wears the armband, and when to transfer —
subject to £100.0m, 2/5/5/3 and max-3-per-club **in every week**, plus FPL's real
free-transfer accounting (one a week, bank up to five, −4 per extra).

This needed per-gameweek projections rather than one horizon average, since a
transfer plan trades entirely on the week-to-week swing. Haaland ranges from 7.14
in GW3 and GW5 down to 5.54 in GW4 and GW6; Gabriel from 6.75 in GW1 to 5.05 in GW2.

### What transfers are worth

| Squad | Held all six weeks | Played with transfers | Gain |
|---|---|---|---|
| **A — Model Optimum** | 381.4 | **384.7** | +3.3 |
| B — Haaland Build | 379.2 | 383.5 | +4.3 |
| D — Conventional | 372.4 | 379.4 | +7.1 |
| C — Differential | 358.1 | 372.0 | **+13.9** |

**Three findings:**

1. **Transfers are worth less than you'd think from a good starting squad** —
   +3.3 points over six weeks for Option A. Fixture swings across six gameweeks
   are small, and a squad that's already near-optimal has little to correct.
2. **They're worth far more from a bad one.** Option C gains +13.9, Option D
   +7.1. Transfers are a *correction mechanism*, so the weaker your start, the
   more they buy back. This is the real argument against agonising over the
   opening squad — but note it never fully closes the gap.
3. **The ranking doesn't change, but the gaps close sharply.** A still leads, but
   A over B narrows from 9 points to 1.2, and A over D from 12 to 5.3. Once
   transfers are in play, A, B and D are within a rounding error of each other.

**Never take a hit in the opening six weeks.** Every optimal plan takes zero,
across all four squads. A −4 is not recoverable over this horizon.

**Planning the GW1 squad with the transfer path in mind produces exactly Option
A's 384.7** — so A is already the right starting point once transfers are
accounted for, not just as a static pick.

### The suggested path for Option A

| GW | Captain | Move |
|---|---|---|
| 1 | Gabriel | — |
| 2 | Bruno Fernandes | — (bank the transfer) |
| 3 | Bruno Fernandes | — (bank again) |
| 4 | Rogers | Thiago → João Pedro, Guéhi → Van Dijk |
| 5 | Bruno Fernandes | Van Dijk → Guéhi |
| 6 | Gabriel | Guéhi → Lacroix, Senesi → Mukiele |

Note the Guéhi → Van Dijk → Guéhi round trip in GW4–5. That is the optimiser
exploiting a one-week fixture edge with perfect foresight, and it is not advice —
in reality you would not spend two transfers renting a defender for a week. Treat
the plan as *where the fixture swings are*, not as instructions.

### The important caveat

This plans against **expected values with nothing going wrong**. In practice the
main value of a transfer is reacting to an injury, a red card or a player losing
his place — none of which this can anticipate. So **+3.3 is a floor on what
transfers are worth, not a ceiling**. It also assumes static prices and ignores
FPL's sell-price rule (you only bank half of any rise), both of which make the
plan slightly optimistic.

---

## Why the big names are missing

Two separate reasons, and it's worth keeping them apart.

### 1. There was a bug, now fixed

The minutes model derived expected minutes from *last season's total minutes*,
which carries an injury-hit season forward at full strength. But how long a
player lasts when he starts is stable — nearly every first-choice player is
between 85 and 93 minutes per start. What varies is how many games he starts.

Isak averaged 86.8 minutes per start but only started 8 games, so the model had
Liverpool's £9.0m first-choice striker down as a **19-minute player**. That was
wrong. The model now splits minutes-per-start from start-rate, and shrinks the
observed start rate towards the club's pecking order in proportion to how little
of it was actually observed.

| Player | Proj before | Proj after |
|---|---|---|
| Isak | 6.6 | **20.8** |
| Estêvão | 8.1 | **11.7** |
| Doku | 19.4 | 23.4 |
| Cherki | 21.3 | 25.6 |
| Palmer | 19.9 | 21.4 |
| Saka | 27.2 | 28.5 |

Durable players came down slightly (Bruno 41.2 → 39.0) because the old formula
carried a 1.05 inflation factor. The squads changed as a result: Option A moved
from 3-5-2 to 4-5-1 and picked up a much better bench.

### 2. The premium bracket is genuinely poor value this season

Even after the fix, this holds — median projected points per £m for players who
start:

| Bracket | Players | Median /£m |
|---|---|---|
| £4.0–5.5m | 99 | 0.521 |
| £5.5–6.5m | 69 | 0.577 |
| £6.5–7.5m | 16 | 0.603 |
| £7.5–9.0m | 12 | 0.606 |
| **£9.0m+** | **4** | **0.454** |

The £6.5–9.0m band is the sweet spot and £9.0m+ is the worst bracket in the game.
Isak (0.38), Palmer (0.37) and Haaland (0.41) are all below every other band's
median. That's not the model disliking them — it's £100m of budget going further
elsewhere.

### 3. Chelsea are actually well represented

Chelsea appear in all three squads, via their two best-value assets:

- **Morgan Rogers £7.5m — 0.79 per £m**, one of the best rates in the game. He
  started 37 games last season and is the model's top-rated midfielder outside
  Bruno.
- **Maxence Lacroix £6.0m — 0.80 per £m**, the best-value Chelsea asset full stop.

What's missing is **Palmer at £9.5m** (0.37) — he only started 24 games last
season, and Chelsea's midfield now also contains a £117m British-record signing.
And **Estêvão at £6.5m** (0.30), who is fourth in Chelsea's midfield pecking
order on price. The research argues he'll push from 12 starts towards 20; the
model won't assume that, so he's a judgement call you can make yourself in the app.

## Why so many £6.0m defenders?

Short version: **DefCon changed the maths and the orthodoxy hasn't caught up.**
Defensive contribution points only arrived in 2025/26, and defenders clear their
threshold (10 actions) more easily than midfielders and forwards clear theirs
(12) — because clearances, blocks, interceptions and tackles are their job.

Last season's actuals, using today's prices, back this up:

| Position | Median pts per £m | Median points from *explosive* sources |
|---|---|---|
| GKP | 24.5 | 7% |
| **DEF** | **24.2** | **19%** |
| MID | 20.7 | 40% |
| FWD | 19.2 | 58% |

Eleven of last season's top thirty scorers were defenders. Gabriel finished third
overall on 209.

**But the second column is the whole argument for the orthodoxy.** Those defender
points are almost entirely floor, not ceiling. Senesi took 18% of his points from
goals, assists and bonus; Lacroix 15%. They're metronomes — appearance points,
clean sheets, DefCon, week after week. Haaland takes 73% of his from explosive
sources, Bruno 67%.

So both things are true at once:

- **On expected points, the model is right.** Defenders return more per £m, and
  the £6.0m band (Guéhi, Senesi, Tarkowski, Lacroix — all 26–28 projected) is
  the single best-value cluster in the game.
- **On ceiling, the orthodoxy is right.** A £6.0m defender's good week is 8–9
  points. Haaland's is 20+. If you need to *win* a mini-league rather than
  finish respectably, you need weeks that swing, and defenders don't provide them.

Which is correct depends on what you're playing for. Load **Option A** and
**Option D** side by side in the app and compare the pitch views — that contrast
is the decision.

### The caveat that matters most

**The optimiser doesn't model captaincy.** The armband doubles a player's score,
so a premium you captain most weeks is worth far more than his raw projection
suggests. This is the single strongest argument for Option B, and the reason I
wouldn't read the 3.2-point gap between A and B as meaningful.
