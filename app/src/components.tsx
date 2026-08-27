import { useMemo, useState, type ReactNode } from 'react'
import type { Data, Player, Team, Fixture, Pos, TickerFx } from './types'
import { POS_ORDER, SQUAD_SHAPE, MAX_PER_CLUB } from './types'
import { analyse, squadOutlook } from './squad'

/* --------------------------------------------------------------- fixture pips
   Six dots for the next six opponents, coloured by FDR. Away games are dimmed.
   This is the fastest way to read a player's opening run. */
export function Pips({ fixtures }: { fixtures: (Fixture | null)[] }) {
  return (
    <span className="pips">
      {fixtures.map((f, i) => (
        <span
          key={i}
          className="pip"
          data-fdr={f?.fdr}
          data-home={f ? String(f.home) : undefined}
          title={f ? `GW${i + 1}: ${f.opp} ${f.home ? '(H)' : '(A)'} · difficulty ${f.fdr}` : `GW${i + 1}: no fixture`}
        />
      ))}
    </span>
  )
}

/* ------------------------------------------------------------------- shirt
   The optional swapOut/swapIn/capModel/viceModel props overlay the model's
   opinion on a lineup you have set: an amber ring where the model would bench,
   a green ring where it would start, and a hollow C/V where its armband
   differs from yours. */
export interface ShirtMarks {
  swapOut?: boolean
  swapIn?: boolean
  capModel?: boolean
  viceModel?: boolean
  hint?: string
}

export function Shirt({
  player, team, fixtures, isCaptain, isVice, onClick,
  swapOut, swapIn, capModel, viceModel, hint,
}: {
  player: Player
  team: Team
  fixtures: (Fixture | null)[]
  isCaptain: boolean
  isVice?: boolean
  onClick: () => void
} & ShirtMarks) {
  const flagged = player.status !== 'a'
  const cls = `shirt${swapOut ? ' swap-out' : ''}${swapIn ? ' swap-in' : ''}`
  return (
    <button className={cls} onClick={onClick}
      title={hint ? `${player.full_name} — ${hint}` : `${player.full_name} — details`}>
      {isCaptain && <span className="cap" aria-label="Captain">C</span>}
      {!isCaptain && isVice && <span className="cap vice" aria-label="Vice-captain">V</span>}
      {!isCaptain && capModel && (
        <span className="cap ghost" aria-label="Model's captain" title="model's captain">C</span>
      )}
      {!isCaptain && !isVice && !capModel && viceModel && (
        <span className="cap ghost" aria-label="Model's vice-captain" title="model's vice">V</span>
      )}
      {flagged && <span className="flag" title={player.news}>!</span>}
      <span
        className="jersey"
        style={{
          background: `linear-gradient(105deg, ${team.primary} 0 58%, ${team.secondary} 58% 100%)`,
        }}
      />
      <span className="nm">{player.name}</span>
      <span className="meta">£{player.price.toFixed(1)} · {player.proj_6gw.toFixed(0)}</span>
      <Pips fixtures={fixtures} />
    </button>
  )
}

export function EmptyShirt({ pos }: { pos: Pos }) {
  return (
    <span className="shirt empty">
      <span className="jersey" />
      <span className="nm">{pos}</span>
      <span className="meta">—</span>
    </span>
  )
}

/* ------------------------------------------------------------------- pitch
   XI in position rows, bench strip beneath in the order they come on. Ghost
   shirts fill an empty keeper slot or an empty row while a squad is being
   drafted. `marks` overlays the model's opinion per player (see Shirt). */
