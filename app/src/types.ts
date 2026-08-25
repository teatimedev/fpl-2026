export type Pos = 'GKP' | 'DEF' | 'MID' | 'FWD'

export interface AvailabilityForecast {
  source: string
  confidence: string
  note: string
  p_cameo: number
  start_minutes: number
  cameo_minutes: number
  last_updated: string | null
  from_gw: number | null
  through_gw: number | null
  generation_rule?: string | null
}

export interface Player {
  id: number
  name: string
  full_name: string
  team: string
  pos: Pos
  price: number
  proj_gw: number
  proj_6gw: number
  /** per-gameweek projection over the window — absolute index, [gw-1] */
  proj_by_gw: number[]
  /** coarse full-season projection, 38 entries, index 0 = GW1; zeros for played weeks */
  season_by_gw: number[]
  /** this season so far — all 0 pre-season */
  pts_now: number
  mins_now: number
  starts_now: number
  games_now: number
  /** shrunk per-90 rates */
  xg90: number
  xa90: number
  dc90: number
  /** 0–1 share of possible starts */
  start_rate: number
  /** deadline-aware per-GW probabilities and expected minutes */
  start_by_gw: number[]
  play_by_gw: number[]
  mins_by_gw: number[]
  availability_by_gw: (AvailabilityForecast | null)[]
  availability_source: string
  availability_confidence: string
  /** 0–1: how much of the xG estimate is the player's own record vs the positional prior */
  evidence: number
  /** PL seasons with 450+ minutes */
  seasons: number
  mins_proj: number
  sel_pct: number
  pts_last: number
  mins_last: number
  ppg_last: number
  goals_last: number
  assists_last: number
  xgi90_last: number
  defcon_last: number
  cs_last: number
  bonus_last: number
  status: string
  news: string
  is_new: boolean
  joined: string
  pens: number | null
  corners: number | null
  fk: number | null
  note: string
  fdr6: number
  value: number
}

export interface Team {
  name: string
  short: string
  primary: string
  secondary: string
}

export interface Fixture {
  opp: string
  home: boolean
  fdr: number
}

export interface SquadPreset {
  label: string
  cost: number
  xi_proj: number
  picks: { id: number; starting: boolean }[]
}

/* ------------------------------------------------------------- scorecard
   Written by v2/scorecard.py: each pre-deadline refresh archives what the model
   believed, and finished gameweeks are graded against actual points. */
export interface ScorecardDecile { lo: number; hi: number; proj: number; actual: number; n: number }
export interface ScorecardPick { id: number; name: string; pts: number }

export interface ScorecardGw {
  gw: number
  generated: string
  /** false until FPL marks the round data-checked (bonus not final) */
  checked: boolean
  n_pool: number
  n_starters: number
  spearman_pool: number | null
  spearman_starters: number | null
  spearman_played: number | null
  mae_starters: number | null
  bias_starters: number | null
  top20_mean_actual: number | null
  top20_in_actual_top50: number
  deciles: ScorecardDecile[]
  captain?: { model?: ScorecardPick; yours?: ScorecardPick; best: ScorecardPick }
  xi?: { model?: number; yours?: number; best: number }
  cs?: { n: number; brier: number; predicted_rate: number; actual_rate: number }
  availability?: {
    n: number; start_brier: number; appearance_brier: number; minutes_mae: number; minutes_bias: number
    baseline_start_brier?: number; start_brier_lift?: number
    /** P2: the two in-season minutes rules graded on identical rows */
    minutes_rule?: string; n_rules?: number
    recency_start_brier?: number; aggregate_start_brier?: number; recency_vs_aggregate_lift?: number
  }
  availability_groups?: Record<string, Record<string, { n: number; start_brier: number }>>
  /** P7: sum(actual)/sum(proj) over likely starters, per position and ALL */
  level_ratio?: Record<string, number | null>
  ep_next?: { n: number; spearman_pool: number | null; spearman_starters: number | null; mae_starters: number | null }
  /** P3: what happened this week to players the previous retrospective put in each class */
  retro_class?: Record<string, { n: number; next_start_rate: number; next_play_rate: number; next_residual_mean: number }>
  retro_gw?: number
  goals?: { n: number; logloss_model: number | null; logloss_market: number | null; brier_model: number | null; brier_market: number | null; actual_rate: number }
}

