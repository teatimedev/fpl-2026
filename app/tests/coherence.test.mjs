import test from 'node:test'
import assert from 'node:assert/strict'
import { recommendationState } from '../src/coherence.ts'

const now = Date.parse('2026-09-06T10:00:00Z')
const ids = Array.from({ length: 15 }, (_, i) => i + 1)
const data = {
  meta: { start_gw: 4, horizon: 9, generated: '2026-09-06 09:00 UTC', forecast_id: 'one' },
  players: [{ id: 1, name: 'Player', status: 'a', news: '', price: 5, proj_by_gw: [0, 0, 0, 5] }],
  weekly: { gw: 4, forecast_id: 'one', squad: { ids, bank: 0, ft: 3, entry_id: 123 } },
}
const live = { gw: 4, deadline: '2026-09-12T12:30:00Z', elements: new Map([[1, { now_cost: 50, status: 'a', news: '' }]]) }
const check = (d = data, l = live, bank = 0, ft = 3, account = '123') => recommendationState(d, l, ids, bank, ft, account, now)

test('matching current forecast and account accepted', () => assert.equal(check().digestReady, true))
test('gameweek rollover cannot relabel an old digest', () => {
  assert.equal(check({ ...data, weekly: { ...data.weekly, gw: 3 } }).digestReady, false)
  assert.equal(check({ ...data, meta: { ...data.meta, start_gw: 3 } }).projectionsReady, false)
})
test('bank FT entry and forecast version must match', () => {
  assert.equal(check(data, live, .1).digestReady, false)
  assert.equal(check(data, live, 0, 4).digestReady, false)
  assert.equal(check(data, live, 0, 3, '456').digestReady, false)
  assert.equal(check({ ...data, weekly: { ...data.weekly, forecast_id: 'two' } }).digestReady, false)
})
test('price rise invalidates transfer budget; injury invalidates projections', () => {
  assert.equal(check(data, { ...live, elements: new Map([[1, { now_cost: 51, status: 'a', news: '' }]]) }).digestReady, false)
  assert.equal(check(data, { ...live, elements: new Map([[1, { now_cost: 50, status: 'i', news: 'Injured' }]]) }).projectionsReady, false)
})
test('live API failure and unknown selling values cannot imply hold', () => {
  assert.equal(check(data, null).digestReady, false)
  assert.equal(check({ ...data, weekly: { ...data.weekly, squad: { ...data.weekly.squad, selling_prices_unknown: [1] } } }).digestReady, false)
})
test('unrelated player news cannot suppress a coherent squad recommendation', () => {
  const d = { ...data, players: [...data.players, { id: 99, name: 'Unrelated', status: 'a', news: '', price: 5, proj_by_gw: [0, 0, 0, 4] }] }
  const l = { ...live, elements: new Map([...live.elements, [99, { now_cost: 49, status: 'i', news: 'Out' }]]) }
  assert.equal(check(d, l).digestReady, true)
})
