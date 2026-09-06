import { useState } from 'react'
import type { Data } from './types'

const signed = (n: number) => `${n > 0 ? '+' : ''}${n.toFixed(2)}`

export function DecisionReview({ D, gw, ids, current, openPlayer }: {
  D: Data; gw: number; ids: number[]; current: boolean; openPlayer(id: number): void
}) {
  const [chosen, setChosen] = useState(106)
  const review = D.weekly?.transfer_review
  const rows = review?.players.filter(p => ids.includes(p.player_id)) ?? []
  const selected = rows.find(p => p.player_id === chosen) ?? rows[0]
  const name = (id: number) => D.players.find(p => p.id === id)?.name ?? `#${id}`
  const scout = D.scouting
  const active = scout?.gw === gw ? scout.claims.filter(c => Date.parse(c.expires_at) > Date.now()) : []
  const watched = selected ? [selected.player_id, selected.replacement].filter((id): id is number => id != null) : ids
  const claims = active.filter(c => watched.includes(c.player_id))
  const covered = new Set(active.map(c => c.player_id))
  const cases = D.weekly?.case_studies
  const recent = cases && cases.forecast_id === D.meta.forecast_id ? cases.players.filter(p => watched.includes(p.id)) : []
  const lab = D.policy_lab && D.policy_lab.forecast_id === D.meta.forecast_id && D.policy_lab.gw === gw
    && D.policy_lab.account.ids.length === ids.length && D.policy_lab.account.ids.every(id => ids.includes(id))
    && D.policy_lab.account.bank === D.weekly?.squad.bank && D.policy_lab.account.ft === D.weekly?.squad.ft
    ? D.policy_lab : null
  const casePolicy = lab?.cases.find(c => c.outgoing === selected?.player_id && c.incoming === selected?.replacement)

  return <section className="panel decision-review" style={{ marginTop: 14 }}>
    <div className="panel-hd"><h2>Hold or sell?</h2><span className="sub">Evidence + alternatives</span></div>
    <div style={{ padding: '0 14px 16px', lineHeight: 1.6 }}>
      <p style={{ marginTop: 0 }}>Every holding has to earn its place. Compare the strongest affordable replacement, then test the concern behind selling.</p>
      {selected && review ? <>
        <label htmlFor="review-player" className="mono">Review a player{' '}</label>
        <select id="review-player" value={selected.player_id} onChange={e => setChosen(Number(e.target.value))}
          style={{ maxWidth: '100%', padding: '8px 12px', background: 'var(--turf-2)', color: 'var(--chalk)', border: 'var(--line)', borderRadius: 4 }}>
          {rows.map(p => <option key={p.player_id} value={p.player_id}>{name(p.player_id)}</option>)}
        </select>
        <p className="stamp mono">GW{review.gw}–{review.horizon} · {current ? 'Current published comparison' : 'Archived comparison · needs refresh'}
          {' '}· {D.weekly?.generated && new Date(D.weekly.generated).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</p>
        {selected.replacement == null ? <p>No affordable same-position replacement was found.</p> : <>
          <p style={{ fontSize: 18, marginBottom: 6 }}>
            <button className="plink strong" onClick={() => openPlayer(selected.player_id)}>{name(selected.player_id)}</button>
            {' → '}<button className="plink strong" onClick={() => openPlayer(selected.replacement!)}>{name(selected.replacement)}</button>
          </p>
          <p>Current model: <strong className="mono">{signed(selected.net ?? 0)} points</strong> over the window;
            {' '}<strong className="mono">{signed(selected.now_net ?? 0)}</strong> in GW{review.gw}, both after hits.
            {selected.sell_price != null && <> Sell £{selected.sell_price.toFixed(1)}m · buy £{selected.buy_price?.toFixed(1)}m.</>}
          </p>
          <div className="tbl-scroll"><table>
            <thead><tr><th className="l">If the outgoing player has…</th><th>Transfer gain</th></tr></thead>
            <tbody>{selected.scenarios.map(s => <tr key={s.label}>
              <td className="l">{s.label}</td><td className="mono" style={{ color: s.net > 0 ? 'var(--ok)' : 'var(--chalk-faint)' }}>{signed(s.net)}</td>
            </tr>)}</tbody>
          </table></div>
          <p className="hint">Positive means the swap beats keeping the current 15 under that assumption. These are stress tests, not a measured decline or a probability of winning. They keep the replacement unchanged.</p>
          {!!selected.paired_scenarios?.length && <details><summary>Test both players’ attacking forecasts</summary>
            <p className="hint">Transfer gain if either forecast is too optimistic. These reductions are hypothetical and apply across GW{review.gw}–{review.horizon}.</p>
            <div className="tbl-scroll"><table>
              <thead><tr><th className="l">{name(selected.player_id)} reduction</th>{[0, 10, 20].map(n => <th key={n}>{name(selected.replacement!)} −{n}%</th>)}</tr></thead>
              <tbody>{[0, .1, .2].map(out => <tr key={out}><td className="l">{Math.round(out*100)}%</td>
                {[0, .1, .2].map(inc => <td className="mono" key={inc}>{signed(selected.paired_scenarios!.find(s => s.outgoing_drop === out && s.incoming_drop === inc)?.net ?? 0)}</td>)}
              </tr>)}</tbody>
            </table></div>
          </details>}
          {recent.length > 0 && <>
            <h3>Recent match evidence</h3>
            <div className="tbl-scroll"><table className="review-matches"><thead><tr><th className="l">Player</th><th>Games</th><th>Minutes</th><th>Goals</th><th>xG</th><th>xA</th><th>Points</th></tr></thead>
              <tbody>{recent.map(p => <tr key={p.id}><td className="l">{p.name}</td><td>{p.matches.length}</td><td>{p.totals.minutes}</td><td>{p.totals.goals}</td><td>{p.totals.xg.toFixed(2)}</td><td>{p.totals.xa.toFixed(2)}</td><td>{p.totals.points}</td></tr>)}</tbody>
            </table></div>
            <p className="hint">{cases?.note}</p>
            {recent.filter(p => p.totals.penalties_missed > 0).map(p => <p key={p.id}>{p.name} has missed {p.totals.penalties_missed} penalty. His total xG includes that chance; it is not all open-play threat.</p>)}
          </>}
          {casePolicy && <>
            <h3>Sell now or wait and replan?</h3>
            <p>Forcing this single transfer now is worth <strong className="mono">{signed(casePolicy.act_vs_wait)} points</strong> versus holding this week and allowing future transfers. Both paths use the same forecasts, selling prices, hit costs and legal lineup scoring.</p>
            {!!casePolicy.replanned_scenarios?.length && <div className="tbl-scroll"><table>
              <thead><tr><th className="l">Hypothetical change</th><th>Act vs wait</th></tr></thead>
              <tbody>{casePolicy.replanned_scenarios.map(s => <tr key={s.incoming_drop}><td className="l">{name(casePolicy.outgoing)} −20%; {name(casePolicy.incoming)} −{Math.round(s.incoming_drop*100)}%</td><td className="mono">{s.act_vs_wait == null ? 'Unavailable' : signed(s.act_vs_wait)}</td></tr>)}</tbody>
            </table></div>}
            <p className="hint">The future plan is recalculated in each scenario. These differences depend on the model and its solver approximation; they are not demonstrated gains.</p>
          </>}
          <details><summary>How this comparison works</summary>
            <p>{review.method}</p><p>{review.caveat}</p><p>{review.threshold_status}</p>
            <p>Bank and transfers come from the last public deadline. Changes you make before the next deadline are not yet visible. Initial purchase prices are inferred from GW1.</p>
          </details>
        </>}
      </> : <p>The next full refresh will publish individual replacement comparisons here.</p>}

      <h3 style={{ marginBottom: 8 }}>What observers actually reported</h3>
      <p className="hint">{ids.filter(id => covered.has(id)).length}/{ids.length} squad players have a current sourced observation.
        {' '}Missing coverage means we do not know. It is not evidence that a player is playing well.</p>
      {scout?.status === 'missing_key' && <p>AI scouting is awaiting its DeepSeek credential.</p>}
      {!!scout?.sources?.length && <details><summary>Scouting source coverage</summary>
        <p className="hint">Reachable pages can still be stale or contain no usable articles. This list reports collection, not agreement with a transfer.</p>
        <ul>{scout.sources.map(s => <li key={s.source_id}>{s.publisher ?? s.source_id}: {s.articles} dated article{s.articles === 1 ? '' : 's'} · {s.status.replaceAll('_', ' ')}</li>)}</ul>
      </details>}
      {claims.length === 0 && <p>No current supported observation for this comparison yet.</p>}
      {watched.map(id => {
        const conflicts = scout?.players.find(p => p.player_id === id)?.conflicts ?? []
        return conflicts.length > 0 && <p key={id}><strong>Mixed evidence for {name(id)}:</strong> {conflicts.join(', ').replaceAll('_', ' ')}. Read both accounts below.</p>
      })}
      {claims.map(c => <article key={c.id} style={{ borderTop: 'var(--line)', padding: '10px 0' }}>
        <p style={{ margin: '0 0 5px' }}><strong>{c.player}</strong> · {c.mechanism.replaceAll('_', ' ')} · {c.scope === 'next_fixture' ? 'preview at publication' : c.scope.replaceAll('_', ' ')}
          {' · '}{new Date(c.published_at).toLocaleDateString('en-GB')}</p>
        <p style={{ margin: '0 0 5px' }}>{c.observation}</p>
        <details><summary>Read the source evidence</summary>
          <blockquote style={{ margin: '8px 0', paddingLeft: 12, borderLeft: 'var(--line-strong)' }}>{c.quote}</blockquote>
          <a href={c.url} target="_blank" rel="noreferrer" style={{ color: 'var(--chalk)', textUnderlineOffset: 3 }}>{c.publisher} ↗</a>
          {' · '}{new Date(c.published_at).toLocaleDateString('en-GB')}
        </details>
      </article>)}
      <p className="hint">AI extracts these observations from articles; it has not watched the matches. Previews refer to the match upcoming when published, which may now be finished. Observations inform the review and the tests above. They do not automatically change player projections.</p>
      {lab && <details><summary>Why the transfer policy says hold or act</summary>
        <p>The flexible plan gains <strong>{signed(lab.act_vs_wait)}</strong> over waiting, across {lab.moves} moves. Here is how an extra uncertainty buffer changes that decision:</p>
        <div className="tbl-scroll"><table><thead><tr><th className="l">Buffer per move</th><th>Required gain</th><th>Decision</th></tr></thead>
          <tbody>{lab.thresholds.map(t => <tr key={t.buffer}><td className="l">{t.buffer.toFixed(2)}</td><td>{t.required.toFixed(2)}</td><td>{t.decision}</td></tr>)}</tbody>
        </table></div>
        <p>{lab.note}</p>
        <p>Giving the waiting plan one additional free transfer changes its estimated value by {signed(lab.extra_ft_value)} points. This is a local model estimate, not a universal price for a transfer.</p>
        <p className="hint">{lab.limitation}</p>
      </details>}
    </div>
  </section>
}
