import { useCallback, useEffect, useState } from 'react'
import {
  loadLive, loadTeam, loadEntry, loadEntryHistory, validateHistory,
  type LiveState, type LoadedTeam, type EntrySummary, type EntryHistory,
} from './weekly'
import { inferFreeTransfers } from './model'
import { confirmedTeam } from './confirmedTeam'
import type { Weekly } from './types'

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
  /** free transfers from validated public history; NaN when unavailable */
  ft: number
}

export function useLinkedTeam(defaultEntryId = '', weekly?: Weekly | null): LinkedTeam {
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
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    const update = () => setRefresh(n => n + 1)
    const timer = setInterval(update, 5 * 60 * 1000)
    window.addEventListener('focus', update)
    window.addEventListener('online', update)
    return () => {
      clearInterval(timer)
      window.removeEventListener('focus', update)
      window.removeEventListener('online', update)
    }
  }, [])

  useEffect(() => {
    if (!live) return
    const remaining = Date.parse(live.deadline) - Date.now()
    if (remaining < 0) return
    const timer = setTimeout(() => setRefresh(n => n + 1),
      Math.min(remaining + 50, 2_147_483_647))
    return () => clearTimeout(timer)
  }, [live])

  useEffect(() => {
    let cancelled = false
    setBusy(true); setErr(null)
    setLive(null)
    setTeam(null); setSummary(null); setHistory(null)
    loadLive()
      .then(async l => {
        if (cancelled) return
        setLive(l)
        if (!entryId) return
        const id = Number(entryId)
        try {
          const [t, s, h] = await Promise.all([
            loadTeam(id, l.gw), loadEntry(id), loadEntryHistory(id),
          ])
          if (cancelled) return
          setSummary(s)
          if (t && !h) throw new Error('FPL team history is unavailable; free transfers cannot be verified')
          if (t && h) validateHistory(h, l.gw - 1)
          setTeam(t); setHistory(h)
        } catch (error) {
          // Account publication can lag the deadline calendar. Preserve the
          // independently verified GW/player feed when that happens.
          if (!cancelled) setErr(String((error as Error)?.message ?? error))
        }
      })
      .catch(e => {
        if (!cancelled) { setLive(null); setErr(String(e?.message ?? e)) }
      })
      .finally(() => !cancelled && setBusy(false))
    return () => { cancelled = true }
  }, [entryId, refresh])

  const gw = live?.gw ?? 1
  const ft = team ? inferFreeTransfers(history, gw) : (live?.gw === 1 ? 15 : NaN)

  const confirmed = confirmedTeam(weekly, entryId, gw, team, ft)
  return { entryId, setEntryId, live, busy, err, summary, history, ...confirmed }
}
