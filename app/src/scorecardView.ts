import type { Scorecard, ScorecardGw } from './types'
import type { EntryHistory } from './weekly'

export interface PersonalResult {
  gw: number
  points: number
  hit: number
  net: number
  total: number | null
  rank: number | null
  rankChange: number | null
  chip: string | null
  source: 'fpl' | 'saved'
}

export const isOwnReview = (g: ScorecardGw | undefined, entryId: string) =>
  !!g?.submitted && g.submitted.entry_id === Number(entryId)

/** Prefer official history; only fall back to submissions for this exact entry.
 * FPL's event points include captain/chips but exclude transfer deductions.
 * Overall totals already include those deductions; never subtract them again.
 */
export function personalResults(sc: Scorecard | null, history: EntryHistory | null, entryId: string): PersonalResult[] {
  if (!entryId) return []
  const rows = new Map<number, PersonalResult>()
  for (const g of sc?.gws ?? []) {
    if (!isOwnReview(g, entryId) || !g.submitted) continue
    const s = g.submitted
    rows.set(g.gw, { gw: g.gw, points: s.points, hit: s.transfer_cost, net: s.points - s.transfer_cost,
      total: null, rank: null, rankChange: null, chip: s.chip, source: 'saved' })
  }
  const official = [...(history?.current ?? [])].sort((a, b) => a.event - b.event)
  for (let i = 0; i < official.length; i++) {
    const r = official[i]
    const prior = official[i - 1]
    rows.set(r.event, { gw: r.event, points: r.points, hit: r.event_transfers_cost,
      net: r.points - r.event_transfers_cost, total: r.total_points, rank: r.overall_rank,
      rankChange: prior?.event === r.event - 1 && prior.overall_rank != null && r.overall_rank != null
        ? prior.overall_rank - r.overall_rank : null,
      chip: history?.chips.find(c => c.event === r.event)?.name ?? null, source: 'fpl' })
  }
  return [...rows.values()].sort((a, b) => a.gw - b.gw)
}

export function captainReturn(g: ScorecardGw | undefined, entryId: string) {
  if (!isOwnReview(g, entryId)) return null
  const pick = g?.captain?.yours
  const multiplier = g?.submitted?.captain_multiplier
  if (!pick || multiplier == null || multiplier < 1) return null
  return { name: pick.name, base: pick.pts, multiplier, total: pick.pts * multiplier }
}

/** A lineup comparison, never a claim of causal gains or full FPL scoring. */
export function lineupDifference(g: ScorecardGw | undefined, entryId: string) {
  if (!isOwnReview(g, entryId) || g?.xi?.model == null || g.xi.yours == null) return null
  return g.xi.model - g.xi.yours
}

export function chipLabel(chip: string | null | undefined) {
  if (!chip) return 'No chip'
  return ({ '3xc': 'Triple Captain', bboost: 'Bench Boost', freehit: 'Free Hit', wildcard: 'Wildcard' } as Record<string, string>)[chip] ?? chip
}
