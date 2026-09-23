import test from 'node:test'
import assert from 'node:assert/strict'
import { adviceStatus, DECISION_VERSION } from '../src/coherence.ts'

const now = Date.parse('2026-09-06T10:00:00Z')
const ids = Array.from({ length: 15 }, (_, i) => i + 1)
const players = [...ids, 20].map(id => ({ id, name: 'Player' + id, status: 'a', news: '', chance: null, price: 5, proj_by_gw: [0, 0, 0, 5] }))
const weekly = {
  gw: 4, deadline: '2026-09-12T12:30:00Z', forecast_id: 'one', decision_version: DECISION_VERSION,
  squad: { ids, bank: 0.5, ft: 2, entry_id: 123, sell_prices: Object.fromEntries(ids.map(id => [id, 5])) },
  decision: { kind: 'transfer', moves: [{ out: 1, in_: 20 }] },
}
const data = {
  meta: { start_gw: 4, horizon: 9, generated: '2026-09-06 09:00 UTC', deadline: weekly.deadline, forecast_id: 'one', decision_version: DECISION_VERSION },
  players, weekly,
}
const el = (over = {}) => ({ now_cost: 50, status: 'a', news: '', chance_of_playing_next_round: null, ...over })
const live = { gw: 4, deadline: weekly.deadline, elements: new Map([...ids, 20].map(id => [id, el()])) }
const withEl = (id, over) => ({ ...live, elements: new Map([...live.elements, [id, el(over)]]) })
const status = (d = data, l = live, opts = {}) =>
  adviceStatus(d, l, opts.ids ?? ids, opts.bank ?? 0.5, opts.ft ?? 2, opts.entry ?? '123', opts.now ?? now)

test('a matching plan is ready', () => {
  const s = status()
  assert.equal(s.level, 'ready')
  assert.deepEqual(s.blockers, [])
})

test('news about a squad player warns but keeps the plan', () => {
  const s = status(data, withEl(3, { status: 'd', news: 'Knock', chance_of_playing_next_round: 75 }))
  assert.equal(s.level, 'warn')
  assert.equal(s.planUsable, true)
  assert.match(s.warnings[0], /Player3/)
})

test('an affordable price rise on a signing warns; an unaffordable one blocks', () => {
  const rise = status(data, withEl(20, { now_cost: 54 }))
  assert.equal(rise.planUsable, true)
  assert.match(rise.warnings.join(' '), /Still affordable/)
  const tooDear = status(data, withEl(20, { now_cost: 56 }))
  assert.equal(tooDear.planUsable, false)
  assert.match(tooDear.blockers[0], /no longer affordable/)
})

test('a new doubt on a recommended signing is surfaced without withholding the plan', () => {
  const s = status(data, withEl(20, { status: 'd', news: 'Knock - 75% chance of playing', chance_of_playing_next_round: 75 }))
  assert.equal(s.level, 'warn')
  assert.match(s.warnings.join(' '), /Player20, a recommended signing, has new FPL news/)
})

test('a fall in the outgoing player lowers the sale proceeds used for affordability', () => {
  // bank 0.5 + sell 5.0 - buy 5.4 = 0.1 left; the sold player falling 0.2 leaves -0.1
  const moved = { ...live, elements: new Map([...live.elements, [20, el({ now_cost: 54 })], [1, el({ now_cost: 48 })]]) }
  const s = status(data, moved)
  assert.equal(s.planUsable, false)
  assert.match(s.blockers[0], /no longer affordable/)
  const rose = { ...live, elements: new Map([...live.elements, [20, el({ now_cost: 54 })], [1, el({ now_cost: 52 })]]) }
  assert.equal(status(data, rose).planUsable, true)
})

test('a recommended signing who is now injured blocks the plan', () => {
  const s = status(data, withEl(20, { status: 'i', news: 'Hamstring', chance_of_playing_next_round: 0 }))
  assert.equal(s.level, 'blocked')
  assert.match(s.blockers[0], /Player20.*Hamstring/)
})

test('a plan for an earlier gameweek is withheld while projections may still be usable', () => {
  const s = status(data, { ...live, gw: 5, deadline: '2026-09-19T10:00:00Z' })
  assert.equal(s.planUsable, false)
  assert.match(s.blockers[0], /Gameweek 5 plan has not been built/)
  assert.equal(s.projectionsUsable, true)
})

test('wrong entry, changed squad, passed deadline and version drift all block', () => {
  assert.equal(status(data, live, { entry: '456' }).planUsable, false)
  assert.equal(status(data, live, { ids: [...ids.slice(1), 30] }).planUsable, false)
  assert.equal(status(data, live, { now: Date.parse(weekly.deadline) }).planUsable, false)
  assert.equal(status({ ...data, weekly: { ...weekly, decision_version: 'old' } }).planUsable, false)
})

test('an unreachable live feed or old build warns instead of hiding the plan', () => {
  const offline = status(data, null)
  assert.equal(offline.planUsable, true)
  assert.match(offline.warnings[0], /Live FPL data is unavailable/)
  const old = status(data, live, { now: now + 4 * 86400000 })
  assert.equal(old.planUsable, true)
  assert.match(old.warnings.join(' '), /4 days old/)
})

test('free-transfer drift is reported, not hidden', () => {
  const s = status(data, live, { ft: 3 })
  assert.equal(s.level, 'warn')
  assert.match(s.warnings[0], /3 free transfers/)
})

test('unrelated player news changes nothing', () => {
  const s = status(data, withEl(99, { status: 'i', news: 'Out' }))
  assert.equal(s.level, 'ready')
})

import { needsDeadlineCheck } from '../src/weeklyActions.ts'
test('good-news attacking signals are not deadline checks; doubts and drops are', () => {
  const fit = { status: 'a' }
  assert.equal(needsDeadlineCheck(fit, { flags: ['role: last 3 starts: 3.53 xGI vs 2.60 expected, above the 80% band'] }), false)
  assert.equal(needsDeadlineCheck(fit, { flags: ['role: last 3 starts: 0.4 xGI vs 1.9 expected, below the 80% band'] }), true)
  assert.equal(needsDeadlineCheck({ status: 'd' }, { flags: ['role: above'] }), true)
  assert.equal(needsDeadlineCheck(fit, { flags: ['starts only 60%'] }), true)
})
