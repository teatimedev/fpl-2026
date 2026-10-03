import type { Player, Weekly, WeeklyCheck, WeeklyLineup } from './types'

/** Keep the chosen action separate from diagnostic alternatives. */
export function weeklyTransferSummary(w: Weekly) {
  const decision = w.decision
  const hold = decision?.kind === 'hold'
  const moves = hold ? [] : decision?.moves ?? []
  const ft = w.squad.ft
  const unlimited = w.gw === 1 || ft >= 15
  const next = decision?.ft_next ?? (unlimited ? 1 : Math.min(5, Math.max(0, ft - moves.length) + 1))
  const lost = decision?.ft_lost ?? (!unlimited && hold && ft === 5 ? 1 : 0)
  return {
    hold, moves, next, lost, unlimited,
    hits: decision?.hit_points ?? (unlimited ? 0 : Math.max(0, moves.length - ft) * 4),
    headline: hold
      ? ft >= 5 || w.gw === 1 || w.gw === 38 ? 'Keep your squad this week.' : 'Save your transfers this week.'
      : moves.length > 0
        ? `Make ${moves.length === 1 ? 'one transfer' : `${moves.length} transfers`} this week.`
        : 'Transfer instructions need updating.',
    // Never label the old squad as the lineup after recommended transfers.
    lineup: decision?.lineup ?? (hold ? w.model : null),
  }
}

export function lineupChanges(current: WeeklyLineup | null, next: WeeklyLineup) {
  if (!current?.xi || current.xi.length !== 11 || !next.xi || next.xi.length !== 11) return null
  return {
    start: next.xi.filter(id => !current.xi!.includes(id)),
    sit: current.xi.filter(id => !next.xi!.includes(id)),
    captainChanged: current.captain !== next.captain,
    viceChanged: current.vice !== next.vice,
    benchChanged: !!current.bench && next.bench?.join(',') !== current.bench.join(','),
  }
}

export function plainPlayerCheck(player: Player | undefined, check: WeeklyCheck) {
  if (player?.news) return player.news
  if (player?.status === 'i') return 'Injured. Check whether he is fit to play.'
  if (player?.status === 's') return 'Suspended. Check when he can return.'
  if (player?.status === 'u') return 'Unavailable. Check his replacement before the deadline.'
  if (player?.status === 'd') return 'A doubt for this game. Check the latest team news.'
  if (check.flags.some(f => /starts only|start estimate/.test(f))) return 'May not start. Check the latest team news.'
  if (check.flags.some(f => /new signing/.test(f))) return 'His place in the team is still settling. Check the latest team news.'
  const attacking = check.flags.find(f => /role:.*xGI/i.test(f))
  if (attacking) return /above/.test(attacking)
    ? 'Recent chance involvement is higher than expected. Check whether his attacking role has changed.'
    : 'Recent chance involvement is lower than expected. Check whether his attacking role has changed.'
  return 'Review the flagged change before the deadline.'
}

/** The numbers behind the transfer decision, in one plain sentence. */
const pct = (p: number | null | undefined) => `${Math.round((p ?? 0) * 100)}%`
const signedPts = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}`

/** One sentence on why the decision is what it is. */
export function decisionReason(w: Weekly, name: (id: number) => string) {
  const plan = w.plan
  const hold = w.decision?.kind === 'hold'
  const window = `Gameweeks ${w.gw}–${w.horizon}`
  const pairs = (out: number[], inn: number[]) => out.map((o, i) => `${name(o)} → ${name(inn[i])}`).join(', ')
  const sampled = plan?.decision
  if (sampled) {
    const n = sampled.samples
    if (hold) {
      const best = sampled.best_move
      if (!best) return `No transfer tested beats saving it over ${window}.`
      const move = pairs(best.out, best.in_)
      return best.gain <= 0
        ? `Saving the transfer beats every move tested: the best (${move}) is ${signedPts(best.gain)} pts against holding and comes out ahead in only ${pct(best.p_beats_hold)} of ${n} forecast scenarios.`
        : `The best move tested (${move}) is only ${signedPts(best.gain)} ± ${best.se.toFixed(1)} pts against holding across ${n} forecast scenarios, which is within the noise, so saving stands.`
    }
    if ((w.decision?.moves?.length ?? 0) > 0) {
      const chosen = sampled.chosen
      return `Expected to gain ${signedPts(chosen.gain)} pts compared with saving your transfer (later weeks discounted, counting what carries past GW${w.horizon}); it came out ahead in ${pct(chosen.p_beats_hold)} of ${n} forecast scenarios.`
    }
    return ''
  }
  // Older weekly data (legacy per-move bar); kept so a stale bundle still reads.
  const scored = (plan?.candidates ?? []).filter(c => c.status === 'scored'
    && (c.in_?.length ?? 0) > 0 && Number.isFinite(c.gain))
  if (hold) {
    const best = scored.sort((a, b) => (b.gain ?? 0) - (a.gain ?? 0))[0]
    if (!best) return `No transfer improves your team over ${window}.`
    const move = pairs(best.out ?? [], best.in_ ?? [])
    const gain = best.gain ?? 0
    return gain <= 0 || best.move_bar == null
      ? `No transfer beats saving it: the best tested (${move}) is ${gain.toFixed(1)} pts over ${window}.`
      : `The best move tested (${move}) adds only ${gain.toFixed(1)} pts over ${window} compared with saving the transfer — below the ${best.move_bar.toFixed(1)}-point bar for spending it.`
  }
  if (plan && Number.isFinite(plan.diff) && (w.decision?.moves?.length ?? 0) > 0)
    return `Expected to gain ${signedPts(plan.diff)} pts over ${window} compared with saving your transfers, after any hits.`
  return ''
}

export function ageLabel(hours: number | null) {
  if (hours == null) return 'build time unknown'
  if (hours < 1) return 'updated just now'
  if (hours < 24) return `updated ${Math.floor(hours)}h ago`
  const days = Math.floor(hours / 24)
  return `updated ${days} day${days === 1 ? '' : 's'} ago`
}

/** A check is worth a deadline action only if it could cost points: an
 *  availability flag, a start doubt or a falling attacking role. A player
 *  getting more chances than expected is good news, not a check. */
export function needsDeadlineCheck(player: Player | undefined, check: WeeklyCheck) {
  if (player && player.status !== 'a') return true
  return check.flags.some(flag => !/^role:.*above/i.test(flag))
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
