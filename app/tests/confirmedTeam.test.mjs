import test from 'node:test'
import assert from 'node:assert/strict'
import { confirmedTeam } from '../src/confirmedTeam.ts'

const ids = Array.from({ length: 15 }, (_, i) => i + 1)
const team = { ids, bank: 0, fromGw: 3, lineup: { xi: ids.slice(0, 11) } }
const weekly = { gw: 4, deadline: '2026-09-12T12:30:00Z', squad: {
  entry_id: 123, ids: [...ids.slice(0, 14), 16], bank: .2, ft: 2,
  confirmed_at: '2026-09-12T10:00:00Z',
  public_baseline: { ids, bank: 0, ft: 3, gw: 3 },
} }
const now = Date.parse('2026-09-12T11:00:00Z')

test('completed transfer survives a refresh of last deadline public picks', () => {
  const result = confirmedTeam(weekly, '123', 4, team, 3, now)
  assert.deepEqual(result.team.ids, weekly.squad.ids)
  assert.equal(result.team.bank, .2)
  assert.equal(result.ft, 2)
  assert.equal(result.team.lineup, null)
  assert.equal(team.ids.at(-1), 15)
})

test('report cannot cross accounts, deadlines or a different public baseline', () => {
  for (const [entry, gw, input, ft, time] of [
    ['456', 4, team, 3, now], ['123', 5, team, 3, now],
    ['123', 4, team, 3, Date.parse(weekly.deadline)],
    ['123', 4, { ...team, fromGw: 4 }, 3, now],
    ['123', 4, { ...team, bank: .1 }, 3, now],
    ['123', 4, team, 2, now],
  ]) {
    const result = confirmedTeam(weekly, entry, gw, input, ft, time)
    assert.equal(result.team, input)
    assert.equal(result.ft, ft)
  }
})
