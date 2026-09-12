import test from 'node:test'
import assert from 'node:assert/strict'
import { captainOptions, lineupIssues } from '../src/model.ts'

const safe = { id: 1, name: 'Safe', pos: 'GKP', proj_by_gw: [6], play_by_gw: [1] }
const risk = { id: 2, name: 'Risk', pos: 'MID', proj_by_gw: [5], play_by_gw: [.5] }
test('captain and vice are chosen together, including fallback', () => {
  const pair = captainOptions([safe, risk], 1)[0]
  assert.equal(pair.captain.id, 2)
  assert.equal(pair.vice.id, 1)
  assert.equal(pair.bonus, 8)
})
test('a keeper with the best fallback points is a valid vice', () => {
  const issues = lineupIssues({ xi: [], bench: [], captain: 2, vice: 1 }, [safe, risk], [safe, risk], [], 1)
  assert.deepEqual(issues, [])
})
test('captain difference reports pair gain instead of a misleading negative individual gain', () => {
  const issues = lineupIssues({ xi: [], bench: [], captain: 1, vice: 2 }, [safe, risk], [safe, risk], [], 1)
  assert.match(issues[0].body, /\+2\.0.*including vice fallback/)
})
