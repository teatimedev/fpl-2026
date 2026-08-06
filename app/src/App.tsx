import { useEffect, useMemo, useState } from 'react'
import raw from './data/fpl.json'
import type { Data, Player, Pos } from './types'
import { POS_ORDER, SQUAD_SHAPE, BUDGET, MAX_PER_CLUB } from './types'
import { analyse, blockReason, bestXI, formationOf, squadOutlook, round1 } from './squad'
import { Pips, Shirt, EmptyShirt, Outlook } from './components'

const D = raw as unknown as Data
const byId = new Map(D.players.map(p => [p.id, p]))

type SortKey = 'proj_6gw' | 'price' | 'value' | 'sel_pct' | 'pts_last' | 'name'

const PRESET_BLURB = [
  'The highest projected XI the rules allow, with no constraints beyond the game rules.',
  'Built around the 75%-owned captain. Costs a little projected upside for a lot less rank volatility.',
  'Nothing owned by more than a quarter of managers. Higher variance, but this is how you climb rather than tread water.',
  'The standard heuristic: keep the keeper and defence cheap, spend it up front. Gets you both Haaland and Bruno.',
]

export default function App() {
  const [picks, setPicks] = useState<Player[]>([])
  const [pos, setPos] = useState<Pos | 'ALL'>('ALL')
  const [club, setClub] = useState('ALL')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('proj_6gw')
  const [asc, setAsc] = useState(false)
  const [hideFlagged, setHideFlagged] = useState(true)
  const [affordableOnly, setAffordableOnly] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  // When a suggested squad is loaded, honour the XI the optimiser actually
  // chose. Recomputing it here can disagree — the solver picks the XI jointly
  // with the squad, and ties break differently — which showed a different
  // eleven on the pitch to the one the projection total was quoted for.
  const [presetXI, setPresetXI] = useState<Set<number> | null>(null)
  const state = useMemo(() => analyse(picks), [picks])
  const computedXI = useMemo(() => bestXI(picks), [picks])
  const xi = presetXI ?? computedXI
  const starters = picks.filter(p => xi.has(p.id))
  const bench = picks.filter(p => !xi.has(p.id))
  const captain = starters.length
    ? starters.reduce((a, b) => (b.proj_6gw > a.proj_6gw ? b : a))
    : null
  const outlook = useMemo(
    () => squadOutlook(picks, xi, D.schedule, D.meta.horizon),
    [picks, xi],
  )

  // Any hand edit invalidates the optimiser's XI, so fall back to computing one.
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

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let list = D.players.filter(p => {
      if (pos !== 'ALL' && p.pos !== pos) return false
      if (club !== 'ALL' && p.team !== club) return false
      if (hideFlagged && p.status !== 'a') return false
      if (affordableOnly && blockReason(p, state)) return false
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
    return list.slice(0, 120)
  }, [pos, club, q, sort, asc, hideFlagged, affordableOnly, state])

  const setSorting = (k: SortKey) => {
    if (k === sort) setAsc(a => !a)
    else { setSort(k); setAsc(k === 'name') }
  }

  const deadline = new Date(D.meta.deadline).getTime()
  const left = Math.max(0, deadline - now)
  const dd = Math.floor(left / 86400000)
  const hh = Math.floor(left / 3600000) % 24
  const mm = Math.floor(left / 60000) % 60
  const ss = Math.floor(left / 1000) % 60

  const pickedIds = new Set(picks.map(p => p.id))
  const rowsByPos = (p: Pos) => starters.filter(s => s.pos === p)

  return (
    <div className="shell">
      <header className="topbar">
        <h1>FPL <em>26/27</em> Selector</h1>
        <span className="tag">
          {D.players.length} players · live prices · locked until deadline
        </span>
        <div className="countdown">
          <span className="k">Gameweek 1 deadline</span>
          <span className="v mono">
            {left > 0 ? `${dd}d ${String(hh).padStart(2, '0')}h ${String(mm).padStart(2, '0')}m ${String(ss).padStart(2, '0')}s` : 'Deadline passed'}
          </span>
        </div>
      </header>

      <div className="main">
        {/* ------------------------------------------------------- pitch */}
        <div>
          <section className="panel">
            <div className="panel-hd">
              <h2>Your squad</h2>
              <span className="sub">
                {picks.length}/15 · {starters.length === 11 ? formationOf(picks, xi) : 'incomplete XI'}
              </span>
            </div>
            <div className="pitch-wrap">
              <div className="pitch">
                {POS_ORDER.map(p => {
                  const inRow = rowsByPos(p)
                  const ghosts = p === 'GKP' ? Math.max(0, 1 - inRow.length) : 0
                  return (
                    <div className="row" key={p}>
                      {inRow.map(pl => (
                        <Shirt
                          key={pl.id}
                          player={pl}
                          team={D.teams[pl.team]}
                          fixtures={D.schedule[pl.team] ?? []}
                          isCaptain={captain?.id === pl.id}
                          onClick={() => remove(pl.id)}
                        />
                      ))}
                      {Array.from({ length: ghosts }).map((_, i) => (
                        <EmptyShirt key={`g${i}`} pos={p} />
                      ))}
                      {inRow.length === 0 && ghosts === 0 && <EmptyShirt pos={p} />}
                    </div>
                  )
                })}
                <div className="bench-strip">
                  <div className="bench-label">
                    Bench — in order they come on
                  </div>
                  <div className="row" style={{ marginBottom: 0 }}>
                    {bench.length === 0 && <EmptyShirt pos="GKP" />}
                    {bench
                      .slice()
                      .sort((a, b) =>
                        (a.pos === 'GKP' ? -1 : 0) - (b.pos === 'GKP' ? -1 : 0)
                        || b.proj_6gw - a.proj_6gw)
                      .map(pl => (
                        <Shirt
                          key={pl.id}
                          player={pl}
                          team={D.teams[pl.team]}
                          fixtures={D.schedule[pl.team] ?? []}
                          isCaptain={false}
                          onClick={() => remove(pl.id)}
                        />
                      ))}
                  </div>
                </div>
              </div>
            </div>
          </section>

          {/* ----------------------------------------------------- market */}
          <section className="panel market">
            <div className="panel-hd">
              <h2>Player market</h2>
              <span className="sub">showing {rows.length} of {D.players.length}</span>
            </div>
            <div className="filters">
              <div className="seg">
                {(['ALL', ...POS_ORDER] as const).map(p => (
                  <button key={p} aria-pressed={pos === p} onClick={() => setPos(p)}>{p}</button>
                ))}
              </div>
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
                      aria-sort={sort === 'proj_6gw' ? (asc ? 'ascending' : 'descending') : undefined}>Proj GW1–6</th>
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
                    const block = picked ? null : blockReason(p, state)
                    const t = D.teams[p.team]
                    return [
                      <tr key={p.id} className={picked ? 'picked' : undefined}>
                        <td className="l">
                          <span className="pname">
                            <span className="bar" style={{ background: t.primary }} />
                            <span className="txt">
                              <span className="n">
                                {p.name}
                                {p.is_new && <span className="badge new">new</span>}
                                {p.pens === 1 && <span className="badge pen">pens</span>}
                                {p.status !== 'a' && <span className="badge out">{p.status === 's' ? 'susp' : 'inj'}</span>}
                              </span>
                              <span className="s">{p.pos} · {t.name}</span>
                            </span>
                          </span>
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
                            <button className="addbtn rm" onClick={() => remove(p.id)}
                              title={`Remove ${p.name}`}>−</button>
                          ) : (
                            <button className="addbtn" disabled={!!block}
                              onClick={() => add(p)}
                              title={block ?? `Add ${p.name}`}>+</button>
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
        </div>

        {/* ------------------------------------------------------- sidebar */}
        <aside>
          <section className={`panel budget${state.cost > BUDGET ? ' over' : ''}`}>
            <div className="panel-hd">
              <h2>Budget</h2>
              <span className="sub">£{BUDGET.toFixed(1)}m cap</span>
            </div>
            <div className="budget">
              <div className="figure">
                <span className="big mono">£{state.remaining.toFixed(1)}</span>
                <span className="unit">m</span>
                <span className="lbl">{state.cost > BUDGET ? 'over' : 'left'}</span>
              </div>
              <div className="meter">
                <i style={{ width: `${Math.min(100, (state.cost / BUDGET) * 100)}%` }} />
              </div>
              <div className="statline">
                <div>
                  <span className="k">Picked</span>
                  <span className="v mono">{picks.length}<span style={{ fontSize: 13, color: 'var(--chalk-faint)' }}>/15</span></span>
                </div>
                <div>
                  <span className="k">Spent</span>
                  <span className="v mono">£{state.cost.toFixed(1)}</span>
                </div>
                <div>
                  <span className="k">XI proj</span>
                  <span className="v mono">{round1(starters.reduce((s, p) => s + p.proj_6gw, 0))}</span>
                </div>
              </div>

              {state.problems.length > 0 && (
                <ul className="problems">
                  {state.problems.map(pr => <li key={pr}>{pr}</li>)}
                </ul>
              )}
              {state.complete && state.problems.length === 0 && (
                <div className="ready">
                  Legal squad. {formationOf(picks, xi)} with {captain?.name} as captain.
                </div>
              )}
              {picks.length > 0 && (
                <button className="toggle" style={{ marginTop: 12, width: '100%', justifyContent: 'center' }}
                  onClick={() => { setPresetXI(null); setPicks([]) }}>Clear squad</button>
              )}
            </div>
          </section>

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Suggested squads</h2>
              <span className="sub">click to load</span>
            </div>
            <div className="presets">
              {D.squads.map((s, i) => (
                <button className="preset" key={s.label} onClick={() => loadPreset(i)}>
                  <span className="t">{s.label.split(' - ')[1]?.split(':')[0] ?? s.label}</span>
                  <span className="d">{PRESET_BLURB[i]}</span>
                  <span className="n">£{s.cost.toFixed(1)}m · {s.xi_proj} proj pts</span>
                </button>
              ))}
            </div>
          </section>

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Fixture outlook</h2>
              <span className="sub">XI avg difficulty</span>
            </div>
            <div className="outlook">
              <Outlook rows={outlook} />
              <p style={{ fontSize: 11.5, color: 'var(--chalk-dim)', margin: '10px 0 0', lineHeight: 1.55 }}>
                Taller and redder means your starting XI collectively faces harder
                opponents that week. Hover a bar to see which clubs.
              </p>
            </div>
          </section>

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Club spread</h2>
              <span className="sub">max {MAX_PER_CLUB}</span>
            </div>
            <div className="spread">
              {Object.entries(state.clubCounts).length === 0 && (
                <span style={{ fontSize: 12, color: 'var(--chalk-faint)' }}>
                  No players picked yet.
                </span>
              )}
              {Object.entries(state.clubCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([c, n]) => (
                  <span className={`club-chip${n >= MAX_PER_CLUB ? ' full' : ''}`} key={c}>
                    <span className="swatch" style={{ background: D.teams[c].primary }} />
                    {c} ×{n}
                  </span>
                ))}
            </div>
          </section>

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Squad shape</h2>
              <span className="sub">2 · 5 · 5 · 3</span>
            </div>
            <div className="spread">
              {POS_ORDER.map(p => (
                <span className={`club-chip${state.counts[p] >= SQUAD_SHAPE[p] ? ' full' : ''}`} key={p}>
                  {p} {state.counts[p]}/{SQUAD_SHAPE[p]}
                </span>
              ))}
            </div>
          </section>
        </aside>
      </div>

      <footer className="foot">
        Prices and availability pulled live from the official Fantasy Premier League
        API on {D.meta.generated}. FPL locks all prices until the Gameweek 1 deadline,
        so these are final for team selection.<br />
        Projections are this project's own model over GW1–{D.meta.horizon}: last
        season's per-90 scoring split into clean-sheet, defensive-contribution and
        attacking components, each re-projected against new clubs, new managers and
        the opening fixtures. They are an estimate, not a forecast.
      </footer>
    </div>
  )
}
