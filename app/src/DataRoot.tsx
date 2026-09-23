import { useCallback, useEffect, useState } from 'react'
import type { Data } from './types'
import App from './App'

const DATA_URL = '/data/fpl.json'
const POLL_MS = 10 * 60 * 1000

async function fetchData(): Promise<Data> {
  // no-cache revalidates with the server's ETag: a 304 when nothing changed.
  const response = await fetch(DATA_URL, { cache: 'no-cache' })
  if (!response.ok) throw new Error(`The forecast could not be loaded (HTTP ${response.status}).`)
  return await response.json() as Data
}

/**
 * The forecast and weekly plan are published as a static file by the scheduled
 * refresh, not compiled into the app. An open tab rechecks it on focus and
 * every ten minutes, and swaps in a newer build without a reload.
 */
export default function DataRoot() {
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    fetchData()
      .then(next => {
        setError(null)
        setData(current => current && current.meta.exported === next.meta.exported ? current : next)
      })
      .catch(e => setError(String(e?.message ?? e)))
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, POLL_MS)
    window.addEventListener('focus', load)
    return () => { clearInterval(timer); window.removeEventListener('focus', load) }
  }, [load])

  if (!data) {
    return <div className="shell boot">
      <h1>FPL <em>26/27</em></h1>
      {error
        ? <p className="boot-msg" role="alert">{error} <button className="plink" onClick={load}>Try again</button></p>
        : <p className="boot-msg" role="status">Loading the latest plan…</p>}
    </div>
  }
  return <App D={data} />
}
