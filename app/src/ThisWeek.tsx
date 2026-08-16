import { useMemo, type ReactNode } from 'react'
import type { Data, Player, Pos, Weekly } from './types'
import { POS_ORDER } from './types'
import { withLive, fxFor, priceMovers } from './weekly'
import {
  xiForGw, thisGw, remaining, rankTransfers, lineupIssues, HIT_COST,
  type TransferOption,
} from './model'
import { signed } from './squad'
import { FxChips, PlayerCell, LinkTeamForm } from './components'
import type { LinkedTeam } from './useLinkedTeam'

/**
 * The weekly view: what to actually do before this deadline.
 *
 * Projections are baked in at build time and refreshed by the scheduled job.
 * Prices, injuries and your real squad are fetched live on every visit, because
 * those are exactly what moves between deploys.
 *
 * When the squad on screen is the one the refresh analysed (weekly.squad.ids),
 * the deep digest — two-move combos, six-week plan, availability checks — is
 * rendered instead of the browser's own quick pass.
 */

const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && a.length > 0 && new Set(a).size === a.length
  && a.every(x => b.includes(x))

/** Render `**bold lead** rest` lines the digest emits. */
function Md({ line }: { line: string }) {
  const parts = line.split(/\*\*/)
  if (parts.length < 3) return <>{line}</>
  const out: ReactNode[] = []
  parts.forEach((s, i) => {
    if (!s) return
    out.push(i % 2 === 1 ? <strong key={i}>{s}</strong> : <span key={i}>{s}</span>)
  })
  return <>{out}</>
}

