import type { Player, Pos } from './types'
import { XI_MIN, XI_MAX, POS_ORDER, MAX_PER_CLUB } from './types'
import type { EntryHistory } from './weekly'

/**
 * The model, in the browser: the single TypeScript mirror of v2/weekly.py.
 *
 * Everything here is pure — projections in, decisions out. Nothing fetches;
 * that lives in weekly.ts. Pick the XI for a gameweek, choose a captain, score
 * a squad over the window, rank the transfers available, and say where the
 * lineup you have set disagrees with the model's.
 */

export const HIT_COST = 4
export const MAX_FT = 5

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

/**
 * What a squad is really worth over the window: the best XI each week plus
 * the captain doubled — mirrors v2/weekly.py squad_score. This, not the sum
 * of fifteen projections, is what a transfer has to improve.
 */
export function squadScore(squad: Player[], gw: number, horizon: number): number {
  let total = 0
  for (let g = gw; g <= horizon; g++) {
    const { xi } = xiForGw(squad, g)
    if (!xi.length) continue
    let sum = 0, best = -Infinity
    for (const p of xi) {
      const v = thisGw(p, g)
      sum += v
      if (v > best) best = v
    }
    total += sum + best
  }
  return total
}

/* ------------------------------------------------------------- transfers */
export interface Move { out: Player; in: Player }

/** Replace each outgoing player with the incoming one, order preserved. */
export function applyMoves(squad: Player[], moves: Move[]): Player[] {
  const swap = new Map(moves.map(m => [m.out.id, m.in]))
  return squad.map(p => swap.get(p.id) ?? p)
}

/** Club limit and total price only — the shape (2-5-5-3) is checked separately. */
export function isLegal(squad: Player[], budget: number): boolean {
  const clubs: Record<string, number> = {}
  for (const p of squad) {
    clubs[p.team] = (clubs[p.team] ?? 0) + 1
    if (clubs[p.team] > MAX_PER_CLUB) return false
  }
  return squad.reduce((s, p) => s + p.price, 0) <= budget + 1e-9
}

export interface Gain {
  before: number
  after: number
  gain: number
  hits: number
  hitCost: number
  net: number
}

/** What a set of moves is worth over the window, net of any hits beyond `ft`. */
export function sandboxGain(
  squad: Player[], moves: Move[], ft: number, gw: number, horizon: number,
): Gain {
  const before = squadScore(squad, gw, horizon)
  const after = squadScore(applyMoves(squad, moves), gw, horizon)
  const gain = after - before
  const hits = Math.max(0, moves.length - ft)
  const hitCost = hits * HIT_COST
  return { before, after, gain, hits, hitCost, net: gain - hitCost }
}

export interface TransferOption {
  out: Player
  in: Player
  gain: number
  net: number
  costChange: number
  worthAHit: boolean
}

const POOL_SIZE = 40

/**
 * XI-aware single transfers: a move's gain is the lift to squadScore, so it
 * counts only if the newcomer actually starts and it credits a new captain.
 * Best replacement per outgoing player, sorted by gain. To stay fast, each
 * outgoing player considers only the top 40 candidates by remaining().
 */
export function rankTransfers(
  squad: Player[], pool: Player[], bank: number, ft: number,
  gw: number, horizon: number, limit = 8,
): TransferOption[] {
  const budget = squad.reduce((s, p) => s + p.price, 0) + bank
  const base = squadScore(squad, gw, horizon)
  const owned = new Set(squad.map(p => p.id))
  const cands: Record<Pos, Player[]> = { GKP: [], DEF: [], MID: [], FWD: [] }
  for (const pos of POS_ORDER) {
    cands[pos] = pool
      .filter(p => p.pos === pos && !owned.has(p.id) && p.status !== 'u'
        && remaining(p, gw, horizon) > 0)
      .sort((a, b) => remaining(b, gw, horizon) - remaining(a, gw, horizon))
      .slice(0, POOL_SIZE)
  }
  const hit = HIT_COST * Math.max(0, 1 - ft)

  const out: TransferOption[] = []
  for (const o of squad) {
    const cash = bank + o.price
    let best: TransferOption | null = null
    for (const n of cands[o.pos]) {
      if (n.price > cash + 1e-9) continue
      const after = applyMoves(squad, [{ out: o, in: n }])
      if (!isLegal(after, budget)) continue
      const gain = squadScore(after, gw, horizon) - base
      if (gain <= 0.05) continue
      if (!best || gain > best.gain) {
        best = {
          out: o, in: n, gain, net: gain - hit,
          costChange: Math.round((n.price - o.price) * 10) / 10,
          worthAHit: gain > HIT_COST,
        }
      }
    }
    if (best) out.push(best)
  }
  return out.sort((a, b) => b.gain - a.gain).slice(0, limit)
}

/**
 * Free transfers available at the deadline of `uptoGw`, from public entry
 * history — mirrors v2/weekly.py infer_free_transfers. FPL: one a week,
 * unused ones roll over up to five; a wildcard or free hit week neither
 * spends nor gains. Gameweek 1 is unlimited and everyone starts Gameweek 2
 * with exactly one.
 */
