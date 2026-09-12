import test from 'node:test'
import assert from 'node:assert/strict'
import { accountSellingValues, bankAfterMoves } from '../src/transferBudget.ts'
import { rankTransfers } from '../src/model.ts'
import { DECISION_VERSION } from '../src/coherence.ts'

test('sandbox uses actual sale proceeds rather than a market-price gain', () => {
  const moves = [{ out: { id: 1, price: 5.5 }, in: { id: 2, price: 5.4 } }]
  assert.equal(bankAfterMoves(.1, moves, { 1: 5.2 }), -.1)
  assert.equal(bankAfterMoves(.1, moves, null), null)
  assert.equal(bankAfterMoves(.1, moves, {}), null)
})

test('selling values require the matching fresh account, forecast and complete map', () => {
  const ids = Array.from({ length: 15 }, (_, i) => i + 1)
  const values = Object.fromEntries(ids.map(id => [id, 5.2]))
  const now = Date.parse('2026-09-12T10:00:00Z')
  const data = {
    meta: { start_gw: 4, generated: '2026-09-12T09:00:00Z', forecast_id: 'f', decision_version: DECISION_VERSION },
    players: ids.map(id => ({ id, price: 5.5, status: 'a', news: '' })),
    weekly: { gw: 4, forecast_id: 'f', decision_version: DECISION_VERSION, squad: { ids, bank: .1, ft: 2, entry_id: 123, sell_prices: values } },
  }
  const live = { gw: 4, deadline: '2026-09-12T12:30:00Z', elements: new Map(ids.map(id => [id, { now_cost: 55, status: 'a', news: '' }])) }
  const check = (account = '123') => accountSellingValues(data, live, ids, .1, 2, account, now)
  assert.deepEqual(check(), values)
  assert.equal(check('456'), null)
  delete values[1]
  assert.equal(check(), null)
})

test('ranked moves cannot spend unearned market-price gains', () => {
  const positions = ['GKP', 'GKP', ...Array(5).fill('DEF'), ...Array(5).fill('MID'), ...Array(3).fill('FWD')]
  const squad = positions.map((pos, id) => ({ id, pos, name: String(id), team: 'C' + Math.floor(id / 3), price: 5.5,
    proj_by_gw: [2], proj_6gw: 2, play_by_gw: [1], status: 'a' }))
  const incoming = { ...squad[0], id: 99, team: 'Z', price: 5.4, proj_by_gw: [10], proj_6gw: 10 }
  const values = Object.fromEntries(squad.map(p => [p.id, 5.2]))
  assert.deepEqual(rankTransfers(squad, [incoming], .1, 1, 1, 1, 8, values), [])
  assert.ok(rankTransfers(squad, [incoming], .2, 1, 1, 1, 8, values).length)
  assert.deepEqual(rankTransfers(squad, [incoming], 1, 1, 1, 1), [])
})
