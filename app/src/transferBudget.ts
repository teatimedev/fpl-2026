import type { Data } from './types'
import type { LiveState } from './weekly'
import type { Move } from './model'
import { recommendationState } from './coherence.ts'

export type SellingValues = Record<number, number>

/** Sale proceeds belong to the reconciled account, not a player's market card. */
export function accountSellingValues(D: Data, live: LiveState | null, ids: number[],
  bank: number, ft: number, entryId: string, now = Date.now()): SellingValues | null {
  if (!recommendationState(D, live, ids, bank, ft, entryId, now).digestReady) return null
  const values = D.weekly?.squad.sell_prices
  if (!values || ids.some(id => !Number.isFinite(values[id]) || values[id] < 0
    || values[id] > (live?.elements.get(id)?.now_cost ?? -1) / 10 + 1e-9)) return null
  return Object.fromEntries(ids.map(id => [id, values[id]]))
}

export function bankAfterMoves(bank: number, moves: Move[], values: SellingValues | null): number | null {
  if (!Number.isFinite(bank) || !values || moves.some(m => !Number.isFinite(values[m.out.id]))) return null
  return Math.round((bank + moves.reduce((sum, m) => sum + values[m.out.id] - m.in.price, 0)) * 10) / 10
}
