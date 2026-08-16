import type { Player, Ticker, TickerFx } from './types'
import type { Lineup } from './model'

/* ---------------------------------------------------------- model fixtures
   The ticker carries the model's view of every remaining fixture. */
export function fxFor(ticker: Ticker | null | undefined, team: string, gw: number): TickerFx[] {
  return ticker?.[team]?.find(r => r.gw === gw)?.fx ?? []
}

export type Tone = 'good' | 'ok' | 'warn' | 'bad'

/** Clean-sheet probability → chip colour. */
export const csTone = (cs: number): Tone =>
  cs >= 0.45 ? 'good' : cs >= 0.30 ? 'ok' : cs >= 0.20 ? 'warn' : 'bad'

/** Expected goals → chip colour. */
export const xgTone = (xg: number): Tone =>
  xg >= 1.8 ? 'good' : xg >= 1.4 ? 'ok' : xg >= 1.0 ? 'warn' : 'bad'

/**
 * Live I/O for the weekly decision.
 *
 * Projections are baked in at build time and refreshed weekly; prices,
 * injuries and your actual squad are fetched live through the API proxy,
 * because those are the things that change between deploys. The model itself
 * — XI, captain, transfer scoring — lives in model.ts.
 */

export interface LiveElement {
  id: number
  now_cost: number
  status: string
  news: string
  chance_of_playing_next_round: number | null
  transfers_in_event: number
  transfers_out_event: number
}

export interface LiveState {
  gw: number
  deadline: string
  elements: Map<number, LiveElement>
}

/** Merge live price and availability over the baked projection. */
export function withLive(p: Player, live: LiveState | null): Player {
  const e = live?.elements.get(p.id)
  if (!e) return p
  return {
    ...p,
    price: e.now_cost / 10,
    status: e.status,
    news: e.news || '',
  }
}

export function priceMovers(live: LiveState | null, players: Player[]) {
  if (!live) return { rising: [], falling: [], active: false }
  const rows = players
    .map(p => {
      const e = live.elements.get(p.id)
      return e ? { p, net: e.transfers_in_event - e.transfers_out_event } : null
    })
    .filter((x): x is { p: Player; net: number } => x !== null)
  const sorted = [...rows].sort((a, b) => b.net - a.net)
  const active = sorted.length > 0 && Math.abs(sorted[0].net) > 0
  return { rising: sorted.slice(0, 5), falling: sorted.slice(-5).reverse(), active }
}

/** Fetch through the serverless proxy — the FPL API blocks browsers directly. */
async function fpl<T>(path: string): Promise<T> {
  const r = await fetch(`/api/fpl?path=${encodeURIComponent(path)}`)
  if (!r.ok) throw new Error(`${path} -> ${r.status}`)
  return r.json() as Promise<T>
}

export async function loadLive(): Promise<LiveState> {
  const boot = await fpl<any>('bootstrap-static/')
  const next = boot.events.find((e: any) => e.is_next)
    ?? boot.events.find((e: any) => e.is_current)
    ?? boot.events[0]
  const elements = new Map<number, LiveElement>()
  for (const e of boot.elements) {
    elements.set(e.id, {
      id: e.id, now_cost: e.now_cost, status: e.status, news: e.news,
      chance_of_playing_next_round: e.chance_of_playing_next_round,
      transfers_in_event: e.transfers_in_event,
      transfers_out_event: e.transfers_out_event,
    })
  }
  return { gw: next.id, deadline: next.deadline_time, elements }
}

export interface LoadedTeam {
  ids: number[]
  bank: number
  fromGw: number
  /** null when the picks payload carries no positions (should not happen once public) */
  lineup: Lineup | null
}

/**
 * Read a real FPL squad by entry id. Picks only become public once a gameweek's
 * deadline has passed, so this walks back from the most recent one and returns
 * null before the season starts.
 */
export async function loadTeam(entryId: number, gw: number): Promise<LoadedTeam | null> {
  for (let ev = gw - 1; ev >= 1; ev--) {
    try {
      const picks = await fpl<any>(`entry/${entryId}/event/${ev}/picks/`)
      // position 1–11 is the XI, 12–15 the bench in the order they come on
      const ps: any[] = [...picks.picks].sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
      const positioned = ps.length === 15 && ps.every(p => typeof p.position === 'number')
      const lineup: Lineup | null = positioned ? {
        xi: ps.filter(p => p.position <= 11).map(p => p.element),
        bench: ps.filter(p => p.position > 11).map(p => p.element),
        captain: ps.find(p => p.is_captain)?.element ?? null,
        vice: ps.find(p => p.is_vice_captain)?.element ?? null,
      } : null
      return {
        ids: ps.map(p => p.element),
        bank: (picks.entry_history?.bank ?? 0) / 10,
        fromGw: ev,
        lineup,
      }
    } catch {
      /* not public yet — try the gameweek before */
    }
  }
  return null
}

/* ------------------------------------------------------------- entry summary
   entry/{id}/ — the team name and the headline numbers. Money is in tenths
   of £m upstream and £m here. */
export interface EntrySummary {
  name: string
  overallPoints: number
  overallRank: number | null
  gwPoints: number
  value: number
  bank: number
}

interface EntryPayload {
  name?: string
  summary_overall_points?: number | null
  summary_overall_rank?: number | null
  summary_event_points?: number | null
  last_deadline_value?: number | null
  last_deadline_bank?: number | null
}

export async function loadEntry(entryId: number): Promise<EntrySummary | null> {
  try {
    const e = await fpl<EntryPayload>(`entry/${entryId}/`)
    return {
      name: e.name ?? '',
      overallPoints: e.summary_overall_points ?? 0,
      overallRank: e.summary_overall_rank ?? null,
      gwPoints: e.summary_event_points ?? 0,
      value: (e.last_deadline_value ?? 0) / 10,
      bank: (e.last_deadline_bank ?? 0) / 10,
    }
  } catch {
    return null
  }
}

/* ------------------------------------------------------------- entry history
   entry/{id}/history/ — one row per finished gameweek plus the chips played.
   Money is left in tenths here, as upstream sends it. */
export interface EntryHistoryRow {
  event: number
  points: number
  total_points: number
  overall_rank: number | null
  bank: number
  value: number
  event_transfers: number
  event_transfers_cost: number
  points_on_bench: number
}

export interface EntryHistory {
  current: EntryHistoryRow[]
  chips: { name: string; event: number }[]
}

interface EntryHistoryPayload {
  current?: Partial<EntryHistoryRow>[]
  chips?: { name?: string; event?: number }[]
}

export async function loadEntryHistory(entryId: number): Promise<EntryHistory | null> {
  try {
    const h = await fpl<EntryHistoryPayload>(`entry/${entryId}/history/`)
    return {
      current: (h.current ?? []).map(r => ({
        event: r.event ?? 0,
        points: r.points ?? 0,
        total_points: r.total_points ?? 0,
        overall_rank: r.overall_rank ?? null,
        bank: r.bank ?? 0,
        value: r.value ?? 0,
        event_transfers: r.event_transfers ?? 0,
        event_transfers_cost: r.event_transfers_cost ?? 0,
        points_on_bench: r.points_on_bench ?? 0,
      })),
      chips: (h.chips ?? []).map(c => ({ name: c.name ?? '', event: c.event ?? 0 })),
    }
  } catch {
    return null
  }
}
