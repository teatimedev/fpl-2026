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

export interface Data {
  meta: { horizon: number; deadline: string; budget: number; generated: string }
  teams: Record<string, Team>
  schedule: Record<string, (Fixture | null)[]>
  players: Player[]
  squads: SquadPreset[]
}

export const SQUAD_SHAPE: Record<Pos, number> = { GKP: 2, DEF: 5, MID: 5, FWD: 3 }
export const XI_MIN: Record<Pos, number> = { GKP: 1, DEF: 3, MID: 2, FWD: 1 }
export const XI_MAX: Record<Pos, number> = { GKP: 1, DEF: 5, MID: 5, FWD: 3 }
export const POS_ORDER: Pos[] = ['GKP', 'DEF', 'MID', 'FWD']
export const BUDGET = 100.0
export const MAX_PER_CLUB = 3
