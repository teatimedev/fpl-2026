import test from 'node:test'
import assert from 'node:assert/strict'
import { personalResults, captainReturn, lineupDifference, isOwnReview, chipLabel } from '../src/scorecardView.ts'

const review = { gw: 3, checked: true,
  submitted: { entry_id: 42, points: 60, transfer_cost: 4, captain_multiplier: 3, chip: '3xc' },
  captain: { yours: { name: 'Haaland', pts: 10 } }, xi: { model: 43, yours: 40 } }
const sc = { gws: [review] }

test('Triple Captain is shown as a contribution inside the actual score, never added twice', () => {
  assert.deepEqual(captainReturn(review, '42'), { name: 'Haaland', base: 10, multiplier: 3, total: 30 })
  const [r] = personalResults(sc, null, '42')
  assert.equal(r.points, 60)
  assert.equal(r.net, 56)
  assert.equal(chipLabel(r.chip), 'Triple Captain')
  assert.equal(lineupDifference(review, '42'), 3)
})

test('official history wins over the saved score; hits apply once and rank movement has the right direction', () => {
  const history = { current: [
    { event: 3, points: 65, event_transfers_cost: 4, total_points: 161, overall_rank: 700 },
    { event: 2, points: 100, event_transfers_cost: 0, total_points: 100, overall_rank: 1000 },
  ], chips: [{ event: 3, name: '3xc' }] }
  const r = personalResults(sc, history, '42').at(-1)
  assert.equal(r.net, 61)
  assert.equal(r.total, 161)
  assert.equal(r.rankChange, 300)
  assert.equal(r.source, 'fpl')
})

test('a newer official result appears even without a model review', () => {
  const history = { current: [{ event: 4, points: 0, event_transfers_cost: 4, total_points: 157, overall_rank: null }], chips: [] }
  const r = personalResults(sc, history, '42').at(-1)
  assert.equal(r.gw, 4)
  assert.equal(r.net, -4)
  assert.equal(r.rankChange, null)
  assert.equal(lineupDifference(undefined, '42'), null)
})

test('another account or missing submitted picks cannot be labelled as yours', () => {
  assert.deepEqual(personalResults(sc, null, '99'), [])
  assert.deepEqual(personalResults(sc, null, ''), [])
  assert.equal(captainReturn(review, '99'), null)
  assert.equal(lineupDifference(review, '99'), null)
  assert.equal(isOwnReview({ ...review, submitted: undefined }, '42'), false)
})

test('unknown multipliers, incomplete comparisons and missing ranks remain unknown', () => {
  assert.equal(captainReturn({ ...review, submitted: { ...review.submitted, captain_multiplier: null } }, '42'), null)
  assert.equal(captainReturn({ ...review, submitted: { ...review.submitted, captain_multiplier: 0 } }, '42'), null)
  assert.equal(lineupDifference({ ...review, xi: { yours: 40 } }, '42'), null)
  const [r] = personalResults(sc, null, '42')
  assert.equal(r.rank, null)
  assert.equal(r.total, null)
})
