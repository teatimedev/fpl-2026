import test from 'node:test'
import assert from 'node:assert/strict'
import { upcomingEvent, loadTeam } from '../src/weekly.ts'

const deadline = '2026-09-12T12:30:00Z'
const events = [
  { id: 5, deadline_time: '2026-09-19T10:00:00Z' },
  { id: 4, deadline_time: deadline, is_next: true },
]
test('the browser clock rolls over at the deadline even when FPL flags lag', () => {
  assert.equal(upcomingEvent(events, Date.parse(deadline) - 1).id, 4)
  assert.equal(upcomingEvent(events, Date.parse(deadline)).id, 5)
})
test('missing calendar and season end cannot masquerade as GW1', () => {
  assert.throws(() => upcomingEvent([], Date.parse(deadline)), /calendar/)
  assert.throws(() => upcomingEvent([events[1]], Date.parse(deadline)), /No future/)
})

test('an API outage does not walk back through 37 weeks or return an empty squad', async t => {
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => {
    calls++
    return new Response('{}', { status: 403 })
  })
  await assert.rejects(loadTeam(123, 38), /403/)
  assert.equal(calls, 1)
})

test('only a 404 permits falling back to earlier public picks', async t => {
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => {
    calls++
    if (calls === 1) return new Response('{}', { status: 404 })
    return Response.json({ picks: Array.from({ length: 15 }, (_, i) => ({
      element: i + 1, position: i + 1, is_captain: i === 0, is_vice_captain: i === 1,
    })), entry_history: { bank: 2 } })
  })
  const team = await loadTeam(123, 5)
  assert.equal(team.fromGw, 3)
  assert.equal(team.bank, .2)
  assert.equal(calls, 2)
})
