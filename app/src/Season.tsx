import { useMemo, useState } from 'react'
import type { ChipInfo, Data, MoverTopRow } from './types'
import { BarSpark, LineSpark } from './components'
import { csTone, xgTone } from './weekly'
import { signed } from './squad'

/**
 * The season view: when to play each chip, how every club's fixtures swing
 * over the next two months, and where the crowd's money is going. All of it
 * is baked by the weekly refresh; nothing here is fetched live.
 */

const DASH = '—'
const TICKER_SPAN = 8

function chipNow(c: ChipInfo): string {
  if (c.now == null) return DASH
  return c.now.toFixed(1) + (c.now_name ? ` (${c.now_name})` : '')
}

function chipBest(c: ChipInfo): string {
  if (c.best_gw == null || c.best == null) return DASH
  return `GW${c.best_gw} · ${c.best.toFixed(1)}${c.best_name ? ` (${c.best_name})` : ''}`
}

export default function Season({
  D, openPlayer,
}: {
  D: Data
  openPlayer: (id: number) => void
}) {
  const chips = D.chips ?? D.weekly?.chips ?? null
  const movers = D.movers ?? null
  const ticker = D.ticker ?? null
  const gw = D.weekly?.gw ?? chips?.gw ?? D.meta.start_gw ?? 1
  const byId = useMemo(() => new Map(D.players.map(p => [p.id, p])), [D.players])

  /* ------------------------------------------------------------- ticker */
  const [view, setView] = useState<'def' | 'att'>('def')
  const gwEnd = Math.min(38, gw + TICKER_SPAN - 1)
  const gws = useMemo(() => {
    const out: number[] = []
    for (let g = gw; g <= gwEnd; g++) out.push(g)
    return out
  }, [gw, gwEnd])

  const tickerRows = useMemo(() => {
    if (!ticker) return []
    return Object.keys(ticker)
      .map(team => {
        const cells = gws.map(g => ticker[team]?.find(r => r.gw === g)?.fx ?? [])
        const all = cells.flat()
        // Sum per gameweek: a double counts twice, a blank contributes nothing,
        // which is exactly how it plays out on the pitch.
        const avg = all.reduce((s, f) => s + (view === 'def' ? f.cs : f.xg), 0) / gws.length
        return { team, cells, avg }
      })
      .sort((a, b) => b.avg - a.avg)
  }, [ticker, view, gws])

  /* ------------------------------------------------------------- movers */
  const moverRow = (row: MoverTopRow) => {
    const p = byId.get(row.id)
    const st = movers?.players?.[String(row.id)]
    if (!p || !st) return null
    return (
      <li key={row.id}>
        <button className="plink" onClick={() => openPlayer(p.id)}>
          <span className="n">{p.name}</span>
          <span className="s">{p.team}</span>
        </button>
        <span className="mv mono">
          {st.sel.toFixed(1)}%
          <em>{signed(st.d_sel_7)} 7d</em>
        </span>
        <LineSpark values={st.spark} />
      </li>
    )
  }

  const dgws = chips ? Object.entries(chips.dgw) : []
  const bgws = chips ? Object.entries(chips.bgw) : []
  const h = chips?.heuristics ?? {}
  const chipList = chips
    ? ([chips.chips.bboost, chips.chips['3xc'], chips.chips.freehit, chips.chips.wildcard]
        .filter((c): c is ChipInfo => !!c))
    : []

  const days = movers?.days ?? 0
  const flow = [
    ...(movers?.top.bought_event ?? []).map(r => ({ r, dir: '▲' })),
    ...(movers?.top.sold_event ?? []).map(r => ({ r, dir: '▼' })),
  ]

  return (
    <div className="season">
      {/* ------------------------------------------------------------ chips */}
      <section className="panel">
        <div className="panel-hd">
          <h2>Chips</h2>
          <span className="sub">when to play what</span>
        </div>
        {chipList.length === 0 ? (
          <div className="empty-state">No chip analysis in this build yet.</div>
        ) : (
          <>
            <div className="tbl-scroll">
              <table className="chips">
                <thead>
                  <tr>
                    <th className="l">Chip</th>
                    <th>This week</th>
                    <th className="l">Best week, this copy</th>
                  </tr>
                </thead>
                <tbody>
                  {chipList.map(c => [
                    <tr key={c.name} className={c.play ? 'play' : undefined}>
                      <td className="l chip-name">
                        {c.name}
                        {c.play && <span className="badge pen">play</span>}
                      </td>
                      <td>{chipNow(c)}</td>
                      <td className="l mono">{chipBest(c)}</td>
                    </tr>,
                    <tr key={`${c.name}-a`} className={`advice-row${c.play ? ' play' : ''}`}>
                      <td colSpan={3}>{c.advice ?? DASH}</td>
                    </tr>,
                  ])}
                </tbody>
              </table>
            </div>
            <div className="chip-sparks">
              {([chips!.chips.bboost, chips!.chips['3xc']]
                .filter((c): c is ChipInfo => !!c && (c.weeks?.length ?? 0) > 0))
                .map(c => (
                  <div key={c.name}>
                    <span className="k">
                      {c.name}, week by week (GW{c.weeks![0][0]}–{c.weeks![c.weeks!.length - 1][0]})
                    </span>
                    <BarSpark items={c.weeks!.map(w => ({
                      label: `GW${w[0]}: ${w[1].toFixed(1)}${w[2] ? ` (${w[2]})` : ''}`,
                      v: w[1],
                      hot: w[0] === c.best_gw,
                    }))} />
                  </div>
                ))}
            </div>
            <p className="hint season-line">
              {dgws.length === 0 && bgws.length === 0
                ? 'No double or blank gameweeks announced yet — chip timing firms up once they are.'
                : [
                    dgws.length > 0 && `Doubles: ${dgws.map(([g, ts]) => `GW${g} (${ts.join(', ')})`).join(' · ')}`,
                    bgws.length > 0 && `Blanks: ${bgws.map(([g, ts]) => `GW${g} (${ts.join(', ')})`).join(' · ')}`,
                  ].filter(Boolean).join(' · ')}
            </p>
            <p className="season-caveat">
              Rules of thumb: play Bench Boost at ≥{h.bb_play_min ?? 12} bench points,
              Triple Captain at ≥{h.tc_play_min ?? 8} extra, Free Hit at a{' '}
              ≥{h.fh_play_min ?? 12}-point gap, Wildcard at ≥{h.wc_play_min ?? 20}.
              Heuristics on point estimates — a nudge, not an order.
            </p>
          </>
        )}
      </section>

      {/* ----------------------------------------------------------- ticker */}
      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Fixture ticker</h2>
          <span className="sub">GW{gw}–{gwEnd}</span>
          <div className="seg" style={{ marginLeft: 10 }}>
            <button aria-pressed={view === 'att'} onClick={() => setView('att')}>Attack</button>
            <button aria-pressed={view === 'def'} onClick={() => setView('def')}>Defence</button>
          </div>
        </div>
        {tickerRows.length === 0 ? (
          <div className="empty-state">No fixture model in this build yet.</div>
        ) : (
          <>
            <div className="tbl-scroll">
              <table className="tick">
                <thead>
                  <tr>
                    <th className="l club">Club</th>
                    {gws.map(g => <th key={g}>GW{g}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {tickerRows.map(row => (
                    <tr key={row.team}>
                      <td className="club" title={D.teams[row.team]?.name ?? row.team}>
                        {row.team}
                      </td>
                      {row.cells.map((fx, i) => (
                        <td key={i} className="tick-cell">
                          {fx.length === 0
                            ? <span className="blankmark">—</span>
                            : fx.map((f, j) => (
                              <span key={j} className="tick-fx"
                                data-tone={view === 'def' ? csTone(f.cs) : xgTone(f.xg)}>
                                <span className="o">{f.opp} ({f.home ? 'H' : 'A'})</span>
                                <span className="x">xG {f.xg.toFixed(1)}</span>
                              </span>
                            ))}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="season-caveat">
              {view === 'def'
                ? 'Coloured by the model’s clean-sheet odds for that fixture: green ≥45%, neutral 30–45%, amber 20–30%, red <20%. Clubs sorted by average over the range shown. The small number is the club’s expected goals in the game.'
                : 'Coloured by the club’s expected goals in that fixture: green ≥1.8, neutral 1.4–1.8, amber 1.0–1.4, red <1.0. Clubs sorted by average over the range shown.'}
              {' '}Two chips stacked = a double gameweek; — = blank.
            </p>
          </>
        )}
      </section>

      {/* ----------------------------------------------------------- movers */}
      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Movers</h2>
          <span className="sub">
            {movers ? `${days} day${days === 1 ? '' : 's'} of ownership log` : 'no log yet'}
          </span>
        </div>
        {!movers ? (
          <div className="empty-state">No ownership log in this build yet.</div>
        ) : (
          <div className="movers-body">
            <div className="movers-cols">
              <div>
                <h3 className="movers-h">Most bought (7d)</h3>
                {movers.top.bought_7d.length === 0 ? (
                  <p className="movers-empty">Nothing yet — the log needs a few days of history.</p>
                ) : (
                  <ul className="movers-list">{movers.top.bought_7d.map(moverRow)}</ul>
                )}
              </div>
              <div>
                <h3 className="movers-h">Most sold (7d)</h3>
                {movers.top.sold_7d.length === 0 ? (
                  <p className="movers-empty">Nothing yet — the log needs a few days of history.</p>
                ) : (
                  <ul className="movers-list">{movers.top.sold_7d.map(moverRow)}</ul>
                )}
              </div>
            </div>
            <h3 className="movers-h">This gameweek's flow</h3>
            {flow.length === 0 ? (
              <p className="movers-empty">
                No transfer flow recorded yet this gameweek.
              </p>
            ) : (
              <div className="spread" style={{ padding: '4px 0 0' }}>
                {flow.map(({ r, dir }) => {
                  const p = byId.get(r.id)
                  const st = movers.players?.[String(r.id)]
                  if (!p || !st) return null
                  return (
                    <button key={`${dir}${r.id}`}
                      className={`club-chip plainbtn${dir === '▲' ? ' full' : ''}`}
                      onClick={() => openPlayer(p.id)}>
                      {dir} {p.name}{' '}
                      <span className="mono">
                        {st.net_event > 0 ? '+' : ''}{st.net_event.toLocaleString()}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
            <p className="season-caveat" style={{ padding: '10px 0 0' }}>
              {days < 7
                ? `The ownership log started ${movers.first} — ${days} day${days === 1 ? '' : 's'} of data so far, so weekly deltas are still filling in.`
                : `Ownership log ${movers.first} → ${movers.latest}.`}
            </p>
          </div>
        )}
      </section>
    </div>
  )
}
