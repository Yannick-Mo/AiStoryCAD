import { useState, useCallback, useRef, useEffect } from 'react'
import type { EditorMockData } from '../types'
import { loadEditorData, syncEditorData, type SyncPayload } from '../../../api/editor'
import { useActs } from './hooks/useActs'
import { useChapters } from './hooks/useChapters'
import { useEdges } from './hooks/useEdges'
import { useCharacters } from './hooks/useCharacters'
import { useThemes } from './hooks/useThemes'

export interface EditorSelection {
  type: 'act' | 'chapter' | 'edge' | null
  id: string | null
}

export interface ChangeEntry {
  entity: string
  op: 'create' | 'update' | 'delete'
  id?: string
  data?: Record<string, unknown>
}

export function useEditorStore(projectId: string, onFlushError?: (msg: string) => void) {
  const [data, setData] = useState<EditorMockData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selection, setSelection] = useState<EditorSelection>({ type: null, id: null })
  const [dirty, setDirty] = useState(false)
  const [version, setVersion] = useState(0)
  const [saving, setSaving] = useState(false)
  const changesRef = useRef<ChangeEntry[]>([])
  const timerRef = useRef<ReturnType<typeof setTimeout>>()
  const retryTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const periodTimerRef = useRef<ReturnType<typeof setInterval>>()
  const flushAttemptRef = useRef(0)
  const inFlightRef = useRef(false)
  const pendingFlushRef = useRef(false)
  const mountedRef = useRef(false)
  const projectIdRef = useRef(projectId)
  projectIdRef.current = projectId
  const onFlushErrorRef = useRef<((msg: string) => void) | undefined>(onFlushError)
  onFlushErrorRef.current = onFlushError

  useEffect(() => {
    let cancelled = false
    mountedRef.current = true
    if (timerRef.current) clearTimeout(timerRef.current)
    if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
    changesRef.current = []
    flushAttemptRef.current = 0
    setDirty(false)
    setLoading(true)
    setError(null)
    loadEditorData(projectId)
      .then(d => { if (!cancelled && mountedRef.current) { setData(d); setLoading(false) } })
      .catch(e => { if (!cancelled && mountedRef.current) { setError(e.message); setLoading(false) } })
    periodTimerRef.current = setInterval(() => {
      if (changesRef.current.length > 0 && !inFlightRef.current) {
        flushChangesRef.current?.()
      }
    }, 15000)
    return () => {
      cancelled = true
      mountedRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
      if (periodTimerRef.current) clearInterval(periodTimerRef.current)
      changesRef.current = []
    }
  }, [projectId])

  const enqueueChange = useCallback((change: ChangeEntry) => {
    changesRef.current.push(change)
    setDirty(true)
    // Debounce auto-save: restart 3s timer on each change
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => flushChangesRef.current?.(), 3000)
  }, [])

  const flushChangesRef = useRef<() => Promise<boolean>>()

  const flushChanges = useCallback(async (): Promise<boolean> => {
    if (!mountedRef.current) return true
    if (inFlightRef.current) {
      pendingFlushRef.current = true
      return false
    }
    if (projectIdRef.current !== projectId) return true
    if (changesRef.current.length === 0) return true
    inFlightRef.current = true
    setSaving(true)
    const entries = changesRef.current
    changesRef.current = []
    setDirty(false)

    const payload: SyncPayload = {}
    for (const entry of entries) {
      if (!payload[entry.entity]) payload[entry.entity] = { created: [], updated: [], deleted: [] }
      if (entry.op === 'create' && entry.data) payload[entry.entity].created!.push(entry.data)
      if (entry.op === 'update' && entry.data) payload[entry.entity].updated!.push(entry.data)
      if (entry.op === 'delete' && entry.id) payload[entry.entity].deleted!.push(entry.id)
    }

    try {
      const result = await syncEditorData(projectId, payload)
      setVersion(result.version)
      flushAttemptRef.current = 0
      return true
    } catch (err) {
      changesRef.current = [...entries, ...changesRef.current]
      setDirty(true)

      const errorMsg = (err as Error)?.message || ''
      const isClientError = /^HTTP 4/.test(errorMsg)

      if (isClientError) {
        flushAttemptRef.current = 0
        onFlushErrorRef.current?.(`同步失败：${errorMsg}`)
      } else {
        flushAttemptRef.current++
        const delay = Math.min(1000 * Math.pow(2, flushAttemptRef.current), 30000)
        if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
        retryTimerRef.current = setTimeout(() => flushChangesRef.current?.(), delay)
        onFlushErrorRef.current?.('同步失败，正在重试...')
      }
      return false
    } finally {
      inFlightRef.current = false
      const rerun = pendingFlushRef.current
      pendingFlushRef.current = false
      if (rerun) {
        if (!mountedRef.current) { setSaving(false) }
        else setTimeout(() => { flushChangesRef.current?.() }, 0)
      } else {
        setSaving(false)
      }
    }
  }, [projectId])

  flushChangesRef.current = flushChanges

  // Selection
  const selectNode = useCallback((type: 'act' | 'chapter', id: string) => {
    setSelection({ type, id })
  }, [])

  const selectEdge = useCallback((edgeId: string) => {
    setSelection({ type: 'edge', id: edgeId })
  }, [])

  const clearSelection = useCallback(() => {
    setSelection({ type: null, id: null })
  }, [])

  // Domain hooks
  const {
    addAct, deleteAct: actsDeleteAct, updateAct, resizeAct,
  } = useActs(data, setData, projectId, enqueueChange, clearSelection)

  const {
    addChapter, updateChapter,
    addScene, deleteScene, updateScene,
  } = useChapters(data, setData, projectId, enqueueChange)

  const deleteChapterAction = useCallback((chapterId: string) => {
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
    if (selection.type === 'chapter' && selection.id === chapterId) {
      setSelection({ type: null, id: null })
    }
  }, [data, selection, enqueueChange, setData])

  const {
    addEdge, deleteEdge, changeEdgeType, reconnectEdge,
    updateEdge,
  } = useEdges(data, setData, projectId, enqueueChange)

  const {
    addCharacter, deleteCharacter, updateCharacter,
    addRelation, deleteRelation, updateRelation,
  } = useCharacters(data, setData, projectId, enqueueChange, clearSelection)

  const {
    addTheme, deleteTheme, updateTheme,
    addThemeChapterIndex, removeThemeChapterIndex,
  } = useThemes(data, setData, projectId, enqueueChange)

  // Wrapped deleteAct to also clear selection
  const deleteActAction = useCallback((actId: string) => {
    actsDeleteAct(actId)
  }, [actsDeleteAct])

  // Global Settings
  const saveGlobalSettings = useCallback((text: string) => {
    setData(d => d ? { ...d, globalSettings: text } : d)
    enqueueChange({ entity: 'projects', op: 'update', data: { id: projectId, global_settings: text } })
  }, [projectId, enqueueChange])

  const hasPendingChanges = useCallback(() => {
    return changesRef.current.length > 0
  }, [])

  const setDataDirect = useCallback(
    (updater: EditorMockData | ((prev: EditorMockData | null) => EditorMockData | null)) => {
      setData(updater)
    },
    [],
  )

  return {
    data, loading, error, saving, version, dirty,
    setData: setDataDirect,
    selection, selectNode, selectEdge, clearSelection,
    addAct, addChapter, deleteAct: deleteActAction, deleteChapter: deleteChapterAction,
    addScene, deleteScene, addEdge, deleteEdge, changeEdgeType, reconnectEdge,
    resizeAct,
    addCharacter, deleteCharacter, addRelation, deleteRelation,
    addTheme, deleteTheme,
    updateTheme, addThemeChapterIndex, removeThemeChapterIndex,
    saveGlobalSettings,
    updateAct, updateChapter, updateScene, updateEdge,
    updateCharacter, updateRelation,
    flushChanges, hasPendingChanges,
    enqueueChange,
  }
}
