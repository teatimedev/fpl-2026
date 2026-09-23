import type { Data, WeeklyPlan } from './types'
import type { LiveState } from './weekly'
import decisionVersion from '../../v2/decision_version.json' with { type: 'json' }

export const DECISION_VERSION = decisionVersion.version

/** A hold instruction cannot display a path that spends transfers now. */
export function planForInstruction(plan: WeeklyPlan | null, hold: boolean) {
  return (hold ? plan?.hold_weeks : plan?.weeks) ?? []
}

/** Read-only access to a dated report never makes the live advice ready. */
export function canReadSavedReview(D: Data, entryId: string, now = Date.now()) {
  const w = D.weekly
  if (!w || !entryId || String(w.squad.entry_id) !== entryId
      || w.gw !== D.meta.start_gw || !D.meta.forecast_id
      || w.forecast_id !== D.meta.forecast_id
      || w.decision_version !== DECISION_VERSION
      || D.meta.decision_version !== DECISION_VERSION) return false
  const generated = Date.parse(D.meta.generated.replace(' ', 'T').replace(/ UTC$/, 'Z'))
  const deadline = Date.parse(w.deadline)
  return Number.isFinite(generated) && Number.isFinite(deadline)
    && generated <= now && now - generated <= 72 * 3600000 && now < deadline
    && w.squad.ids.length === 15 && new Set(w.squad.ids).size === 15
    && w.squad.ids.every(id => D.players.some(p => p.id === id))
}

/** Pure contract: a live label must never relabel a different forecast. */
export function recommendationState(D: Data, live: LiveState | null, ids: number[],
  bank: number, ft: number, entryId: string, now = Date.now()) {
  const gw = live?.gw ?? D.meta.start_gw ?? 1
  const w = D.weekly
  const relevant = new Set(ids)
  w?.decision?.moves?.forEach(move => relevant.add(move.in_))
  w?.transfers?.singles.forEach(r => relevant.add(r.in_))
  w?.transfers?.pairs.forEach(r => r.in_.forEach(id => relevant.add(id)))
  w?.plan?.weeks.forEach(r => r.in_.forEach(id => relevant.add(id)))
  w?.plan?.hold_weeks?.forEach(r => r.in_.forEach(id => relevant.add(id)))
  w?.transfer_review?.players.forEach(r => { if (r.replacement != null) relevant.add(r.replacement) })
  const reason: string[] = []
  if (!live) reason.push('Waiting for live FPL data to verify the deadline and player status.')
  if (D.meta.start_gw !== gw) reason.push(`The published model is for GW${D.meta.start_gw}; the next deadline is GW${gw}.`)
  if (!D.meta.forecast_id) reason.push('This model predates the forecast identity checks.')
  if (D.meta.decision_version !== DECISION_VERSION) reason.push('The saved analysis needs rebuilding for this decision model.')
  const generated = Date.parse(D.meta.generated.replace(' ', 'T').replace(/ UTC$/, 'Z'))
  if (!Number.isFinite(generated) || generated > now || now - generated > 72 * 3600000) reason.push('The model needs a fresh rebuild.')
  if (live && (!Number.isFinite(Date.parse(live.deadline)) || now >= Date.parse(live.deadline)))
    reason.push('The live deadline is missing or has passed. Refresh the live state.')
  const known = new Set(D.players.map(p => p.id))
  if (ids.length !== 15 || new Set(ids).size !== 15
      || ids.some(id => !known.has(id) || (live && !live.elements.has(id))))
    reason.push('The squad is incomplete in the model or live player feed.')
  const statusChanges = D.players.filter(p => {
    const e = live?.elements.get(p.id)
    return e && (p.status !== e.status || (p.news || '') !== (e.news || '')
      || (p.chance ?? null) !== (e.chance_of_playing_next_round ?? null))
  })
  const ownedChanges = statusChanges.filter(p => ids.includes(p.id))
  if (ownedChanges.length) reason.push(`Squad news changed since the model ran: ${ownedChanges.slice(0, 4).map(p => p.name).join(', ')}. Projections need updating.`)
  const projectionsReady = reason.length === 0
  if (live && [...relevant].some(id => !known.has(id) || !live.elements.has(id)))
    reason.push('A transfer target is missing from the model or live player feed.')
  const targetChanges = statusChanges.filter(p => relevant.has(p.id) && !ids.includes(p.id))
  if (targetChanges.length) reason.push(`Transfer-target news needs refreshing: ${targetChanges.slice(0, 4).map(p => p.name).join(', ')}.`)
  if (!w || w.gw !== gw || w.forecast_id !== D.meta.forecast_id || w.decision_version !== DECISION_VERSION)
    reason.push('The detailed recommendation does not match this forecast.')
  if (w && (ids.length !== 15 || new Set(ids).size !== 15 || w.squad.ids.length !== 15
      || !ids.every(i => w.squad.ids.includes(i)))) reason.push('The analysed squad differs from the loaded squad.')
  if (!Number.isFinite(bank) || bank < 0 || !Number.isInteger(ft) || ft < 0
      || (w && (!Number.isFinite(w.squad.bank) || Math.abs(w.squad.bank - bank) > 0.001 || w.squad.ft !== ft)))
    reason.push('Bank or free-transfer count differs from the analysed account.')
  if (w?.squad.entry_id && String(w.squad.entry_id) !== entryId)
    reason.push('The analysis belongs to a different FPL entry.')
  if (w?.squad.selling_prices_unknown?.length) reason.push('Some selling values are unknown; affordability needs checking.')
  if (live && D.players.some(p => relevant.has(p.id) && live.elements.has(p.id)
      && Math.abs(live.elements.get(p.id)!.now_cost / 10 - p.price) > 0.001))
    reason.push('Prices moved after this analysis. Transfer affordability needs refreshing.')
  return { gw, projectionsReady, digestReady: reason.length === 0, reasons: reason }
}

