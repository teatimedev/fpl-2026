import test from 'node:test'
import assert from 'node:assert/strict'
import { inferFreeTransfers } from '../src/model.ts'
import { loadTeam, loadEntryHistory, validateHistory } from '../src/weekly.ts'

const history = (first, last, transfers = {}, chips = []) => ({
  current: Array.from({ length: last - first + 1 }, (_, i) => ({ event: first + i, event_transfers: transfers[first + i] ?? 0 })), chips,
})
for (const [first, last, transfers, chips, expected] of [
  [1, 1, {}, [], 1], [1, 3, {}, [], 3], [10, 10, { 10: 12 }, [], 1],
  [10, 11, {}, [], 2], [10, 12, { 11: 1 }, [], 2], [1, 8, {}, [], 5],
  [10, 12, { 12: 12 }, [{ name: 'wildcard', event: 12 }], 2],
  [10, 12, { 12: 12 }, [{ name: 'freehit', event: 12 }], 2],
]) test(`transfer count matches Python: joined GW${first}, through GW${last}, ${JSON.stringify(chips)}`, () => {
  assert.equal(inferFreeTransfers(history(first, last, transfers, chips), last + 1), expected)
})

test('after Free Hit the permanent squad is loaded instead of the temporary picks', async t => {
  const requested = []
  t.mock.method(globalThis, 'fetch', async url => {
    requested.push(decodeURIComponent(url))
    return Response.json({ picks: Array.from({ length: 15 }, (_, i) => ({
      element: i + 1, position: i + 1, is_captain: i === 0, is_vice_captain: i === 1,
    })), entry_history: { bank: 3, event: 3 } })
  })
  const team = await loadTeam(123, 5, history(1, 4, {}, [{ name: 'freehit', event: 4 }]))
  assert.equal(team.fromGw, 3)
  assert.equal(team.bank, .3)
  assert.equal(requested.length, 1)
  assert.match(requested[0], /event\/3\/picks/)
})

test('missing transfer counts cannot turn into zero transfers', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ current: [{ event: 3 }], chips: [] }))
  const h = await loadEntryHistory(123)
  assert.throws(() => validateHistory(h, 3), /incomplete/)
  assert.ok(Number.isNaN(inferFreeTransfers(null, 4)))
})

test('wrong-gameweek public picks cannot be adopted after a deadline', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({
    picks: Array.from({ length: 15 }, (_, i) => ({ element: i + 1, position: i + 1,
      is_captain: i === 0, is_vice_captain: i === 1 })), entry_history: { bank: 3, event: 3 },
  }))
  await assert.rejects(loadTeam(123, 5), /incomplete/)
})
