# 2026/27 predictions

Built on the v2 pipeline: the fitted Dixon-Coles team model (`v2/season_view.json`),
the full-season player projections (`v2/projections_season.json`) and the real
380-fixture schedule. Two Monte Carlo layers on top — 100,000 simulated seasons
for the league, 40,000 for the players, sharing the same per-season club draws
so teammates rise and fall together.

Every number below is a simulated frequency, not a projection read off a table.

---

## The picks

| | pick | confidence |
|---|---|---|
| **Winner** | **Arsenal** | 42% |
| **Top 4** | **Arsenal, Man City, Liverpool, Man Utd** | 86 / 66 / 38 / 25% |
| **Best player** | **Erling Haaland** | 14% to finish top, 26% top three |
| **Top scorer** | **Erling Haaland** | 41%, ~22 goals |
| **Overachiever — team** | **Nottingham Forest** (16th → 11th) | 27% top six |
| **Overachiever — player** | **Riccardo Calafiori** (£5.5m) | 29% to finish top ten overall |
| **Underachiever — team** | **Sunderland** (7th → 17th) | 17% relegation risk |
| **Underachiever — player** | **Morgan Rogers** (£7.5m, 27% owned) | 8th-priciest mid, 34th on the model |
| **Relegated** | **Hull, Coventry, Ipswich** | 58 / 58 / 55% |

---

## Winner — Arsenal, 42%

Arsenal's defensive rating is the outlier of the whole model: **+0.413 against
City's +0.142** and nobody else above zero. It is not a one-season artefact —
they have been the best defence in the league for three straight years
(+0.71, +0.46, +0.65 relative to the mean). The simulation concedes them 31
goals across 38 games; nobody else is under 40.

City are the live threat at 24%. They have the better attack (+0.373 vs +0.359)
and the deeper squad, but Guardiola has gone and Maresca's side carries a 20%
shrink towards the mean plus the widest defensive uncertainty of any elite club
(±0.186). Liverpool at 9% is the drop-off: Iraola inherits a side that has
declined in each of the last two fits, Salah has left the game entirely, and
Isak — their first-choice striker and penalty taker — started 8 league games
last season.

Simulated champion's total: **86 points** (5–95%: 75–98). The four real seasons
in the database finished on 89, 91, 84, 85.

## Top 4 — Arsenal, Man City, Liverpool, Man Utd

Arsenal 86%, City 66%, Liverpool 38%, United 25%.

United are the coin-flip pick and the one to argue with. They rated **+0.30
attack in 2025/26**, comfortably their best of the four seasons, Carrick is now
permanent, and Mbeumo and Cunha have a full season together. The alternatives
are tightly bunched: Newcastle 23%, Chelsea 21%, Villa 20%, Bournemouth 19%.
Any of those four is a defensible swap and the model cannot separate them —
they sit within 2.5 points of each other over a whole season.

Fifth place is genuinely open. Nobody outside the top two is better than
one-in-four to make the four.

## Best player — Haaland, but it is close

This is the one place where the projection table and the answer disagree, and
the disagreement is the point.

| | projection | mean in sim | sd | **wins** | top 3 |
|---|---|---|---|---|---|
| Haaland | 224 | 173 | **58** | **13.6%** | 26% |
| B.Fernandes | 228 | 174 | 45 | 8.5% | 22% |
| Saka | 205 | 162 | 57 | 10.3% | 22% |
| Palmer | 192 | 154 | 58 | 8.5% | 17% |

Fernandes projects *higher* than Haaland and wins 40% less often. His points are
made of appearances, defensive contributions and bonus — near-deterministic once
he is on the pitch, which makes him the best bet for a top-ten finish (47%) and
a poor one for first. Haaland's are made of goals, which are Poisson, which is
exactly the shape you need to win a maximum.

Saka at 10.3% is the value pick of the three: £9.5m against Haaland's £15.5m,
**10.5% owned against 73.3%**, and only a quarter less likely to top the table.

## Top scorer — Haaland, 41%

Nobody else is in double figures. Thiago (BRE) 8.7% and Isak (LIV) 7.4% are the
next two, and both need something to go right — Thiago to repeat a 22-goal
season that has no history behind it, Isak to actually start after 8 league
starts last year.

