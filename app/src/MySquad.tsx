import { useEffect, useMemo, useRef, useState } from 'react'
import type { Data, Player, WeeklyLineup } from './types'
import { POS_ORDER, MAX_PER_CLUB } from './types'
import { round1, signed, type SquadState } from './squad'
import { Pitch, ContextPanels, LinkTeamForm, type ShirtMarks } from './components'
import { withLive, type LiveState } from './weekly'
import {
  xiForGw, thisGw, remaining, applyMoves, isLegal, sandboxGain, rankTransfers,
  lineupIssues, lineupDiff, HIT_COST, type Lineup, type Move, type TransferOption,
} from './model'
import MarketTable from './MarketTable'
import SquadBuilder from './SquadBuilder'
import type { LinkedTeam } from './useLinkedTeam'

/**
 * My squad: the squad you actually have, with the model's opinion overlaid.
 *
 * The squad comes from the linked FPL team when its picks are public, else
 * from the one the weekly refresh analysed. The pitch shows YOUR lineup with
 * the model's disagreements ringed; the sandbox lets you try up to three
 * transfers and see what they are worth over the window, net of hits. Nothing
 * here persists — it is a scratchpad for the decision, not a record of it.
 *
 * Draft mode is the from-scratch builder (SquadBuilder). Its state lives in
 * App so the drawer and This Week can see the drafted squad.
 */

const MAX_MOVES = 3
const DASH = '—'

const toLineup = (l: WeeklyLineup | null | undefined): Lineup | null =>
  l ? { xi: l.xi ?? [], bench: l.bench ?? [], captain: l.captain ?? null, vice: l.vice ?? null } : null

interface Source {
  kind: 'linked' | 'digest'
  ids: number[]
  lineup: Lineup | null
  bank: number
  ft: number
}

function Tile({ k, v, s }: { k: string; v: string; s?: string }) {
  return (
    <div>
      <span className="k">{k}</span>
      <span className="v mono">{v}</span>
      {s && <span className="s">{s}</span>}
    </div>
  )
}

