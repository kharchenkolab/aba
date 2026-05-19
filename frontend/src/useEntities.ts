import { useCallback, useEffect, useState } from 'react'
import type { Entity } from './types'

export function useEntities() {
  const [entities, setEntities] = useState<Entity[]>([])

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/entities')
      if (!r.ok) return
      const rows: Entity[] = await r.json()
      setEntities(rows)
    } catch {
      // network error — leave previous state
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { entities, refresh }
}
