import { rankTransfers } from './model'

self.onmessage = event => {
  try {
    const { squad, pool, bank, ft, gw, horizon, values } = event.data
    self.postMessage({ options: rankTransfers(squad, pool, bank, ft, gw, horizon, 8, values) })
  } catch {
    self.postMessage({ error: 'The transfer comparison could not be completed.' })
  }
}
