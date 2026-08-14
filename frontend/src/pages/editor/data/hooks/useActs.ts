import { useCallback } from 'react'
import type { Act, EditorMockData } from '../../types'
import type { ChangeEntry } from '../editorStore'

const COLORS = ['#f97316', '#8b5cf6', '#06b6d4', '#ec4899', '#10b981', '#eab308']

export function useActs(
  data: { acts: Act[] } | null,
  setData: (updater: (prev: EditorMockData | null) => EditorMockData | null) => void,
  projectId: string,
  enqueueChange: (c: ChangeEntry) => void,
  clearSelection: () => void,
) {
  const addAct = useCallback((name?: string) => {
    if (!data) throw new Error("Store not initialized")
    const id = crypto.randomUUID()
    const maxOrder = data.acts.reduce((m, a) => Math.max(m, a.order), 0)
    const newAct: Act = {
      id, name: name ?? `第 ${data.acts.length + 1} 幕`,
      order: maxOrder + 1,
      color: COLORS[data.acts.length % COLORS.length],
    }
    setData(d => d ? { ...d, acts: [...d.acts, newAct] } : d)
    enqueueChange({ entity: 'acts', op: 'create', data: { id, project_id: projectId, name: newAct.name, sort_order: newAct.order, color: newAct.color } })
    return newAct
  }, [data, projectId, enqueueChange, setData])

  const deleteAct = useCallback((actId: string) => {
    if (!data) return
    setData(d => {
      if (!d) return d
      const chapterIds = new Set(d.chapters.filter(c => c.actId === actId).map(c => c.id))
      return {
        ...d,
        acts: d.acts.filter(a => a.id !== actId),
        chapters: d.chapters.filter(c => c.actId !== actId),
        edges: d.edges.filter(e => !chapterIds.has(e.sourceId) && !chapterIds.has(e.targetId)),
      }
    })
    enqueueChange({ entity: 'acts', op: 'delete', id: actId })
    clearSelection()
  }, [data, enqueueChange, clearSelection, setData])

  const updateAct = useCallback((id: string, updates: Partial<Pick<Act, 'name' | 'color'>>) => {
    setData(d => d ? { ...d, acts: d.acts.map(a => a.id === id ? { ...a, ...updates } : a) } : d)
    enqueueChange({ entity: 'acts', op: 'update', data: { id, ...updates } })
  }, [enqueueChange, setData])

  const resizeAct = useCallback((actId: string, width: number, height: number) => {
    setData(d => d ? { ...d, acts: d.acts.map(a => a.id === actId ? { ...a, width, height } : a) } : d)
    enqueueChange({ entity: 'acts', op: 'update', data: { id: actId, width, height } })
  }, [enqueueChange, setData])

  return { addAct, deleteAct, updateAct, resizeAct }
}
