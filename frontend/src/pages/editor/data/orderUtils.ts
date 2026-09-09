import type { Chapter, ChapterEdge, Act } from '../types'

/**
 * Chapters in reading order: act order first, then the in-act ``sortOrder``.
 *
 * ``sort_order`` is the single source of truth for order everywhere (outline,
 * canvas, preview, export and the agent's own context).  Timeline edges only
 * describe narrative relations — causal / foreshadow / character arcs — and
 * deliberately do *not* reorder the manuscript, so there is exactly one
 * ordering to reason about.  Ties fall back to the incoming array order, which
 * the API already returns in narrative order.
 */
export function orderChapters(chapters: Chapter[], acts: Act[]): Chapter[] {
  const actIndex = new Map(
    [...acts].sort((a, b) => a.order - b.order).map((act, i) => [act.id, i]),
  )
  return chapters
    .map((chapter, index) => ({ chapter, index }))
    .sort((a, b) => {
      const actA = actIndex.get(a.chapter.actId) ?? Number.MAX_SAFE_INTEGER
      const actB = actIndex.get(b.chapter.actId) ?? Number.MAX_SAFE_INTEGER
      if (actA !== actB) return actA - actB
      const orderA = a.chapter.sortOrder ?? 0
      const orderB = b.chapter.sortOrder ?? 0
      if (orderA !== orderB) return orderA - orderB
      return a.index - b.index
    })
    .map(entry => entry.chapter)
}

/** Chapters of a single act in reading order. */
export function orderActChapters(chapters: Chapter[]): Chapter[] {
  return chapters
    .map((chapter, index) => ({ chapter, index }))
    .sort((a, b) => {
      const orderA = a.chapter.sortOrder ?? 0
      const orderB = b.chapter.sortOrder ?? 0
      if (orderA !== orderB) return orderA - orderB
      return a.index - b.index
    })
    .map(entry => entry.chapter)
}

export function wouldCreateCycle(edges: ChapterEdge[], sourceId: string, targetId: string): boolean {
  if (sourceId === targetId) return true
  const adj = new Map<string, string[]>()
  const allIds = new Set<string>()
  for (const e of edges) {
    if (e.sourceId === sourceId && e.targetId === targetId) continue
    if (!adj.has(e.sourceId)) adj.set(e.sourceId, [])
    adj.get(e.sourceId)!.push(e.targetId)
    allIds.add(e.sourceId)
    allIds.add(e.targetId)
  }
  allIds.add(sourceId)
  allIds.add(targetId)
  if (!adj.has(sourceId)) adj.set(sourceId, [])
  adj.get(sourceId)!.push(targetId)

  const visited = new Set<string>()
  const stack = [targetId]
  while (stack.length > 0) {
    const id = stack.pop()!
    if (id === sourceId) return true
    if (visited.has(id)) continue
    visited.add(id)
    for (const next of adj.get(id) ?? []) {
      stack.push(next)
    }
  }
  return false
}

export function hasIncomingTimeline(edges: ChapterEdge[], nodeId: string): boolean {
  return edges.some(e => e.type === 'timeline' && e.targetId === nodeId)
}

export function hasOutgoingTimeline(edges: ChapterEdge[], nodeId: string): boolean {
  return edges.some(e => e.type === 'timeline' && e.sourceId === nodeId)
}

