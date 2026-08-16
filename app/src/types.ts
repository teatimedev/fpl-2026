export type Pos = 'GKP' | 'DEF' | 'MID' | 'FWD'

export interface Player {
  id: number
  name: string
  full_name: string
  team: string
  pos: Pos
  price: number
  proj_gw: number
  proj_6gw: number
  /** per-gameweek projection over GW1..horizon — what weekly decisions use */
  proj_by_gw: number[]
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
}

export interface Scorecard {
  generated: string
  summary: ScorecardSummary
  gws: ScorecardGw[]
  notes: string[]
}

export interface Data {
  /** The window rolls: start_gw is the next gameweek, horizon the last one modelled. */
  meta: { horizon: number; start_gw?: number; deadline: string; budget: number; generated: string }
  teams: Record<string, Team>
  schedule: Record<string, (Fixture | null)[]>
  players: Player[]
  squads: SquadPreset[]
  scorecard?: Scorecard | null
}

export const SQUAD_SHAPE: Record<Pos, number> = { GKP: 2, DEF: 5, MID: 5, FWD: 3 }
export const XI_MIN: Record<Pos, number> = { GKP: 1, DEF: 3, MID: 2, FWD: 1 }
export const XI_MAX: Record<Pos, number> = { GKP: 1, DEF: 5, MID: 5, FWD: 3 }
export const POS_ORDER: Pos[] = ['GKP', 'DEF', 'MID', 'FWD']
export const BUDGET = 100.0
export const MAX_PER_CLUB = 3
