import { useMemo } from 'react'
import type { Data, NewsClaim, Player, Weekly } from './types'
import { withLive } from './weekly'
import { xiForGw, thisGw, lineupIssues, HIT_COST, captainOptions, type Lineup } from './model'
import { signed } from './squad'
import { Pitch } from './components'
import { LastWeek } from './LastWeek'
import type { LinkedTeam } from './useLinkedTeam'
import { adviceStatus, planForInstruction, type AdviceStatus } from './coherence'
import { ageLabel } from './weeklyActions'
import { DecisionReview } from './DecisionReview'
import { WeeklyBrief } from './WeeklyBrief'

/**
 * The weekly view: what to actually do before this deadline.
 *
 * The plan (transfers, captain, lineup, chips) is computed by the scheduled
 * refresh and published with the forecast. Prices, injuries and your real
 * squad are fetched live, and `adviceStatus` grades any drift: the plan is
 * withheld only when following it could be wrong, and otherwise shown with
 * plain notes about what changed since it was built.
 */

function splitRiskEvidence(line: string): { message: string; evidence: string | null } {
  const match = line.match(/^(.*?)\s+\[(.+)\]$/)
  return match ? { message: match[1], evidence: match[2] } : { message: line, evidence: null }
}

export default function ThisWeek(
  { D, linked, builtSquad, openPlayer, goResults }: {
    D: Data; linked: LinkedTeam; builtSquad: Player[]; openPlayer: (id: number) => void
    goResults?: () => void
  },
) {
  const { entryId, live, err } = linked
  const lineup = linked.team?.lineup ?? null
  const bank = linked.team?.bank ?? NaN
  const weekly: Weekly | null = D.weekly ?? null

  const pool = useMemo(() => D.players.map(p => withLive(p, live)), [D.players, live])
  const poolById = useMemo(() => new Map(pool.map(p => [p.id, p])), [pool])

  // The real squad once the account is read. The plan's own squad stands in
  // only when the account cannot be read, never while it is still loading.
  const accountFailed = !!err && !linked.team
  const loadingAccount = !!entryId && !linked.team && !accountFailed
  const squadIds = linked.team?.ids
    ?? (accountFailed && weekly && String(weekly.squad.entry_id) === entryId ? weekly.squad.ids : null)
  const squad: Player[] = useMemo(() => squadIds
    ? squadIds.map(i => poolById.get(i)).filter((p): p is Player => !!p)
    : builtSquad.map(p => poolById.get(p.id) ?? p), [squadIds, builtSquad, poolById])
  const ready = squad.length === 15

  const status = adviceStatus(D, live, squad.map(p => p.id),
    linked.team ? bank : NaN, linked.team ? linked.ft : NaN, entryId)
  const gw = status.gw
  const showPlan = ready && status.planUsable && !!weekly
  const provisional = ready && !showPlan && status.projectionsUsable

  const deadlineIso = live?.deadline ?? (weekly?.gw === gw ? weekly.deadline : null)
  const nameOf = (id: number) => poolById.get(id)?.name ?? `#${id}`
  const lastRow = linked.history?.current.filter(r => r.event < gw).at(-1) ?? null

  if (!ready) {
    return <div className="week">
      <WeekHeading gw={gw} deadline={deadlineIso} status={status} />
      <section className="panel" style={{ marginTop: 16 }}>
        <div className="empty-state">
          {loadingAccount ? 'Loading your team from FPL…'
            : <>No squad yet. Link your FPL team in <strong>My squad</strong>, or draft one there.</>}
        </div>
      </section>
    </div>
  }

  return (
    <div className="week">
      <WeekHeading gw={gw} deadline={deadlineIso} status={status} />
      <StatusCallout status={status} err={err} showPlan={showPlan} provisional={provisional} />

      {showPlan && (
        <WeeklyBrief D={D} W={weekly!} poolById={poolById}
          currentLineup={lineup} openPlayer={openPlayer} />
      )}

      {provisional && (
        <ProvisionalLineup D={D} squad={squad} gw={gw} lineup={lineup} openPlayer={openPlayer} />
      )}

      {lastRow && <LastResult row={lastRow} prev={linked.history?.current.find(r => r.event === lastRow.event - 1) ?? null}
        onOpen={goResults} />}

      {weekly && (showPlan || status.projectionsUsable) && (
        <details className="weekly-more">
          <summary>{showPlan ? 'Why this plan?' : `Last plan (Gameweek ${weekly.gw})`}</summary>
          <div className="weekly-more-body"><PlanDetails D={D} W={weekly} gw={weekly.gw} horizon={weekly.horizon}
            poolById={poolById} nameOf={nameOf} openPlayer={openPlayer} /></div>
        </details>
      )}
      {status.projectionsUsable && <details className="weekly-more">
        <summary>Keep or sell a player?</summary>
        <div className="weekly-more-body"><DecisionReview D={D} gw={gw} ids={squad.map(p => p.id)} current={showPlan} openPlayer={openPlayer} /></div>
      </details>}
      <details className="weekly-more">
        <summary>Club news</summary>
        <div className="weekly-more-body"><NewsStatus D={D} gw={gw} squadIds={squad.map(p => p.id)} openPlayer={openPlayer} /></div>
      </details>
    </div>
  )
}

