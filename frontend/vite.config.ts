import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const _dir = dirname(fileURLToPath(import.meta.url))

// Dev-proxy hang fix (2026-05-31):
//  Without explicit agent options, http-proxy uses Node's default agent
//  with keepAlive=false. With the long-lived /api/chat SSE stream pinning
//  one connection (held open for the entire turn — minutes for R
//  pipelines), polling fetches (advisor-notes, proposals,
//  context-suggestions every 2s) queue behind it on the same agent.
//  After ~30s the browser shows 50+ in-flight fetches that NEVER resolve,
//  even though the backend served them all 200 OK. Symptom: Files tab
//  stuck Loading, chat images don't appear until the turn ends, Results
//  figures missing.
//
//  Explicit Agent with keepAlive=true + maxSockets=Infinity + a generous
//  free-pool size = no socket-pool queuing.
const proxyAgent = new http.Agent({
  keepAlive: true,
  maxSockets: Infinity,
  maxFreeSockets: 256,
})

// altui2 (living-notebook prototype): the Record app (/notebook.html) is fully
// client-side — fixture module + static assets under public/ — so the dev
// server needs no backend. Set ABA_PROXY=1 to restore the /api + /artifacts
// proxy for running the original app against a real backend on :8000.
const withProxy = process.env.ABA_PROXY === '1'

export default defineConfig({
  // Normally served at root ("/"). For an Open OnDemand build we bake a
  // placeholder prefix that the app's script.sh rewrites to /rnode/<host>/<port>/
  // at session start (set ABA_OOD_BASE=/__OOD_PREFIX__/ for `npm run build`).
  base: process.env.ABA_OOD_BASE || '/',
  plugins: [react()],
  // two entries: the classic workspace (index.html) and the Record face
  // (notebook.html) — one build serves both from the same origin
  build: {
    rollupOptions: {
      input: {
        main: resolve(_dir, 'index.html'),
        notebook: resolve(_dir, 'notebook.html'),
      },
    },
  },
  server: withProxy ? {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        agent: proxyAgent,
      },
      '/artifacts': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        agent: proxyAgent,
      },
    }
  } : {}
})
