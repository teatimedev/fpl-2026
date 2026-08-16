import { useEffect } from 'react'
import type { Data, Player } from './types'
import { FxChips } from './components'
import { fxFor } from './weekly'
import { thisGw } from './model'
import { signed } from './squad'

/**
 * Slide-over player card: bottom sheet on a phone, right-side panel on desktop.
 * Opened by tapping a player name anywhere; closes on backdrop click / Escape.
 * Everything here is baked data — proj_by_gw for the window, season_by_gw for
 * the rest of the season, the shrunk per-90 rates, and the ownership log.
 */

const DASH = '—'

function ProjChart({ p, start, horizon }: { p: Player; start: number; horizon: number }) {
  const season = p.season_by_gw ?? []
  const window_ = p.proj_by_gw ?? []
  const N = 38
  const W = 360, H = 84
  const bw = W / N
  const winBars: { gw: number; v: number }[] = []
  for (let g = start; g <= Math.min(horizon, N); g++) {
    winBars.push({ gw: g, v: window_[g - 1] ?? 0 })
  }
  const max = Math.max(0.5, ...season, ...winBars.map(b => b.v))
  const y = (v: number) => H - 2 - (v / max) * (H - 8)

  const seasonPts = season.length
    ? season.map((v, i) => `${(i * bw + bw / 2).toFixed(1)},${y(v).toFixed(1)}`)
    : []
  const area = seasonPts.length
    ? `M ${bw / 2},${H} L ${seasonPts.join(' L ')} L ${((N - 1) * bw + bw / 2).toFixed(1)},${H} Z`
    : null

  return (
    <div className="drawer-chart">
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Projected points by gameweek for ${p.name}`}>
        {area && <path d={area} fill="rgba(232, 240, 234, 0.10)" />}
        {seasonPts.length > 1 && (
          <polyline points={seasonPts.join(' ')} fill="none"
            stroke="rgba(232, 240, 234, 0.22)" strokeWidth="1" />
        )}
        {winBars.map(b => (
          <rect key={b.gw} x={(b.gw - 1) * bw + 0.8} y={y(b.v)}
            width={bw - 1.6} height={Math.max(1.5, H - 2 - y(b.v))} rx={1}
            fill="var(--flood)">
            <title>{`GW${b.gw}: ${b.v.toFixed(1)}`}</title>
          </rect>
        ))}
      </svg>
      <div className="axis mono">
        <span>GW1</span><span>GW19</span><span>GW38</span>
      </div>
      <p className="drawer-note">
        Amber bars: the modelled window, GW{start}–{horizon}. Faint line: coarse
        full-season projection.
      </p>
    </div>
  )
}

export default function PlayerDrawer({
  player, D, gw, onClose, action,
}: {
  player: Player | null
  D: Data
  gw: number
  onClose: () => void
  action?: { label: string; run: () => void } | null
}) {
  useEffect(() => {
    if (!player) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [player, onClose])

  if (!player) return null
  const p = player
  const t = D.teams[p.team]
  const start = D.meta.start_gw ?? 1
  const horizon = D.meta.horizon
  const fx = fxFor(D.ticker, p.team, gw)
  const mv = D.movers?.players?.[String(p.id)]
  const days = D.movers?.days ?? 0

  return (
    <div className="drawer-root">
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={p.full_name}>
        <button className="drawer-x" onClick={onClose} aria-label="Close">×</button>
        <div className="drawer-hd">
          <span className="bar" style={{ background: t?.primary }} />
          <div>
            <h2>{p.name}</h2>
            <span className="drawer-sub mono">
              {t?.name ?? p.team} · {p.pos} · £{p.price.toFixed(1)}m · {p.sel_pct.toFixed(1)}% owned
            </span>
          </div>
        </div>

        {p.status !== 'a' && (
          <p className="drawer-news">{p.news || `Status: ${p.status}`}</p>
        )}
        {p.note && <p className="drawer-fact">{p.note}</p>}

        <p className="drawer-gw">
          This gameweek: <FxChips fx={fx} /> · projected{' '}
          <strong className="mono">{thisGw(p, gw).toFixed(1)}</strong>
        </p>

        <ProjChart p={p} start={start} horizon={horizon} />

        <div className="facts">
          <div><span className="k">xG/90</span><span className="v mono">{p.xg90?.toFixed(2) ?? DASH}</span></div>
          <div><span className="k">xA/90</span><span className="v mono">{p.xa90?.toFixed(2) ?? DASH}</span></div>
          <div><span className="k">DefCon/90</span><span className="v mono">{p.dc90?.toFixed(1) ?? DASH}</span></div>
          <div><span className="k">Starts</span><span className="v mono">{p.start_rate != null ? `${Math.round(p.start_rate * 100)}%` : DASH}</span></div>
          <div><span className="k">Own record</span><span className="v mono">{p.evidence != null ? `${Math.round(p.evidence * 100)}%` : DASH}</span></div>
          <div><span className="k">PL seasons</span><span className="v mono">{p.seasons ?? DASH}</span></div>
        </div>
        <p className="drawer-note">
          “Own record” is how much of the xG estimate rests on his own numbers
          rather than the positional prior.
        </p>

        <div className="drawer-lines">
          <p>Last season: <strong className="mono">{p.pts_last}</strong> pts
            {p.mins_last > 0 && <> in <span className="mono">{p.mins_last.toLocaleString()}</span> mins</>}.</p>
          {p.games_now > 0 && (
            <p>This season: <strong className="mono">{p.pts_now}</strong> pts ·{' '}
              <span className="mono">{p.mins_now.toLocaleString()}</span> mins ·
              started <span className="mono">{p.starts_now}</span> of{' '}
              <span className="mono">{p.games_now}</span>.</p>
          )}
          {mv && (
            <p className="drawer-mv">
              Ownership <span className="mono">{mv.sel.toFixed(1)}%</span>
              {' '}(<span className="mono">{signed(mv.d_sel_1)}</span> 1d,{' '}
              <span className="mono">{signed(mv.d_sel_7)}</span> 7d) ·
              price <span className="mono">{signed(mv.d_price_7)}</span> 7d,{' '}
              <span className="mono">{signed(mv.d_price_season)}</span> season
              {mv.spark.length > 1 && <> <LineSparkInline values={mv.spark} /></>}
              {days > 0 && days < 7 && (
                <span className="drawer-note-inline">
                  {' '}· {days} day{days === 1 ? '' : 's'} of log so far
                </span>
              )}
            </p>
          )}
        </div>

        {action && (
          <button className="toggle drawer-action" onClick={action.run}>
            {action.label}
          </button>
        )}
      </aside>
    </div>
  )
}

// Local wrapper so the momentum line stays a single <p> flow.
function LineSparkInline({ values }: { values: number[] }) {
  const min = Math.min(...values)
  const span = (Math.max(...values) - min) || 1
  const W = 64, H = 16
  const pts = values.map((v, i) =>
    `${(i / (values.length - 1)) * (W - 2) + 1},`
    + `${H - 2 - ((v - min) / span) * (H - 4)}`).join(' ')
  return (
    <svg className="spark inline" width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
      <polyline points={pts} fill="none" stroke="var(--flood-soft)" strokeWidth="1.5" />
    </svg>
  )
}
