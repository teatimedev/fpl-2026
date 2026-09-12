import { useMemo } from 'react'
import type { Data, NewsClaim, Player, Pos, Weekly } from './types'
import { withLive, priceMovers } from './weekly'
import {
  xiForGw, thisGw, lineupIssues, HIT_COST, captainOptions,
  type TransferOption,
} from './model'
import { signed } from './squad'
import { Pitch } from './components'
import { LastWeek } from './LastWeek'
import type { LinkedTeam } from './useLinkedTeam'
import { recommendationState, planForInstruction, canReadSavedReview } from './coherence'
import { DecisionReview } from './DecisionReview'
import { WeeklyBrief } from './WeeklyBrief'

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

function splitRiskEvidence(line: string): { message: string; evidence: string | null } {
  const match = line.match(/^(.*?)\s+\[(.+)\]$/)
  return match ? { message: match[1], evidence: match[2] } : { message: line, evidence: null }
}

export default function ThisWeek(
  { D, linked, builtSquad, openPlayer }: {
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
  const ft = linked.ft
  const state = recommendationState(D, live, squad.map(p => p.id), bank, ft, entryId)
  const digest = ready && state.digestReady
  const savedReview = !busy && !live && !!err && !digest && canReadSavedReview(D, entryId)

  const { xi, bench } = ready ? xiForGw(squad, gw) : { xi: [], bench: [] }
  const pairs = captainOptions(xi, gw)
  const captain = pairs[0]?.captain
  const vice = pairs[0]?.vice
  const ranked = [vice, ...pairs.map(row => row.captain).filter(p => p.id !== captain?.id && p.id !== vice?.id)]
    .filter((p): p is Player => !!p)
  const flagged = squad.filter(p => p.status !== 'a')
  // Only meaningful for a real team: the lineup you have set, against the model's.
  const issues = ready && usingReal && lineup
    ? lineupIssues(lineup, squad, xi, bench, gw) : null
  // A client price merge cannot reconstruct purchase lots or a new forecast.
  const options: TransferOption[] = []
  const movers = priceMovers(live, pool)

  const dl = live ? new Date(live.deadline) : new Date(D.meta.deadline)
  const msLeft = dl.getTime() - Date.now()
  const days = Math.floor(msLeft / 86400000)
  const hours = Math.floor(msLeft / 3600000) % 24

  // Stale-model guard: projections are baked at deploy time by the scheduled
  // refresh; live prices/injuries keep moving between deploys, so if the
  // pipeline stops landing this must be visible, not silent.
  // 72h, not less: legitimate cadence gaps exceed two days whenever the
  // next deadline sits more than a day past the guaranteed Thursday build
  // (midweek gameweeks, international breaks). Alerting earlier would cry
  // wolf weekly; a truly stalled pipeline grows this number without bound.
  const generatedAt = D.meta.generated
    ? new Date(D.meta.generated.replace(' ', 'T').replace(/ UTC$/, 'Z'))
    : null
  const dataAgeH = generatedAt && !isNaN(generatedAt.getTime())
    ? Math.floor((Date.now() - generatedAt.getTime()) / 3600000)
    : null
  const staleData = dataAgeH != null && dataAgeH > 72

  const nameOf = (id: number) => poolById.get(id)?.name ?? `#${id}`

  return (
    <div className="week">
      {staleData && (
        <p
          role="alert"
          /* Same treatment as .drawer-news: alert colour on a soft red strip. */
          style={{
            margin: '0 0 12px', padding: '7px 10px', fontSize: 12,
            color: 'var(--alert)', background: 'rgba(255, 90, 90, 0.09)',
            borderLeft: '2px solid var(--alert)', lineHeight: 1.5,
            borderRadius: '0 var(--r) var(--r) 0',
          }}
        >
          This analysis is over three days old. It needs updating before you act on it.
        </p>
      )}
      <p className="week-deadline">
        Gameweek {gw} · deadline {dl.toLocaleString('en-GB', {
          weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
        })}{msLeft > 0 && <> · <strong>{days}d {hours}h</strong> to go</>}
        {!entryId && builtSquad.length === 15 && <> · drafted squad</>}
      </p>

      {busy && !digest && <p className="week-refresh" role="status">Checking your team and the latest player news…</p>}

      {!ready && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="empty-state">
            No squad yet. Draft one in the <strong>My squad</strong> tab, or link an
            FPL team id above once the season has started.
          </div>
        </section>
      )}

      {ready && !digest && !busy && (
        <section className="panel" role="status" style={{ marginTop: 12 }}>
          <div className="panel-hd"><h2>{err ? 'Live FPL checks are unavailable' : 'Your weekly advice needs updating'}</h2></div>
          <p className="week-refresh">{err
            ? 'The live feed could not be checked. Verify your current squad, prices and team news in FPL before making changes.'
            : 'Your team or the latest information no longer matches this analysis. Fresh advice is needed before making transfers.'}</p>
          <details className="scenario-details"><summary>What needs updating?</summary>
            <ul className="problems soft" style={{ margin: 14 }}>
              {(live ? state.reasons : state.reasons.slice(0, 1)).map(reason => <li key={reason}>{reason}</li>)}
            </ul>
          </details>
        </section>
      )}

      {savedReview && weekly && (
        <details className="weekly-more" open>
          <summary>Saved GW{weekly.gw} review · live checks unavailable</summary>
          <div className="weekly-more-body">
            <p>This is the saved report for entry {weekly.squad.entry_id}, analysed {new Date(weekly.generated).toLocaleString('en-GB')}.</p>
            <p>At that analysis: {weekly.squad.ft} free transfers and £{weekly.squad.bank.toFixed(1)}m in the bank. {weekly.squad.account_basis}</p>
            <p>Squad recorded: {weekly.squad.ids.map(nameOf).join(', ')}.</p>
            <Digest D={D} W={weekly} gw={weekly.gw} horizon={weekly.horizon} poolById={poolById}
              nameOf={nameOf} openPlayer={openPlayer} />
          </div>
        </details>
      )}

      {ready && digest && weekly && (
        <>
          <WeeklyBrief D={D} W={weekly} poolById={poolById} currentLineup={lineup} openPlayer={openPlayer} />
          <details className="weekly-more">
            <summary>Why this recommendation?</summary>
            <div className="weekly-more-body"><Digest D={D} W={weekly} gw={gw} horizon={horizon} poolById={poolById}
              nameOf={nameOf} openPlayer={openPlayer} /></div>
          </details>
        </>
      )}

      {ready && !digest && state.projectionsReady && captain && (
        <details className="weekly-more">
          <summary>See the last available lineup and estimates</summary>
          <div className="weekly-more-body">
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
                {ranked.slice(0, 3).map((p, i) => (
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
              <h2>Your recommended lineup</h2>
              <span className="sub">
                {['DEF', 'MID', 'FWD'].map(k =>
                  xi.filter(p => p.pos === k).length).join('-')}
              </span>
            </div>
            <Pitch D={D} xi={xi} bench={bench} captain={captain.id}
              vice={vice?.id ?? null} openPlayer={openPlayer} />
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
                Transfer analysis needs a refresh for this account and current prices.
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
          </div>
        </details>
      )}

      {ready && <details className="weekly-more">
        <summary>Review a player: keep or sell?</summary>
        <div className="weekly-more-body"><DecisionReview D={D} gw={gw} ids={squad.map(p => p.id)} current={digest} openPlayer={openPlayer} /></div>
      </details>}
      <details className="weekly-more">
        <summary>Club news and sources</summary>
        <div className="weekly-more-body"><NewsStatus D={D} gw={gw} squadIds={squad.map(p => p.id)} openPlayer={openPlayer} /></div>
      </details>
    </div>
  )
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

/* ---------------------------------------------------------------- digest
   The CI-computed analysis, rendered when the loaded squad is the one it saw. */
function Digest({
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
  const posOf = (id: number): Pos | undefined => poolById.get(id)?.pos
  const teamOf = (id: number) => poolById.get(id)?.team ?? ''
  const shape = (['DEF', 'MID', 'FWD'] as Pos[])
    .map(k => m.xi.filter(id => posOf(id) === k).length).join('-')
  const xiPlayers = m.xi.map(id => poolById.get(id)).filter((p): p is Player => !!p)
  const benchPlayers = m.bench.map(id => poolById.get(id)).filter((p): p is Player => !!p)

  const checks = W.checks ?? []
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
  const capGameweek = gw + Math.max(0, 5 - W.squad.ft)
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
        {checks.length === 0 && ' · nobody flagged'}
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
              <p className="hint">Each option includes future transfers and hit costs over GW{gw}–{horizon}. The two-point buffer per move is an unvalidated policy rule.</p>
              <div className="tbl-scroll"><table>
                <thead><tr><th className="l">Out → In</th><th>Gain vs hold</th><th>Buffer</th><th>Clears it?</th></tr></thead>
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
                  {' '}<strong className="mono">{signed(plan?.diff ?? 0)}</strong>.
                  The legacy policy requires <strong className="mono">+{moveBar.toFixed(1)}</strong> for {moveCount} moves.
                  This threshold has not been validated; it is shown as an assumption.</>
              ) : (
                <>This measures match-outcome uncertainty under fixed forecasts; it does not prove a transfer is the right decision.</>
              )}
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

      {checks.length > 0 && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-hd">
            <h2>Check before the deadline</h2>
            <span className="sub">{`${checks.length} to watch`}</span>
          </div>
          <ul className="problems soft" style={{ margin: 14 }}>
            {checks.map(c => (
              <li key={c.id}>
                <button className="plink strong" onClick={() => openPlayer(c.id)}>{nameOf(c.id)}</button>
                <span className="s"> {c.xi ? 'XI' : 'bench'}</span> — {c.flags.join('; ')}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Transfers</h2>
          <span className="sub">GW{gw}–{horizon} window</span>
        </div>
        {decisionInstruction && <p className="lede-sm">{decisionInstruction}</p>}
        {W.squad.ft < 15 && <p style={{ padding: '0 14px' }}>
          You have <strong>{W.squad.ft} free transfers for GW{gw}</strong>.
          {W.squad.ft < 5
            ? <> Rolling every week would reach the five-transfer cap at GW{capGameweek}.</>
            : <> Your transfer bank is full.</>}
          {' '}At five, using one transfer leaves five available the following week;
          making none forfeits that week’s new transfer. Reassess each deadline.
        </p>}
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
            <summary>Compare optional alternatives (not instructions)</summary>
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
            <summary>Explore future weeks · plans can change{pathHits > 0 ? ` (${pathHits * HIT_COST} points in transfer costs)` : ''}</summary>
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

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>Your recommended lineup</h2>
          <span className="sub">{shape}</span>
        </div>
        <Pitch D={D} xi={xiPlayers} bench={benchPlayers} captain={m.captain}
          vice={m.vice} openPlayer={openPlayer} />
      </section>
      </div>
      </div>




    </>
  )
}
