import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import http from 'node:http'

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

export default defineConfig({
  plugins: [react()],
  server: {
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
  }
})