const fmtDeadline = (iso: string) => new Date(iso).toLocaleString('en-GB', {
  weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
})

function WeekHeading({ gw, deadline, status }: { gw: number; deadline: string | null; status: AdviceStatus }) {
  const tone = status.level === 'blocked' ? 'bad' : status.level === 'warn' ? 'warn' : 'good'
  return <div className="week-heading">
    <p className="week-deadline">
      <strong>Gameweek {gw}</strong>{deadline && <> · deadline {fmtDeadline(deadline)}</>}
    </p>
    <span className={`fresh-chip fresh-${tone}`} title="When the forecast and plan were last rebuilt">
      {status.level === 'blocked' ? 'Plan not ready' : status.level === 'warn' ? 'Plan · check notes' : 'Plan ready'}
      {' · '}{ageLabel(status.ageHours)}
    </span>
  </div>
}

function StatusCallout({ status, err, showPlan, provisional }: {
  status: AdviceStatus; err: string | null; showPlan: boolean; provisional: boolean
}) {
  if (status.level === 'ready' && !err) return null
  if (!showPlan) {
    // Notes about the withheld plan's drift would only add noise; keep the
    // other blockers and a stalled-pipeline warning.
    const notes = [...status.blockers.slice(1), ...status.warnings.filter(w => /days old/.test(w)),
      ...(err ? [`FPL account check: ${err}`] : [])]
    return <section className="callout callout-bad" role="status">
      <h2>{status.blockers[0] ?? 'Your plan is not ready.'}</h2>
      <p>{provisional ? 'Showing a provisional lineup from the latest projections. ' : ''}
        Transfer advice returns when the plan rebuilds: automatically each morning, after each deadline, and 24h and 2h before the next.</p>
      {notes.length > 0 && <ul>{notes.map(line => <li key={line}>{line}</li>)}</ul>}
    </section>
  }
  const notes = [...status.warnings, ...(err ? [`FPL account check: ${err}`] : [])]
  return <section className="callout callout-warn" role="status">
    <h2>Check before you act</h2>
    <ul>{notes.map(line => <li key={line}>{line}</li>)}</ul>
  </section>
}

function ProvisionalLineup({ D, squad, gw, lineup, openPlayer }: {
  D: Data; squad: Player[]; gw: number; lineup: Lineup | null; openPlayer: (id: number) => void
}) {
  const { xi, bench } = xiForGw(squad, gw)
  const pairs = captainOptions(xi, gw)
  const captain = pairs[0]?.captain
  const vice = pairs[0]?.vice
  if (!captain) return null
  const issues = lineup ? lineupIssues(lineup, squad, xi, bench, gw) : null
  const expected = xi.reduce((sum, p) => sum + thisGw(p, gw), 0) + thisGw(captain, gw)
  return <section className="panel brief-lineup" style={{ marginTop: 12 }}>
    <div className="panel-hd"><h2>Provisional lineup · Gameweek {gw}</h2>
      <span className="sub">{expected.toFixed(0)} pts expected</span></div>
    <p className="step-detail" style={{ padding: '0 14px' }}>
      Captain <button className="plink" onClick={() => openPlayer(captain.id)}>{captain.name}</button>
      {' '}({thisGw(captain, gw).toFixed(1)}){vice && <>, vice <button className="plink" onClick={() => openPlayer(vice.id)}>{vice.name}</button></>}.
      {' '}From your current squad, before any transfers.
    </p>
    <Pitch D={D} xi={xi} bench={bench} captain={captain.id} vice={vice?.id ?? null} simple openPlayer={openPlayer} />
    {issues && issues.length > 0 && <ul className="problems soft" style={{ margin: 14 }}>
      {issues.map((it, i) => <li key={i}><strong>{it.head}</strong> {it.body}</li>)}
    </ul>}
  </section>
}

