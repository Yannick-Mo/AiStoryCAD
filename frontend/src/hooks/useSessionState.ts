import { useCallback, useState } from 'react'

const cache = new Map<string, unknown>()

/**
 * Like useState, but the value survives the component being unmounted. The dock
 * remounts a panel whenever it is floated, docked or moved in the split tree,
 * so in-progress edits would otherwise be lost.
 */
export function useSessionState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => (cache.has(key) ? (cache.get(key) as T) : initial))

  const set = useCallback((next: T | ((prev: T) => T)) => {
    setValue(prev => {
      const resolved = typeof next === 'function' ? (next as (p: T) => T)(prev) : next
      cache.set(key, resolved)
      return resolved
    })
  }, [key])

  return [value, set] as const
}