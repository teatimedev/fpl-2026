import { useState } from 'react'
import type { ReactNode } from 'react'
import type { Scorecard as ScorecardData, ScorecardPick } from './types'
import type { LinkedTeam } from './useLinkedTeam'
import { captainReturn, chipLabel, isOwnReview, lineupDifference, personalResults } from './scorecardView'

const DASH = '—'
const num = (v: number | null | undefined, dp = 2) => v == null ? DASH : v.toFixed(dp)
const signed = (v: number | null | undefined, dp = 2) => v == null ? DASH : `${v > 0 ? '+' : ''}${v.toFixed(dp)}`
const count = (v: number | null | undefined) => v == null ? DASH : v.toLocaleString('en-GB')
const pct = (v: number | null | undefined) => v == null ? DASH : `${Math.round(v * 100)}%`
const pick = (p?: ScorecardPick) => p ? `${p.name} · ${p.pts} pts` : DASH
const date = (value: string) => new Date(value).toLocaleString('en-GB', {
  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
})

function Metric({ label, value, detail, children }: {
  label: string; value: string; detail: string; children?: ReactNode
}) {
  return <div className="score-metric">
    <h3>{label}</h3><p className="score-value mono">{value}</p>
    <p className="score-detail">{detail}</p>
    {children && <p className="score-explanation">{children}</p>}
  </div>
}

