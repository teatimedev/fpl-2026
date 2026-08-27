import type { Player, RetroClass, RetroComponents, WeeklyRetro, WeeklyRetroRow } from './types'
import { signed } from './squad'

/* ------------------------------------------------------------ last week
   The gameweek review the digest computes: why each player's score diverged
   from the projection, split into the parts that carry information (minutes,
   chance quality) and the parts that are noise by design (finishing, bonus).
   Wording and ordering only — nothing here changes a projection. */

const CLS: Record<RetroClass, { label: string; tone: string }> = {
  unavailable:   { label: 'unavailable',  tone: 'bad' },
  minutes_loss:  { label: 'minutes lost', tone: 'bad' },
  minutes_watch: { label: 'minutes watch', tone: 'warn' },
  minutes_gain:  { label: 'more minutes', tone: 'ok' },
  role_change:   { label: 'role change',  tone: 'warn' },
  variance:      { label: 'variance',     tone: 'dim' },
  on_model:      { label: 'on model',     tone: 'faint' },
}
const PARTS: (keyof RetroComponents)[] = ['minutes', 'chance', 'finishing', 'team', 'bonus']
const PART_LABEL: Record<string, string> = {
  minutes: 'minutes', chance: 'chances', finishing: 'finishing', team: 'team', bonus: 'bonus',
}
const f1 = (x: number) => x.toFixed(1)

function dominant(c: RetroComponents): keyof RetroComponents {
  return PARTS.reduce((best, k) => Math.abs(c[k]) > Math.abs(c[best]) ? k : best, PARTS[0])
}

function Bar({ c, scale }: { c: RetroComponents; scale: number }) {
  const neg = PARTS.filter(k => c[k] < -0.05)
  const pos = PARTS.filter(k => c[k] > 0.05)
  const w = (k: keyof RetroComponents) => `${Math.min(100, Math.abs(c[k]) / scale * 100)}%`
  return (
    <div className="retro-bar" aria-hidden>
      <div className="retro-half neg">
        {neg.map(k => <span key={k} className={`retro-seg ${k}`} style={{ width: w(k) }} title={`${PART_LABEL[k]} ${signed(c[k])}`} />)}
      </div>
      <div className="retro-half pos">
        {pos.map(k => <span key={k} className={`retro-seg ${k}`} style={{ width: w(k) }} title={`${PART_LABEL[k]} ${signed(c[k])}`} />)}
      </div>
    </div>
  )
}

