import type { Player, Team, Fixture, Pos, TickerFx } from './types'

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

/* ------------------------------------------------------------------- shirt */
export function Shirt({
  player, team, fixtures, isCaptain, isVice, onClick,
}: {
  player: Player
  team: Team
  fixtures: (Fixture | null)[]
  isCaptain: boolean
  isVice?: boolean
  onClick: () => void
}) {
  const flagged = player.status !== 'a'
  return (
    <button className="shirt" onClick={onClick}
      title={`${player.full_name} — details`}>
      {isCaptain && <span className="cap" aria-label="Captain">C</span>}
      {!isCaptain && isVice && <span className="cap vice" aria-label="Vice-captain">V</span>}
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

export function EmptyShirt({ pos }: { pos: Pos }) {
  return (
    <span className="shirt empty">
      <span className="jersey" />
      <span className="nm">{pos}</span>
      <span className="meta">—</span>
    </span>
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