function LastResult({ row, prev, onOpen }: {
  row: { event: number; points: number; overall_rank: number | null; event_transfers_cost: number }
  prev: { overall_rank: number | null } | null; onOpen?: () => void
}) {
  const move = row.overall_rank != null && prev?.overall_rank != null ? prev.overall_rank - row.overall_rank : null
  return <section className="last-result">
    <span className="k">Gameweek {row.event}</span>
    <span className="v mono">{row.points - row.event_transfers_cost} pts</span>
    {row.overall_rank != null && <span className="s">rank {row.overall_rank.toLocaleString('en-GB')}
      {move != null && move !== 0 && <span className={move > 0 ? 'up' : 'down'}> {move > 0 ? '▲' : '▼'} {Math.abs(move).toLocaleString('en-GB')}</span>}</span>}
    {onOpen && <button className="plink" onClick={onOpen}>Results →</button>}
  </section>
}

function NewsStatus({ D, gw, squadIds, openPlayer }: {
  D: Data; gw: number; squadIds: number[]; openPlayer: (id: number) => void
}) {
  const news = D.news
  if (!news?.run || !news.health) {
    return (
      <details className="panel news-health news-health-red" style={{ marginTop: 16 }}>
        <summary>Club news scan · none in this build</summary>
        <div className="empty-state">
          No completed public-source scan is bundled into this build. Treat the FPL injury flags above as the only live news.
        </div>
      </details>
    )
  }
  if (news.run.gw !== gw) return <details className="panel news-health" style={{ marginTop: 16 }}>
    <summary>Club news archive · GW{news.run.gw} · awaiting a GW{gw} scan</summary>
    <div className="news-health-body"><p>This club-news scan belongs to an earlier deadline. Current scouting coverage is shown in the evidence review above.</p></div>
  </details>
  const squad = new Set(squadIds)
  const allClaims = news.evidence?.claims ?? []
  const owned = allClaims.filter(claim => squad.has(claim.player_id))
  const applied = owned.filter(claim => claim.decision === 'applied')
  const candidates = owned.filter(claim => claim.decision === 'candidate')
  const checked = new Date(news.run.checked_at)
  const checkedText = isNaN(checked.getTime()) ? news.run.checked_at
    : checked.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  const tone = news.health.status
  // Plumbing stays folded unless it has something to say about your 15 (or
  // the official feed was down, which changes what the numbers above mean).
  const matters = owned.length > 0 || news.health.official_fpl_ok === false || tone === 'red'
  const headline = owned.length > 0
    ? `${owned.length} claim${owned.length === 1 ? '' : 's'} about your 15`
    : 'nothing new about your 15'
  return (
    <details className={`panel news-health news-health-${tone}`} style={{ marginTop: 16 }} open={matters}>
      <summary>
        Club news scan · {headline} · {news.health.healthy}/{news.health.enabled} sources · {checkedText}
      </summary>
      <div className="news-health-body">
        <p>
          {news.health.official_fpl_ok === false
            ? 'Official FPL data was unavailable, so the model rebuild was stopped and the last safe inputs were retained.'
            : tone === 'green'
            ? 'Official club pages were healthy in the last published scan. Only explicit, recent absences for the upcoming fixture can change the model automatically.'
            : 'Some club sources failed or supplied no articles. Existing evidence is retained only while its publication date remains valid; missing coverage does not mean a player is available.'}
        </p>
        {applied.map(claim => <NewsClaimRow key={claim.id} claim={claim} applied openPlayer={openPlayer} />)}
        {candidates.map(claim => <NewsClaimRow key={claim.id} claim={claim} openPlayer={openPlayer} />)}
        {owned.length === 0 && <p className="news-none">No new club-news claims matched anyone in your 15.</p>}
        <details className="sync-details">
          <summary>Source health and audit trail</summary>
          <p>{Math.round(news.health.coverage * 100)}% of club sources supplied article pages at the latest check. This measures collection coverage; it does not guarantee recent news about every player.</p>
          <ul>
            {news.health.sources.filter(source => source.status !== 'ok').map(source => (
              <li key={source.id}><strong>{source.club}</strong>: {source.status === 'no_articles'
                ? 'The news page loaded, but no article links were found.'
                : source.error || source.status.replaceAll('_', ' ')}</li>
            ))}
          </ul>
        </details>
      </div>
    </details>
  )
}

