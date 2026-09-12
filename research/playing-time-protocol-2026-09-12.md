# Playing-time experiment protocol

Written before running the new comparison, 12 September 2026.

Question: can observed player selection and substitution history improve the
fixed 20% conditional cameo probability and fixed-duration 60-minute eligibility?

- Fit positional distributions on recorded 2022/23 GW16–38 rows only.
- Choose evidence strength from 4, 8, 16 and recency half-life from 6, 12,
  infinity using 2023/24 only. A positional-only candidate is included.
- Freeze that choice before evaluating 2024/25 and 2025/26 independently.
- Predict each whole gameweek before its first recorded kickoff, using only
  earlier rounds whose matches have finished (kickoff plus three hours).
  Earliest recorded kickoff minus two hours is a conservative calendar proxy;
  authentic historical FPL deadline calendars and availability are unavailable.
- The player universe is the historical fixture panel, not today's survivor
  roster. Use historical positions. Remove assistant-manager rows, missing
  start labels and conflicting duplicates. Do not read present-day prices,
  overlays, team strength, news or player identities into the estimates.
- Evaluate conditional cameo probability on non-starts, P60 conditional on
  starts, and conditional minutes on starts and cameos. Record sample counts,
  Brier scores, minutes RMSE/MAE, and calibration. Show an active-player cohort
  defined only by positive minutes in the preceding six known fixtures.
- Select settings using the equal-weight sum of cameo and starter-P60 Brier
  scores in the active cohort. Report the positional-only baseline as well as
  the legacy fixed assumptions. Use gameweek-block resampling for uncertainty.
- The backward panel does not contain fit-but-benched versus injured labels.
  A cameo result here cannot by itself validate the production availability-
  conditioned probability. Any such candidate starts as a frozen forward
  experiment. Correct FPL scoring identities do not require statistical tuning.

This experiment measures components, not total FPL points or season strategy.
