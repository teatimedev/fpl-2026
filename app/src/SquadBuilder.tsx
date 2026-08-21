import { useMemo } from 'react'
import type { Data, Player } from './types'
import { BUDGET } from './types'
import { blockReason, bestXI, formationOf, round1, type SquadState } from './squad'
import { Pitch, ContextPanels } from './components'
import MarketTable from './MarketTable'

/**
 * The from-scratch squad builder — Draft mode on the My squad tab, for
 * wildcard / free hit weeks. Pitch, market, budget and the optimiser's
 * suggested squads. The drafted squad lives in App so the drawer and This
 * Week can see it.
 */

function presetBlurb(label: string): string {
  if (label.includes('Best Found')) return 'The highest exact score found across all modelled search seeds and legal local refinements.'
  if (label.includes('Haaland Build')) return 'Built around Haaland as the premium captain and rank-risk anchor.'
  if (label.includes('Differential')) return 'Nothing owned by more than a quarter of managers: a deliberately higher-variance build.'
  if (label.includes('Conventional')) return 'The standard heuristic: keep goalkeeper and defence cheaper, then spend in attack.'
  return 'The unrestricted linear seed, retained as a distinct alternative after exact scoring.'
}

export default function SquadBuilder({
  D, picks, presetXI, state, add, remove, loadPreset, clear, openPlayer,
}: {
  D: Data
  picks: Player[]
  presetXI: Set<number> | null
  state: SquadState
  add: (p: Player) => void
  remove: (id: number) => void
  loadPreset: (i: number) => void
  clear: () => void
  openPlayer: (id: number) => void
}) {
  // When a suggested squad is loaded, honour the XI the optimiser actually
  // chose. Recomputing it here can disagree — the solver picks the XI jointly
  // with the squad, and ties break differently — which showed a different
  // eleven on the pitch to the one the projection total was quoted for.
  const computedXI = useMemo(() => bestXI(picks), [picks])
  const xi = presetXI ?? computedXI
  const starters = picks.filter(p => xi.has(p.id))
  const bench = picks.filter(p => !xi.has(p.id))
    .sort((a, b) =>
      (a.pos === 'GKP' ? -1 : 0) - (b.pos === 'GKP' ? -1 : 0)
      || b.proj_6gw - a.proj_6gw)
  const rankedStarters = [...starters].sort((a, b) => b.proj_6gw - a.proj_6gw)
  const captain = rankedStarters[0] ?? null
  const vice = rankedStarters[1] ?? null
  const pickedIds = useMemo(() => new Set(picks.map(p => p.id)), [picks])
  const blockOf = (p: Player) => blockReason(p, state)

  return (
    <div className="main">
      {/* Three direct grid children, so the phone can reorder them: on a
          narrow screen the sidebar (budget, suggested squads) has to come
          before the 120-row market table, or the suggested squads are buried
          below it and effectively invisible. */}
      <section className="panel squad-panel">
        <div className="panel-hd">
          <h2>Your squad</h2>
          <span className="sub">
            {picks.length}/15 · {starters.length === 11 ? formationOf(picks, xi) : 'incomplete XI'}
          </span>
        </div>
        <Pitch D={D} xi={starters} bench={bench}
          captain={captain?.id ?? null} vice={vice?.id ?? null} openPlayer={openPlayer} />
      </section>

      {/* ----------------------------------------------------- market */}
      <MarketTable D={D} players={D.players} pickedIds={pickedIds} blockOf={blockOf}
        onAdd={add} onRemove={remove} openPlayer={openPlayer} />

      {/* ------------------------------------------------------- sidebar */}
      <aside>
        <section className={`panel budget${state.cost > BUDGET ? ' over' : ''}`}>
          <div className="panel-hd">
            <h2>Budget</h2>
            <span className="sub">£{BUDGET.toFixed(1)}m cap</span>
          </div>
          <div className="budget">
            <div className="figure">
              <span className="big mono">£{state.remaining.toFixed(1)}</span>
              <span className="unit">m</span>
              <span className="lbl">{state.cost > BUDGET ? 'over' : 'left'}</span>
            </div>
            <div className="meter">
              <i style={{ width: `${Math.min(100, (state.cost / BUDGET) * 100)}%` }} />
            </div>
            <div className="statline">
              <div>
                <span className="k">Picked</span>
                <span className="v mono">{picks.length}<span style={{ fontSize: 13, color: 'var(--chalk-faint)' }}>/15</span></span>
              </div>
              <div>
                <span className="k">Spent</span>
                <span className="v mono">£{state.cost.toFixed(1)}</span>
              </div>
              <div>
                <span className="k">XI proj</span>
                <span className="v mono">{round1(starters.reduce((s, p) => s + p.proj_6gw, 0))}</span>
              </div>
            </div>

            {state.problems.length > 0 && (
              <ul className="problems">
                {state.problems.map(pr => <li key={pr}>{pr}</li>)}
              </ul>
            )}
            {state.complete && state.problems.length === 0 && (
              <div className="ready">
                Legal squad. {formationOf(picks, xi)} with {captain?.name} as captain.
              </div>
            )}
            {picks.length > 0 && (
              <button className="toggle" style={{ marginTop: 12, width: '100%', justifyContent: 'center' }}
                onClick={clear}>Clear squad</button>
            )}
          </div>
        </section>

        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-hd">
            <h2>Suggested squads</h2>
            <span className="sub">click to load</span>
          </div>
          <div className="presets">
            {D.squads.map((s, i) => (
              <button className="preset" key={s.label} onClick={() => loadPreset(i)}>
                <span className="t">{s.label.split(' - ')[1]?.split(':')[0] ?? s.label}</span>
                <span className="d">{presetBlurb(s.label)}</span>
                <span className="n">£{s.cost.toFixed(1)}m · {s.xi_proj} proj pts</span>
              </button>
            ))}
          </div>
        </section>

        <div style={{ marginTop: 16 }}>
          <ContextPanels D={D} squad={picks} xi={xi} />
        </div>
      </aside>
    </div>
  )
}
