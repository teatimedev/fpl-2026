import type { Player } from './types'

// Exhaustive counterpart of v2/lineup_search.py. This optimises the selection
// under independent appearances; it does not validate the player forecasts.
const labels = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2]
const minimum = [3, 2, 1], maximum = [5, 5, 3]
const positions = ['DEF', 'MID', 'FWD']
const missing = Array.from({ length: 144 }, (_, i) => [Math.floor(i / 24), Math.floor(i / 4) % 6, i % 4])
interface Choice { parent: number; bench: number[]; masks: Int8Array }
interface Template { starters: number[][]; choices: Choice[] }
let template: Template | null = null
const coefficientCache = new Map<string, Float64Array>()

function replace(absent: number[], counts: number[], sub: number): [number[], number[]] | null {
  for (const pos of [sub, ...[0, 1, 2].filter(p => p !== sub)]) {
    if (!absent[pos]) continue
    const trial = [...counts]
    trial[pos]--; trial[sub]++
    if (trial.every((count, p) => count >= minimum[p] && count <= maximum[p])) {
      const next = [...absent]; next[pos]--
      return [next, trial]
    }
  }
  return null
}

function masksFor(bench: number[]) {
  const counts = maximum.map((n, p) => n - bench.filter(b => b === p).length)
  const masks = new Int8Array(7 * 144)
  let row = 0
  for (let j = 0; j < 3; j++) {
    for (let pattern = 0; pattern < 2 ** j; pattern++, row++) {
      for (let m = 0; m < 144; m++) {
        if (missing[m].some((v, p) => v > counts[p])) continue
        let state: [number[], number[]] = [missing[m], counts]
        for (let k = 0; k < j; k++) {
          if (pattern & (1 << (j - 1 - k))) state = replace(...state, bench[k]) ?? state
        }
        masks[row * 144 + m] = replace(...state, bench[j]) ? 1 : 0
      }
    }
  }
  return masks
}

function getTemplate(): Template {
  if (template) return template
  const starters: number[][] = [], choices: Choice[] = []
  const masks = new Map<string, Int8Array>()
  const add = (xi: number[]) => {
    const counts = [0, 1, 2].map(pos => xi.filter(i => labels[i] === pos).length)
    if (counts.some((n, p) => n < minimum[p] || n > maximum[p])) return
    const parent = starters.length
    starters.push([...xi])
    const reserves = labels.map((_, i) => i).filter(i => !xi.includes(i))
    for (const a of reserves) for (const b of reserves) {
      if (a === b) continue
      const c = reserves.find(i => i !== a && i !== b)!
      const bench = [a, b, c], shape = bench.map(i => labels[i]), key = shape.join(',')
      if (!masks.has(key)) masks.set(key, masksFor(shape))
      choices.push({ parent, bench, masks: masks.get(key)! })
    }
  }
  const choose = (next: number, xi: number[]) => {
    if (xi.length === 10) { add(xi); return }
    for (let i = next; i <= 13 - (10 - xi.length); i++) choose(i + 1, [...xi, i])
  }
  choose(0, [])
  template = { starters, choices }
  return template
}

function coefficients(play: number[], T: Template): Float64Array {
  const key = play.join(',')
  const cached = coefficientCache.get(key)
  if (cached) return cached
  const masses = T.starters.map(xi => {
    const distributions = maximum.map(n => new Float64Array(n + 1))
    for (const d of distributions) d[0] = 1
    for (const index of xi) {
      const d = distributions[labels[index]], q = 1 - play[index]
      for (let k = d.length - 1; k > 0; k--) d[k] = d[k] * (1 - q) + d[k - 1] * q
      d[0] *= 1 - q
    }
    return Float64Array.from(missing, ([d, m, f]) => distributions[0][d] * distributions[1][m] * distributions[2][f])
  })
  const output = new Float64Array(T.choices.length * 3)
  T.choices.forEach((choice, i) => {
    const mass = masses[choice.parent], a = play[choice.bench[0]], b = play[choice.bench[1]]
    const prefix = [1, 1 - a, a, (1 - a) * (1 - b), (1 - a) * b, a * (1 - b), a * b]
    for (let k = 0; k < 7; k++) {
      let activation = 0
      for (let m = 0; m < 144; m++) activation += mass[m] * choice.masks[k * 144 + m]
      output[i * 3 + (k === 0 ? 0 : k < 3 ? 1 : 2)] += activation * prefix[k]
    }
  })
  if (coefficientCache.size >= 128) coefficientCache.delete(coefficientCache.keys().next().value!)
  coefficientCache.set(key, output)
  return output
}

export function optimalLineup(squad: Player[], gw: number,
  points: (p: Player, gw: number) => number, probability: (p: Player, gw: number) => number) {
  const keepers = squad.filter(p => p.pos === 'GKP')
  const outfield = positions.flatMap(pos => squad.filter(p => p.pos === pos))
  if (keepers.length !== 2 || outfield.length !== 13 || new Set(squad.map(p => p.id)).size !== 15
    || outfield.some((p, i) => p.pos !== positions[labels[i]])) throw new Error('A complete positional squad is required')
  const means = outfield.map(p => points(p, gw)), play = outfield.map(p => probability(p, gw))
  if (![...means, ...play, ...keepers.flatMap(p => [points(p, gw), probability(p, gw)])].every(Number.isFinite)) throw new Error('Lineup values must be finite')
  const T = getTemplate(), cover = coefficients(play, T)
  let best: { score: number; xi: Player[]; bench: Player[]; captain: Player; vice: Player } | null = null
  let bestKey: number[] = []
  keepers.forEach((keeper, ki) => {
    const reserve = keepers[1 - ki]
    const options = T.starters.map(xi => {
      const players = [keeper, ...xi.map(i => outfield[i])]
      const ranked = [...players].sort((a, b) => points(b, gw) - points(a, gw))
      let cap = ranked[0], vice = ranked[1], bonus = -Infinity
      for (const captain of players) {
        const backup = ranked.find(p => p.id !== captain.id)!
        const value = points(captain, gw) + (1 - probability(captain, gw)) * points(backup, gw)
        if (value > bonus + 1e-12 || (Math.abs(value - bonus) <= 1e-12 && points(captain, gw) > points(cap, gw))) {
          cap = captain; vice = backup; bonus = value
        }
      }
      const xiCaptain = players.reduce((s, p) => s + points(p, gw), 0) + bonus
      const base = xiCaptain + (1 - probability(keeper, gw)) * points(reserve, gw)
      return { players, cap, vice, base, xiCaptain }
    })
    T.choices.forEach((choice, index) => {
      const option = options[choice.parent]
      const score = option.base + choice.bench.reduce((sum, b, j) => sum + cover[index * 3 + j] * means[b], 0)
      const key = [option.xiCaptain, ...choice.bench.map(i => means[i])]
      const difference = key.findIndex((v, i) => v !== bestKey[i])
      if (!best || score > best.score + 1e-10
        || (Math.abs(score - best.score) <= 1e-10 && difference >= 0 && key[difference] > bestKey[difference])) {
        best = { score, xi: option.players, bench: [reserve, ...choice.bench.map(i => outfield[i])], captain: option.cap, vice: option.vice }
        bestKey = key
      }
    })
  })
  if (!best) throw new Error('No legal lineup')
  return best as { score: number; xi: Player[]; bench: Player[]; captain: Player; vice: Player }
}
