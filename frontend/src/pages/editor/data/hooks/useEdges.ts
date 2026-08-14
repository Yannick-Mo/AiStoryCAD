import { useCallback } from 'react'
import type { Chapter, ChapterEdge, EdgeType, EdgeResult, EditorMockData } from '../../types'
import type { ChangeEntry } from '../editorStore'
import { topologicalSort, wouldCreateCycle, hasIncomingTimeline, hasOutgoingTimeline } from '../orderUtils'

export function useEdges(
  data: { chapters: Chapter[]; edges: ChapterEdge[] } | null,
  setData: (updater: (prev: EditorMockData | null) => EditorMockData | null) => void,
  projectId: string,
  enqueueChange: (c: ChangeEntry) => void,
) {
  const reSort = useCallback((chapters: Chapter[], edges: ChapterEdge[]) => {
    const ordered = topologicalSort(chapters, edges)
    const map = new Map(chapters.map(c => [c.id, c]))
    return ordered.map(id => map.get(id)!).filter(Boolean)
  }, [])

  const addEdge = useCallback((sourceId: string, targetId: string, type: EdgeType = 'timeline', sourceHandle?: string, targetHandle?: string): EdgeResult => {
    if (!data) return { edge: null }
    let result: EdgeResult = { edge: null }
    setData(d => {
      if (!d) return d
      if (type === 'timeline') {
        if (wouldCreateCycle(d.edges, sourceId, targetId)) { result = { edge: null, cycle: true }; return d }
        const filtered = d.edges.filter(e => !(e.type === 'timeline' && e.sourceId === sourceId && e.targetId === targetId))
        const newEdge: ChapterEdge = { id: crypto.randomUUID(), sourceId, targetId, type, sourceHandle, targetHandle }
        result = { edge: newEdge }
        return { ...d, edges: [...filtered, newEdge], chapters: reSort(d.chapters, [...filtered, newEdge]) }
      }
      if (wouldCreateCycle(d.edges, sourceId, targetId)) { result = { edge: null, cycle: true }; return d }
      const newEdge: ChapterEdge = { id: crypto.randomUUID(), sourceId, targetId, type, sourceHandle, targetHandle }
      result = { edge: newEdge }
      return { ...d, edges: [...d.edges, newEdge] }
    })
    if (result.edge) {
      enqueueChange({ entity: 'edges', op: 'create', data: { id: result.edge.id, project_id: projectId, source_id: sourceId, target_id: targetId, edge_type: type, source_handle: sourceHandle || '', target_handle: targetHandle || '' } })
    }
    return result
  }, [data, projectId, reSort, enqueueChange, setData])

  const deleteEdge = useCallback((edgeId: string) => {
    if (!data) return
    setData(d => {
      if (!d) return d
      const edge = d.edges.find(e => e.id === edgeId)
      if (!edge) return d
      const newEdges = d.edges.filter(e => e.id !== edgeId)
      if (edge.type === 'timeline') return { ...d, edges: newEdges, chapters: reSort(d.chapters, newEdges) }
      return { ...d, edges: newEdges }
    })
    enqueueChange({ entity: 'edges', op: 'delete', id: edgeId })
  }, [data, reSort, enqueueChange, setData])

  const changeEdgeType = useCallback((edgeId: string, newType: EdgeType): boolean => {
    if (!data) return false
    let blocked = false
    setData(d => {
      if (!d) return d
      const edge = d.edges.find(e => e.id === edgeId)
      if (!edge) return d
      if (newType === 'timeline' && edge.type !== 'timeline') {
        if (hasOutgoingTimeline(d.edges, edge.sourceId) || hasIncomingTimeline(d.edges, edge.targetId)) { blocked = true; return d }
      }
      return { ...d, edges: d.edges.map(e => e.id === edgeId ? { ...e, type: newType } : e) }
    })
    if (!blocked) enqueueChange({ entity: 'edges', op: 'update', data: { id: edgeId, edge_type: newType } })
    return !blocked
  }, [data, enqueueChange, setData])

  const reconnectEdge = useCallback((edgeId: string, newSource?: string, newTarget?: string, sourceHandle?: string, targetHandle?: string): boolean => {
    if (!data) return false
    let blocked = false
    setData(d => {
      if (!d) return d
      const edge = d.edges.find(e => e.id === edgeId)
      if (!edge) return d
      const source = newSource ?? edge.sourceId; const target = newTarget ?? edge.targetId
      if (wouldCreateCycle(d.edges.filter(e => e.id !== edgeId), source, target)) { blocked = true; return d }
      if (edge.type === 'timeline') {
        const filtered = d.edges.filter(e => e.id === edgeId || !(e.type === 'timeline' && e.sourceId === source && e.targetId === target))
        return {
          ...d,
          edges: filtered.map(e => e.id === edgeId ? { ...e, sourceId: source, targetId: target, sourceHandle: sourceHandle ?? e.sourceHandle, targetHandle: targetHandle ?? e.targetHandle } : e),
          chapters: reSort(d.chapters, filtered.map(e => e.id === edgeId ? { ...e, sourceId: source, targetId: target } : e)),
        }
      }
      return { ...d, edges: d.edges.map(e => e.id === edgeId ? { ...e, sourceId: source, targetId: target, sourceHandle: sourceHandle ?? e.sourceHandle, targetHandle: targetHandle ?? e.targetHandle } : e) }
    })
    if (!blocked) {
      const updates: Record<string, unknown> = { id: edgeId }
      if (newSource) updates.source_id = newSource
      if (newTarget) updates.target_id = newTarget
      if (sourceHandle) updates.source_handle = sourceHandle
      if (targetHandle) updates.target_handle = targetHandle
      enqueueChange({ entity: 'edges', op: 'update', data: updates })
    }
    return !blocked
  }, [data, reSort, enqueueChange, setData])

  const updateEdge = useCallback((id: string, updates: Partial<Pick<ChapterEdge, 'label'>>) => {
    setData(d => d ? { ...d, edges: d.edges.map(e => e.id === id ? { ...e, ...updates } : e) } : d)
    enqueueChange({ entity: 'edges', op: 'update', data: { id, ...updates } })
  }, [enqueueChange, setData])

  return { addEdge, deleteEdge, changeEdgeType, reconnectEdge, updateEdge, reSort }
}
