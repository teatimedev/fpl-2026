import { useMemo, useState } from 'react'
import type { Data, Player, Pos } from './types'
import { POS_ORDER } from './types'
import { Pips, PlayerCell } from './components'

/**
 * The player market: every player, filterable and sortable, with an add (or
 * remove) button per row. Owns its own filter state.
 *
 * Two configurations: the draft builder passes the whole pool with add/remove
 * and the rule-based blockOf; the transfer sandbox locks the position and caps
 * the price to what selling one player frees, and every row is a replacement.
 */

type SortKey = 'proj_6gw' | 'price' | 'value' | 'sel_pct' | 'pts_last' | 'name'

export default function MarketTable({
  D, players, pickedIds, blockOf, onAdd, onRemove, openPlayer,
  lockPos, maxPrice, title, sub, defaultHideFlagged = true, actionLabel = 'Add',
}: {
  D: Data
  players: Player[]
  pickedIds: Set<number>
  blockOf: (p: Player) => string | null
  onAdd: (p: Player) => void
  onRemove?: (id: number) => void
  openPlayer: (id: number) => void
  lockPos?: Pos
  maxPrice?: number
  title?: string
  sub?: string
  defaultHideFlagged?: boolean
  actionLabel?: string
}) {
  const [pos, setPos] = useState<Pos | 'ALL'>('ALL')
  const [club, setClub] = useState('ALL')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('proj_6gw')
  const [asc, setAsc] = useState(false)
  const [hideFlagged, setHideFlagged] = useState(defaultHideFlagged)
  const [affordableOnly, setAffordableOnly] = useState(false)

  const limit = lockPos ? 60 : 120

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let list = players.filter(p => {
      if (lockPos && p.pos !== lockPos) return false
      if (maxPrice != null && p.price > maxPrice + 1e-9) return false
      if (!lockPos && pos !== 'ALL' && p.pos !== pos) return false
      if (club !== 'ALL' && p.team !== club) return false
      if (hideFlagged && p.status !== 'a') return false
      if (affordableOnly && blockOf(p)) return false
      if (needle && !p.full_name.toLowerCase().includes(needle)
        && !p.name.toLowerCase().includes(needle)
        && !p.team.toLowerCase().includes(needle)) return false
      return true
    })
    list = [...list].sort((a, b) => {
      const dir = asc ? 1 : -1
      if (sort === 'name') return dir * a.name.localeCompare(b.name)
      return dir * ((a[sort] as number) - (b[sort] as number))
    })
    return list.slice(0, limit)
  }, [players, lockPos, maxPrice, pos, club, q, sort, asc, hideFlagged, affordableOnly,
    blockOf, limit])

  const setSorting = (k: SortKey) => {
    if (k === sort) setAsc(a => !a)
    else { setSort(k); setAsc(k === 'name') }
  }

  return (
    <section className="panel market">
      <div className="panel-hd">
        <h2>{title ?? 'Player market'}</h2>
        <span className="sub">{sub ?? `showing ${rows.length} of ${players.length}`}</span>
      </div>
      <div className="filters">
        {!lockPos && (
          <div className="seg">
            {(['ALL', ...POS_ORDER] as const).map(p => (
              <button key={p} aria-pressed={pos === p} onClick={() => setPos(p)}>{p}</button>
            ))}
          </div>
        )}
        <select value={club} onChange={e => setClub(e.target.value)} aria-label="Filter by club">
          <option value="ALL">All clubs</option>
          {Object.keys(D.teams).sort().map(t => (
            <option key={t} value={t}>{D.teams[t].name}</option>
          ))}
        </select>
        <input
          type="search" value={q} placeholder="Search a player…"
          onChange={e => setQ(e.target.value)} aria-label="Search players"
        />
        <button className="toggle" aria-pressed={hideFlagged}
          onClick={() => setHideFlagged(v => !v)}>
          Hide injured
        </button>
        <button className="toggle" aria-pressed={affordableOnly}
          onClick={() => setAffordableOnly(v => !v)}>
          Only what I can add
        </button>
      </div>
      <div className="tbl-scroll">
        <table>
          <thead>
            <tr>
              <th className="l" onClick={() => setSorting('name')}
                aria-sort={sort === 'name' ? (asc ? 'ascending' : 'descending') : undefined}>Player</th>
              <th onClick={() => setSorting('price')}
                aria-sort={sort === 'price' ? (asc ? 'ascending' : 'descending') : undefined}>Price</th>
              <th onClick={() => setSorting('proj_6gw')}
                aria-sort={sort === 'proj_6gw' ? (asc ? 'ascending' : 'descending') : undefined}>Proj GW{D.meta.start_gw ?? 1}–{D.meta.horizon}</th>
              <th onClick={() => setSorting('value')}
                aria-sort={sort === 'value' ? (asc ? 'ascending' : 'descending') : undefined}>Per £m</th>
              <th onClick={() => setSorting('pts_last')}
                aria-sort={sort === 'pts_last' ? (asc ? 'ascending' : 'descending') : undefined}>25/26</th>
              <th onClick={() => setSorting('sel_pct')}
                aria-sort={sort === 'sel_pct' ? (asc ? 'ascending' : 'descending') : undefined}>Owned</th>
              <th>Mins</th>
              <th className="l">Next 6</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map(p => {
              const picked = pickedIds.has(p.id)
              const block = picked ? null : blockOf(p)
              const t = D.teams[p.team]
              return [
                <tr key={p.id} className={picked ? 'picked' : undefined}>
                  <td className="l">
                    <PlayerCell p={p} id={p.id} D={D} openPlayer={openPlayer}
                      sub={`${p.pos} · ${t.name}`} extra={<>
                        {p.is_new && <span className="badge new">new</span>}
                        {p.pens === 1 && <span className="badge pen">pens</span>}
                        {p.status !== 'a' && <span className="badge out">{p.status === 's' ? 'susp' : 'inj'}</span>}
                      </>} />
                  </td>
                  <td>£{p.price.toFixed(1)}</td>
                  <td style={{ color: 'var(--flood-soft)' }}>{p.proj_6gw.toFixed(1)}</td>
                  <td>{p.value.toFixed(2)}</td>
                  <td style={{ color: 'var(--chalk-dim)' }}>{p.pts_last}</td>
                  <td style={{ color: 'var(--chalk-dim)' }}>{p.sel_pct.toFixed(1)}%</td>
                  <td style={{ color: 'var(--chalk-dim)' }}>{p.mins_proj}</td>
                  <td className="l"><Pips fixtures={D.schedule[p.team] ?? []} /></td>
                  <td>
                    {picked ? (
                      onRemove ? (
                        <button className="addbtn rm" onClick={() => onRemove(p.id)}
                          title={`Remove ${p.name}`}>−</button>
                      ) : (
                        <button className="addbtn" disabled title="Already in your squad">+</button>
                      )
                    ) : (
                      <button className="addbtn" disabled={!!block}
                        onClick={() => onAdd(p)}
                        title={block ?? `${actionLabel} ${p.name}`}>+</button>
                    )}
                  </td>
                </tr>,
                p.note ? (
                  <tr className="note-row" key={`${p.id}-n`}>
                    <td colSpan={9}>{p.note}</td>
                  </tr>
                ) : null,
                p.news ? (
                  <tr className="news-row" key={`${p.id}-w`}>
                    <td colSpan={9}>{p.news}</td>
                  </tr>
                ) : null,
              ]
            })}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="empty-state">
            No players match those filters. Try clearing the search or switching club.
          </div>
        )}
      </div>
    </section>
  )
}
