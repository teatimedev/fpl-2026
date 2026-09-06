"""Synthetic multi-transfer control experiment, not a football backtest.

Three independent opportunities each week; true gains use the existing
stylised mixture. Compare fixed barriers to posterior mean decisions and
a finite-horizon Bellman policy that prices the free-transfer bank once.
The Bayesian policies know the synthetic data-generating distribution.
"""
import json
import sys
from pathlib import Path

import numpy as np
from numpy.polynomial.hermite import hermgauss

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'v2'))
from threshold_sweep import draw_gains, MIXTURE


def posterior_grid(sigma):
    nodes, weights = hermgauss(80)
    atoms, masses = [], []
    for probability, mean, sd in MIXTURE:
        atoms.extend(np.clip(mean+np.sqrt(2)*sd*nodes,-2,12))
        masses.extend(probability*weights/np.sqrt(np.pi))
    atoms, masses = np.array(atoms), np.array(masses)
    grid = np.linspace(-40,50,1801)
    likelihood = np.exp(-.5*((grid[:,None]-atoms)/sigma)**2)*masses
    means = likelihood@atoms/likelihood.sum(axis=1)
    return grid, means


def prefix(values):
    return np.concatenate([np.zeros((*values.shape[:-1],1)),np.cumsum(values,axis=-1)],axis=-1)


def main():
    sigma, weeks, n_seasons = 5.16, 38, 20000
    grid, posterior = posterior_grid(sigma)
    rng = np.random.default_rng(60209)
    train_g = draw_gains(rng,(100000,3))
    train_obs = np.sort(train_g+sigma*rng.standard_normal(train_g.shape),axis=1)[:,::-1]
    train_prefix = prefix(np.interp(train_obs,grid,posterior))
    actions = np.arange(4)
    value = np.zeros((weeks+1,6))
    for remaining in range(1,weeks+1):
        for ft in range(6):
            nxt = np.minimum(5,np.maximum(ft-actions,0)+1)
            q = train_prefix-4*np.maximum(actions-ft,0)+value[remaining-1,nxt]
            value[remaining,ft] = q.max(axis=1).mean()
    # Independent evaluation sample, common random numbers across policies.
    rng = np.random.default_rng(20260906)
    truth = draw_gains(rng,(n_seasons,weeks,3))
    obs = truth+sigma*rng.standard_normal(truth.shape)
    order = np.argsort(-obs,axis=-1)
    obs = np.take_along_axis(obs,order,axis=-1)
    truth = np.take_along_axis(truth,order,axis=-1)
    true_prefix = prefix(truth)
    estimates = {'fixed_2':prefix(obs), 'no_bar':prefix(obs),
                 'posterior_myopic':prefix(np.interp(obs,grid,posterior)),
                 'posterior_dynamic':prefix(np.interp(obs,grid,posterior))}
    results, totals = {}, {}
    for policy, pred in estimates.items():
        ft = np.ones(n_seasons,dtype=int)
        total = np.zeros(n_seasons)
        transfers = np.zeros(n_seasons)
        hits = np.zeros(n_seasons)
        for t in range(weeks):
            hit = np.maximum(actions[None,:]-ft[:,None],0)
            nxt = np.minimum(5,np.maximum(ft[:,None]-actions[None,:],0)+1)
            q = pred[:,t,:]-4*hit
            if policy=='fixed_2':
                q -= 2*actions
            if policy=='posterior_dynamic':
                q += value[weeks-t-1,nxt]
            chosen = q.argmax(axis=1)
            index = np.arange(n_seasons)
            total += true_prefix[index,t,chosen]-4*hit[index,chosen]
            transfers += chosen
            hits += hit[index,chosen]
            ft = nxt[index,chosen]
        totals[policy] = total
        results[policy] = dict(mean_gain=total.mean(), transfers=transfers.mean(),
                               paid_transfers=hits.mean(),p10=np.quantile(total,.1))
    for policy,total in totals.items():
        diff = total-totals['fixed_2']
        results[policy]['gain_vs_fixed_2_ci95'] = [diff.mean()-1.96*diff.std(ddof=1)/n_seasons**.5,
                                                  diff.mean()+1.96*diff.std(ddof=1)/n_seasons**.5]
    report = dict(method=__doc__,sigma=sigma,seasons=n_seasons,weeks=weeks,results=results,
                  ft_marginal_value_6_weeks=np.diff(value[6]).tolist(),
                  ft_marginal_value_1_week=np.diff(value[1]).tolist())
    out = ROOT/'research'/'experiments-2026-09-06'/'decision.json'
    out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)
    print('DECISION DONE',flush=True)


if __name__=='__main__':
    main()
