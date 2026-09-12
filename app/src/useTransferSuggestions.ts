import { useEffect, useMemo, useState } from 'react'
import type { Player } from './types'
import type { TransferOption } from './model'
import type { SellingValues } from './transferBudget'

/** A new scenario cancels its predecessor; stale worker results never appear. */
export function useTransferSuggestions(squad: Player[], pool: Player[], bank: number | null,
  ft: number, gw: number, horizon: number, values: SellingValues | null) {
  // Reconstructed account objects must not restart work on countdown ticks.
  const squadKey = JSON.stringify(squad)
  const valuesKey = values ? JSON.stringify(values) : null
  const context = useMemo(() => ({ squad: JSON.parse(squadKey) as Player[], pool,
    bank, ft, gw, horizon, values: valuesKey ? JSON.parse(valuesKey) as SellingValues : null }),
    [squadKey, pool, bank, ft, gw, horizon, valuesKey])
  const [result, setResult] = useState<{ context?: typeof context; options: TransferOption[]; loading: boolean; error?: string }>({ options: [], loading: false })
  useEffect(() => {
    const { squad, pool, bank, ft, gw, horizon, values } = context
    if (!values || bank == null || bank < 0) {
      setResult({ context, options: [], loading: false })
      return
    }
    const worker = new Worker(new URL('./transferWorker.ts', import.meta.url), { type: 'module' })
    let active = true
    setResult({ context, options: [], loading: true })
    worker.onmessage = event => {
      if (active) setResult({ context, options: event.data.options ?? [], loading: false, error: event.data.error })
      worker.terminate()
    }
    worker.onerror = () => {
      if (active) setResult({ context, options: [], loading: false, error: 'The transfer comparison could not be completed.' })
      worker.terminate()
    }
    worker.postMessage({ squad, pool, bank, ft, gw, horizon, values })
    return () => { active = false; worker.terminate() }
  }, [context])
  return result.context === context ? result : { options: [], loading: values != null && bank != null && bank >= 0 }
}
