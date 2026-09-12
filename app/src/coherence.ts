import type { Data, WeeklyPlan } from './types'
import type { LiveState } from './weekly'

/** A hold instruction cannot display a path that spends transfers now. */
export function planForInstruction(plan: WeeklyPlan | null, hold: boolean) {
  return (hold ? plan?.hold_weeks : plan?.weeks) ?? []
}

/** Read-only access to a dated report never makes the live advice ready. */
export function canReadSavedReview(D: Data, entryId: string, now = Date.now()) {
  const w = D.weekly
  if (!w || !entryId || String(w.squad.entry_id) !== entryId
      || w.gw !== D.meta.start_gw || !D.meta.forecast_id
      || w.forecast_id !== D.meta.forecast_id) return false
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
  if (!w || w.gw !== gw || w.forecast_id !== D.meta.forecast_id)
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
