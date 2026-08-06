import { useEffect, useState } from 'react'
import type { Data, Player } from './types'
import {
  loadLive, loadTeam, withLive, xiForGw, thisGw, remaining,
  transferOptions, priceMovers, HIT_COST,
  type LiveState, type TransferOption,
} from './weekly'

/**
 * The weekly view: what to actually do before this deadline.
 *
 * Projections are baked in at build time and refreshed by the scheduled job.
 * Prices, injuries and your real squad are fetched live on every visit, because
 * those are exactly what moves between deploys.
 */
export default function ThisWeek(
  { D, builtSquad }: { D: Data; builtSquad: Player[] },
) {
  const [entryId, setEntryId] = useState(
    () => localStorage.getItem('fplEntryId') ?? '')
  const [input, setInput] = useState(entryId)
  const [live, setLive] = useState<LiveState | null>(null)
  const [squadIds, setSquadIds] = useState<number[] | null>(null)
  const [bank, setBank] = useState(0)
  const [fromGw, setFromGw] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    let cancelled = false
    setBusy(true); setErr(null)
    loadLive()
      .then(async l => {
        if (cancelled) return
        setLive(l)
        if (entryId) {
          const t = await loadTeam(Number(entryId), l.gw)
          if (cancelled) return
          if (t) { setSquadIds(t.ids); setBank(t.bank); setFromGw(t.fromGw) }
          else { setSquadIds(null); setFromGw(null) }
        }
      })
      .catch(e => !cancelled && setErr(String(e?.message ?? e)))
      .finally(() => !cancelled && setBusy(false))
    return () => { cancelled = true }
  }, [entryId])

  const gw = live?.gw ?? 1
  const horizon = D.meta.horizon

  const pool = D.players.map(p => withLive(p, live))
  const poolById = new Map(pool.map(p => [p.id, p]))

  const squad: Player[] = squadIds
    ? squadIds.map(i => poolById.get(i)).filter((p): p is Player => !!p)
    : builtSquad.map(p => poolById.get(p.id) ?? p)

  const usingReal = !!squadIds
  const ready = squad.length === 15

  const save = () => {
    const v = input.trim()
    localStorage.setItem('fplEntryId', v)
    setEntryId(v)
  }

  const { xi, bench } = ready ? xiForGw(squad, gw) : { xi: [], bench: [] }
  const ranked = [...xi].sort((a, b) => thisGw(b, gw) - thisGw(a, gw))
  const captain = ranked[0]
  const vice = ranked[1]
  const flagged = squad.filter(p => p.status !== 'a')
  const options: TransferOption[] = ready
    ? transferOptions(squad, pool, bank, gw, horizon) : []
  const movers = priceMovers(live, pool)

  const dl = live ? new Date(live.deadline) : null
  const msLeft = dl ? dl.getTime() - Date.now() : 0
  const days = Math.floor(msLeft / 86400000)
  const hours = Math.floor(msLeft / 3600000) % 24

  return (
    <div className="week">
      <section className="panel">
        <div className="panel-hd">
          <h2>Gameweek {gw}</h2>
          <span className="sub">
            {dl ? `deadline ${dl.toLocaleString('en-GB', {
              weekday: 'short', day: 'numeric', month: 'short',
              hour: '2-digit', minute: '2-digit',
            })}` : ''}
          </span>
        </div>
        <div className="week-hd">
          {dl && msLeft > 0 && (
            <p className="lede">
              <strong className="mono">{days}d {hours}h</strong> until the deadline.
              {live && ' Prices and injuries are live; projections were built by the weekly job.'}
            </p>
          )}
          <div className="linkrow">
            <label htmlFor="entry">Your FPL team id</label>
            <input
              id="entry" type="text" inputMode="numeric" placeholder="e.g. 1234567"
              value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && save()}
            />
            <button className="toggle" onClick={save}>Link team</button>
          </div>
          <p className="hint">
            {busy ? 'Loading live data…'
              : err ? `Could not reach the FPL API: ${err}`
              : usingReal
                ? `Showing your real squad, as picked in Gameweek ${fromGw}. £${bank.toFixed(1)}m in the bank.`
                : entryId
                  ? 'Your picks are not public yet — they appear once a deadline has passed. Using the squad from the Build tab meanwhile.'
                  : 'Find the number in the URL of your FPL points page. Until you link it, this uses the squad from the Build tab.'}
          </p>
        </div>
      </section>

      {!ready && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="empty-state">
            No squad yet. Build one in the <strong>Build</strong> tab, or link an
            FPL team id above once the season has started.
          </div>
        </section>
      )}

      {ready && (
        <>
          <section className="panel accent" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Captain</h2>
              <span className="sub">doubles this week</span>
            </div>
            <div className="captain">
              <div className="pick">
                <span className="big">{captain.name}</span>
                <span className="meta">
                  {D.teams[captain.team]?.name} · projected{' '}
                  <strong>{thisGw(captain, gw).toFixed(1)}</strong>, doubled to{' '}
                  <strong>{(thisGw(captain, gw) * 2).toFixed(1)}</strong>
                </span>
              </div>
              <ol className="alts">
                {ranked.slice(1, 4).map((p, i) => (
                  <li key={p.id}>
                    <span className="n">{i === 0 ? 'vice' : `#${i + 2}`}</span>
                    {p.name} <span className="mono">{thisGw(p, gw).toFixed(1)}</span>
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Start these eleven</h2>
              <span className="sub">
                {['DEF', 'MID', 'FWD'].map(k =>
                  xi.filter(p => p.pos === k).length).join('-')}
              </span>
            </div>
            <div className="tbl-scroll">
              <table>
                <thead>
                  <tr>
                    <th className="l">Player</th><th>Price</th>
                    <th>GW{gw}</th><th>GW{gw}–{horizon}</th>
                  </tr>
                </thead>
                <tbody>
                  {xi.map(p => (
                    <tr key={p.id}>
                      <td className="l">
                        <span className="pname">
                          <span className="bar" style={{ background: D.teams[p.team]?.primary }} />
                          <span className="txt">
                            <span className="n">
                              {p.name}
                              {p.id === captain.id && <span className="badge pen">C</span>}
                              {p.id === vice?.id && <span className="badge new">V</span>}
                            </span>
                            <span className="s">{p.pos} · {p.team}</span>
                          </span>
                        </span>
                      </td>
                      <td>£{p.price.toFixed(1)}</td>
                      <td style={{ color: 'var(--flood-soft)' }}>{thisGw(p, gw).toFixed(1)}</td>
                      <td style={{ color: 'var(--chalk-dim)' }}>{remaining(p, gw, horizon).toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="hint" style={{ padding: '10px 14px 14px' }}>
              Bench, in order: {bench.map(p => p.name).join(' → ')}
            </p>
          </section>

          {flagged.length > 0 && (
            <section className="panel" style={{ marginTop: 16 }}>
              <div className="panel-hd"><h2>Check before the deadline</h2></div>
              <ul className="problems" style={{ margin: 14 }}>
                {flagged.map(p => (
                  <li key={p.id}>
                    <strong>{p.name}</strong> — {p.news || `status ${p.status}`}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Transfers</h2>
              <span className="sub">£{bank.toFixed(1)}m banked</span>
            </div>
            {options.length === 0 ? (
              <div className="empty-state">
                Nothing improves this squad over the remaining gameweeks. Bank it.
              </div>
            ) : (
              <div className="tbl-scroll">
                <table>
                  <thead>
                    <tr>
                      <th className="l">Out</th><th className="l">In</th>
                      <th>Cost</th><th>Gain</th><th className="l">Verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    {options.map(o => (
                      <tr key={o.out.id}>
                        <td className="l">{o.out.name} <span className="s">{o.out.team}</span></td>
                        <td className="l">{o.in.name} <span className="s">{o.in.team}</span></td>
                        <td>{o.costChange >= 0 ? '+' : ''}{o.costChange.toFixed(1)}</td>
                        <td style={{ color: 'var(--flood-soft)' }}>+{o.gain.toFixed(1)}</td>
                        <td className="l">
                          {o.worthAHit
                            ? <span style={{ color: 'var(--ok)' }}>worth a −{HIT_COST} hit</span>
                            : <span style={{ color: 'var(--chalk-faint)' }}>free transfer only</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Price watch</h2>
              <span className="sub">net transfers this gameweek</span>
            </div>
            {!movers.active ? (
              <div className="empty-state">
                Prices are locked until the Gameweek 1 deadline, so there is no
                transfer flow to read yet.
              </div>
            ) : (
              <div className="spread">
                {movers.rising.map(({ p, net }) => (
                  <span className="club-chip full" key={`r${p.id}`}>
                    ▲ {p.name} <span className="mono">{net > 0 ? '+' : ''}{net.toLocaleString()}</span>
                  </span>
                ))}
                {movers.falling.map(({ p, net }) => (
                  <span className="club-chip" key={`f${p.id}`}>
                    ▼ {p.name} <span className="mono">{net.toLocaleString()}</span>
                  </span>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
