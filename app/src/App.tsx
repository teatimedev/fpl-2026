import { useCallback, useEffect, useMemo, useState } from 'react'
import raw from './data/fpl.json'
import type { Data, Player } from './types'
import { analyse, blockReason } from './squad'
import ThisWeek from './ThisWeek'
import Season from './Season'
import MySquad from './MySquad'
import Scorecard from './Scorecard'
import PlayerDrawer from './PlayerDrawer'
import { useLinkedTeam } from './useLinkedTeam'

const D = raw as unknown as Data
const byId = new Map(D.players.map(p => [p.id, p]))

type Tab = 'week' | 'season' | 'squad' | 'score'

export default function App() {
  // Open on whichever tab is actually useful. With no squad drafted and no FPL
  // team linked there is nothing for the weekly view to talk about, so start on
  // My squad; once either exists, the weekly decision is the reason to open
  // this on a phone. The scorecard is never the default.
  const [tab, setTab] = useState<Tab>(() => {
    const hasSquad = (localStorage.getItem('fplSquad') ?? '[]') !== '[]'
    const hasTeam = !!localStorage.getItem('fplEntryId')
    const hasConfirmedSquad = (D.weekly?.squad.ids.length ?? 0) === 15
    return hasSquad || hasTeam || hasConfirmedSquad ? 'week' : 'squad'
  })

  // Live prices, availability and the linked team, loaded once for every tab.
  const linked = useLinkedTeam(D.weekly?.squad.entry_id?.toString() ?? '')

  // The drafted squad (My squad → Draft mode). Persisted so the weekly view
  // can fall back to it, and so a phone does not lose it between visits.
  const [picks, setPicks] = useState<Player[]>(() => {
    const confirmedIds = D.weekly?.squad.ids ?? []
    const snapshotKey = D.weekly && confirmedIds.length === 15
      ? D.weekly.squad.confirmed_at || JSON.stringify([
          confirmedIds,
          D.weekly.squad.lineup ?? null,
        ])
      : ''
    try {
      const saved = JSON.parse(localStorage.getItem('fplSquad') ?? '[]') as number[]
      const unseenSnapshot = snapshotKey
        && localStorage.getItem('fplEmbeddedSquadKey') !== snapshotKey
        && confirmedIds.length === 15
      if (unseenSnapshot) {
        localStorage.setItem('fplEmbeddedSquadKey', snapshotKey)
      }
      const initial = unseenSnapshot || saved.length === 0 ? confirmedIds : saved
      return initial.map(id => byId.get(id)).filter((p): p is Player => !!p)
    } catch {
      return confirmedIds.map(id => byId.get(id)).filter((p): p is Player => !!p)
    }
  })
  useEffect(() => {
    localStorage.setItem('fplSquad', JSON.stringify(picks.map(p => p.id)))
  }, [picks])
  const [draftMode, setDraftMode] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  // When a suggested squad is loaded, honour the XI the optimiser actually
  // chose (see MySquad's Draft for why). Any hand edit invalidates it.
  const [presetXI, setPresetXI] = useState<Set<number> | null>(null)
  const state = useMemo(() => analyse(picks), [picks])

  // One drawer for the whole app: any player name or shirt opens it.
  const [drawerId, setDrawerId] = useState<number | null>(null)
  const openPlayer = useCallback((id: number) => setDrawerId(id), [])
  const closeDrawer = useCallback(() => setDrawerId(null), [])
  const drawerPlayer = drawerId != null ? byId.get(drawerId) ?? null : null
  const drawerGw = D.weekly?.gw ?? D.meta.start_gw ?? 1

  const add = (p: Player) => {
    if (!blockReason(p, state)) { setPresetXI(null); setPicks(s => [...s, p]) }
  }
  const remove = (id: number) => {
    setPresetXI(null)
    setPicks(s => s.filter(p => p.id !== id))
  }
  const loadPreset = (i: number) => {
    const s = D.squads[i]
    setPicks(s.picks.map(x => byId.get(x.id)!).filter(Boolean))
    setPresetXI(new Set(s.picks.filter(x => x.starting).map(x => x.id)))
  }
  const clear = () => { setPresetXI(null); setPicks([]) }

  const deadline = new Date(D.meta.deadline).getTime()
  const left = Math.max(0, deadline - now)
  const dd = Math.floor(left / 86400000)
  const hh = Math.floor(left / 3600000) % 24
  const mm = Math.floor(left / 60000) % 60
  const ss = Math.floor(left / 1000) % 60

  const pickedIds = new Set(picks.map(p => p.id))
  const drafting = tab === 'squad' && draftMode

  return (
    <div className="shell">
      <header className="topbar">
        <h1>FPL <em>26/27</em> Selector</h1>
        <span className="tag">
          {D.players.length} players · live prices · locked until deadline
        </span>
        <div className="seg tabs">
          <button aria-pressed={tab === 'week'} onClick={() => setTab('week')}>This week</button>
          <button aria-pressed={tab === 'season'} onClick={() => setTab('season')}>Season</button>
          <button aria-pressed={tab === 'squad'} onClick={() => setTab('squad')}>My squad</button>
          <button aria-pressed={tab === 'score'} onClick={() => setTab('score')}>Scorecard</button>
        </div>
        <div className="countdown">
          <span className="k">Gameweek {D.meta.start_gw ?? 1} deadline</span>
          <span className="v mono">
            {left > 0 ? `${dd}d ${String(hh).padStart(2, '0')}h ${String(mm).padStart(2, '0')}m ${String(ss).padStart(2, '0')}s` : 'Deadline passed'}
          </span>
        </div>
      </header>

      <PlayerDrawer player={drawerPlayer} D={D} gw={drawerGw} onClose={closeDrawer}
        action={drawerPlayer && drafting ? (
          pickedIds.has(drawerPlayer.id)
            ? { label: `Remove ${drawerPlayer.name} from squad`, run: () => { remove(drawerPlayer.id); closeDrawer() } }
            : blockReason(drawerPlayer, state)
              ? null
              : { label: `Add ${drawerPlayer.name} to squad`, run: () => { add(drawerPlayer); closeDrawer() } }
        ) : null} />

      {tab === 'week' && (
        <ThisWeek D={D} linked={linked} builtSquad={picks} openPlayer={openPlayer}
          loadSquad={ids => { setPresetXI(null); setPicks(ids.map(id => byId.get(id)).filter((p): p is Player => !!p)) }} />
      )}
      {tab === 'season' && <Season D={D} openPlayer={openPlayer} />}
      {tab === 'score' && <Scorecard sc={D.scorecard ?? null} />}
      {tab === 'squad' && (
        <MySquad D={D} linked={linked} picks={picks} presetXI={presetXI} state={state}
          draftMode={draftMode} setDraftMode={setDraftMode}
          add={add} remove={remove} loadPreset={loadPreset} clear={clear}
          openPlayer={openPlayer} />
      )}

      <footer className="foot">
        Prices, injuries and your squad are read live from the official Fantasy
        Premier League API each time you open this. Projections are rebuilt about
        24 hours and 2 hours before every deadline (and each Thursday). Public
        official club news is scanned every three hours from T−30h, then hourly
        from T−6h to T−45m; an owned-player change triggers another full rebuild.
        Bookmaker odds are blended in where posted — model last built {D.meta.generated}.<br />
        Attack and defence ratings are fitted by maximum likelihood on four
        seasons of real results and validated against bookmaker closing odds;
        player rates are shrunk by measured year-over-year stability. Hold-out
        rank correlation is about 0.46, so treat the ordering as a strong hint
        and the totals as rough.
      </footer>
    </div>
  )
}