export function LastWeek({ retro, poolById, openPlayer }: {
  retro: WeeklyRetro
  poolById: Map<number, Player>
  openPlayer: (id: number) => void
}) {
  const nameOf = (id: number) => poolById.get(id)?.name ?? `#${id}`
  const rows: WeeklyRetroRow[] = [...retro.table].sort((a, b) => (a.actual - a.proj) - (b.actual - b.proj))
  if (rows.length === 0) return null
  const total = rows.reduce((s, r) => s + r.actual, 0)
  const proj = rows.reduce((s, r) => s + r.proj, 0)
  const sums = Object.fromEntries(PARTS.map(k => [k, rows.reduce((s, r) => s + r.components[k], 0)])) as Record<string, number>
  const ranked = [...PARTS].sort((a, b) => Math.abs(sums[b]) - Math.abs(sums[a]))
  const noise = sums.finishing + sums.bonus
  const signal = sums.minutes + sums.chance + sums.team
  const scale = Math.max(1, ...rows.flatMap(r => PARTS.map(k => Math.abs(r.components[k])))) * 1.05

  const holdRows = retro.hold.map(id => rows.find(r => r.id === id)).filter((r): r is WeeklyRetroRow => !!r)
  const actRows = retro.act.map(id => rows.find(r => r.id === id)).filter((r): r is WeeklyRetroRow => !!r)
  const pool = Object.entries(retro.pool).filter(([, ids]) => ids.length > 0)

  return (
    <section className="panel" style={{ marginTop: 16 }}>
      <div className="panel-hd">
        <h2>Last week, explained</h2>
        <span className="sub">GW{retro.gw} · all 15, captain single: {total.toFixed(0)} scored vs {proj.toFixed(0)} projected</span>
      </div>
      <div className="retro-body">
        <p className="lede-sm" style={{ padding: 0, margin: '0 0 8px' }}>
          Of the <strong className="mono">{signed(total - proj)}</strong>:{' '}
          {ranked.slice(0, 3).map((k, i) => (
            <span key={k}>{i > 0 && ', '}{PART_LABEL[k]} <strong className="mono">{signed(sums[k])}</strong></span>
          ))}.
          {' '}Finishing and bonus (<span className="mono">{signed(noise)}</span>) are a sample the model ignores by design;
          minutes, chances and team (<span className="mono">{signed(signal)}</span>) are the parts that carry information.
        </p>

        {(holdRows.length > 0 || actRows.length > 0) && (
          <ul className="problems soft" style={{ margin: '0 0 12px' }}>
            {actRows.map(r => (
              <li key={r.id} className="act">
                <strong>Act:</strong>{' '}
                <button className="plink strong" onClick={() => openPlayer(r.id)}>{nameOf(r.id)}</button>
                {' '}— {CLS[r.cls].label}{r.subtype ? ` (${r.subtype})` : ''}: {r.note}
                {r.start_move && r.start_move !== 'start estimate unchanged' && <> · {r.start_move}</>}
              </li>
            ))}
            {holdRows.map(r => (
              <li key={r.id}>
                <strong>Hold — {r.cls === 'variance' ? 'variance, no action' : CLS[r.cls].label}:</strong>{' '}
                <button className="plink strong" onClick={() => openPlayer(r.id)}>{nameOf(r.id)}</button>
                {' '}{r.note}
                {r.proj_next != null && <> Projection next: <span className="mono">{f1(r.proj_next)}</span>.</>}
              </li>
            ))}
          </ul>
        )}

        <div className="retro-rows">
          {rows.map(r => {
            const d = r.actual - r.proj
            const k = dominant(r.components)
            return (
              <div key={r.id} className="retro-row">
                <div className="retro-l">
                  <button className="plink strong" onClick={() => openPlayer(r.id)}>{nameOf(r.id)}</button>
                  <span className={`retro-cls ${CLS[r.cls].tone}`}>{CLS[r.cls].label}</span>
                </div>
                <div className="retro-m">
                  <span className="mono">{r.actual.toFixed(0)}</span>
                  <span className="s"> / {f1(r.proj)} · {r.minutes}'</span>
                </div>
                <div className="retro-d mono" style={{ color: d > 0.5 ? 'var(--ok)' : d < -0.5 ? 'var(--alert)' : 'var(--chalk-dim)' }}>
                  {signed(d)}
                </div>
                <Bar c={r.components} scale={scale} />
                <div className="retro-why s">
                  {Math.abs(r.components[k]) >= 0.5 ? <>{PART_LABEL[k]} {signed(r.components[k])}</> : 'as expected'}
                </div>
              </div>
            )
          })}
        </div>
        <p className="retro-legend">
          {PARTS.map(k => <span key={k}><i className={`retro-key ${k}`} />{PART_LABEL[k]}</span>)}
        </p>

        {pool.length > 0 && (
          <details className="sync-details" style={{ marginTop: 10 }}>
            <summary>Around the league — who changed, who didn't</summary>
            {pool.map(([label, ids]) => (
              <p key={label} className="retro-pool">
                <span className="s">{label}:</span>{' '}
                {ids.map((id, i) => (
                  <span key={id}>{i > 0 && ', '}<button className="plink" onClick={() => openPlayer(id)}>{nameOf(id)}</button></span>
                ))}
              </p>
            ))}
          </details>
        )}
      </div>
    </section>
  )
}