Haaland's simulated mean is 21.5 goals with a 54% chance of clearing 20 and 34%
of clearing 25. The Golden Boot itself lands on **26.5 goals** on average.

## Overachiever

**Team — Nottingham Forest.** They finished 16th on 44 points and the underlying
numbers deserved **49.2** — the second-largest underperformance in the league.
Their attack and defence ratings have improved in three of the last four
seasons, and Glasner arrives having already made Crystal Palace better than
their squad. Model has them 11th, consensus 14th, 27% for a top-six finish.

*The bolder alternative is Newcastle*, whom the model puts **6th against a
consensus 13th** — the biggest gap in the league. I am not leading with it: that
rating leans on 2022–24 evidence against a clean four-season decline
(+0.25/+0.48 → +0.05/−0.08), they have sold Tonali for £100m+, and Jaissle has
replaced Howe. The model cannot see any of that. Take it if you want variance.

**Player — Riccardo Calafiori, £5.5m.** A 29% chance of finishing in the overall
top ten and a 14% chance of clearing 200 points, from a defender priced at
£5.5m. He is the best expression of the two things the model is most confident
about: Arsenal's defence, and the fact that attacking full-backs now score like
midfielders. At 19% owned he is not a secret; if you want one that is,
**Malick Thiaw (NEW, £5.0m, 2.0% owned)** carries a 15% top-ten chance.

## Underachiever

**Team — Sunderland, 7th → 17th.** They took 54 points from a performance worth
**47.3**, and did it on a **minus-six goal difference** — the only side in the
top half with a negative one. Sides that finish seventh on a negative GD do not
stay there. They also have a single season of top-flight record, which gives
them the widest uncertainty band in the model (±0.287 attack), so this is the
call most likely to be wrong in either direction: 17% relegation risk, but also
a 10% chance of the top four.

**Player — Morgan Rogers, £7.5m, 26.5% owned.** The British transfer record,
the 8th-most expensive midfielder in the game, the third-most-owned — and
**34th among midfielders on the model**. A £117m move to a club with a new
manager, into a forward line already holding João Pedro, Palmer, Estêvão,
Gittens, Jackson and Welbeck. 0.1% to finish top. Semenyo (£8.5m, 28% owned)
is the same trade with corroboration — Maresca is playing him wide, and he said
so himself.

## Relegated — Hull, Coventry, Ipswich

58%, 58%, 55%. All three promoted clubs going down together is only a **19%**
event; the honest read is **1.7 of the 3**, so one of them probably stays up.

The model literally cannot separate Hull from Coventry — neither has a Premier
League record, so both take the identical fitted promoted-club prior. FPL's own
pricing is the only tiebreak available: **Hull £77.0m, Coventry £81.5m, Ipswich
£82.5m** for their sixteen dearest players, which is the order given above.
Ipswich also have 2024/25 data and Gary O'Neil.

**Sunderland (17%) is the most likely established club to go down**, then Palace
(15%) and Spurs (15%).

---

## Full simulated table

| | team | pts | 5–95% | title | top 4 | rel |
|---|---|---|---|---|---|---|
| 1 | ARS | 78.4 | 60–96 | 41.6% | 85.5% | 0.0% |
| 2 | MCI | 71.9 | 49–93 | 24.0% | 65.9% | 0.4% |
| 3 | LIV | 62.3 | 39–85 | 8.6% | 38.4% | 2.5% |
| 4 | MUN | 58.4 | 39–78 | 3.1% | 24.8% | 2.2% |
| 5 | AVL | 56.5 | 37–76 | 2.1% | 19.7% | 3.0% |
| 6 | NEW | 55.9 | 33–79 | 3.6% | 22.7% | 6.1% |
| 7 | CHE | 55.4 | 33–78 | 3.2% | 21.1% | 6.3% |
| 8 | BHA | 54.5 | 35–74 | 1.5% | 15.7% | 4.4% |
| 9 | BRE | 54.2 | 34–74 | 1.5% | 15.6% | 5.1% |
| 10 | BOU | 54.3 | 32–77 | 2.6% | 18.8% | 7.2% |
| 11 | NFO | 52.5 | 31–75 | 2.0% | 15.2% | 9.0% |
| 12 | FUL | 50.5 | 29–74 | 1.6% | 12.5% | 11.8% |
| 13 | LEE | 48.7 | 28–71 | 1.1% | 9.6% | 13.9% |
| 14 | EVE | 48.6 | 31–68 | 0.3% | 6.2% | 9.9% |
| 15 | TOT | 48.3 | 27–71 | 1.0% | 9.3% | 14.7% |
| 16 | CRY | 48.2 | 27–71 | 0.9% | 8.9% | 14.8% |
| 17 | SUN | 47.7 | 26–72 | 1.2% | 9.7% | 17.0% |
| 18 | IPS | 33.5 | 19–49 | 0.0% | 0.1% | 55.4% |
| 19 | HUL | 32.8 | 18–50 | 0.0% | 0.2% | 58.2% |
| 20 | COV | 32.8 | 18–50 | 0.0% | 0.1% | 58.1% |

