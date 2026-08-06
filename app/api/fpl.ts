import type { VercelRequest, VercelResponse } from '@vercel/node'

/**
 * Proxy for the official Fantasy Premier League API.
 *
 * The FPL API returns no `Access-Control-Allow-Origin` header, so a browser
 * cannot call it directly — every request from the page is blocked before it
 * leaves. This forwards them server-side, which is the whole reason the
 * deployed app can read live prices and your actual squad.
 *
 * Deliberately not an open proxy: only the handful of endpoints this app uses
 * are allowed, so it cannot be turned into a general-purpose relay.
 */

const UPSTREAM = 'https://fantasy.premierleague.com/api'

const ALLOWED: RegExp[] = [
  /^bootstrap-static\/$/,
  /^fixtures\/$/,
  /^entry\/\d+\/$/,
  /^entry\/\d+\/history\/$/,
  /^entry\/\d+\/event\/\d+\/picks\/$/,
]

// Prices and availability change at most once a day; team picks change once a
// week. A minute of edge caching keeps a phone refresh instant without ever
// serving genuinely stale team news.
const CACHE = 'public, s-maxage=60, stale-while-revalidate=600'

export default async function handler(req: VercelRequest, res: VercelResponse) {
  const raw = req.query.path
  const path = Array.isArray(raw) ? raw[0] : raw

  if (!path) {
    return res.status(400).json({ error: 'missing ?path=' })
  }
  if (!ALLOWED.some(rx => rx.test(path))) {
    return res.status(403).json({
      error: 'endpoint not allowed',
      allowed: ALLOWED.map(String),
    })
  }

  try {
    const upstream = await fetch(`${UPSTREAM}/${path}`, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        Accept: 'application/json',
      },
    })
    if (!upstream.ok) {
      // 404 here is normal and expected: picks for a gameweek are not public
      // until its deadline has passed.
      return res.status(upstream.status)
        .json({ error: `upstream ${upstream.status}`, path })
    }
    const body = await upstream.json()
    res.setHeader('Cache-Control', CACHE)
    return res.status(200).json(body)
  } catch (err) {
    return res.status(502).json({
      error: 'could not reach the FPL API',
      detail: err instanceof Error ? err.message : String(err),
    })
  }
}