export interface ScorecardSummary {
  n_gws: number
  spearman_starters: number | null
  spearman_pool: number | null
  mae_starters: number | null
  bias_starters: number | null
  captain_model: number | null
  captain_yours: number | null
  captain_best: number | null
  xi_model: number | null
  xi_yours: number | null
  xi_best: number | null
  cs_brier: number | null
  cs_predicted_rate: number | null
  cs_actual_rate: number | null
  start_brier?: number | null
  appearance_brier?: number | null
  minutes_mae?: number | null
  minutes_bias?: number | null
  baseline_start_brier?: number | null
  start_brier_lift?: number | null
  recency_start_brier?: number | null
  aggregate_start_brier?: number | null
  recency_vs_aggregate_lift?: number | null
  n_rule_gws?: number
  recency_wins?: number
  level_ratio_cum?: Record<string, number | null>
  spearman_ep_next_starters?: number | null
  spearman_ep_next_pool?: number | null
  mae_ep_next_starters?: number | null
  n_retro_gws?: number
  goal_logloss_model?: number | null
  goal_logloss_market?: number | null
}

export interface Scorecard {
  generated: string
  summary: ScorecardSummary
  gws: ScorecardGw[]
  notes: string[]
}

/* ---------------------------------------------------------------- weekly
   The CI-computed digest for ONE squad (weekly.squad.ids): captaincy, XI,
   two-move combos, a six-week plan and price pressure, all computed by the
   scheduled refresh. Rendered only when the loaded squad matches. */
export interface WeeklyLineup { xi?: number[]; bench?: number[]; captain?: number | null; vice?: number | null }

export interface WeeklySquad {
  ids: number[]
  bank: number
  ft: number
  source: string
  confirmed_at?: string
  entry_id?: number
  changes?: string[]
  lineup?: WeeklyLineup | null
}

export interface WeeklyModel {
  xi: number[]
  bench: number[]
  captain: number
  vice: number
  captain_pts: number
  vice_pts: number
  ranked: { id: number; pts: number }[]
  gw_pts: Record<string, number>
  remaining: Record<string, number>
}

export interface WeeklyCheck { id: number; xi: boolean; flags: string[] }

export interface WeeklySingle {
  out: number; in_: number; gain: number; net: number
  xi_gain?: number; autosub_gain?: number
}
export interface WeeklyPair {
  out: number[]; in_: number[]; gain: number; net: number
  xi_gain?: number; autosub_gain?: number
}
export interface WeeklyTransfers {
  base: number; base_xi?: number; base_autosub?: number; unavailable?: number[]
  singles: WeeklySingle[]; pairs: WeeklyPair[]; advice: string
}

export interface WeeklyPlanWeek {
  gw: number; pts: number; hits: number; captain: number; ft: number
  in_: number[]; out: number[]
}
export interface WeeklyPlan {
  total: number; hold_total: number; diff: number; hits: number; weeks: WeeklyPlanWeek[]
  /** moves the plan makes this week, and whether diff clears the per-move hold threshold */
  n_now?: number; worth_it?: boolean
}

export interface WeeklyPriceRow { id: number; net: number; pressure: number }
export interface WeeklyPrice { locked: boolean; rises: WeeklyPriceRow[]; falls: WeeklyPriceRow[] }
export interface WeeklyDecision {
  kind: 'hold' | 'transfer' | 'rebuild'
  instruction: string
}

/* ------------------------------------------------------------ retro (P3)
   The gameweek review: why each player diverged from the archived projection
   and whether it carries information. Ordering and wording only — it never
   changes a projection or a transfer number. */
export type RetroClass =
  | 'unavailable' | 'minutes_loss' | 'minutes_watch' | 'minutes_gain'
  | 'role_change' | 'variance' | 'on_model'

export interface RetroComponents {
  minutes: number; chance: number; finishing: number; team: number
  defcon: number; bonus: number; other: number; unexplained: number
}

export interface WeeklyRetroRow {
  id: number; proj: number; actual: number; minutes: number
  components: RetroComponents; cls: RetroClass; subtype?: string | null
  note: string; proj_next?: number | null; start_move?: string | null
}

export interface WeeklyRetro {
  gw: number
  counts: Partial<Record<RetroClass, number>>
  graded_gws: number
  act: number[]
  hold: number[]
  table: WeeklyRetroRow[]
  pool: Record<string, number[]>
  push?: string | null
}

export interface RetroSummary {
  generated: string
  latest_gw: number
  gws: { gw: number; n: number; counts: Partial<Record<RetroClass, number>>; generated: string }[]
  classes_graded_gws: number
  by_class: Record<string, { n: number; gws: number; next_start_rate: number | null; next_residual_mean: number | null }>
  note: string
}

export interface Weekly {
  gw: number
  deadline: string
  horizon: number
  generated: string
  squad: WeeklySquad
  model: WeeklyModel
  /** markdown-ish lines with a **bold** lead */
  lineup_issues: string[]
  checks: WeeklyCheck[]
  transfers: WeeklyTransfers
  decision?: WeeklyDecision
  plan?: WeeklyPlan | null
  chips?: ChipsData | null
  price: WeeklyPrice
  retro?: WeeklyRetro | null
}