---

## What had to be built, and what it exposed

The v2 pipeline hands over point estimates. A season prediction needs
distributions, so three things were measured from the project's own data and
added:

- **Year-to-year drift in club strength** — attack sd 0.111, defence 0.099 over
  51 consecutive club-seasons, after bootstrapping out single-season estimation
  noise. The production fit's evidence is centred on June 2025 and the season
  runs to May 2027, a **1.54-year horizon**, so drift scales by √1.54.
- **The promoted-club prior has a width** — nine promoted club-seasons sit around
  it with attack sd 0.158. Sunderland came up last year and defended *better*
  than the league average; Southampton came up 0.70 below on attack.
- **The minutes residual** — a player who started 28+ games averages 26.8 the
  next season, sd 9.7, with a **21% chance of fewer than 20 starts**. Sampled
  non-parametrically so the injury tail keeps its real shape.

Both simulations were then checked against the four real seasons in the
database. The league sim lands the champion on 86 (real mean 87), fourth on 68
(68) and the survival line on 36 (38). The player sim lands the top scorer on
244 (248), 4.2 players past 200 (4.25), and the Golden Boot on 26.5 (28).

### Three things in the pipeline worth a look

1. **The cameo term over-credits substitutes.** `project()` scales attacking,
   DefCon and bonus by `p_play` (which includes a 20% cameo probability) but
   leaves the minutes fraction at minutes-per-*start*. A 25-minute substitute is
   therefore given a full starter's chance of reaching 10 defensive actions.
   243 players projected to start under 35% of games collect 7,573 points
   between them, 59% of their appearances being cameos.

2. **`calibrate()` is fitted on a survivor cohort.** The per-position multiplier
   is set against players who logged 2,000+ minutes last season, then applied
   across 38 gameweeks to everyone. The result: v2 projects **38 players past
   150 points against 16–28 in the real seasons**, and hands out roughly twice
   as many total points as a season contains. The very top is unaffected (5 past
   200, against 4–5 in reality), which is why the ordering — the part the
   backtest validated — is still trustworthy and only the level is not.

3. **The volume multiplier double-counts team strength over a full season.**
   `f['xg']/1.45` is a fixture adjustment, correct over a six-week window. Over
   38 games it averages to the club's overall attacking strength — which is
   already inside a stayer's own xG/90, because that is where it was measured.
   Regressed against what those players actually delivered, the coefficient
   comes out at **0.56, not the 1.00 the model applies**. Removing half of it
   moves Haaland from 246 projected points to 228 and Fernandes past him. This
   is v1's error (README: *"Players who stayed put get no team-strength
   multiplier"*) surviving in a different form.

None of these change the ordering enough to move the picks. All three are worth
fixing before the level numbers are quoted anywhere.

### Known limits

- The sim's bottom club averages 23 points against a real 18. Independent-match
  models cannot produce a side that collapses to 16, so **relegation
  probabilities for the promoted three are if anything understated**.
- No bookmaker season odds exist for 2026/27 yet (`market` is empty), so
  "expectation" for the over/underachiever picks is built from last season's
  table plus FPL squad value rather than a real market line. Re-run once
  football-data posts forward prices.
- Ten new managers. The 20% shrink towards the mean is a judgement in the
  pipeline, not a measurement, and it is doing real work on City, Liverpool,
  Chelsea, Newcastle and Spurs.
