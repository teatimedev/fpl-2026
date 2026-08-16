import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // In production `/api/fpl?path=…` is the Vercel function in api/fpl.ts.
    // `vite dev` does not run serverless functions, so without this the dev
    // server hands the page the TypeScript source and This Week shows
    // "Could not reach the FPL API". Forward to the real API instead — the
    // browser never sees the cross-origin call, so CORS is not an issue.
    proxy: {
      '/api/fpl': {
        target: 'https://fantasy.premierleague.com',
        changeOrigin: true,
        headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)' },
        rewrite: (url) => {
          const q = new URL(url, 'http://x').searchParams.get('path') ?? ''
          return `/api/${q}`
        },
      },
    },
  },
})
