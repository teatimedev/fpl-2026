import { useCallback, useEffect, useState } from 'react'
import {
  loadLive, loadTeam, loadEntry, loadEntryHistory,
  type LiveState, type LoadedTeam, type EntrySummary, type EntryHistory,
} from './weekly'
import { inferFreeTransfers } from './model'

/**
 * The linked FPL team, loaded once for the whole app.
 *
 * Live prices and availability come down first; if a team id is set, the real
 * picks, the entry summary and the transfer history are fetched in parallel
 * after that. The id persists in localStorage so a phone remembers it.
 */
export interface LinkedTeam {
  entryId: string
  setEntryId(id: string): void
  live: LiveState | null
  busy: boolean
  err: string | null
  /** real picks (ids, bank, fromGw, lineup) or null when not public */
  team: LoadedTeam | null
  summary: EntrySummary | null
  history: EntryHistory | null
  /** free transfers at this deadline: inferred from history when linked and public, else 1 (15 before GW1) */
  ft: number
}

export function useLinkedTeam(defaultEntryId = ''): LinkedTeam {
  const [entryId, setEntryIdState] = useState(
    () => localStorage.getItem('fplEntryId') || defaultEntryId)
  const setEntryId = useCallback((id: string) => {
    localStorage.setItem('fplEntryId', id)
    setEntryIdState(id)
  }, [])

  useEffect(() => {
    if (!entryId && defaultEntryId) {
      setEntryId(defaultEntryId)
    }
  }, [defaultEntryId, entryId, setEntryId])

  const [live, setLive] = useState<LiveState | null>(null)
  const [team, setTeam] = useState<LoadedTeam | null>(null)
  const [summary, setSummary] = useState<EntrySummary | null>(null)
  const [history, setHistory] = useState<EntryHistory | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    let cancelled = false
    setBusy(true); setErr(null)
    setTeam(null); setSummary(null); setHistory(null)
    loadLive()
      .then(async l => {
        if (cancelled) return
        setLive(l)
        if (!entryId) return
        const id = Number(entryId)
        const [t, s, h] = await Promise.all([
          loadTeam(id, l.gw), loadEntry(id), loadEntryHistory(id),
        ])
        if (cancelled) return
        setTeam(t); setSummary(s); setHistory(h)
      })
      .catch(e => !cancelled && setErr(String(e?.message ?? e)))
      .finally(() => !cancelled && setBusy(false))
    return () => { cancelled = true }
  }, [entryId])

  const gw = live?.gw ?? 1
  const ft = team ? inferFreeTransfers(history, gw) : (gw <= 1 ? 15 : 1)

  return { entryId, setEntryId, live, busy, err, team, summary, history, ft }
}
