import test from 'node:test'
import assert from 'node:assert/strict'
import { weeklyTransferSummary, lineupChanges, plainPlayerCheck, decisionReason } from '../src/weeklyActions.ts'

const xi = Array.from({ length: 11 }, (_, i) => i + 1)
const lineup = { xi, bench: [12, 13, 14, 15], captain: 1, vice: 2 }
const weekly = { gw: 4, squad: { ft: 3 }, model: lineup,
  decision: { kind: 'hold' }, plan: { weeks: [{ in_: [16], out: [11] }] },
  transfers: { singles: [{ in_: 16, out: 11 }] } }

test('the simple hold instruction never turns an alternative into a sale', () => {
  const action = weeklyTransferSummary(weekly)
  assert.deepEqual(action.moves, [])
  assert.equal(action.next, 4)
  assert.equal(action.hits, 0)
  assert.deepEqual(action.lineup, lineup)
})

test('holding at five explicitly loses a new transfer; using one keeps five', () => {
  const full = { ...weekly, squad: { ft: 5 } }
  const hold = weeklyTransferSummary(full)
  assert.equal(hold.next, 5)
  assert.equal(hold.lost, 1)
  const use = weeklyTransferSummary({ ...full, decision: { kind: 'transfer', moves: [{ out: 11, in_: 16 }] } })
  assert.equal(use.next, 5)
  assert.equal(use.lost, 0)
})

test('a recommended transfer uses its new lineup and shows the hit', () => {
  const next = { ...lineup, xi: [...xi.slice(0, 10), 16] }
  const decision = { kind: 'transfer', moves: [{ out: 11, in_: 16 }], lineup: next }
  const action = weeklyTransferSummary({ ...weekly, squad: { ft: 0 }, decision })
  assert.equal(action.hits, 4)
  assert.equal(action.lineup.xi.includes(11), false)
  assert.equal(action.lineup.xi.includes(16), true)
  assert.equal(weeklyTransferSummary({ ...weekly, decision: { ...decision, lineup: undefined } }).lineup, null)
})

test('a vice-captain change is visible even when the starting eleven matches', () => {
  const changes = lineupChanges({ ...lineup, vice: 3 }, { ...lineup, xi: [...xi].reverse() })
  assert.deepEqual(changes.start, [])
  assert.equal(changes.viceChanged, true)
  assert.equal(changes.captainChanged, false)
  assert.equal(lineupChanges(null, lineup), null)
})

test('bench switches are explicit and sold players never become starters', () => {
  const next = { ...lineup, xi: [...xi.slice(0, 10), 13], bench: [12, 11, 14, 15] }
  const changes = lineupChanges(lineup, next)
  assert.deepEqual(changes.start, [13])
  assert.deepEqual(changes.sit, [11])
  assert.equal(changes.benchChanged, true)
})

test('a minutes estimate is explained as uncertainty without implying an injury', () => {
  assert.equal(plainPlayerCheck({ status: 'a' }, { flags: ['starts only 64% of the time'] }),
    'May not start. Check the latest team news.')
  assert.equal(plainPlayerCheck({ status: 'i', news: 'Hamstring injury' }, { flags: [] }), 'Hamstring injury')
})

test('a positive attacking-role signal does not become a false playing-time warning', () => {
  const text = plainPlayerCheck({ status: 'a', news: '' }, { flags: ['role: last 3 starts: 2.78 xGI vs 1.86 expected, above the 80% band'] })
  assert.match(text, /higher than expected/)
  assert.doesNotMatch(text, /playing time|may not start/i)
})

const nameOf = id => `P${id}`
const sampledPlan = (best, chosen) => ({ weeks: [], diff: chosen.gain, decision: {
  method: 'sampled act-versus-hold', samples: 24, rule: 'one-SE', chosen, best_move: best } })

test('a sampled hold explains the best move against saving, not a points bar', () => {
  const best = { out: [11], in_: [16], gain: -1.4, se: 0.4, p_beats_hold: 0.19 }
  const hold = { in_: [], out: [], gain: 0, se: 0, p_beats_hold: null }
  const text = decisionReason({ ...weekly, horizon: 9, plan: sampledPlan(best, hold) }, nameOf)
  assert.match(text, /P11 → P16/)
  assert.match(text, /-1\.4 pts against holding/)
  assert.match(text, /19% of 24 forecast scenarios/)
  assert.doesNotMatch(text, /bar/)
})

test('a small positive best move under a sampled hold is described as noise', () => {
  const best = { out: [11], in_: [16], gain: 0.3, se: 0.5, p_beats_hold: 0.55 }
  const hold = { in_: [], out: [], gain: 0, se: 0, p_beats_hold: null }
  const text = decisionReason({ ...weekly, horizon: 9, plan: sampledPlan(best, hold) }, nameOf)
  assert.match(text, /\+0\.3 ± 0\.5 pts/)
  assert.match(text, /within the noise/)
})

test('a sampled transfer states its expected gain and how often it won', () => {
  const chosen = { out: [11], in_: [16], gain: 2.1, se: 0.5, p_beats_hold: 1 }
  const w = { ...weekly, horizon: 9, plan: sampledPlan(chosen, chosen),
    decision: { kind: 'transfer', moves: [{ out: 11, in_: 16 }] } }
  const text = decisionReason(w, nameOf)
  assert.match(text, /\+2\.1 pts compared with saving/)
  assert.match(text, /100% of 24 forecast scenarios/)
})

test('legacy bundles without a sampled decision still explain a hold', () => {
  const plan = { weeks: [], diff: 0, candidates: [{ status: 'scored', in_: [16], out: [11], gain: 1.2, move_bar: 2 }] }
  assert.match(decisionReason({ ...weekly, horizon: 9, plan }, nameOf), /2\.0-point bar/)
})