export default function MySquad({
  D, linked, picks, presetXI, state, draftMode, setDraftMode,
  add, remove, loadPreset, clear, openPlayer,
}: {
  D: Data
  linked: LinkedTeam
  picks: Player[]
  presetXI: Set<number> | null
  state: SquadState
  draftMode: boolean
  setDraftMode: (v: boolean) => void
  add: (p: Player) => void
  remove: (id: number) => void
  loadPreset: (i: number) => void
  clear: () => void
  openPlayer: (id: number) => void
}) {
  const gw = linked.live?.gw ?? D.weekly?.gw ?? D.meta.start_gw ?? 1
  const horizon = D.meta.horizon
  const weekly = D.weekly ?? null
  const { entryId, busy, err, summary } = linked

  const pool = useMemo(() => D.players.map(p => withLive(p, linked.live)), [D.players, linked.live])
  const poolById = useMemo(() => new Map(pool.map(p => [p.id, p])), [pool])

  // Where the squad comes from, in priority order.
  const source = useMemo<Source | null>(() => {
    if (linked.team) {
      return { kind: 'linked', ids: linked.team.ids, lineup: linked.team.lineup,
        bank: linked.team.bank, ft: linked.ft }
    }
    if (weekly && weekly.squad.ids.length === 15) {
      return { kind: 'digest', ids: weekly.squad.ids, lineup: toLineup(weekly.squad.lineup),
        bank: weekly.squad.bank, ft: weekly.squad.ft }
    }
    return null
  }, [linked.team, linked.ft, weekly])

  const squad = useMemo(() => source
    ? source.ids.map(id => poolById.get(id)).filter((p): p is Player => !!p)
    : [], [source, poolById])
  const ready = squad.length === 15
  const squadKey = squad.map(p => p.id).join(',')

  // last_deadline_value/bank are 0 until a deadline has passed, so only trust
  // the summary's money once the picks themselves are public.
  const valueShown = linked.team && summary ? summary.value : null
  const bankShown = linked.team
    ? (summary?.bank ?? linked.team.bank)
    : (source?.kind === 'digest' ? source.bank : null)

  const genStr = (() => {
    if (!weekly) return ''
    const sourceStamp = weekly.squad.confirmed_at ?? weekly.generated
    const d = new Date(sourceStamp)
    return isNaN(d.getTime()) ? sourceStamp
      : d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  })()

  return (
    <div className="mysquad-page">
      <section className="panel ms-head">
        <div className="panel-hd">
          <h2>{draftMode ? 'Draft a squad' : (summary?.name || 'My squad')}</h2>
          <span className="sub">{draftMode ? 'wildcard / free hit' : `gameweek ${gw}`}</span>
          {draftMode && (
            <button className="toggle" onClick={() => setDraftMode(false)}>← Back to my squad</button>
          )}
        </div>

        {!draftMode && (
          <>
            {summary && (
              <div className="tiles">
                <div className="statline wide">
                  <Tile k="GW points" v={String(summary.gwPoints)} />
                  <Tile k="Overall points" v={summary.overallPoints.toLocaleString('en-GB')} />
                  <Tile k="Overall rank"
                    v={summary.overallRank != null ? summary.overallRank.toLocaleString('en-GB') : DASH} />
                  <Tile k="Team value" v={valueShown != null ? `£${valueShown.toFixed(1)}m` : DASH} />
                  <Tile k="In the bank" v={bankShown != null ? `£${bankShown.toFixed(1)}m` : DASH} />
                </div>
              </div>
            )}
            <div className="week-hd" style={summary ? { paddingTop: 10 } : undefined}>
              <LinkTeamForm
                entryId={entryId} onSave={linked.setEntryId} busy={busy} err={err} inputId="entry-ms"
                hint="Find the number in the URL of your FPL points page. Picks become public after the first deadline."
                linkedLine={linked.team
                  ? <> · picks from GW{linked.team.fromGw} · £{linked.team.bank.toFixed(1)}m banked</>
                  : (!busy && !err ? ' · picks not public yet' : null)}
              />
              {source?.kind === 'digest' && (
                <p className="source-line">
                  Showing the squad confirmed from official FPL on <strong>{genStr}</strong>
                  {' '}({weekly?.squad.source})
                  {!summary && bankShown != null && <> · £{bankShown.toFixed(1)}m in the bank</>}
                  {' '}— public picks will take over automatically after the deadline.
                </p>
              )}
              <p className="draft-row">
                <button className="toggle" onClick={() => setDraftMode(true)}>
                  Draft a squad from scratch
                </button>
                <span className="s">for wildcard / free hit weeks</span>
              </p>
            </div>
          </>
        )}
      </section>

      {draftMode ? (
        <SquadBuilder D={D} picks={picks} presetXI={presetXI} state={state}
          add={add} remove={remove} loadPreset={loadPreset} clear={clear} openPlayer={openPlayer} />
      ) : ready && source ? (
        <SquadView key={squadKey} D={D} squad={squad} lineup={source.lineup}
          bank={source.bank} ft={source.ft} gw={gw} horizon={horizon}
          pool={pool} live={linked.live} openPlayer={openPlayer} />
      ) : (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="empty-state">
            {busy ? 'Loading live data…' : (
              <>
                Nothing to show yet. Link your FPL team id above once the first
                deadline has passed — until then,{' '}
                <button className="plink" onClick={() => setDraftMode(true)}>
                  draft a squad from scratch
                </button>.
              </>
            )}
          </div>
        </section>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ squad view
   Pitch, sandbox, health and context for one resolved squad. Keyed by the
   squad ids from the parent so the sandbox resets when the squad changes. */
function SquadView({
  D, squad, lineup, bank, ft, gw, horizon, pool, live, openPlayer,
}: {
  D: Data
  squad: Player[]
  lineup: Lineup | null
  bank: number
  ft: number
  gw: number
  horizon: number
  pool: Player[]
  live: LiveState | null
  openPlayer: (id: number) => void
}) {
  const [pending, setPending] = useState<Move[]>([])
  const [selling, setSelling] = useState<Player | null>(null)
  const [showAll, setShowAll] = useState(false)
  const marketRef = useRef<HTMLDivElement>(null)

  const inSandbox = pending.length > 0
  const after = useMemo(() => applyMoves(squad, pending), [squad, pending])
  const shown = inSandbox ? after : squad
  const incoming = useMemo(() => new Set(pending.map(m => m.in.id)), [pending])
  const pendingOut = useMemo(() => new Map(pending.map(m => [m.out.id, m.in])), [pending])

  // The model's XI for whatever is on the pitch.
  const model = useMemo(() => xiForGw(shown, gw), [shown, gw])
  const modelRanked = useMemo(
    () => [...model.xi].sort((a, b) => thisGw(b, gw) - thisGw(a, gw)), [model, gw])
  const modelCap = modelRanked[0]?.id ?? null
  const modelVice = modelRanked[1]?.id ?? null

  // Your lineup applies to the squad you own, not to a sandbox after-squad.
  const yours = !inSandbox ? lineup : null
  const hasXi = !!yours && yours.xi.length === 11
  const byId = useMemo(() => new Map(shown.map(p => [p.id, p])), [shown])
  const pick = (ids: number[]) => ids.map(id => byId.get(id)).filter((p): p is Player => !!p)
  const xiPlayers = hasXi && yours ? pick(yours.xi) : model.xi
  const benchPlayers = hasXi && yours ? pick(yours.bench) : model.bench
  const captain = yours ? (yours.captain ?? modelCap) : modelCap
  const vice = yours ? (yours.vice ?? modelVice) : modelVice
  const diff = yours ? lineupDiff(yours, squad, gw) : null
  const issues = yours ? lineupIssues(yours, squad, model.xi, model.bench, gw) : null
  const xiIds = useMemo(() => new Set(xiPlayers.map(p => p.id)), [xiPlayers])

  const marks = (p: Player): ShirtMarks | undefined => {
    if (inSandbox) return incoming.has(p.id) ? { swapIn: true, hint: 'in' } : undefined
    if (!diff) return undefined
    const capModel = diff.capModel === p.id && diff.capModel !== captain
    const viceModel = diff.viceModel === p.id && diff.viceModel !== vice
    const swapOut = diff.swapOut.has(p.id)
    const swapIn = diff.swapIn.has(p.id)
    if (!capModel && !viceModel && !swapOut && !swapIn) return undefined
    return {
      swapOut, swapIn, capModel, viceModel,
      hint: swapOut ? 'model benches him' : swapIn ? 'model starts him'
        : capModel ? "model's captain" : "model's vice",
    }
  }

  /* --------------------------------------------------------- sandbox */
  const budget = squad.reduce((s, p) => s + p.price, 0) + bank
  const bankAfter = round1(bank + pending.reduce((s, m) => s + m.out.price - m.in.price, 0))
  const legal = isLegal(after, budget)
  const ftLeft = Math.max(0, ft - pending.length)
  const full = pending.length >= MAX_MOVES
  const suggestions = useMemo(
    () => rankTransfers(after, pool, bankAfter, ftLeft, gw, horizon, 8)
      .filter(o => !incoming.has(o.out.id)).slice(0, 5),
    [after, pool, bankAfter, ftLeft, gw, horizon, incoming])
  const gain = useMemo(
    () => sandboxGain(squad, pending, ft, gw, horizon), [squad, pending, ft, gw, horizon])
  // gain is independent of free transfers; only net moves with them
  const singleGain = (m: Move) => sandboxGain(squad, [m], 0, gw, horizon).gain

  const trySuggestion = (o: TransferOption) => {
    if (full) return
    setPending(ps => [...ps, { out: o.out, in: o.in }])
    setSelling(null)
  }
  const removeMove = (i: number) => setPending(ps => ps.filter((_, j) => j !== i))
  const reset = () => { setPending([]); setSelling(null) }

  // Everything you own or have already brought in: never a candidate.
  const owned = useMemo(
    () => new Set([...squad.map(p => p.id), ...pending.map(m => m.in.id)]), [squad, pending])
  const candidates = useMemo(() => pool.filter(p => !owned.has(p.id)), [pool, owned])
  const maxPrice = selling ? round1(bankAfter + selling.price) : 0
  const clubsAfterSale = useMemo(() => {
    const c: Record<string, number> = {}
    for (const p of after) if (p.id !== selling?.id) c[p.team] = (c[p.team] ?? 0) + 1
    return c
  }, [after, selling])
  const blockOf = (p: Player): string | null => {
    if (owned.has(p.id)) return 'Already in your squad'
    if ((clubsAfterSale[p.team] ?? 0) >= MAX_PER_CLUB) return `You already have 3 from ${p.team}`
    if (p.price > maxPrice + 1e-9) return `£${round1(p.price - maxPrice)}m over budget`
    return null
  }
  const chooseReplacement = (p: Player) => {
    if (!selling) return
    const out = selling
    setPending(ps => [...ps, { out, in: p }])
    setSelling(null)
  }

  useEffect(() => {
    if (selling) marketRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [selling])

  /* ---------------------------------------------------------- health */
  const health = squad.map(p => {
    const e = live?.elements.get(p.id)
    const flags: { tone: 'bad' | 'warn' | 'info'; text: string }[] = []
    if (p.status !== 'a') {
      const chance = e?.chance_of_playing_next_round
      flags.push({
        tone: 'bad',
        text: (p.news || `status ${p.status}`) + (chance != null ? ` · ${chance}% chance` : ''),
      })
    }
    if (xiIds.has(p.id) && p.start_rate != null && p.start_rate < 0.8) {
      flags.push({ tone: 'warn', text: `starts ${Math.round(p.start_rate * 100)}% of games` })
    }
    const mv = D.movers?.players?.[String(p.id)]
    if (mv) {
      const bits: string[] = []
      if (Math.abs(mv.d_sel_7) >= 1) bits.push(`ownership ${signed(mv.d_sel_7)}pp 7d`)
      if (Math.abs(mv.d_price_7) >= 0.1) bits.push(`price ${signed(mv.d_price_7)} 7d`)
      if (bits.length) flags.push({ tone: 'info', text: bits.join(', ') })
    }
    return { p, flags }
  }).filter(h => h.flags.length > 0)

  const shape = ['DEF', 'MID', 'FWD'].map(k => xiPlayers.filter(p => p.pos === k).length).join('-')

  return (
    <div className="mysquad">
      <div className="ms-col">
        {/* -------------------------------------------------- pitch */}
        <section className="panel ms-pitch">
          <div className="panel-hd">
            <h2>{inSandbox ? 'After your moves' : 'Your lineup'}</h2>
            <span className="sub">
              {shape}{hasXi && !inSandbox ? ' · as set' : " · model's XI"}
            </span>
          </div>
          <Pitch D={D} xi={xiPlayers} bench={benchPlayers} captain={captain} vice={vice}
            marks={marks} openPlayer={openPlayer} />
          <div className="diff-line">
            {inSandbox ? (
              <p className="hint" style={{ margin: 0 }}>
                The model's XI, captain and bench order for the squad after{' '}
                {pending.length} pending move{pending.length === 1 ? '' : 's'}; incoming
                players are ringed green.
              </p>
            ) : !yours ? (
              <p className="hint" style={{ margin: 0 }}>
                No lineup on record for this squad — showing the model's XI, captain
                and bench order.
              </p>
            ) : issues && issues.length === 0 ? (
              <div className="ready">
                Your captain, vice, XI and bench order all match the model. ✓
              </div>
            ) : issues && (
              <>
                <p className="hint" style={{ margin: 0 }}>
                  <strong>{issues.length} difference{issues.length === 1 ? '' : 's'}</strong> from
                  the model{issues.length > 1 ? ' — first' : ''}:{' '}
                  <strong>{issues[0].head}</strong> {issues[0].body}
                  {issues.length > 1 && (
                    <button className="linkbtn" onClick={() => setShowAll(v => !v)}>
                      {showAll ? 'hide' : 'see all'}
                    </button>
                  )}
                </p>
                {showAll && (
                  <ul className="problems">
                    {issues.map((it, i) => (
                      <li key={i}><strong>{it.head}</strong> {it.body}</li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </section>

        {/* ------------------------------------------------ sandbox */}
        <div className="ms-sandbox">
          <section className="panel">
            <div className="panel-hd">
              <h2>Transfer sandbox</h2>
              <span className="sub">nothing here is saved</span>
            </div>
            <p className="lede-sm">
              <strong>{ft >= 15 ? 'Unlimited' : ft} free transfer{ft === 1 ? '' : 's'}</strong>
              {' '}and <strong>£{bank.toFixed(1)}m</strong> in the bank
              {inSandbox && <> · <strong>£{bankAfter.toFixed(1)}m</strong> after your moves</>}.
              {' '}Sell a player to find a replacement.
            </p>

            {suggestions.length > 0 && (
              <>
                <p className="subhead">Model's best single moves{inSandbox ? ' from here' : ''}</p>
                <ul className="sugg">
                  {suggestions.map(o => (
                    <li key={o.out.id}>
                      <span className="mv">
                        <button className="plink" onClick={() => openPlayer(o.out.id)}>{o.out.name}</button>
                        {' → '}
                        <button className="plink" onClick={() => openPlayer(o.in.id)}>{o.in.name}</button>
                      </span>
                      <span className="g mono">
                        £{signed(o.costChange)} · <span style={{ color: 'var(--flood-soft)' }}>+{o.gain.toFixed(1)}</span>
                        {o.net < o.gain - 0.05 && <span className="s"> net {signed(o.net)}</span>}
                      </span>
                      <button className="toggle" onClick={() => trySuggestion(o)} disabled={full}>try</button>
                    </li>
                  ))}
                </ul>
              </>
            )}

            <p className="subhead">Your 15</p>
            <ul className="sq-list">
              {POS_ORDER.flatMap(pos => squad.filter(p => p.pos === pos)).map(p => {
                const inn = pendingOut.get(p.id)
                const active = selling?.id === p.id
                return (
                  <li key={p.id} className={`sq-row${inn ? ' pending' : ''}${active ? ' active' : ''}`}>
                    <span className="bar" style={{ background: D.teams[p.team]?.primary }} />
                    <button className="plink n" onClick={() => openPlayer(p.id)}>{p.name}</button>
                    <span className="s">{p.pos} · {p.team} · £{p.price.toFixed(1)}</span>
                    <span className="v" title={`projected GW${gw}–${horizon}`}>
                      {remaining(p, gw, horizon).toFixed(1)}
                    </span>
                    {inn ? (
                      <span className="act">→ {inn.name}</span>
                    ) : (
                      <button className="toggle sell" aria-pressed={active}
                        disabled={!active && full}
                        onClick={() => setSelling(active ? null : p)}>
                        {active ? 'Cancel' : 'Sell'}
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>

            {inSandbox && (
              <>
                <p className="subhead">Pending moves</p>
                <ul className="pending-list">
                  {pending.map((m, i) => (
                    <li key={`${m.out.id}-${m.in.id}`} className="pending-row">
                      <span>
                        <button className="plink" onClick={() => openPlayer(m.out.id)}>{m.out.name}</button>
                        {' → '}
                        <button className="plink" onClick={() => openPlayer(m.in.id)}>{m.in.name}</button>
                      </span>
                      <span className="mono s">
                        £{signed(m.in.price - m.out.price)} · {signed(singleGain(m))} pts
                      </span>
                      <button className="x" onClick={() => removeMove(i)} aria-label="Remove this move">✕</button>
                    </li>
                  ))}
                </ul>
                <div className="sandbox-foot">
                  <span>
                    Over GW{gw}–{horizon}: <strong className="mono">{signed(gain.gain)}</strong> pts ·{' '}
                    {pending.length} move{pending.length === 1 ? '' : 's'},{' '}
                    {Math.min(pending.length, ft)} free
                    {gain.hits > 0 && <>, hit <span className="mono" style={{ color: 'var(--alert)' }}>−{gain.hitCost}</span></>}
                    {' → '}
                    <strong className={`mono net${gain.net > 0 ? ' pos' : ''}`}>net {signed(gain.net)}</strong>
                  </span>
                  {!legal && (
                    <span className="bad">
                      Over budget or too many from one club — remove a move.
                    </span>
                  )}
                  <button className="toggle" onClick={reset}>Reset</button>
                </div>
              </>
            )}
            {!inSandbox && suggestions.length === 0 && (
              <p className="hint" style={{ padding: '0 14px 14px', margin: 0 }}>
                Nothing improves this squad over GW{gw}–{horizon}. Bank the transfer.
              </p>
            )}
            <p className="hint" style={{ padding: '0 14px 14px', margin: 0 }}>
              Gain is the lift to your best XI over GW{gw}–{horizon}, captain included;
              hits cost {HIT_COST} each beyond your free transfers.
            </p>
          </section>

          {selling && (
            <div ref={marketRef} className="ms-market">
              <MarketTable D={D} players={candidates} pickedIds={owned} blockOf={blockOf}
                onAdd={chooseReplacement} openPlayer={openPlayer}
                lockPos={selling.pos} maxPrice={maxPrice}
                title={`Replace ${selling.name}`}
                sub={`${selling.pos} · up to £${maxPrice.toFixed(1)}m`}
                actionLabel="Bring in" />
            </div>
          )}
        </div>
      </div>

      <div className="ms-col">
        {/* ------------------------------------------------- health */}
        <section className="panel ms-health">
          <div className="panel-hd">
            <h2>Squad health</h2>
            <span className="sub">{health.length === 0 ? 'nobody flagged' : `${health.length} to watch`}</span>
          </div>
          {health.length === 0 ? (
            <div style={{ padding: '2px 14px 14px' }}>
              <div className="ready">Nobody flagged. All 15 look available and are regular starters.</div>
            </div>
          ) : (
            <ul className="health">
              {health.map(({ p, flags }) => {
                const tone = flags.some(f => f.tone === 'bad') ? 'bad'
                  : flags.some(f => f.tone === 'warn') ? 'warn' : 'info'
                return (
                  <li key={p.id} className={tone}>
                    <button className="plink strong" onClick={() => openPlayer(p.id)}>{p.name}</button>
                    <span className="s"> {xiIds.has(p.id) ? 'XI' : 'bench'}</span>
                    {' — '}{flags.map(f => f.text).join('; ')}
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {/* ------------------------------------------------ context */}
        <div className="ms-context">
          <ContextPanels D={D} squad={shown} xi={xiIds} />
        </div>
      </div>
    </div>
  )
}
