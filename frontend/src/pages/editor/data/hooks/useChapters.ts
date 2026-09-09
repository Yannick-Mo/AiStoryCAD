import { useCallback } from 'react'
import type { Chapter, Scene, EditorMockData } from '../../types'
import type { ChangeEntry } from '../editorStore'
import { orderActChapters } from '../orderUtils'

export function useChapters(
  data: { chapters: Chapter[] } | null,
  setData: (updater: (prev: EditorMockData | null) => EditorMockData | null) => void,
  projectId: string,
  enqueueChange: (c: ChangeEntry) => void,
) {
  const addChapter = useCallback((actId: string) => {
    if (!data) throw new Error("Store not initialized")
    const id = crypto.randomUUID()
    const actChapters = data.chapters.filter(c => c.actId === actId)
    const maxOrder = actChapters.reduce((m, c) => Math.max(m, c.sortOrder ?? 0), 0)
    const newCh: Chapter = {
      id, actId,
      title: `第 ${actChapters.length + 1} 章`,
      goal: '', wordCount: 0, status: 'draft', scenes: [], sortOrder: maxOrder + 1,
    }
    setData(d => d ? { ...d, chapters: [...d.chapters, newCh] } : d)
    enqueueChange({
      entity: 'chapters', op: 'create',
      data: { id, project_id: projectId, act_id: actId, title: newCh.title, sort_order: maxOrder + 1 },
    })
    return newCh
  }, [data, projectId, enqueueChange, setData])

  const deleteChapter = useCallback((chapterId: string) => {
    if (!data) return
    const ch = data.chapters.find(c => c.id === chapterId)
    if (ch) {
      for (const scene of ch.scenes) {
        enqueueChange({ entity: 'scenes', op: 'delete', id: scene.id })
      }
    }
    setData(d => d ? {
      ...d,
      chapters: d.chapters.filter(c => c.id !== chapterId),
      edges: d.edges.filter(e => e.sourceId !== chapterId && e.targetId !== chapterId),
    } : d)
    enqueueChange({ entity: 'chapters', op: 'delete', id: chapterId })
  }, [data, enqueueChange, setData])

  const updateChapter = useCallback((id: string, updates: Partial<Pick<Chapter, 'title' | 'goal' | 'status'>>) => {
    setData(d => d ? { ...d, chapters: d.chapters.map(c => c.id === id ? { ...c, ...updates } : c) } : d)
    enqueueChange({ entity: 'chapters', op: 'update', data: { id, ...updates } })
  }, [enqueueChange, setData])

  const addScene = useCallback((chapterId: string) => {
    if (!data) throw new Error("Store not initialized")
    const id = crypto.randomUUID()
    const ch = data.chapters.find(c => c.id === chapterId)
    const order = ch ? ch.scenes.length + 1 : 1
    const newScene: Scene = {
      id, chapter_id: chapterId,
      title: `场景 ${order}`, povCharacter: '', setting: '', time: '', summary: '', content: '', wordCount: 0,
    }
    setData(d => d ? {
      ...d,
      chapters: d.chapters.map(c => c.id === chapterId ? { ...c, scenes: [...c.scenes, newScene] } : c),
    } : d)
    enqueueChange({
      entity: 'scenes', op: 'create',
      data: { id, project_id: projectId, chapter_id: chapterId, title: newScene.title, sort_order: order },
    })
    return newScene
  }, [data, projectId, enqueueChange, setData])

  const deleteScene = useCallback((chapterId: string, sceneId: string) => {
    if (!data) return
    setData(d => d ? {
      ...d,
      chapters: d.chapters.map(ch => ch.id === chapterId ? {
        ...ch,
        scenes: ch.scenes.filter(s => s.id !== sceneId),
        wordCount: ch.scenes.filter(s => s.id !== sceneId).reduce((sum, s) => sum + s.wordCount, 0),
      } : ch),
    } : d)
    enqueueChange({ entity: 'scenes', op: 'delete', id: sceneId })
  }, [data, enqueueChange, setData])

  const updateScene = useCallback((chapterId: string, sceneId: string, updates: Partial<Pick<Scene, 'title' | 'povCharacter' | 'setting' | 'time' | 'summary'>>) => {
    setData(d => d ? {
      ...d,
      chapters: d.chapters.map(ch => ch.id === chapterId ? {
        ...ch,
        scenes: ch.scenes.map(s => s.id === sceneId ? { ...s, ...updates } : s),
      } : ch),
    } : d)
    const backendData: Record<string, unknown> = { id: sceneId }
    if (updates.title !== undefined) backendData.title = updates.title
    if (updates.povCharacter !== undefined) backendData.pov_character = updates.povCharacter
    if (updates.setting !== undefined) backendData.setting = updates.setting
    if (updates.time !== undefined) backendData.scene_time = updates.time
    if (updates.summary !== undefined) backendData.summary = updates.summary
    enqueueChange({ entity: 'scenes', op: 'update', data: backendData })
  }, [enqueueChange, setData])

  const moveChapter = useCallback((chapterId: string, direction: -1 | 1) => {
    if (!data) return
    const chapter = data.chapters.find(c => c.id === chapterId)
    if (!chapter) return
    const siblings = orderActChapters(data.chapters.filter(c => c.actId === chapter.actId))
    const index = siblings.findIndex(c => c.id === chapterId)
    const swapIndex = index + direction
    if (index < 0 || swapIndex < 0 || swapIndex >= siblings.length) return

    const reordered = [...siblings]
    ;[reordered[index], reordered[swapIndex]] = [reordered[swapIndex], reordered[index]]
    // sort_order 是顺序的唯一真相：幕内重新编号 1..N 并落库，这样刷新、
    // 导出和 AI 上下文看到的是同一个顺序（以前只改内存，刷新就回去了）。
    const orders = new Map(reordered.map((c, i) => [c.id, i + 1] as const))
    setData(d => d ? {
      ...d,
      chapters: d.chapters.map(c => orders.has(c.id) ? { ...c, sortOrder: orders.get(c.id)! } : c),
    } : d)
    for (const [id, sortOrder] of orders) {
      enqueueChange({ entity: 'chapters', op: 'update', data: { id, sort_order: sortOrder } })
    }
  }, [data, enqueueChange, setData])

  return { addChapter, deleteChapter, updateChapter, addScene, deleteScene, updateScene, moveChapter }
}