function NewsClaimRow({ claim, applied = false, openPlayer }: {
  claim: NewsClaim; applied?: boolean; openPlayer: (id: number) => void
}) {
  return (
    <div className={`news-claim ${applied ? 'news-applied' : 'news-candidate'}`}>
      <p>
        <strong>{applied ? 'Approved model input' : 'Review only'}:</strong>{' '}
        <button className="plink" onClick={() => openPlayer(claim.player_id)}>{claim.player}</button>
        {' '}· {claim.claim_type.replaceAll('_', ' ')}
      </p>
      <p>{claim.excerpt}</p>
      <a href={claim.url} target="_blank" rel="noreferrer">{claim.publisher} source ↗</a>
    </div>
  )
}

/* ------------------------------------------------------------ plan details
   The CI-computed analysis behind the weekly plan: how the transfer decision
   was reached, the captain comparison, alternatives, future path and last
   week's review. The instruction itself lives in WeeklyBrief. */
function PlanDetails({
  D, W, gw, horizon, poolById, nameOf, openPlayer,
}: {
  D: Data
  W: Weekly
  gw: number
  horizon: number
  poolById: Map<number, Player>
  nameOf: (id: number) => string
  openPlayer: (id: number) => void
}) {
  const m = W.model
  const cap = poolById.get(m.captain)
  const vice = poolById.get(m.vice)
  const price = (id: number) => poolById.get(id)?.price ?? 0
  const teamOf = (id: number) => poolById.get(id)?.team ?? ''

  const tr = W.transfers
  const plan = W.plan ?? null
  const sim = plan?.this_week_sim ?? null
  const moveCount = plan?.n_now ?? 0
  const moveBar = plan?.move_bar ?? moveCount * 2
  const holdRisk = W.price?.hold_risk ?? null
  const decisionInstruction = W.decision?.instruction ?? tr.advice
  const noTransfer = W.decision?.kind === 'hold'
    || (!W.decision && !plan?.worth_it
      && /nothing compelling|\bhold\b|nothing to change|close enough|no single transfer improves/i.test(tr.advice))
  const pathWeeks = planForInstruction(plan, noTransfer)
  const pathHits = pathWeeks.reduce((sum, week) => sum + week.hits, 0)
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
        {W.squad.source.includes('user-confirmed') ? 'public picks + your confirmed transfers' : 'latest public FPL picks'} · analysed {stampStr}
        {W.squad.ft > 0 && ` · ${W.squad.ft >= 15 ? 'unlimited' : W.squad.ft} free transfer${W.squad.ft === 1 ? '' : 's'}`}
        {' '}· £{W.squad.bank.toFixed(1)}m banked
      </p>

      <section className="panel decision" style={{ marginTop: 10 }}>
        <div className="panel-hd">
          <h2>How the transfer decision was made</h2>
        </div>
        <div className="decision-body">
          <p>{decisionInstruction}</p>
          {W.squad.account_basis && <p className="hint">{W.squad.account_basis}</p>}
          {!!plan?.candidates?.length && (
            <details className="sync-details">
              <summary>Transfers compared with holding</summary>
              <p className="hint">Each option includes the future transfers and hit costs it leads to over GW{gw}–{horizon}. A move must beat saving by the bar shown.</p>
              <div className="tbl-scroll"><table>
                <thead><tr><th className="l">Out → In</th><th>Gain vs saving</th><th>Bar</th><th>Worth it?</th></tr></thead>
                <tbody>{plan.candidates.filter(row => row.status === 'scored').map((row, index) => (
                  <tr key={index}>
                    <td className="l">{names(row.out ?? [])} → {names(row.in_ ?? [])}</td>
                    <td>{signed(row.gain ?? 0)}</td><td>{(row.move_bar ?? 0).toFixed(1)}</td>
                    <td>{row.qualifies ? 'Yes' : 'No'}</td>
                  </tr>
                ))}</tbody>
              </table></div>
              {plan.candidates.some(row => row.status !== 'scored') && <p className="hint">Some candidate plans did not return a feasible result.</p>}
            </details>
          )}
          {sim && (
            <p className="decision-evidence">
              In {sim.n_sims.toLocaleString('en-GB')} simulations, the proposed moves
              beat holding <strong>{Math.round(sim.p_b_wins * 100)}% of the time</strong>
              {' '}and gained <strong className="mono">{signed(sim.mean_delta)} pts</strong>
              {' '}on average this gameweek, after transfer hits. {noTransfer && moveCount > 0 ? (
                <>Over the full planning window, acting now instead of waiting gains
                  {' '}<strong className="mono">{signed(plan?.diff ?? 0)}</strong>, short of the
                  {' '}<strong className="mono">+{moveBar.toFixed(1)}</strong> bar for {moveCount} move{moveCount === 1 ? '' : 's'}.</>
              ) : null}
            </p>
          )}
          {(W.squad.changes?.length ?? 0) > 0 && (
            <details className="sync-details">
              <summary>{W.squad.source.includes('user-confirmed') ? 'Transfers you confirmed' : 'What changed in the latest FPL sync'}</summary>
              <ul>{W.squad.changes!.map(change => <li key={change}>{change}</li>)}</ul>
            </details>
          )}
        </div>
      </section>

      <div className="week-grid">
      <div className="wcol">
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
        </div>
        <div className="cap-ctx">
          <div className="cap-table-scroll">
          <table className="cap-table">
            <thead>
              <tr>
                <th className="l">option</th><th className="l">fixture</th><th>team xG</th>
                <th>starts</th><th>owned</th><th>pts</th><th title="Expected extra points with the best vice, assuming independent appearances">C + fallback</th>
              </tr>
            </thead>
            <tbody>
              {m.ranked.slice(0, 4).map((r, i) => {
                const p = poolById.get(r.id)
                const fx = p ? (D.ticker?.[p.team]?.find(t => t.gw === gw)?.fx ?? []) : []
                const start = p ? (p.start_by_gw?.[gw - 1] ?? p.start_rate) : null
                return (
                  <tr key={r.id} className={i === 0 ? 'pick' : undefined}>
                    <td className="l">
                      <span className="n">{i === 0 ? 'C' : r.id === m.vice ? 'V' : `#${i + 1}`}</span>{' '}
                      <button className="plink" onClick={() => openPlayer(r.id)}>{nameOf(r.id)}</button>
                    </td>
                    <td className="l mono">
                      {fx.length === 0 ? '—' : fx.map((f, j) => (
                        <span key={j}>{j > 0 && ' + '}{f.home ? '' : '@'}{f.opp}</span>
                      ))}
                    </td>
                    <td className="mono">{fx.length === 0 ? '—' : fx.map(f => f.xg.toFixed(1)).join('+')}</td>
                    <td className="mono">{start != null ? `${Math.round(start * 100)}%` : '—'}</td>
                    <td className="mono">{p ? `${p.sel_pct.toFixed(0)}%` : '—'}</td>
                    <td className="mono strong">{r.pts.toFixed(1)}</td>
                    <td className="mono">{r.bonus?.toFixed(1) ?? '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>
          {m.ranked.length > 1 && (() => {
            const hasFallback = m.ranked[0].bonus != null && m.ranked[1].bonus != null
            const edge = (m.ranked[0].bonus ?? m.ranked[0].pts) - (m.ranked[1].bonus ?? m.ranked[1].pts)
            const basis = hasFallback ? 'including vice fallback' : 'before vice fallback'
            const a = poolById.get(m.ranked[0].id), b = poolById.get(m.ranked[1].id)
            const template = a && b && b.sel_pct > a.sel_pct + 15 ? b : null
            return (
              <p className="cap-edge">
                {edge >= 1.0 ? (
                  <><strong>{nameOf(m.ranked[0].id)}</strong> leads by {edge.toFixed(1)} in projected captain bonus, {basis}.</>
                ) : (
                  <>The captain bonus edge is <strong className="mono">{edge.toFixed(1)}</strong> {basis} — inside the noise of a single match.
                    {template
                      ? <> {template.name} is owned by {template.sel_pct.toFixed(0)}%: captaining him protects your rank if he hauls, {nameOf(m.ranked[0].id)} is the points play. Both are defensible.</>
                      : ' Either is defensible; the model leans ' + nameOf(m.ranked[0].id) + '.'}
                  </>
                )}
                {hasFallback && ' Fallback assumes the two players’ appearances are independent.'}
              </p>
            )
          })()}
        </div>
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Transfers</h2>
          <span className="sub">GW{gw}–{horizon} window</span>
        </div>
        {holdRisk && holdRisk.lines.length > 0 && (
          <details className="scenario-details price-risk-details">
            <summary>Price timing if you wait · experimental</summary>
            <div className="scenario-body">
              <ul className="price-risk-list">
                {holdRisk.lines.map((line, i) => {
                  const { message, evidence } = splitRiskEvidence(line)
                  return (
                    <li key={`${i}-${message}`}>
                      <span>{message}</span>
                      {evidence && <span className="price-risk-evidence mono">{evidence}</span>}
                    </li>
                  )
                })}
              </ul>
              <p className="hint">
                Based on {holdRisk.snapshots} price snapshots. This does not change the
                transfer advice until enough actual rises and falls exist to calibrate it.
              </p>
            </div>
          </details>
        )}
        {tr.singles.length === 0 && tr.pairs.length === 0 ? (
          <div className="empty-state">
            Nothing improves this squad over the remaining gameweeks. Bank it.
          </div>
        ) : (
          <details className="scenario-details">
            <summary>Other transfers considered</summary>
            <div className="scenario-body">
            {tr.singles.length > 0 && (
              <div className="tbl-scroll">
                <table>
                  <thead>
                    <tr>
                      <th className="l">Out</th><th className="l">In</th>
                      <th>£</th><th>On pitch</th><th>Gain</th><th>Net of hits</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tr.singles.map((s, i) => (
                      <tr key={i}>
                        <td className="l">{names([s.out])} <span className="s">{teamOf(s.out)}</span></td>
                        <td className="l">{names([s.in_])} <span className="s">{teamOf(s.in_)}</span></td>
                        <td>{money([s.out], [s.in_])}</td>
                        <td className="mono">{s.xi_gain == null ? '—' : signed(s.xi_gain)}</td>
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
                        <th>£</th><th>On pitch</th><th>Gain</th><th>Net of hits</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tr.pairs.map((s, i) => (
                        <tr key={i}>
                          <td className="l">{names(s.out)}</td>
                          <td className="l">{names(s.in_)}</td>
                          <td>{money(s.out, s.in_)}</td>
                          <td className="mono">{s.xi_gain == null ? '—' : signed(s.xi_gain)}</td>
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
              On pitch is the lift to your expected XI and captain over GW{gw}–{horizon};
              gain adds auto-sub cover when a starter does not play at all; net takes off {HIT_COST} per move
              beyond your free transfers. The decision above also compares future transfer paths.
            </p>
            </div>
          </details>
        )}
        {plan && pathWeeks.length > 0 && (
          <details className="scenario-details">
            <summary>Planned path for the coming weeks{pathHits > 0 ? ` (${pathHits * HIT_COST} points in transfer costs)` : ''}</summary>
            <div className="scenario-body tbl-scroll">
              <table>
              <thead>
                <tr>
                  <th className="l">GW</th><th>Pts</th><th className="l">Captain</th>
                  <th>FT available</th><th className="l">Moves</th>
                </tr>
              </thead>
              <tbody>
                {pathWeeks.map(w => (
                  <tr key={w.gw}>
                    <td className="l mono">GW{w.gw}{w.hits > 0 && <span className="badge out">−{w.hits * HIT_COST}</span>}</td>
                    <td style={{ color: 'var(--flood-soft)' }}>{w.pts.toFixed(1)}</td>
                    <td className="l">
                      <button className="plink" onClick={() => openPlayer(w.captain)}>{nameOf(w.captain)}</button>
                    </td>
                    <td>{w.ft >= 15 ? '∞' : w.ft}{!!w.ft_lost && <span className="badge out">new FT lost</span>}</td>
                    <td className="l moves">
                      {w.in_.length === 0 ? <span className="s">hold</span> : (
                        <>{names(w.in_)} <span className="s">for</span> {names(w.out)}</>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              </table>
              <p className="hint" style={{ padding: '10px 14px 14px' }}>
                Transfers are available before each deadline and assume every earlier
                move in this path happens. Future weeks are conditional and are recalculated
                each refresh. Holding this week is not a commitment to keep holding.
              </p>
            </div>
          </details>
        )}
      </section>
      </div>
      <div className="wcol">
      {W.retro && W.retro.table.length > 0 && (
        <LastWeek retro={W.retro} poolById={poolById} openPlayer={openPlayer} />
      )}

      </div>
      </div>




    </>
  )
}
