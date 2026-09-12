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
  const active = sorted.some(row => row.net !== 0)
  return { rising: sorted.filter(row => row.net > 0).slice(0, 5),
    falling: sorted.filter(row => row.net < 0).slice(-5).reverse(), active }
}

/** Fetch through the serverless proxy — the FPL API blocks browsers directly. */
class FplHttpError extends Error {
  status: number
  constructor(path: string, status: number) {
    super(`${path} -> ${status}`)
    this.status = status
  }
}

async function fpl<T>(path: string): Promise<T> {
  const r = await fetch(`/api/fpl?path=${encodeURIComponent(path)}`, {
    signal: AbortSignal.timeout(12000),
  })
  if (!r.ok) throw new FplHttpError(path, r.status)
  return r.json() as Promise<T>
}

export function upcomingEvent(events: { id: number; deadline_time: string }[], now = Date.now()) {
  if (!events?.length || events.some(e => !Number.isFinite(Date.parse(e.deadline_time)))) {
    throw new Error('FPL deadline calendar is unavailable or invalid')
  }
  const next = [...events].filter(e => Date.parse(e.deadline_time) > now)
    .sort((a, b) => Date.parse(a.deadline_time) - Date.parse(b.deadline_time))[0]
  if (!next) throw new Error('No future FPL deadline remains in this season')
  return next
}

export async function loadLive(): Promise<LiveState> {
  const boot = await fpl<any>('bootstrap-static/')
  const next = upcomingEvent(boot.events)
  const elements = new Map<number, LiveElement>()
  if (!Array.isArray(boot.elements) || !boot.elements.length) throw new Error('FPL player data is empty')
  for (const e of boot.elements) {
    if (!Number.isInteger(e.id) || e.id <= 0 || elements.has(e.id)
        || !Number.isFinite(e.now_cost) || e.now_cost < 0
        || typeof e.status !== 'string' || !e.status
        || (e.chance_of_playing_next_round != null && (!Number.isFinite(e.chance_of_playing_next_round)
          || e.chance_of_playing_next_round < 0 || e.chance_of_playing_next_round > 100))) {
      throw new Error('FPL player data is incomplete or invalid')
    }
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
  confirmedAt?: string
}

/**
 * Read a real FPL squad by entry id. Picks only become public once a gameweek's
 * deadline has passed, so this walks back from the most recent one and returns
 * null before the season starts.
 */
export async function loadTeam(entryId: number, gw: number, history?: EntryHistory): Promise<LoadedTeam | null> {
  if (!Number.isInteger(entryId) || entryId <= 0) throw new Error('Enter a valid FPL team ID')
  for (let ev = gw - 1; ev >= 1; ev--) {
    if (history?.chips.some(c => c.name === 'freehit' && c.event === ev)) continue
    try {
      const picks = await fpl<any>(`entry/${entryId}/event/${ev}/picks/`)
      // position 1–11 is the XI, 12–15 the bench in the order they come on
      const ps: any[] = [...picks.picks].sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
      if (ps.length !== 15 || new Set(ps.map(p => p.element)).size !== 15
          || !ps.every((p, i) => Number.isInteger(p.element) && p.element > 0 && p.position === i + 1)
          || !Number.isFinite(picks.entry_history?.bank) || picks.entry_history.bank < 0
          || picks.entry_history?.event !== ev
          || ps.filter(p => p.is_captain).length !== 1 || ps.filter(p => p.is_vice_captain).length !== 1) {
        throw new Error('Public FPL picks or bank balance are incomplete')
      }
      const captain = ps.find(p => p.is_captain), vice = ps.find(p => p.is_vice_captain)
      if (captain.element === vice.element || captain.position > 11 || vice.position > 11)
        throw new Error('Captain and vice must be distinct starting players')
      const lineup: Lineup = {
        xi: ps.filter(p => p.position <= 11).map(p => p.element),
        bench: ps.filter(p => p.position > 11).map(p => p.element),
        captain: ps.find(p => p.is_captain)?.element ?? null,
        vice: ps.find(p => p.is_vice_captain)?.element ?? null,
      }
      return {
        ids: ps.map(p => p.element),
        bank: picks.entry_history.bank / 10,
        fromGw: ev,
        lineup,
      }
    } catch (error) {
      // Only unpublished picks justify walking back. A 403, timeout or broken
      // payload must not turn an outage into an apparently empty older team.
      if (!(error instanceof FplHttpError) || error.status !== 404) throw error
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

export function validateHistory(history: EntryHistory, previousGw: number) {
  const events = history.current?.map(row => row.event)
  if (!events?.length || !Array.isArray(history.chips)
      || new Set(events).size !== events.length || !events.includes(previousGw)
      || events.some(event => !Number.isInteger(event) || event < 1 || event > 38)
      || new Set(history.chips.map(c => c.event)).size !== history.chips.length
      || history.current.some(row => !Number.isInteger(row.event_transfers) || row.event_transfers < 0)
      || history.chips.some(c => !['wildcard', 'freehit', 'bboost', '3xc'].includes(c.name)
        || !Number.isInteger(c.event) || c.event < 1 || c.event > 38)) {
    throw new Error('FPL transfer history is incomplete for the previous deadline')
  }
  for (let g = Math.min(...events); g <= previousGw; g++) {
    if (!events.includes(g)) throw new Error('FPL transfer history has missing gameweeks')
  }
}

interface EntryHistoryPayload {
  current?: Partial<EntryHistoryRow>[]
  chips?: { name?: string; event?: number }[]
}

export async function loadEntryHistory(entryId: number): Promise<EntryHistory | null> {
  try {
    const h = await fpl<EntryHistoryPayload>(`entry/${entryId}/history/`)
    if (!Array.isArray(h.current) || !Array.isArray(h.chips)) return null
    return {
      current: (h.current ?? []).map(r => ({
        event: r.event ?? 0,
        points: r.points ?? 0,
        total_points: r.total_points ?? 0,
        overall_rank: r.overall_rank ?? null,
        bank: r.bank ?? 0,
        value: r.value ?? 0,
        event_transfers: r.event_transfers ?? NaN,
        event_transfers_cost: r.event_transfers_cost ?? 0,
        points_on_bench: r.points_on_bench ?? 0,
      })),
      chips: (h.chips ?? []).map(c => ({ name: c.name ?? '', event: c.event ?? 0 })),
    }
  } catch {
    return null
  }
}
