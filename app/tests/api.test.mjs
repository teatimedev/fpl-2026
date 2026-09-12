import test from 'node:test'
import assert from 'node:assert/strict'
import handler from '../api/fpl.ts'

function response() {
  return {
    headers: {}, code: 0, body: null,
    setHeader(k, v) { this.headers[k] = v; return this },
    status(code) { this.code = code; return this },
    json(body) { this.body = body; return this },
  }
}

test('proxy rejects writes and arbitrary upstream paths without a request', async t => {
  t.mock.method(globalThis, 'fetch', () => { throw new Error('must not call upstream') })
  for (const [method, path, status] of [['POST', 'fixtures/', 405], ['GET', '../me/', 403]]) {
    const res = response()
    await handler({ method, query: { path } }, res)
    assert.equal(res.code, status)
    assert.equal(res.headers['Cache-Control'], 'no-store')
  }
})

test('successful FPL response has bounded freshness and a request timeout', async t => {
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    assert.ok(options.signal instanceof AbortSignal)
    return Response.json({ events: [] })
  })
  const res = response()
  await handler({ method: 'GET', query: { path: 'bootstrap-static/' } }, res)
  assert.equal(res.code, 200)
  assert.equal(res.headers['Cache-Control'], 'public, s-maxage=60, must-revalidate')
})

test('upstream errors remain uncached and never expose request error internals', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('private internals') })
  const res = response()
  await handler({ method: 'GET', query: { path: 'fixtures/' } }, res)
  assert.equal(res.code, 502)
  assert.equal(res.headers['Cache-Control'], 'no-store')
  assert.doesNotMatch(JSON.stringify(res.body), /private internals/)
})