export type AdviceLevel = 'ready' | 'warn' | 'blocked'

export interface AdviceStatus {
  gw: number
  level: AdviceLevel
  /** The published plan applies to this gameweek, account and squad. */
  planUsable: boolean
  /** The projections cover the upcoming gameweek, even if the plan does not. */
  projectionsUsable: boolean
  /** Why the weekly instruction is withheld: it would be wrong, not merely dated. */
  blockers: string[]
  /** Things that changed since the plan was built but do not invalidate it. */
  warnings: string[]
  /** Hours since the model was built, or null when unknown. */
  ageHours: number | null
}

const STALE_WARNING_HOURS = 72
const DOUBTFUL = new Set(['i', 's', 'u', 'n'])

function builtAt(D: Data) {
  const t = Date.parse((D.meta.generated ?? '').replace(' ', 'T').replace(/ UTC$/, 'Z'))
  return Number.isFinite(t) ? t : null
}

/**
 * Severity-graded replacement for `recommendationState`.
 *
 * A plan is withheld only when following it could be wrong: it belongs to a
 * different gameweek, account, squad or decision algorithm, the deadline has
 * passed, or a recommended signing is now flagged or unaffordable. Everything
 * else that drifts between rebuilds — news about a squad player, a price move
 * that still fits the budget, an unreachable live feed, an older build — is a
 * visible warning beside the plan instead of a reason to show nothing.
 */
