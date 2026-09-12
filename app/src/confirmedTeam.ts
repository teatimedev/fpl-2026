import type { Weekly } from './types'
import type { LoadedTeam } from './weekly'

/** Apply a user-confirmed report only to its exact public baseline/deadline. */
export function confirmedTeam(weekly: Weekly | null | undefined, entryId: string,
  gw: number, team: LoadedTeam | null, ft: number, now = Date.now()) {
  const unchanged = { team, ft }
  const state = weekly?.squad
  const base = state?.public_baseline
  if (!weekly || !state || !base || !team || !state.confirmed_at
      || weekly.gw !== gw || state.entry_id !== Number(entryId)
      || !Number.isFinite(Date.parse(weekly.deadline)) || now >= Date.parse(weekly.deadline)
      || !Number.isFinite(Date.parse(state.confirmed_at)) || now < Date.parse(state.confirmed_at)
      || base.gw !== team.fromGw || base.ft !== ft || Math.abs(base.bank - team.bank) > .001
      || base.ids.length !== 15 || new Set(base.ids).size !== 15
      || team.ids.length !== 15 || !team.ids.every(id => base.ids.includes(id))
      || state.ids.length !== 15 || new Set(state.ids).size !== 15) return unchanged
  return { team: { ...team, ids: state.ids, bank: state.bank, lineup: null,
                   confirmedAt: state.confirmed_at }, ft: state.ft }
}