/* ----------------------------------------------------------------- chips
   Season-long chip timing. `weeks` may be [] and most fields absent once both
   copies of a chip are used — everything is optional and guarded. */
export interface ChipLater { lo: number; hi: number; best_gw?: number; best?: number; best_name?: string }

export interface ChipInfo {
  name: string
  /** [gw, value] or [gw, value, playerName] for triple captain */
  weeks?: [number, number, string?][]
  best_gw?: number
  best?: number
  best_name?: string
  now?: number | null
  now_name?: string
  play?: boolean
  advice?: string
  last_eligible?: number
  later?: ChipLater[]
  /** 3xc only: [gw, value, name] allowing captains outside the squad */
  anyone?: [number, number, string?]
  /** wildcard only: [gw, gap] */
  gap_trend?: [number, number][]
}

export interface ChipsData {
  gw: number
  dgw: Record<string, string[]>
  bgw: Record<string, string[]>
  heuristics: {
    bb_play_min?: number; tc_play_min?: number
    fh_play_min?: number; wc_play_min?: number
  }
  chips: { bboost?: ChipInfo; '3xc'?: ChipInfo; freehit?: ChipInfo; wildcard?: ChipInfo }
  gaps: Record<string, number>
}

/* ---------------------------------------------------------------- movers
   Ownership and price log. Only days of history that actually exist — deltas
   are 0 until the log has run for a while, and the UI says so. */
export interface MoverStat {
  sel: number
  price: number
  d_sel_1: number
  d_sel_7: number
  d_price_7: number
  d_price_season: number
  net_event: number
  spark: number[]
}

export type MoverTopRow = { id: number } & Record<string, number>

export interface Movers {
  days: number
  latest: string
  first: string
  players: Record<string, MoverStat>
  top: {
    bought_7d: MoverTopRow[]
    sold_7d: MoverTopRow[]
    bought_event: MoverTopRow[]
    sold_event: MoverTopRow[]
    risen_7d: MoverTopRow[]
    fallen_7d: MoverTopRow[]
  }
}

/* ---------------------------------------------------------------- ticker
   Model fixtures for every club × remaining gameweek. A double gameweek has
   two entries in fx; a blank week is simply missing. */
export interface TickerFx {
  opp: string
  home: boolean
  cs: number
  xg: number
  xgc: number
  src: string
}
export interface TickerGw { gw: number; fx: TickerFx[] }
export type Ticker = Record<string, TickerGw[]>

/* --------------------------------------------------------------- team news
   Public first-party sources only. Explicit recent absences may feed the model;
   everything nuanced is shown as a candidate for review. */
export interface NewsClaim {
  id: string
  player_id: number
  player: string
  club: string
  claim_type: string
  decision: 'applied' | 'candidate'
  reason: string
  confidence: string
  publisher: string
  url: string
  title?: string
  excerpt: string
  published_at?: string | null
  observed_at?: string
}
export interface NewsSourceHealth {
  id: string; club: string; publisher?: string; url?: string
  status: 'ok' | 'error' | 'unsupported'; error?: string
}
export interface NewsData {
  run?: {
    gw: number; checked_at: string; status: 'green' | 'amber' | 'red'
    coverage: number; claims: number; rebuild_required: boolean
    affected_owned?: number[]
  } | null
  health?: {
    gw: number; checked_at: string; status: 'green' | 'amber' | 'red'
    coverage: number; healthy: number; enabled: number; official_fpl_ok?: boolean
    sources: NewsSourceHealth[]
  } | null
  evidence?: { gw: number; deadline: string; policy: string; claims: NewsClaim[] } | null
}

export interface Data {
  /** The window rolls: start_gw is the next gameweek, horizon the last one modelled. */
  meta: { horizon: number; start_gw?: number; deadline: string; budget: number; generated: string }
  teams: Record<string, Team>
  schedule: Record<string, (Fixture | null)[]>
  players: Player[]
  squads: SquadPreset[]
  scorecard?: Scorecard | null
  weekly?: Weekly | null
  retro?: RetroSummary | null
  chips?: ChipsData | null
  movers?: Movers | null
  ticker?: Ticker | null
  news?: NewsData | null
}

export const SQUAD_SHAPE: Record<Pos, number> = { GKP: 2, DEF: 5, MID: 5, FWD: 3 }
export const XI_MIN: Record<Pos, number> = { GKP: 1, DEF: 3, MID: 2, FWD: 1 }
export const XI_MAX: Record<Pos, number> = { GKP: 1, DEF: 5, MID: 5, FWD: 3 }
export const POS_ORDER: Pos[] = ['GKP', 'DEF', 'MID', 'FWD']
export const BUDGET = 100.0
export const MAX_PER_CLUB = 3
