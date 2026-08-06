import type { Player, Pos, Fixture } from './types'
import { SQUAD_SHAPE, XI_MIN, XI_MAX, POS_ORDER, BUDGET, MAX_PER_CLUB } from './types'

/** Everything the UI needs to know about the current 15. */
export interface SquadState {
  picks: Player[]
  cost: number
  remaining: number
  counts: Record<Pos, number>
  clubCounts: Record<string, number>
  complete: boolean
  problems: string[]
}

export function analyse(picks: Player[]): SquadState {
  const cost = round1(picks.reduce((s, p) => s + p.price, 0))
  const counts = { GKP: 0, DEF: 0, MID: 0, FWD: 0 } as Record<Pos, number>
  const clubCounts: Record<string, number> = {}
  for (const p of picks) {
    counts[p.pos]++
    clubCounts[p.team] = (clubCounts[p.team] ?? 0) + 1
  }
  const problems: string[] = []
  if (cost > BUDGET) problems.push(`£${round1(cost - BUDGET)}m over budget`)
  for (const pos of POS_ORDER) {
    if (counts[pos] > SQUAD_SHAPE[pos])
      problems.push(`${counts[pos]} ${pos} — the limit is ${SQUAD_SHAPE[pos]}`)
  }
  for (const [club, n] of Object.entries(clubCounts)) {
    if (n > MAX_PER_CLUB) problems.push(`${n} players from ${club} — the limit is ${MAX_PER_CLUB}`)
  }
  const complete = picks.length === 15 && POS_ORDER.every(p => counts[p] === SQUAD_SHAPE[p])
  return {
    picks, cost, remaining: round1(BUDGET - cost),
    counts, clubCounts, complete, problems,
  }
}

/** Can this player be added without breaking a rule? Returns the reason if not. */
export function blockReason(p: Player, s: SquadState): string | null {
  if (s.picks.some(q => q.id === p.id)) return 'Already picked'
  if (s.counts[p.pos] >= SQUAD_SHAPE[p.pos]) return `You already have ${SQUAD_SHAPE[p.pos]} ${p.pos}`
  if ((s.clubCounts[p.team] ?? 0) >= MAX_PER_CLUB) return `You already have 3 from ${p.team}`
  if (round1(s.cost + p.price) > BUDGET) return `£${round1(round1(s.cost + p.price) - BUDGET)}m over budget`
  return null
}

/**
 * Best legal starting XI from a 15, by projected points.
 * Fills the minimum at each position first, then the remaining four slots with
 * whoever scores highest, respecting the per-position maximums.
 */
export function bestXI(picks: Player[]): Set<number> {
  const byPos: Record<Pos, Player[]> = { GKP: [], DEF: [], MID: [], FWD: [] }
  for (const p of picks) byPos[p.pos].push(p)
  for (const pos of POS_ORDER) byPos[pos].sort((a, b) => b.proj_6gw - a.proj_6gw)

  const xi = new Set<number>()
  const used: Record<Pos, number> = { GKP: 0, DEF: 0, MID: 0, FWD: 0 }
  for (const pos of POS_ORDER) {
    for (let i = 0; i < XI_MIN[pos] && i < byPos[pos].length; i++) {
      xi.add(byPos[pos][i].id)
      used[pos]++
    }
  }
  const rest = picks
    .filter(p => !xi.has(p.id))
    .sort((a, b) => b.proj_6gw - a.proj_6gw)
  for (const p of rest) {
    if (xi.size >= 11) break
    if (used[p.pos] >= XI_MAX[p.pos]) continue
    xi.add(p.id)
    used[p.pos]++
  }
  return xi
}

export function formationOf(picks: Player[], xi: Set<number>): string {
  const n = (pos: Pos) => picks.filter(p => xi.has(p.id) && p.pos === pos).length
  return `${n('DEF')}-${n('MID')}-${n('FWD')}`
}

/**
 * Aggregate fixture difficulty per gameweek across the whole squad.
 * This is what tells you your team has a nightmare in GW3 before you build it.
 */
export function squadOutlook(
  picks: Player[], xi: Set<number>, schedule: Record<string, (Fixture | null)[]>, horizon: number,
): { gw: number; avg: number; hardest: string[] }[] {
  const starters = picks.filter(p => xi.has(p.id))
  const out = []
  for (let gw = 1; gw <= horizon; gw++) {
    const fdrs: { team: string; fdr: number }[] = []
    for (const p of starters) {
      const f = schedule[p.team]?.[gw - 1]
      if (f) fdrs.push({ team: p.team, fdr: f.fdr })
    }
    const avg = fdrs.length ? fdrs.reduce((s, f) => s + f.fdr, 0) / fdrs.length : 0
    const hardest = [...new Set(fdrs.filter(f => f.fdr >= 4).map(f => f.team))]
    out.push({ gw, avg, hardest })
  }
  return out
}

export const round1 = (n: number) => Math.round(n * 10) / 10