export default function ThisWeek(
  { D, linked, builtSquad, openPlayer, loadSquad }: {
    D: Data; linked: LinkedTeam; builtSquad: Player[]; openPlayer: (id: number) => void
    loadSquad?: (ids: number[]) => void
  },
) {
  const { entryId, live, busy, err } = linked
  const squadIds = linked.team?.ids ?? null
  const lineup = linked.team?.lineup ?? null
  const bank = linked.team?.bank ?? 0
  const fromGw = linked.team?.fromGw ?? null

  const gw = live?.gw ?? D.weekly?.gw ?? D.meta.start_gw ?? 1
  const horizon = D.meta.horizon

  const pool = useMemo(() => D.players.map(p => withLive(p, live)), [D.players, live])
  const poolById = useMemo(() => new Map(pool.map(p => [p.id, p])), [pool])

  const squad: Player[] = useMemo(() => squadIds
    ? squadIds.map(i => poolById.get(i)).filter((p): p is Player => !!p)
    : builtSquad.map(p => poolById.get(p.id) ?? p), [squadIds, builtSquad, poolById])

  const usingReal = !!squadIds
  const ready = squad.length === 15

  // The digest applies only to the exact 15 the refresh saw.
  const weekly: Weekly | null = D.weekly ?? null
  const digest = !!weekly && ready && sameSet(squad.map(p => p.id), weekly.squad.ids)
  const ft = digest && weekly ? weekly.squad.ft : linked.ft

  const { xi, bench } = ready ? xiForGw(squad, gw) : { xi: [], bench: [] }
  const ranked = [...xi].sort((a, b) => thisGw(b, gw) - thisGw(a, gw))
  const captain = ranked[0]
  const vice = ranked[1]
  const flagged = squad.filter(p => p.status !== 'a')
  // Only meaningful for a real team: the lineup you have set, against the model's.
  const issues = ready && usingReal && lineup
    ? lineupIssues(lineup, squad, xi, bench, gw) : null
  const options: TransferOption[] = useMemo(() => ready && !digest
    ? rankTransfers(squad, pool, bank, ft, gw, horizon) : [],
    [ready, digest, squad, pool, bank, ft, gw, horizon])
  const movers = priceMovers(live, pool)

  const dl = live ? new Date(live.deadline) : new Date(D.meta.deadline)
  const msLeft = dl.getTime() - Date.now()
  const days = Math.floor(msLeft / 86400000)
  const hours = Math.floor(msLeft / 3600000) % 24

  const nameOf = (id: number) => poolById.get(id)?.name ?? `#${id}`

  return (
    <div className="week">
      <section className="panel">
        <div className="panel-hd">
          <h2>Gameweek {gw}</h2>
          <span className="sub">
            {`deadline ${dl.toLocaleString('en-GB', {
              weekday: 'short', day: 'numeric', month: 'short',
              hour: '2-digit', minute: '2-digit',
            })}`}
          </span>
        </div>
        <div className="week-hd">
          {msLeft > 0 && (
            <p className="lede">
              <strong className="mono">{days}d {hours}h</strong> until the deadline.
              {live && ` Prices and injuries are live; projections rebuilt ${D.meta.generated}.`}
            </p>
          )}
          <LinkTeamForm
            entryId={entryId} onSave={linked.setEntryId} busy={busy} err={err}
            hint="Find the number in the URL of your FPL points page. Until you link it, this uses the squad from the My squad tab."
            linkedLine={usingReal && fromGw != null
              ? <> · picks from GW{fromGw} · £{bank.toFixed(1)}m banked</>
              : (!busy && !err ? ' · picks not public yet, using the My squad tab squad' : null)}
            unlinkedLine={builtSquad.length === 15
              ? <>Using the squad from the <strong>My squad</strong> tab</>
              : undefined}
          />
        </div>
      </section>

      {!ready && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="empty-state">
            No squad yet. Draft one in the <strong>My squad</strong> tab, or link an
            FPL team id above once the season has started.
          </div>
        </section>
      )}

      {ready && !digest && weekly && (
        <p className="hint" style={{ margin: '12px 2px 0' }}>
          Deep analysis (two-move combos, six-week plan, chips) is computed for
          the squad the refresh saw; link your team id, or{' '}
          {loadSquad ? (
            <button className="plink" onClick={() => loadSquad(weekly.squad.ids)}>
              load that squad
            </button>
          ) : 'load that squad'}{' '}
          to see it.
        </p>
      )}

      {ready && digest && weekly && (
        <Digest D={D} W={weekly} gw={gw} horizon={horizon} poolById={poolById}
          nameOf={nameOf}
          liveIssues={issues} fromGw={fromGw} openPlayer={openPlayer} />
      )}

      {ready && !digest && (
        <>
          <section className="panel accent" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Captain</h2>
              <span className="sub">doubles this week</span>
            </div>
            <div className="captain">
              <div className="pick">
                <button className="big plink-big" onClick={() => openPlayer(captain.id)}>{captain.name}</button>
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
                    <button className="plink" onClick={() => openPlayer(p.id)}>{p.name}</button>
                    <span className="mono">{thisGw(p, gw).toFixed(1)}</span>
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
                    <th className="l">Player</th><th className="l">Fixture</th><th>Price</th>
                    <th>GW{gw}</th><th>GW{gw}–{horizon}</th>
                  </tr>
                </thead>
                <tbody>
                  {POS_ORDER.flatMap(pos => xi.filter(p => p.pos === pos)).map(p => (
                    <tr key={p.id}>
                      <td className="l">
                        <PlayerCell p={p} id={p.id} D={D} openPlayer={openPlayer} extra={<>
                          {p.id === captain.id && <span className="badge pen">C</span>}
                          {p.id === vice?.id && <span className="badge new">V</span>}
                        </>} />
                      </td>
                      <td className="l"><FxChips fx={fxFor(D.ticker, p.team, gw)} /></td>
                      <td>£{p.price.toFixed(1)}</td>
                      <td style={{ color: 'var(--flood-soft)' }}>{thisGw(p, gw).toFixed(1)}</td>
                      <td style={{ color: 'var(--chalk-dim)' }}>{remaining(p, gw, horizon).toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="hint" style={{ padding: '10px 14px 14px' }}>
              Bench, in order:{' '}
              {bench.map((p, i) => (
                <span key={p.id}>
                  {i > 0 && ' → '}
                  <button className="plink" onClick={() => openPlayer(p.id)}>{p.name}</button>
                </span>
              ))}
            </p>
          </section>

          {issues && (
            <section className="panel" style={{ marginTop: 16 }}>
              <div className="panel-hd">
                <h2>Your lineup vs the model</h2>
                <span className="sub">picks from GW{fromGw}</span>
              </div>
              {issues.length === 0 ? (
                <div style={{ padding: '2px 14px 14px' }}>
                  <div className="ready">
                    Your captain, vice, XI and bench order all match the model. ✓
                  </div>
                </div>
              ) : (
                <ul className="problems" style={{ margin: 14 }}>
                  {issues.map((it, i) => (
                    <li key={i}><strong>{it.head}</strong> {it.body}</li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd"><h2>Check before the deadline</h2></div>
            {flagged.length === 0 ? (
              <div style={{ padding: '2px 14px 14px' }}>
                <div className="ready">Nobody flagged. All 15 are available as far as the FPL feed knows.</div>
              </div>
            ) : (
              <ul className="problems" style={{ margin: 14 }}>
                {flagged.map(p => (
                  <li key={p.id}>
                    <strong>{p.name}</strong> — {p.news || `status ${p.status}`}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-hd">
              <h2>Transfers</h2>
              <span className="sub">
                {ft >= 15 ? 'unlimited' : ft} free · £{bank.toFixed(1)}m banked
              </span>
            </div>
            {options.length === 0 ? (
              <div className="empty-state">
                Nothing improves this squad over the remaining gameweeks. Bank it.
              </div>
            ) : (
              <>
                <div className="tbl-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th className="l">Out</th><th className="l">In</th>
                        <th>Cost</th><th>Gain</th><th>Net</th><th className="l">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {options.map(o => (
                        <tr key={o.out.id}>
                          <td className="l">
                            <button className="plink" onClick={() => openPlayer(o.out.id)}>{o.out.name}</button>
                            {' '}<span className="s">{o.out.team}</span>
                          </td>
                          <td className="l">
                            <button className="plink" onClick={() => openPlayer(o.in.id)}>{o.in.name}</button>
                            {' '}<span className="s">{o.in.team}</span>
                          </td>
                          <td>{signed(o.costChange)}</td>
                          <td style={{ color: 'var(--flood-soft)' }}>+{o.gain.toFixed(1)}</td>
                          <td style={{ color: o.net > 0 ? 'var(--ok)' : 'var(--chalk-faint)' }}>
                            {signed(o.net)}
                          </td>
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
                <p className="hint" style={{ padding: '10px 14px 14px' }}>
                  Gain is the lift to your best XI over GW{gw}–{horizon}, captain
                  included; net takes off {HIT_COST} if the move is not free.
                </p>
              </>
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
                  <button className="club-chip plainbtn full" key={`r${p.id}`} onClick={() => openPlayer(p.id)}>
                    ▲ {p.name} <span className="mono">{net > 0 ? '+' : ''}{net.toLocaleString()}</span>
                  </button>
                ))}
                {movers.falling.map(({ p, net }) => (
                  <button className="club-chip plainbtn" key={`f${p.id}`} onClick={() => openPlayer(p.id)}>
                    ▼ {p.name} <span className="mono">{net.toLocaleString()}</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- digest
   The CI-computed analysis, rendered when the loaded squad is the one it saw. */
function Digest({
  D, W, gw, horizon, poolById, nameOf, liveIssues, fromGw, openPlayer,
}: {
  D: Data
  W: Weekly
  gw: number
  horizon: number
  poolById: Map<number, Player>
  nameOf: (id: number) => string
  liveIssues: { head: string; body: string }[] | null
  fromGw: number | null
  openPlayer: (id: number) => void
}) {
  const m = W.model
  const cap = poolById.get(m.captain)
  const vice = poolById.get(m.vice)
  const gwp = (id: number) => m.gw_pts[String(id)] ?? 0
  const rem = (id: number) => m.remaining[String(id)] ?? 0
  const price = (id: number) => poolById.get(id)?.price ?? 0
  const posOf = (id: number): Pos | undefined => poolById.get(id)?.pos
  const teamOf = (id: number) => poolById.get(id)?.team ?? ''
  const xiByPos = POS_ORDER.flatMap(pos => m.xi.filter(id => posOf(id) === pos))
  const shape = (['DEF', 'MID', 'FWD'] as Pos[])
    .map(k => m.xi.filter(id => posOf(id) === k).length).join('-')
  const alts = m.ranked.filter(r => r.id !== m.captain).slice(0, 3)

  const issues = W.lineup_issues ?? []
  const hasDigestLineup = !!W.squad.lineup && (W.squad.lineup.xi?.length ?? 0) > 0
  const checks = W.checks ?? []
  const tr = W.transfers
  const plan = W.plan ?? null
  const price_ = W.price
  const stamp = new Date(W.generated)
  const stampStr = isNaN(stamp.getTime()) ? W.generated
    : stamp.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

  const money = (out: number[], inn: number[]) => {
    const d = inn.reduce((s, i) => s + price(i), 0) - out.reduce((s, i) => s + price(i), 0)
    return signed(d)
  }
  const netCell = (net: number, gain: number) => {
    const hit = gain - net
    return (
      <>
        <span style={{ color: net > 0 ? 'var(--ok)' : 'var(--chalk-faint)' }}>
          {signed(net)}
        </span>
        {hit > 0.05 && <span className="s"> after −{hit.toFixed(0)}</span>}
      </>
    )
  }
  const names = (ids: number[]) => ids.map((id, i) => (
    <span key={id}>{i > 0 && ', '}
      <button className="plink" onClick={() => openPlayer(id)}>{nameOf(id)}</button>
    </span>
  ))

  return (
    <>
      <p className="stamp mono">
        computed {stampStr} for {W.squad.source}
        {W.squad.ft > 0 && ` · ${W.squad.ft >= 15 ? 'unlimited' : W.squad.ft} free transfer${W.squad.ft === 1 ? '' : 's'}`}
        {' '}· £{W.squad.bank.toFixed(1)}m banked
      </p>

      <section className="panel accent" style={{ marginTop: 10 }}>
        <div className="panel-hd">
          <h2>Captain</h2>
          <span className="sub">doubles this week</span>
        </div>
        <div className="captain">
          <div className="pick">
            <button className="big plink-big" onClick={() => cap && openPlayer(cap.id)}>
              {cap?.name ?? nameOf(m.captain)}
            </button>
            <span className="meta">
              {cap && D.teams[cap.team]?.name} · projected{' '}
              <strong>{m.captain_pts.toFixed(1)}</strong>, doubled to{' '}
              <strong>{(m.captain_pts * 2).toFixed(1)}</strong>
              {vice && <> · vice <strong>{vice.name}</strong> {m.vice_pts.toFixed(1)}</>}
            </span>
          </div>
          <ol className="alts">
            {alts.map((r, i) => (
              <li key={r.id}>
                <span className="n">{r.id === m.vice ? 'vice' : `#${i + 2}`}</span>
                <button className="plink" onClick={() => openPlayer(r.id)}>{nameOf(r.id)}</button>
                <span className="mono">{r.pts.toFixed(1)}</span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Start these eleven</h2>
          <span className="sub">{shape}</span>
        </div>
        <div className="tbl-scroll">
          <table>
            <thead>
              <tr>
                <th className="l">Player</th><th className="l">Fixture</th><th>Price</th>
                <th>GW{gw}</th><th>GW{gw}–{horizon}</th>
              </tr>
            </thead>
            <tbody>
              {xiByPos.map(id => (
                <tr key={id}>
                  <td className="l">
                    <PlayerCell p={poolById.get(id)} id={id} D={D} openPlayer={openPlayer} extra={<>
                      {id === m.captain && <span className="badge pen">C</span>}
                      {id === m.vice && <span className="badge new">V</span>}
                    </>} />
                  </td>
                  <td className="l"><FxChips fx={fxFor(D.ticker, teamOf(id), gw)} /></td>
                  <td>£{price(id).toFixed(1)}</td>
                  <td style={{ color: 'var(--flood-soft)' }}>{gwp(id).toFixed(1)}</td>
                  <td style={{ color: 'var(--chalk-dim)' }}>{rem(id).toFixed(1)}</td>
                </tr>
              ))}
              {m.bench.map((id, i) => (
                <tr key={id} className="benchrow">
                  <td className="l">
                    <PlayerCell p={poolById.get(id)} id={id} D={D} openPlayer={openPlayer}
                      extra={<span className="badge bench">{i === 0 ? 'GK' : `sub ${i}`}</span>} />
                  </td>
                  <td className="l"><FxChips fx={fxFor(D.ticker, teamOf(id), gw)} /></td>
                  <td>£{price(id).toFixed(1)}</td>
                  <td>{gwp(id).toFixed(1)}</td>
                  <td>{rem(id).toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint" style={{ padding: '10px 14px 14px' }}>
          Dimmed rows are the bench, in the order they come on.
        </p>
      </section>

      {(hasDigestLineup || liveIssues) && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-hd">
            <h2>Your lineup vs the model</h2>
            <span className="sub">
              {hasDigestLineup ? `lineup from ${W.squad.source}` : `picks from GW${fromGw}`}
            </span>
          </div>
          {hasDigestLineup ? (
            issues.length === 0 ? (
              <div style={{ padding: '2px 14px 14px' }}>
                <div className="ready">
                  Your captain, vice, XI and bench order all match the model. ✓
                </div>
              </div>
            ) : (
              <ul className="problems" style={{ margin: 14 }}>
                {issues.map((line, i) => <li key={i}><Md line={line} /></li>)}
              </ul>
            )
          ) : liveIssues && liveIssues.length === 0 ? (
            <div style={{ padding: '2px 14px 14px' }}>
              <div className="ready">
                Your captain, vice, XI and bench order all match the model. ✓
              </div>
            </div>
          ) : (
            <ul className="problems" style={{ margin: 14 }}>
              {liveIssues!.map((it, i) => (
                <li key={i}><strong>{it.head}</strong> {it.body}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Check before the deadline</h2>
          <span className="sub">{checks.length === 0 ? 'nobody flagged' : `${checks.length} to watch`}</span>
        </div>
        {checks.length === 0 ? (
          <div style={{ padding: '2px 14px 14px' }}>
            <div className="ready">Nobody flagged. All 15 look available and are regular starters.</div>
          </div>
        ) : (
          <ul className="problems soft" style={{ margin: 14 }}>
            {checks.map(c => (
              <li key={c.id}>
                <button className="plink strong" onClick={() => openPlayer(c.id)}>{nameOf(c.id)}</button>
                <span className="s"> {c.xi ? 'XI' : 'bench'}</span> — {c.flags.join('; ')}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Transfers</h2>
          <span className="sub">
            hold = {tr.base.toFixed(1)} over GW{gw}–{horizon}
          </span>
        </div>
        {tr.advice && <p className="lede-sm">{tr.advice}</p>}
        {tr.singles.length === 0 && tr.pairs.length === 0 ? (
          <div className="empty-state">
            Nothing improves this squad over the remaining gameweeks. Bank it.
          </div>
        ) : (
          <>
            {tr.singles.length > 0 && (
              <div className="tbl-scroll">
                <table>
                  <thead>
                    <tr>
                      <th className="l">Out</th><th className="l">In</th>
                      <th>£</th><th>Gain</th><th>Net of hits</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tr.singles.map((s, i) => (
                      <tr key={i}>
                        <td className="l">{names([s.out])} <span className="s">{teamOf(s.out)}</span></td>
                        <td className="l">{names([s.in_])} <span className="s">{teamOf(s.in_)}</span></td>
                        <td>{money([s.out], [s.in_])}</td>
                        <td style={{ color: 'var(--flood-soft)' }}>+{s.gain.toFixed(1)}</td>
                        <td>{netCell(s.net, s.gain)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {tr.pairs.length > 0 && (
              <>
                <p className="subhead">Two moves at once</p>
                <div className="tbl-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th className="l">Out</th><th className="l">In</th>
                        <th>£</th><th>Gain</th><th>Net of hits</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tr.pairs.map((s, i) => (
                        <tr key={i}>
                          <td className="l">{names(s.out)}</td>
                          <td className="l">{names(s.in_)}</td>
                          <td>{money(s.out, s.in_)}</td>
                          <td style={{ color: 'var(--flood-soft)' }}>+{s.gain.toFixed(1)}</td>
                          <td>{netCell(s.net, s.gain)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <p className="hint" style={{ padding: '10px 14px 14px' }}>
              Gain is projected points over GW{gw}–{horizon} against holding; net
              takes off {HIT_COST} per move beyond your free transfers.
            </p>
          </>
        )}
      </section>

      {plan && plan.weeks?.length > 0 && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-hd">
            <h2>Next six weeks, planned</h2>
            <span className="sub">{plan.hits > 0 ? `${plan.hits} hit${plan.hits === 1 ? '' : 's'}` : 'no hits'}</span>
          </div>
          <p className="lede-sm">
            Best path <strong className="mono">{plan.total.toFixed(1)}</strong> vs
            hold <strong className="mono">{plan.hold_total.toFixed(1)}</strong> —
            acting now is worth <strong className="mono" style={{ color: plan.worth_it ? 'var(--ok)' : undefined }}>
              {signed(plan.diff)}
            </strong>
            {(plan.n_now ?? 0) > 1 ? ` across ${plan.n_now} moves` : ''}
            {plan.worth_it ? ' — worth doing.' : (plan.n_now ?? 0) > 0 ? ' — not enough; hold.' : ' — nothing to do this week.'}
          </p>
          <div className="tbl-scroll">
            <table>
              <thead>
                <tr>
                  <th className="l">GW</th><th>Pts</th><th className="l">Captain</th>
                  <th>FT</th><th className="l">Moves</th>
                </tr>
              </thead>
              <tbody>
                {plan.weeks.map(w => (
                  <tr key={w.gw}>
                    <td className="l mono">GW{w.gw}{w.hits > 0 && <span className="badge out">−{w.hits * HIT_COST}</span>}</td>
                    <td style={{ color: 'var(--flood-soft)' }}>{w.pts.toFixed(1)}</td>
                    <td className="l">
                      <button className="plink" onClick={() => openPlayer(w.captain)}>{nameOf(w.captain)}</button>
                    </td>
                    <td>{w.ft >= 15 ? '∞' : w.ft}</td>
                    <td className="l moves">
                      {w.in_.length === 0 ? <span className="s">hold</span> : (
                        <>{names(w.in_)} <span className="s">for</span> {names(w.out)}</>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint" style={{ padding: '10px 14px 14px' }}>
            A plan on point estimates: read it for direction (who to move
            towards, and when), not as a script — it is re-planned every refresh
            and churns on small fixture swings.
          </p>
        </section>
      )}

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Price watch</h2>
          <span className="sub">net transfers this gameweek</span>
        </div>
        {price_.locked ? (
          <div className="empty-state">
            Prices are locked until the Gameweek 1 deadline, so there is no
            transfer flow to read yet.
          </div>
        ) : (price_.rises.length + price_.falls.length) === 0 ? (
          <div className="empty-state">No price pressure in your squad this week.</div>
        ) : (
          <div className="spread">
            {price_.rises.filter(r => r.net !== 0 || r.pressure !== 0).map(r => (
              <button className="club-chip plainbtn full" key={`r${r.id}`} onClick={() => openPlayer(r.id)}>
                ▲ {nameOf(r.id)}{' '}
                <span className="mono">{r.net > 0 ? '+' : ''}{r.net.toLocaleString()}</span>
              </button>
            ))}
            {price_.falls.filter(r => r.net !== 0 || r.pressure !== 0).map(r => (
              <button className="club-chip plainbtn" key={`f${r.id}`} onClick={() => openPlayer(r.id)}>
                ▼ {nameOf(r.id)} <span className="mono">{r.net.toLocaleString()}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </>
  )
}
