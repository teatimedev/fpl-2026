import type { Player, Pos } from './types'
import { XI_MIN, XI_MAX, POS_ORDER, MAX_PER_CLUB } from './types'

/**
 * The weekly decision, computed in the browser.
 *
 * Mirrors v2/weekly.py: pick the XI for the coming gameweek, choose a captain,
 * rank every transfer available, and flag availability. Projections are baked
 * in at build time and refreshed weekly; prices, injuries and your actual squad
 * are fetched live through the API proxy, because those are the things that
 * change between deploys.
 */

export const HIT_COST = 4

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

export function remaining(p: Player, gw: number, horizon: number): number {
  const v = p.proj_by_gw ?? []
  if (!v.length) return p.proj_6gw
  return v.slice(Math.max(0, gw - 1), horizon).reduce((s, x) => s + x, 0)
}

export function thisGw(p: Player, gw: number): number {
  const v = p.proj_by_gw ?? []
  if (!v.length) return p.proj_6gw / 6
  return v[gw - 1] ?? 0
}

/** Best legal XI for one specific gameweek (not the whole horizon). */
export function xiForGw(squad: Player[], gw: number) {
  const byPos: Record<Pos, Player[]> = { GKP: [], DEF: [], MID: [], FWD: [] }
  for (const p of squad) byPos[p.pos].push(p)
  for (const k of POS_ORDER) byPos[k].sort((a, b) => thisGw(b, gw) - thisGw(a, gw))

  const xi: Player[] = []
  const used: Record<Pos, number> = { GKP: 0, DEF: 0, MID: 0, FWD: 0 }
  for (const pos of POS_ORDER) {
    for (const p of byPos[pos].slice(0, XI_MIN[pos])) { xi.push(p); used[pos]++ }
  }
  const rest = squad
    .filter(p => !xi.includes(p))
    .sort((a, b) => thisGw(b, gw) - thisGw(a, gw))
  for (const p of rest) {
    if (xi.length >= 11) break
    if (used[p.pos] >= XI_MAX[p.pos]) continue
    xi.push(p); used[p.pos]++
  }
  const bench = squad.filter(p => !xi.includes(p))
  bench.sort((a, b) =>
    (a.pos === 'GKP' ? -1 : 0) - (b.pos === 'GKP' ? -1 : 0)
    || thisGw(b, gw) - thisGw(a, gw))
  return { xi, bench }
}

export interface TransferOption {
  out: Player
  in: Player
  gain: number
  costChange: number
  worthAHit: boolean
}

/**
 * Every single transfer available, scored over the remaining gameweeks.
 * Keeps only the best upgrade per outgoing player so one standout replacement
 * cannot fill the whole list.
 */
export function transferOptions(
  squad: Player[], pool: Player[], bank: number,
  gw: number, horizon: number, limit = 8,
): TransferOption[] {
  const owned = new Set(squad.map(p => p.id))
  const clubCount: Record<string, number> = {}
  for (const p of squad) clubCount[p.team] = (clubCount[p.team] ?? 0) + 1

  const candidates = pool.filter(p =>
    !owned.has(p.id) && p.status !== 'u' && remaining(p, gw, horizon) > 0)

  const out: TransferOption[] = []
  for (const o of squad) {
    const cash = bank + o.price
    let best: TransferOption | null = null
    for (const n of candidates) {
      if (n.pos !== o.pos || n.price > cash + 1e-9) continue
      if (n.team !== o.team && (clubCount[n.team] ?? 0) >= MAX_PER_CLUB) continue
      const gain = remaining(n, gw, horizon) - remaining(o, gw, horizon)
      if (gain <= 0) continue
      if (!best || gain > best.gain) {
        best = {
          out: o, in: n, gain,
          costChange: Math.round((n.price - o.price) * 10) / 10,
          worthAHit: gain > HIT_COST,
        }
      }
    }
    if (best) out.push(best)
  }
  return out.sort((a, b) => b.gain - a.gain).slice(0, limit)
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
      return {
        ids: picks.picks.map((p: any) => p.element),
        bank: (picks.entry_history?.bank ?? 0) / 10,
        fromGw: ev,
      }
    } catch {
      /* not public yet — try the gameweek before */
    }
  }
  return null
}
