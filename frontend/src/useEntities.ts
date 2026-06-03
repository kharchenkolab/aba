import { useCallback, useEffect, useState } from 'react'
import type { Entity } from './types'

export function useEntities(projectId?: string) {
  const [entities, setEntities] = useState<Entity[]>([])

  // projectId pinned per-request so a stale backend in-process current can't
  // misroute the read (PK 2026-06-03 — observed cross-project state bleed:
  // chat showed test1 content under test5's project header because /api/entities
  // had no pid and read from whatever the in-process current happened to be).
  const refresh = useCallback(async () => {
    try {
      const url = projectId
        ? `/api/entities?project_id=${encodeURIComponent(projectId)}`
        : '/api/entities'
      const r = await fetch(url)
      if (!r.ok) return
      const rows: Entity[] = await r.json()
      setEntities(rows)
    } catch {
      // network error — leave previous state
    }
  }, [projectId])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { entities, refresh }
}