export function inferFreeTransfers(history: EntryHistory | null, uptoGw: number): number {
  if (uptoGw <= 1) return 15
  if (!history) return 1
  const chips = new Map(history.chips.map(c => [c.event, c.name]))
  const made = new Map(history.current.map(e => [e.event, e.event_transfers ?? 0]))
  let ft = 1
  for (let g = 2; g < uptoGw; g++) {
    if (!made.has(g)) break
    const chip = chips.get(g)
    if (chip === 'wildcard' || chip === 'freehit') ft = Math.min(MAX_FT, ft + 1)
    else ft = Math.min(MAX_FT, Math.max(ft - (made.get(g) ?? 0), 0) + 1)
  }
  return ft
}

/* ------------------------------------------------------ lineup vs model */

/** The lineup you actually have set: XI, bench in order, armbands. */
export interface Lineup {
  xi: number[]
  bench: number[]
  captain: number | null
  vice: number | null
}

export interface LineupIssue { head: string; body: string }

/**
 * Where the lineup you have set disagrees with the model's for this gameweek.
 * Mirrors the "Your lineup vs the model" block in v2/weekly.py, wording
 * included. `xi`/`bench` are the model's, from xiForGw.
 */
export function lineupIssues(
  lineup: Lineup, squad: Player[], xi: Player[], bench: Player[], gw: number,
): LineupIssue[] {
  const key = (p: Player) => thisGw(p, gw)
  const f1 = (x: number) => x.toFixed(1)
  const signed = (x: number) => (x >= 0 ? '+' : '') + f1(x)
  const byId = new Map(squad.map(p => [p.id, p]))
  const ranked = [...xi].sort((a, b) => key(b) - key(a))
  const cap = ranked[0], vice = ranked[1]
  if (!cap || !vice) return []

  const issues: LineupIssue[] = []
  const ycap = lineup.captain != null ? byId.get(lineup.captain) : undefined
  const yvice = lineup.vice != null ? byId.get(lineup.vice) : undefined
  // where the model would put the vice armband, given who you captain
  const armband = ycap && vice.id === ycap.id ? cap : vice

  if (ycap && ycap.id !== cap.id) {
    issues.push({
      head: 'Captain:',
      body: `you have ${ycap.name} (${f1(key(ycap))}); the model prefers ${cap.name} `
        + `(${f1(key(cap))}) — ${signed((key(cap) - key(ycap)) * 2)} expected once doubled.`,
    })
  }
  if (yvice) {
    if (yvice.pos === 'GKP') {
      issues.push({
        head: `Vice on a goalkeeper (${yvice.name}):`,
        body: `if the captain misses, the armband doubles your keeper. Move it to ${armband.name}.`,
      })
    } else if (!ranked.slice(0, 3).some(p => p.id === yvice.id)) {
      issues.push({
        head: 'Vice:',
        body: `${yvice.name} (${f1(key(yvice))}) is not one of your top three; `
          + `the model would use ${armband.name}.`,
      })
    }
  }
  if (lineup.xi.length) {
    const yxi = lineup.xi.map(i => byId.get(i)).filter((p): p is Player => !!p)
    const ybench = lineup.bench.map(i => byId.get(i)).filter((p): p is Player => !!p)
    const yset = new Set(yxi.map(p => p.id))
    const mset = new Set(xi.map(p => p.id))
    for (const p of xi) {
      if (yset.has(p.id)) continue
      const alt = yxi.find(q => !mset.has(q.id) && q.pos === p.pos)
        ?? yxi.find(q => !mset.has(q.id))
      const gap = key(p) - (alt ? key(alt) : 0)
      issues.push({
        head: 'Bench → start:',
        body: `${p.name} (${f1(key(p))}) is on your bench; the model starts him`
          + (alt ? ` over ${alt.name} (${f1(key(alt))}), ${signed(gap)}.` : '.'),
      })
    }
    const yfirst = ybench.find(q => q.pos !== 'GKP')
    const mfirst = bench.find(q => q.pos !== 'GKP')
    if (yfirst && mfirst && yfirst.id !== mfirst.id && !yset.has(mfirst.id)) {
      issues.push({
        head: 'Bench order:',
        body: `your first sub is ${yfirst.name} (${f1(key(yfirst))}); `
          + `${mfirst.name} (${f1(key(mfirst))}) is the better first man off.`,
      })
    }
    const shape = (ps: Player[]) =>
      (['DEF', 'MID', 'FWD'] as Pos[]).map(k => ps.filter(p => p.pos === k).length).join('-')
    if (shape(yxi) !== shape(xi)) {
      issues.push({ head: 'Formation:', body: `you ${shape(yxi)}, model ${shape(xi)}.` })
    }
  }
  return issues
}

export interface DiffBadges {
  /** in your XI, but the model benches him */
  swapOut: Set<number>
  /** on your bench, but the model starts him */
  swapIn: Set<number>
  capModel: number | null
  viceModel: number | null
  capYours: number | null
  viceYours: number | null
}

/** Your lineup against the model's XI for the gameweek, as per-player badges. */
export function lineupDiff(lineup: Lineup, squad: Player[], gw: number): DiffBadges {
  const { xi } = xiForGw(squad, gw)
  const modelXi = new Set(xi.map(p => p.id))
  const yourXi = new Set(lineup.xi)
  const ranked = [...xi].sort((a, b) => thisGw(b, gw) - thisGw(a, gw))
  return {
    swapOut: new Set(lineup.xi.filter(id => !modelXi.has(id))),
    swapIn: new Set(lineup.xi.length ? xi.filter(p => !yourXi.has(p.id)).map(p => p.id) : []),
    capModel: ranked[0]?.id ?? null,
    viceModel: ranked[1]?.id ?? null,
    capYours: lineup.captain,
    viceYours: lineup.vice,
  }
}