export function Pitch({
  D, xi, bench, captain, vice, marks, openPlayer,
}: {
  D: Data
  xi: Player[]
  bench: Player[]
  captain: number | null
  vice: number | null
  marks?: (p: Player) => ShirtMarks | undefined
  openPlayer: (id: number) => void
}) {
  const rowsByPos = (p: Pos) => xi.filter(s => s.pos === p)
  return (
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
                  isCaptain={captain === pl.id}
                  isVice={vice === pl.id}
                  onClick={() => openPlayer(pl.id)}
                  {...marks?.(pl)}
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
            {bench.map(pl => (
              <Shirt
                key={pl.id}
                player={pl}
                team={D.teams[pl.team]}
                fixtures={D.schedule[pl.team] ?? []}
                isCaptain={false}
                onClick={() => openPlayer(pl.id)}
                {...marks?.(pl)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- player cell
   Player name with club bar, tappable to open the drawer. `extra` sits after
   the name (badges); `sub` overrides the "POS · club" line. */
export function PlayerCell({ p, id, D, openPlayer, extra, sub }: {
  p: Player | undefined; id: number; D: Data
  openPlayer: (id: number) => void; extra?: ReactNode; sub?: string
}) {
  if (!p) return <span>#{id}</span>
  return (
    <span className="pname">
      <span className="bar" style={{ background: D.teams[p.team]?.primary }} />
      <span className="txt">
        <span className="n">
          <button className="plink" onClick={() => openPlayer(p.id)}>{p.name}</button>
          {extra}
        </span>
        <span className="s">{sub ?? `${p.pos} · ${p.team}`}</span>
      </span>
    </span>
  )
}

/* ---------------------------------------------------------- link team form
   The FPL team-id input, collapsed to a "Linked: entry N · … change" line once
   an id is set. The caller supplies what follows "Linked: entry N" (it knows
   whether the picks are public) and, optionally, a line to show instead of the
   form when no id is set but there is still a squad to talk about. */
export function LinkTeamForm({
  entryId, onSave, busy, err, hint, linkedLine, unlinkedLine, inputId = 'entry',
}: {
  entryId: string
  onSave: (id: string) => void
  busy: boolean
  err: string | null
  /** copy under the input while idle */
  hint: ReactNode
  /** follows "Linked: entry N" once an id is set */
  linkedLine?: ReactNode
  /** with no id set, collapse to this line instead of showing the form */
  unlinkedLine?: ReactNode
  inputId?: string
}) {
  const [input, setInput] = useState(entryId)
  const [editing, setEditing] = useState(false)
  const showForm = editing || (!entryId && !unlinkedLine)
  const save = () => { onSave(input.trim()); setEditing(false) }

  if (showForm) {
    return (
      <>
        <div className="linkrow">
          <label htmlFor={inputId}>Your FPL team id</label>
          <input
            id={inputId} type="text" inputMode="numeric" placeholder="e.g. 1234567"
            value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && save()}
          />
          <button className="toggle" onClick={save}>Link team</button>
          {editing && (
            <button className="toggle" onClick={() => { setInput(entryId); setEditing(false) }}>
              Cancel
            </button>
          )}
        </div>
        <p className="hint">
          {busy ? 'Loading live data…'
            : err ? `Could not reach the FPL API: ${err}`
            : hint}
        </p>
      </>
    )
  }
  return (
    <p className="linked-line">
      {entryId ? (
        <>Linked: entry <span className="mono">{entryId}</span>
          {linkedLine}
          {busy && ' · loading…'}
          {err && ` · could not reach the FPL API: ${err}`}
        </>
      ) : unlinkedLine}
      <button className="linkbtn" onClick={() => { setInput(entryId); setEditing(true) }}>
        change
      </button>
    </p>
  )
}

/* ------------------------------------------------------------ fixture chips
   One gameweek's fixture(s) for a club, from the model ticker. A double shows
   two chips, a blank shows a dashed "blank" chip. */
export function FxChips({ fx }: { fx: TickerFx[] }) {
  if (fx.length === 0) return <span className="fx-chip" data-tone="none">blank</span>
  return (
    <span className="fx-chips">
      {fx.map((f, i) => (
        <span key={i} className="fx-chip">{f.opp} ({f.home ? 'H' : 'A'})</span>
      ))}
    </span>
  )
}

/* -------------------------------------------------------------- sparklines
   Pure inline SVG, no deps. BarSpark for chip weeks, LineSpark for the
   ownership log in the drawer and movers lists. */
export function BarSpark({ items }: { items: { label: string; v: number; hot?: boolean }[] }) {
  if (items.length === 0) return null
  const bw = 7, gap = 2, H = 30
  const W = items.length * (bw + gap) - gap
  const max = Math.max(0.1, ...items.map(x => x.v))
  return (
    <svg className="spark" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img">
      {items.map((x, i) => {
        const h = Math.max(1.5, (x.v / max) * (H - 2))
        return (
          <rect key={i} x={i * (bw + gap)} y={H - h} width={bw} height={h} rx={1}
            fill={x.hot ? 'var(--flood)' : 'rgba(232, 240, 234, 0.26)'}>
            <title>{x.label}</title>
          </rect>
        )
      })}
    </svg>
  )
}

export function LineSpark({ values, width = 64, height = 18 }: {
  values: number[]; width?: number; height?: number
}) {
  if (values.length < 2) return <span className="spark-flat mono">—</span>
  const min = Math.min(...values)
  const span = (Math.max(...values) - min) || 1
  const pts = values.map((v, i) =>
    `${(i / (values.length - 1)) * (width - 2) + 1},`
    + `${height - 2 - ((v - min) / span) * (height - 4)}`).join(' ')
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img">
      <polyline points={pts} fill="none" stroke="var(--flood-soft)" strokeWidth="1.5" />
    </svg>
  )
}

/* ------------------------------------------------------------- squad outlook
   Aggregate difficulty per gameweek across the starting XI. Tall red bar = the
   week your team collectively has a hard time. */
export function Outlook({
  rows,
}: {
  rows: { gw: number; avg: number; hardest: string[] }[]
}) {
  return (
    <div className="outlook-grid">
      {rows.map(r => {
        const pct = r.avg ? Math.min(100, ((r.avg - 1.6) / 3.0) * 100) : 0
        const colour =
          r.avg >= 3.9 ? 'var(--fdr-5)' :
          r.avg >= 3.4 ? 'var(--fdr-4)' :
          r.avg >= 2.9 ? 'var(--fdr-3)' : 'var(--fdr-2)'
        return (
          <div className="ogw" key={r.gw}>
            <div className="g">GW{r.gw}</div>
            <div className="bar"
              title={r.hardest.length
                ? `Tough games: ${r.hardest.join(', ')}`
                : 'No difficulty-4+ fixtures'}>
              <i style={{ height: `${pct}%`, background: colour }} />
            </div>
            <div className="v">{r.avg ? r.avg.toFixed(1) : '—'}</div>
          </div>
        )
      })}
    </div>
  )
}

/* ---------------------------------------------------------------- context
   Fixture outlook, club spread and squad shape for any 15 (or fewer). Shared
   by the squad page and the draft builder's sidebar. */
export function ContextPanels({ D, squad, xi, draft = false }: {
  D: Data; squad: Player[]; xi: Set<number>
  /** club spread and squad shape only matter while a squad is being built */
  draft?: boolean
}) {
  const state = useMemo(() => analyse(squad), [squad])
  const outlook = useMemo(
    () => squadOutlook(squad, xi, D.schedule, D.meta.horizon, D.meta.start_gw ?? 1),
    [squad, xi, D],
  )
  return (
    <>
      <section className="panel">
        <div className="panel-hd">
          <h2>Fixture outlook</h2>
          <span className="sub">XI avg difficulty</span>
        </div>
        <div className="outlook">
          <Outlook rows={outlook} />
          <p style={{ fontSize: 11.5, color: 'var(--chalk-dim)', margin: '10px 0 0', lineHeight: 1.55 }}>
            Taller and redder means your starting XI collectively faces harder
            opponents that week. Tap a bar to see which clubs.
          </p>
        </div>
      </section>

      {draft && (<>
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
      </>)}
    </>
  )
}
