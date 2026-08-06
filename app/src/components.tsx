import type { Player, Team, Fixture, Pos } from './types'

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
  player, team, fixtures, isCaptain, onClick,
}: {
  player: Player
  team: Team
  fixtures: (Fixture | null)[]
  isCaptain: boolean
  onClick: () => void
}) {
  const flagged = player.status !== 'a'
  return (
    <button className="shirt" onClick={onClick}
      title={`Remove ${player.full_name}`}>
      {isCaptain && <span className="cap" aria-label="Captain">C</span>}
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
