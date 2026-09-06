import type { Data, Player, Weekly, WeeklyLineup } from './types'
import { Pitch } from './components'
import { lineupChanges, plainPlayerCheck, weeklyTransferSummary } from './weeklyActions'

export function WeeklyBrief({ D, W, poolById, currentLineup, openPlayer }: {
  D: Data; W: Weekly; poolById: Map<number, Player>; currentLineup: WeeklyLineup | null
  openPlayer: (id: number) => void
}) {
  const action = weeklyTransferSummary(W)
  const lineup = action.lineup
  const changes = lineup ? lineupChanges(currentLineup, lineup) : null
  const players = (ids: number[]) => ids.map(id => poolById.get(id)).filter((p): p is Player => !!p)
  const xi = players(lineup?.xi ?? [])
  const bench = players(lineup?.bench ?? [])
  const outfieldBench = bench.filter(p => p.pos !== 'GKP')
  const spareKeeper = bench.find(p => p.pos === 'GKP')
  const shape = ['DEF', 'MID', 'FWD'].map(pos => xi.filter(p => p.pos === pos).length).join('–')
  const checks = (W.checks ?? []).filter(c => !action.moves.some(m => m.out === c.id))
  const incomingChecks = action.moves.flatMap(move => {
    const p = poolById.get(move.in_)
    return p && (p.status !== 'a' || (p.start_by_gw?.[W.gw - 1] ?? p.start_rate) < .8)
      ? [{ id: p.id, xi: !!lineup?.xi?.includes(p.id), flags: ['start estimate'] }] : []
  })
  const allChecks = [...checks, ...incomingChecks]
  const chipData = W.chips?.gw === W.gw ? W.chips : null
  const chips = chipData ? Object.values(chipData.chips).filter(c => c?.play) : []
  const name = (id: number) => poolById.get(id)?.name ?? `Player ${id}`
  const playerLink = (id: number) => <button className="plink" onClick={() => openPlayer(id)}>{name(id)}</button>
  const names = (ids: number[]) => ids.map((id, i) => <span key={id}>{i > 0 && ', '}{playerLink(id)}</span>)
  const analysed = new Date(W.generated).toLocaleString('en-GB', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })

  return <div className="weekly-brief">
    <section className="weekly-actions" aria-labelledby="weekly-action-title">
      <p className="brief-eyebrow">Your Gameweek {W.gw} plan</p>
      <h2 id="weekly-action-title">{action.headline}</h2>
      <p className="brief-reason">{action.hold
        ? `The expected benefit of changing your team now is too small.${W.gw < 38 ? ' Reassess next week.' : ''}`
        : action.moves.length > 0 ? 'Make the moves below, then set your team.'
          : 'A clear transfer instruction is not available yet. Check the analysis below.'}</p>

      <ol className="weekly-steps">
        <li>
          <div className="step-label">Transfers</div>
          {action.hold ? <p className="step-main">Keep your players for this week.</p>
            : <ul className="brief-moves">{action.moves.map(move => <li key={move.out}>
              Sell {playerLink(move.out)} <span aria-hidden="true">→</span> buy {playerLink(move.in_)}.
            </li>)}</ul>}
          {action.hits > 0 && <p className="brief-hit">This costs {action.hits} points.</p>}
          <p className="step-detail">{action.unlimited ? 'Transfers are free before Gameweek 1.'
            : W.gw === 38 ? `${W.squad.ft} free transfers available for the final gameweek.`
            : <><strong>{W.squad.ft} free transfers now → {action.next} next week.</strong>
              {action.lost > 0
                ? ' Your bank is full: making no move means losing the next new transfer. Using one would still leave five next week.'
                : action.hold && W.squad.ft < 5
                  ? ` If you keep saving, you reach the limit of five in Gameweek ${W.gw + 5 - W.squad.ft}.`
                  : ''}</>}</p>
        </li>
        <li>
          <div className="step-label">Captain</div>
          {lineup?.captain && lineup?.vice ? <>
            <p className="step-main">{playerLink(lineup.captain)} <span className="brief-armband">C</span>
              <span className="brief-vice">Vice: {playerLink(lineup.vice)}</span></p>
            {changes && (changes.captainChanged || changes.viceChanged) && <p className="step-detail">
              {changes.captainChanged && <>Set {name(lineup.captain)} as captain. </>}
              {changes.viceChanged && <>Set {name(lineup.vice)} as vice-captain.</>}
            </p>}
          </> : <p>Captain advice needs an updated lineup.</p>}
        </li>
        <li>
          <div className="step-label">Starting team</div>
          <p className="step-main">{!lineup ? 'Lineup needs updating after the transfers.'
            : changes && changes.start.length === 0 ? 'Keep the same starting eleven.'
              : changes ? <>Start {names(changes.start)}.</> : 'Use the starting eleven shown here.'}</p>
          {changes && changes.sit.length > 0 && <p className="step-detail">
            {changes.sit.some(id => !action.moves.some(m => m.out === id)) && <>
              Bench {names(changes.sit.filter(id => !action.moves.some(m => m.out === id)))}.
            </>}
          </p>}
          {changes?.benchChanged && <p className="step-detail">Change your bench order to the order shown here.</p>}
        </li>
        <li>
          <div className="step-label">Before the deadline</div>
          {allChecks.length > 0 ? <ul className="brief-checks">{allChecks.map(c => <li key={c.id}>
            <strong>{playerLink(c.id)}</strong>: {plainPlayerCheck(poolById.get(c.id), c)}
          </li>)}</ul> : <p className="step-main">Check the latest team news before saving your team.</p>}
          <p className="step-detail">{!chipData ? 'Chip advice has not been updated for this week.'
            : chips.length > 0 ? `Chip advice: play ${chips.map(c => c.name).join(' / ')}.`
              : 'Save your chips this week.'}</p>
        </li>
      </ol>
      <p className="brief-source">Updated {analysed}. Based on your last published team;
        changes made since the last deadline are not visible yet.</p>
      <a className="brief-fpl-link" href="https://fantasy.premierleague.com/my-team" target="_blank" rel="noreferrer">
        Open FPL to set your team ↗
      </a>
    </section>

    {lineup && xi.length === 11 && <section className="panel brief-lineup" aria-labelledby="weekly-lineup-title">
      <div className="panel-hd"><h2 id="weekly-lineup-title">{action.hold ? 'Start these players' : 'Your team after the transfers'}</h2>
        <span className="sub">{shape}</span></div>
      <Pitch D={D} xi={xi} bench={bench} captain={lineup.captain ?? null}
        vice={lineup.vice ?? null} simple openPlayer={openPlayer} />
      <div className="brief-bench">
        <strong>Bench order</strong>
        <p>{outfieldBench.map((p, i) => <span key={p.id}>{i > 0 && ' · '}{i + 1}. {playerLink(p.id)}</span>)}</p>
        {spareKeeper && <p>Spare goalkeeper: {playerLink(spareKeeper.id)}</p>}
      </div>
    </section>}
  </div>
}
