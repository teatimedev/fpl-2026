import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { optimalLineup } from '../src/lineupSearch.ts'
import { thisGw, playProbability, xiForGw, squadBreakdown } from '../src/model.ts'

const { cases } = JSON.parse(readFileSync(new URL('./fixtures/lineup-parity.json', import.meta.url)))
for (const { case: label, squad, expected } of cases) {
  test(`exhaustive browser/Python lineup and scalar score agree: ${label}`, () => {
    const actual = optimalLineup(squad, 1, thisGw, playProbability)
    assert.ok(Math.abs(actual.score - expected.score) < 1e-9)
    assert.deepEqual(actual.xi.map(p => p.id), expected.xi)
    assert.deepEqual(actual.bench.map(p => p.id), expected.bench)
    assert.equal(actual.captain.id, expected.captain)
    assert.equal(actual.vice.id, expected.vice)
    assert.deepEqual(xiForGw(squad, 1).xi.map(p => p.id), expected.xi)
    assert.ok(Math.abs(squadBreakdown(squad, 1, 1).total - expected.score) < 1e-9)
  })
}

test('non-finite keeper forecasts cannot produce an apparently valid XI', () => {
  const squad = structuredClone(cases[0].squad)
  squad[0].proj_by_gw = [NaN]
  assert.throws(() => optimalLineup(squad, 1, thisGw, playProbability), /finite/)
})
