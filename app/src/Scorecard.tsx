import type { Scorecard as ScorecardData, ScorecardPick } from './types'
import { signed as signedNum } from './squad'

/**
 * How the model has actually done. Every refresh before a deadline archives
 * what the model believed; once a gameweek finishes, v2/scorecard.py grades it
 * against real points and the result is baked into fpl.json with the projections.
 */

const DASH = '—'
const num = (v: number | null | undefined, dp = 2) => (v == null ? DASH : v.toFixed(dp))
const signed = (v: number | null | undefined, dp = 2) => (v == null ? DASH : signedNum(v, dp))
const pct = (v: number | null | undefined) => (v == null ? DASH : `${Math.round(v * 100)}%`)
const pick = (p?: ScorecardPick) => (p ? `${p.name} ${p.pts}` : DASH)
const trio = (a: number | null | undefined, b: number | null | undefined, c: number | null | undefined) =>
  `${num(a, 1)} / ${num(b, 1)} / ${num(c, 1)}`

function Tile({ k, v, s }: { k: string; v: string; s?: string }) {
  return (
    <div>
      <span className="k">{k}</span>
      <span className="v mono">{v}</span>
      {s && <span className="s">{s}</span>}
    </div>
  )
}

export default function Scorecard({ sc }: { sc: ScorecardData | null }) {
  if (!sc || sc.summary.n_gws === 0) {
    return (
      <div className="score">
        <section className="panel">
          <div className="panel-hd">
            <h2>How good is the model?</h2>
            <span className="sub">nothing graded yet</span>
          </div>
          <div className="week-hd">
            <p className="hint" style={{ margin: 0 }}>
              Nothing to grade yet. Every refresh before a deadline archives what
              the model believed; once a gameweek finishes it is graded here —
              rank correlation of projections vs actual points, calibration by
              decile, and how the model's captain and XI did against yours.
              Pre-season hold-out backtest: rank correlation about 0.46.
            </p>
          </div>
        </section>
      </div>
    )
  }

  const s = sc.summary
  const gws = [...sc.gws].sort((a, b) => a.gw - b.gw)
  const latest = gws[gws.length - 1]

  return (
    <div className="score">
      <section className="panel">
        <div className="panel-hd">
          <h2>How good is the model?</h2>
          <span className="sub">{s.n_gws} gameweek{s.n_gws === 1 ? '' : 's'} graded</span>
        </div>
        <div className="tiles">
          <div className="statline wide">
            <Tile k="Rank corr (starters)" v={num(s.spearman_starters)}
              s={`pool ${num(s.spearman_pool)}`} />
            <Tile k="MAE (starters)" v={num(s.mae_starters)}
              s={`bias ${signed(s.bias_starters)}`} />
            <Tile k="Captain avg" v={trio(s.captain_model, s.captain_yours, s.captain_best)}
              s="model / yours / best" />
            <Tile k="XI avg" v={trio(s.xi_model, s.xi_yours, s.xi_best)}
              s="model / yours / best" />
            <Tile k="CS Brier" v={num(s.cs_brier, 3)}
              s={`predicted ${pct(s.cs_predicted_rate)} · actual ${pct(s.cs_actual_rate)}`} />
          </div>
        </div>
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-hd">
          <h2>By gameweek</h2>
          <span className="sub">actual points against what the model said</span>
        </div>
        <div className="tbl-scroll">
          <table>
            <thead>
              <tr>
                <th className="l">GW</th>
                <th>Corr starters</th>
                <th>Corr pool</th>
                <th>MAE</th>
                <th>Top-20 mean</th>
                <th>Top-20 in top-50</th>
                <th>Cap model</th>
                <th>Cap yours</th>
                <th>Cap best</th>
                <th>XI model</th>
                <th>XI yours</th>
                <th>XI best</th>
                <th>CS Brier</th>
              </tr>
            </thead>
            <tbody>
              {gws.map(g => (
                <tr key={g.gw}>
                  <td className="l">
                    GW{g.gw}
                    {!g.checked && <span className="badge new">provisional</span>}
                  </td>
                  <td style={{ color: 'var(--flood-soft)' }}>{num(g.spearman_starters)}</td>
                  <td>{num(g.spearman_pool)}</td>
                  <td>{num(g.mae_starters)}</td>
                  <td>{num(g.top20_mean_actual, 1)}</td>
                  <td>{g.top20_in_actual_top50}/20</td>
                  <td>{pick(g.captain?.model)}</td>
                  <td>{pick(g.captain?.yours)}</td>
                  <td style={{ color: 'var(--chalk-dim)' }}>{pick(g.captain?.best)}</td>
                  <td>{g.xi?.model ?? DASH}</td>
                  <td>{g.xi?.yours ?? DASH}</td>
                  <td style={{ color: 'var(--chalk-dim)' }}>{g.xi?.best ?? DASH}</td>
                  <td>{num(g.cs?.brier, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint" style={{ padding: '10px 14px 14px', margin: 0 }}>
          Captain and XI are actual points scored by the model's choice, yours,
          and the hindsight-best pick from the same squad. Provisional means
          bonus was not final when graded.
        </p>
      </section>

      {latest && latest.deciles.length > 0 && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-hd">
            <h2>Calibration</h2>
            <span className="sub">GW{latest.gw} · likely starters by projected decile</span>
          </div>
          <div className="tbl-scroll">
            <table>
              <thead>
                <tr>
                  <th className="l">Decile</th>
                  <th>Projected range</th>
                  <th>Proj mean</th>
                  <th>Actual mean</th>
                  <th>n</th>
                </tr>
              </thead>
              <tbody>
                {latest.deciles.map((d, i) => (
                  <tr key={i}>
                    <td className="l">{i + 1}</td>
                    <td>{d.lo.toFixed(1)}–{d.hi.toFixed(1)}</td>
                    <td style={{ color: 'var(--flood-soft)' }}>{d.proj.toFixed(2)}</td>
                    <td>{d.actual.toFixed(2)}</td>
                    <td style={{ color: 'var(--chalk-dim)' }}>{d.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="score-notes">
        {sc.notes.map(n => <p key={n}>{n}</p>)}
        <p className="mono">graded {sc.generated}</p>
      </div>
    </div>
  )
}