export default function Scorecard({ sc, linked }: { sc: ScorecardData | null; linked: LinkedTeam }) {
  const [chosenGw, setChosenGw] = useState<number | null>(null)
  const results = personalResults(sc, linked.history, linked.entryId)
  const gws = [...(sc?.gws ?? [])].sort((a, b) => a.gw - b.gw)
  const weeks = [...new Set([...results.map(r => r.gw), ...gws.map(g => g.gw)])].sort((a, b) => b - a)
  const selectedGw = chosenGw != null && weeks.includes(chosenGw) ? chosenGw : weeks[0]
  const result = results.find(r => r.gw === selectedGw)
  const review = gws.find(g => g.gw === selectedGw)
  const ownReview = isOwnReview(review, linked.entryId)
  const captain = captainReturn(review, linked.entryId)
  const difference = lineupDifference(review, linked.entryId)
  const latest = gws.at(-1)
  const calibration = review ?? latest
  const s = sc?.summary
  const reviewedOwn = gws.filter(g => isOwnReview(g, linked.entryId))
  const avg = (values: (number | undefined)[]) => {
    const known = values.filter((v): v is number => v != null)
    return known.length ? known.reduce((a, b) => a + b, 0) / known.length : null
  }

  return <div className="score">
    <header className="score-heading">
      <div><p className="brief-eyebrow">Your results & the model’s record</p>
        <h2>Your Scorecard</h2>
        <p>See how your team did, how the picks compared, and how accurate the predictions were.</p>
      </div>
      {weeks.length > 0 && <label className="score-week">Review gameweek
        <select value={selectedGw} onChange={e => setChosenGw(Number(e.target.value))}>
          {weeks.map(gw => <option key={gw} value={gw}>Gameweek {gw}</option>)}
        </select>
      </label>}
    </header>

    <section className="panel score-results" aria-labelledby="score-result-title">
      <div className="panel-hd"><h2 id="score-result-title">Your Gameweek {selectedGw ?? ''} result</h2>
        <span className="score-tag">{review?.checked && ownReview ? 'Reviewed' : 'Awaiting model review'}</span>
      </div>
      {result ? <>
        <div className="score-result-grid">
          <Metric label="Your points" value={count(result.net)} detail="After transfer hits">
            {result.hit ? `${result.points} scored − ${result.hit} in transfer hits.` : 'No points deducted for transfers.'}
            {' '}Includes captain bonuses, chips and automatic substitutes.
          </Metric>
          <Metric label="Overall rank" value={count(result.rank)} detail="Your place among all FPL teams">
            {result.rankChange == null ? 'Lower rank is better.' : result.rankChange === 0 ? 'No change from the previous gameweek.'
              : `${result.rankChange > 0 ? 'Up' : 'Down'} ${count(Math.abs(result.rankChange))} places since the previous gameweek.`}
          </Metric>
          <Metric label="Season points" value={count(result.total)} detail={`Total through Gameweek ${result.gw}`}>
            {result.total == null ? 'Season total is unavailable in this saved result.' : 'Transfer hits are already included in this total.'}
          </Metric>
          <Metric label="Chip played" value={chipLabel(result.chip)} detail={captain ? `Captain: ${captain.name}` : 'From your submitted FPL team'}>
            {captain ? `${captain.base} points × ${captain.multiplier} = ${captain.total} captain points, included in your score.`
              : 'The full captain breakdown appears once this week is reviewed.'}
          </Metric>
        </div>
        <p className="score-footnote">{result.source === 'fpl' ? 'Result loaded from FPL.' : `Saved official result · reviewed ${sc ? date(sc.generated) : ''}.`}
          {' '}{linked.summary?.name && result.source === 'fpl' ? `${linked.summary.name} · ` : ''}Team {linked.entryId}.
          {!review?.checked && ' Points and rank may still change until FPL finalises the round.'}
        </p>
      </> : <p className="score-copy">{linked.busy ? 'Loading your FPL result…' :
        linked.entryId ? 'Your official result is unavailable for this week. We’ll show it when FPL data is available.' :
          'Link your FPL team in My squad to see your points and compare your picks.'}</p>}
      {linked.err && <p className="score-footnote">FPL could not be reached. Any saved results are labelled above.</p>}
    </section>

    <section className="panel" aria-labelledby="score-advice-title">
      <div className="panel-hd"><h2 id="score-advice-title">How did the model’s picks compare?</h2></div>
      {!review ? <p className="score-copy">The Gameweek {selectedGw ?? ''} model review is not ready yet.
        {latest ? ` Reviews currently run through Gameweek ${latest.gw}.` : ' Reviews appear after a gameweek is graded.'}
        {' '}Your FPL result can appear before the model review.</p> : <>
        <div className="score-copy">
          <p className="score-verdict">{difference == null ? 'No matching lineup comparison for your team this week.'
            : difference === 0 ? 'The model’s starting eleven matched your eleven’s points.'
              : `The model’s starting eleven scored ${Math.abs(difference)} ${Math.abs(difference) === 1 ? 'point' : 'points'} ${difference > 0 ? 'more' : 'fewer'} than yours.`}</p>
          <p>This compares the players picked before the deadline using their basic points.
            Captain multipliers, automatic substitutes, chips and transfer hits are excluded from this comparison.</p>
          {!ownReview && <p>The archived picks below belong to another or unverified team. Your comparison is unavailable.</p>}
          {!review.checked && <p>Provisional review: points may still change.</p>}
        </div>
        <div className="score-comparison">
          <div><span>Pick</span><strong>Model’s choice</strong><strong>Your choice</strong><span>Best with hindsight</span></div>
          <div><strong>Captain</strong><span>{pick(review.captain?.model)}</span><span>{ownReview ? pick(review.captain?.yours) : DASH}</span><span>{pick(review.captain?.best)}</span></div>
          <div><strong>Starting eleven</strong><span>{count(review.xi?.model)} pts</span><span>{ownReview ? count(review.xi?.yours) : DASH} pts</span><span>{count(review.xi?.best)} pts</span></div>
        </div>
        <p className="score-footnote">“Best with hindsight” means the highest scoring choice from the archived squad after seeing the results.
          It is a reference, not a score we could have known in advance.</p>
      </>}
      <div className="score-learning">
        <strong>What we can learn so far</strong>
        <p>{`${gws.length} gameweek${gws.length === 1 ? ' has' : 's have'} been reviewed. Look for repeated patterns across weeks; these results alone do not prove a reliable advantage.`}
          {' '}This page measures lineup choices and forecast accuracy. It does not yet measure whether the transfer strategy improved your season score.</p>
      </div>
    </section>

    {s && gws.length > 0 ? <>
      <section className="panel" aria-labelledby="score-accuracy-title">
        <div className="panel-hd"><h2 id="score-accuracy-title">How accurate are the predictions?</h2>
          <span className="score-tag">{gws.length} reviewed weeks</span></div>
        <p className="score-copy">These stats cover players across the league. Technical names are kept alongside the explanations.
          “Likely starters” means players given at least a 60% chance of starting. A dash means no measurement is available.</p>
        <div className="score-metrics">
          <Metric label="Player ranking" value={num(s.spearman_starters)} detail="Rank correlation · higher is better">
            How well the predicted player order matched the results. 1 is perfect, 0 means no rank relationship; this is not a percentage accuracy.
            {' '}Wider player pool: {num(s.spearman_pool)}.
          </Metric>
          <Metric label="Average points miss" value={`${num(s.mae_starters)} pts`} detail="Mean absolute error (MAE) · lower is better">
            Average distance between predicted and actual points per likely starter.
            {' '}Bias: {signed(s.bias_starters)} pts. Negative means predictions were too high on average; positive means too low.
          </Metric>
          <Metric label="Clean-sheet predictions" value={num(s.cs_brier, 3)} detail="Clean-sheet Brier score · lower is better">
            Checks the predicted chances against whether teams kept a clean sheet. 0 is perfect; this is not a points score.
            {' '}Predicted clean-sheet rate: {pct(s.cs_predicted_rate)}. Actual: {pct(s.cs_actual_rate)}.
          </Metric>
          <Metric label="Who starts?" value={num(s.start_brier, 3)} detail="Start Brier score · lower is better">
            How accurate the players’ starting chances were. 0 is perfect. A confident prediction gets a larger penalty when wrong.
          </Metric>
          <Metric label="Who gets on the pitch?" value={num(s.appearance_brier, 3)} detail="Appearance Brier score · lower is better">
            Checks the chance of playing at all, including coming on as a substitute. 0 is perfect.
          </Metric>
          <Metric label="Average playing-time miss" value={`${num(s.minutes_mae, 1)} min`} detail="Minutes MAE · lower is better">
            How far predicted minutes were from actual minutes, on average.
            {' '}Bias: {signed(s.minutes_bias, 1)} min. Negative means players played less than predicted.
          </Metric>
          <Metric label="Improvement in predicting starts" value={signed(s.start_brier_lift, 3)} detail="Start lift vs baseline · positive is better">
            Reduction in prediction error compared with the pre-season starting estimate.
            {' '}That older estimate’s Brier score: {num(s.baseline_start_brier, 3)}. This is an accuracy change, not extra FPL points.
          </Metric>
        </div>
        <div className="score-copy score-averages">
          <h3>Average points from the picks</h3>
          <p>Basic points before captain bonuses, substitutions, chips or hits. Each average uses the weeks with that measurement available.</p>
          <div className="tbl-scroll" tabIndex={0} role="region" aria-label="Average captain and starting eleven points">
            <table><thead><tr><th scope="col" className="l">Pick</th><th scope="col">Model</th><th scope="col">Yours</th><th scope="col">Best with hindsight</th></tr></thead>
              <tbody><tr><th scope="row" className="l">Captain</th><td>{num(s.captain_model, 1)}</td><td>{num(avg(reviewedOwn.map(g => g.captain?.yours?.pts)), 1)}</td><td>{num(s.captain_best, 1)}</td></tr>
                <tr><th scope="row" className="l">Starting eleven (XI)</th><td>{num(s.xi_model, 1)}</td><td>{num(avg(reviewedOwn.map(g => g.xi?.yours)), 1)}</td><td>{num(s.xi_best, 1)}</td></tr></tbody>
            </table>
          </div>
          <p className="score-footnote">Your averages use only submissions for team {linked.entryId || 'not linked'}:
            {' '}{reviewedOwn.filter(g => g.captain?.yours != null).length} captain reviews and {reviewedOwn.filter(g => g.xi?.yours != null).length} starting-eleven reviews.</p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-hd"><h2>All stats by gameweek</h2></div>
        <p className="score-copy">Explore the picks and prediction checks for each reviewed week. On a phone, swipe each table sideways.</p>
        <details className="score-disclosure">
          <summary>Captain and starting-eleven points</summary>
          <p className="score-copy">Basic points, with each player counted once. Captain multipliers and substitutions are not applied; chips and hits are excluded.</p>
          <div className="tbl-scroll" tabIndex={0} role="region" aria-label="Picks by gameweek">
            <table><thead><tr><th scope="col" className="l">Week</th><th scope="col">Captain · model</th><th scope="col">Captain · yours</th><th scope="col">Captain · hindsight</th><th scope="col">Eleven · model</th><th scope="col">Eleven · yours</th><th scope="col">Eleven · hindsight</th></tr></thead>
              <tbody>{gws.map(g => <tr key={g.gw}><th scope="row" className="l">GW{g.gw}{!g.checked && ' *'}</th>
                <td>{pick(g.captain?.model)}</td><td>{isOwnReview(g, linked.entryId) ? pick(g.captain?.yours) : DASH}</td><td>{pick(g.captain?.best)}</td>
                <td>{count(g.xi?.model)}</td><td>{isOwnReview(g, linked.entryId) ? count(g.xi?.yours) : DASH}</td><td>{count(g.xi?.best)}</td></tr>)}</tbody>
            </table>
          </div>
        </details>
        <details className="score-disclosure">
          <summary>Prediction accuracy for every week</summary>
          <p className="score-copy">“Top 20” is the model’s 20 highest rated players. Their average score measures how those picks did;
            “in actual top 50” counts how many finished among the 50 highest scorers. Higher is better for both.
            The wider pool includes available players projected to score at least 0.5 points.</p>
          <div className="tbl-scroll" tabIndex={0} role="region" aria-label="Prediction accuracy by gameweek">
            <table><thead><tr><th scope="col" className="l">Week</th><th scope="col">Ranking · likely starters ↑</th><th scope="col">Ranking · wider pool ↑</th><th scope="col">Points miss ↓</th><th scope="col">Top 20 · avg points ↑</th><th scope="col">Top 20 · in actual top 50 ↑</th><th scope="col">Clean-sheet Brier ↓</th><th scope="col">Start Brier ↓</th><th scope="col">Appearance Brier ↓</th><th scope="col">Minutes miss ↓</th></tr></thead>
              <tbody>{gws.map(g => <tr key={g.gw}><th scope="row" className="l">GW{g.gw}{!g.checked && ' *'}</th>
                <td>{num(g.spearman_starters)}</td><td>{num(g.spearman_pool)}</td><td>{num(g.mae_starters)}</td>
                <td>{num(g.top20_mean_actual, 1)}</td><td>{g.top20_in_actual_top50}/20</td><td>{num(g.cs?.brier, 3)}</td>
                <td>{num(g.availability?.start_brier, 3)}</td><td>{num(g.availability?.appearance_brier, 3)}</td><td>{num(g.availability?.minutes_mae, 1)}</td></tr>)}</tbody>
            </table>
          </div>
        </details>
        <p className="score-footnote">↑ Higher is better. ↓ Lower is better. * Provisional: FPL had not finalised the points when graded.</p>
      </section>

      {calibration && calibration.deciles.length > 0 && <section className="panel">
        <div className="panel-hd"><h2>Do predicted points match reality?</h2><span className="score-tag">{review ? '' : 'Latest review · '}Gameweek {calibration.gw}</span></div>
        <p className="score-copy">Calibration groups likely starters into ten bands, from lowest to highest predicted points.
          Compare each band’s predicted and actual average: closer is better. “Players” is the number in that band.</p>
        <div className="tbl-scroll" tabIndex={0} role="region" aria-label="Calibration by predicted points">
          <table><thead><tr><th scope="col" className="l">Band (decile)</th><th scope="col">Predicted range</th><th scope="col">Predicted avg</th><th scope="col">Actual avg</th><th scope="col">Players</th></tr></thead>
            <tbody>{calibration.deciles.map((d, i) => <tr key={i}><th scope="row" className="l">{i + 1}{i === 0 ? ' · lowest' : i === 9 ? ' · highest' : ''}</th>
              <td>{num(d.lo, 1)}–{num(d.hi, 1)}</td><td>{num(d.proj)}</td><td>{num(d.actual)}</td><td>{d.n}</td></tr>)}</tbody>
          </table>
        </div>
      </section>}
      <details className="score-disclosure score-method">
        <summary>Original methodology notes</summary>
        <div className="score-copy">{sc.notes.map(n => <p key={n}>{n}</p>)}</div>
      </details>
      <p className="score-footnote">Model review updated {date(sc.generated)}. Your FPL results are loaded separately, so they may cover a newer week.</p>
    </> : <section className="panel"><div className="panel-hd"><h2>Model accuracy</h2></div>
      <p className="score-copy">No gameweeks have been reviewed yet. Player rankings, points and minutes errors, probability scores and calibration will appear here once results are graded.</p></section>}
  </div>
}
