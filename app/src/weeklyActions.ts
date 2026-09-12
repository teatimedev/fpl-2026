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