export function adviceStatus(D: Data, live: LiveState | null, ids: number[],
  bank: number, ft: number, entryId: string, now = Date.now()): AdviceStatus {
  const w = D.weekly
  const gw = live?.gw ?? w?.gw ?? D.meta.start_gw ?? 1
  const blockers: string[] = []
  const warnings: string[] = []
  const built = builtAt(D)
  const ageHours = built == null ? null : Math.max(0, (now - built) / 3600000)
  const byId = new Map(D.players.map(p => [p.id, p]))
  const name = (id: number) => byId.get(id)?.name ?? `Player ${id}`

  const projectionsUsable = D.meta.start_gw != null && D.meta.start_gw <= gw
    && gw <= (D.meta.horizon ?? 0) && ids.length === 15
    && ids.every(id => byId.has(id))

  if (!w) blockers.push('No weekly plan has been published yet.')
  else if (w.gw !== gw) blockers.push(`The Gameweek ${gw} plan has not been built yet. The last plan was for Gameweek ${w.gw}.`)
  else if (!D.meta.forecast_id || w.forecast_id !== D.meta.forecast_id
      || w.decision_version !== DECISION_VERSION || D.meta.decision_version !== DECISION_VERSION)
    blockers.push('The published plan and forecast are out of step. The next automatic rebuild fixes this.')

  const deadline = Date.parse(live?.deadline ?? w?.deadline ?? D.meta.deadline)
  if (!Number.isFinite(deadline) || now >= deadline)
    blockers.push('This deadline has passed. The next plan is built automatically.')

  if (w?.squad.entry_id && entryId && String(w.squad.entry_id) !== entryId)
    blockers.push('The plan was built for a different FPL team.')
  if (w && w.gw === gw && ids.length === 15
      && (w.squad.ids.length !== 15 || !ids.every(id => w.squad.ids.includes(id))))
    blockers.push('Your squad has changed since the plan was built.')

  const moves = w?.gw === gw ? w.decision?.moves ?? [] : []
  if (live) {
    for (const move of moves) {
      const e = live.elements.get(move.in_)
      if (!e) blockers.push(`${name(move.in_)} is missing from the live FPL player list.`)
      else if (DOUBTFUL.has(e.status) || (e.chance_of_playing_next_round != null && e.chance_of_playing_next_round <= 50))
        blockers.push(`${name(move.in_)}, a recommended signing, is now flagged${e.news ? `: ${e.news}` : '.'}`)
    }
    const sell = w?.squad.sell_prices
    if (moves.length && sell && Number.isFinite(w?.squad.bank)) {
      const proceeds = moves.reduce((sum, m) => sum + (sell[m.out] ?? NaN), 0)
      const cost = moves.reduce((sum, m) => sum + (live.elements.get(m.in_)?.now_cost ?? NaN) / 10, 0)
      const planned = moves.reduce((sum, m) => sum + (byId.get(m.in_)?.price ?? NaN), 0)
      const left = w!.squad.bank + proceeds - cost
      if (!Number.isFinite(left)) warnings.push('Could not recheck the transfer budget against live prices.')
      else if (left < -0.001) blockers.push(`The recommended transfer${moves.length > 1 ? 's are' : ' is'} no longer affordable at today's prices (short by £${(-left).toFixed(1)}m).`)
      else if (Math.abs(cost - planned) > 0.001) warnings.push(`Prices of the recommended signings moved since the plan was built. Still affordable, with £${left.toFixed(1)}m left.`)
    }
  } else {
    warnings.push('Live FPL data is unavailable, so news and prices could not be rechecked. Showing the plan as built.')
  }

  if (live) {
    const changed = ids.filter(id => {
      const p = byId.get(id), e = live.elements.get(id)
      return p && e && (p.status !== e.status || (p.news || '') !== (e.news || '')
        || (p.chance ?? null) !== (e.chance_of_playing_next_round ?? null))
    })
    if (changed.length) warnings.push(`News changed since the plan was built: ${changed.slice(0, 4).map(name).join(', ')}${changed.length > 4 ? ` and ${changed.length - 4} more` : ''}. The plan does not include it yet.`)
  }
  if (w && w.gw === gw && Number.isFinite(bank) && Number.isFinite(ft)
      && (Math.abs(w.squad.bank - bank) > 0.001 || w.squad.ft !== ft))
    warnings.push(`FPL now shows ${ft} free transfer${ft === 1 ? '' : 's'} and £${bank.toFixed(1)}m in the bank; the plan used ${w.squad.ft} and £${w.squad.bank.toFixed(1)}m.`)
  if (w?.squad.selling_prices_unknown?.length)
    warnings.push('Some selling prices are unknown, so the transfer budget is approximate.')
  if (ageHours != null && ageHours > STALE_WARNING_HOURS)
    warnings.push(`The plan is ${Math.floor(ageHours / 24)} days old. The automatic refresh may have stalled.`)

  const planUsable = blockers.length === 0
  return {
    gw, planUsable, projectionsUsable, blockers, warnings, ageHours,
    level: !planUsable ? 'blocked' : warnings.length ? 'warn' : 'ready',
  }
}
