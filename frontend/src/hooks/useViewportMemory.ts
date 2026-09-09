import { useCallback, useState } from 'react'
import type { Viewport } from 'reactflow'

const PREFIX = 'aistorycad_viewport_'

function load(key: string): Viewport | null {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    if (!raw) return null
    const saved = JSON.parse(raw)
    return typeof saved?.zoom === 'number' && typeof saved?.x === 'number' && typeof saved?.y === 'number'
      ? saved
      : null
  } catch {
    return null
  }
}

/**
 * Remembers a canvas pan/zoom. The dock remounts a panel whenever it floats,
 * docks or moves in the split tree, and reactflow would otherwise re-run
 * fitView and throw away the viewport.
 */
export function useViewportMemory(key: string) {
  const [initial] = useState<Viewport | null>(() => load(key))

  const onMoveEnd = useCallback((_: unknown, viewport: Viewport) => {
    try { localStorage.setItem(PREFIX + key, JSON.stringify(viewport)) } catch { /* ignore */ }
  }, [key])

  return { initial, onMoveEnd }
}