import { useState, useCallback, useRef, useEffect } from 'react'
import type { EditorMockData, SelectionState, Setup } from '../types'
import {
  loadEditorData, syncEditorData, loadAllChapterContents,
  createSetup as apiCreateSetup, updateSetup as apiUpdateSetup, deleteSetup as apiDeleteSetup,
  type SyncPayload,
} from '../../../api/editor'
import { useActs } from './hooks/useActs'
import { useChapters } from './hooks/useChapters'
import { useCharacters } from './hooks/useCharacters'

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
  const [selection, setSelection] = useState<SelectionState>({ type: null, id: null })
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const changesRef = useRef<ChangeEntry[]>([])
  const timerRef = useRef<ReturnType<typeof setTimeout>>()
  const retryTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const periodTimerRef = useRef<ReturnType<typeof setInterval>>()
  const flushAttemptRef = useRef(0)
  const inFlightRef = useRef(false)
  const inFlightPromiseRef = useRef<Promise<boolean> | null>(null)
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
      .then(async d => {
        if (cancelled || !mountedRef.current) return
        setData(d)
        setLoading(false)
        // chapters.content 是延迟列，editor-data 不返回正文：单独取一次。
        // best-effort —— 取不到时预览 / 导出为空，但不阻塞编辑。
        try {
          const contents = await loadAllChapterContents(projectId)
          if (!cancelled && mountedRef.current) {
            setData(prev => prev ? { ...prev, chapterContents: contents } : prev)
          }
        } catch { /* 正文拉取失败时保持为空 */ }
      })
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
      flushOnUnmount()
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
  const runFlushRef = useRef<() => Promise<boolean>>()

  const runFlush = useCallback(async (): Promise<boolean> => {
    if (changesRef.current.length === 0) return true
    if (inFlightRef.current) return false
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
      // 服务端在同一次调用里把 sort_order 归一化成连续 1..N；不再回传 version
      // 或 timeline_edges（顺序是唯一真相，线是渲染）。
      await syncEditorData(projectId, payload)
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
      } else if (mountedRef.current) {
        flushAttemptRef.current++
        const delay = Math.min(1000 * Math.pow(2, flushAttemptRef.current), 30000)
        if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
        retryTimerRef.current = setTimeout(() => flushChangesRef.current?.(), delay)
        onFlushErrorRef.current?.('同步失败，正在重试...')
      }
      return false
    } finally {
      inFlightRef.current = false
    }
  }, [projectId])

  runFlushRef.current = runFlush

  const flushOnUnmount = useCallback(() => {
    const inflight = inFlightPromiseRef.current
    const fire = () => { runFlushRef.current?.() }
    if (inflight) {
      inflight.finally(fire)
    } else {
      fire()
    }
  }, [])

  const flushChanges = useCallback(async (): Promise<boolean> => {
    if (!mountedRef.current) return true
    if (projectIdRef.current !== projectId) return true
    if (changesRef.current.length === 0) return true
    if (inFlightRef.current) {
      pendingFlushRef.current = true
      return false
    }
    const promise = runFlush()
    inFlightPromiseRef.current = promise
    try {
      return await promise
    } finally {
      inFlightPromiseRef.current = null
      const rerun = pendingFlushRef.current
      pendingFlushRef.current = false
      if (rerun) {
        if (!mountedRef.current) { setSaving(false) }
        else setTimeout(() => { flushChangesRef.current?.() }, 0)
      } else {
        setSaving(false)
      }
    }
  }, [projectId, runFlush])

  flushChangesRef.current = flushChanges

  // Selection
  const selectNode = useCallback((type: 'act' | 'chapter', id: string) => {
    setSelection({ type, id })
  }, [])

  const clearSelection = useCallback(() => {
    setSelection({ type: null, id: null })
  }, [])

  // Domain hooks
  const {
    addAct, deleteAct: actsDeleteAct, updateAct, resizeAct,
  } = useActs(data, setData, projectId, enqueueChange, clearSelection)

  const {
    addChapter, deleteChapter, updateChapter, moveChapter, moveChapterToAct,
  } = useChapters(data, setData, projectId, enqueueChange)

  const {
    addCharacter, deleteCharacter, updateCharacter,
    addRelation, deleteRelation, updateRelation,
  } = useCharacters(data, setData, projectId, enqueueChange, clearSelection)

  const flushSoon = useCallback(() => { void flushChangesRef.current?.() }, [])

  const addChapterAction = useCallback((actId: string) => {
    const created = addChapter(actId)
    flushSoon()
    return created
  }, [addChapter, flushSoon])

  const moveChapterAction = useCallback((chapterId: string, direction: -1 | 1) => {
    moveChapter(chapterId, direction)
    flushSoon()
  }, [moveChapter, flushSoon])

  const moveChapterToActAction = useCallback((chapterId: string, actId: string) => {
    moveChapterToAct(chapterId, actId)
    flushSoon()
  }, [moveChapterToAct, flushSoon])

  const deleteChapterAction = useCallback((chapterId: string) => {
    if (!data) return
    deleteChapter(chapterId)
    if (selection.type === 'chapter' && selection.id === chapterId) {
      setSelection({ type: null, id: null })
    }
    flushSoon()
  }, [data, deleteChapter, selection, flushSoon])

  const deleteActAction = useCallback((actId: string) => {
    actsDeleteAct(actId)
    flushSoon()
  }, [actsDeleteAct, flushSoon])

  // Chapter prose（正文就是 chapters.content；保存走 PUT 专用端点而不是增量同步）
  const setChapterContent = useCallback((chapterId: string, text: string) => {
    setData(d => d ? { ...d, chapterContents: { ...d.chapterContents, [chapterId]: text } } : d)
  }, [])

  // ── Setups（伏笔）：走通用 CRUD 端点 ────────────────────────────────
  const addSetup = useCallback(async (plantedIn: string, note = '') => {
    const id = crypto.randomUUID()
    const newSetup: Setup = { id, note, plantedIn, payOffIn: null, status: 'open' }
    setData(d => d ? { ...d, setups: [...d.setups, newSetup] } : d)
    try {
      await apiCreateSetup(projectId, {
        id, note, planted_in: plantedIn, pay_off_in: null, status: 'open',
      })
    } catch (e) {
      setData(d => d ? { ...d, setups: d.setups.filter(s => s.id !== id) } : d)
      throw e
    }
    return newSetup
  }, [projectId])

  const updateSetupById = useCallback(async (
    id: string,
    updates: Partial<Pick<Setup, 'note' | 'plantedIn' | 'payOffIn' | 'status'>>,
  ) => {
    const payload: Record<string, unknown> = {}
    if (updates.note !== undefined) payload.note = updates.note
    if (updates.plantedIn !== undefined) payload.planted_in = updates.plantedIn
    if (updates.payOffIn !== undefined) payload.pay_off_in = updates.payOffIn
    if (updates.status !== undefined) payload.status = updates.status
    setData(d => d ? { ...d, setups: d.setups.map(s => s.id === id ? { ...s, ...updates } : s) } : d)
    if (Object.keys(payload).length === 0) return
    await apiUpdateSetup(projectId, id, payload)
  }, [projectId])

  const deleteSetupById = useCallback(async (id: string) => {
    setData(d => d ? { ...d, setups: d.setups.filter(s => s.id !== id) } : d)
    await apiDeleteSetup(projectId, id)
  }, [projectId])

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
    data, loading, error, saving, dirty,
    setData: setDataDirect,
    selection, selectNode, clearSelection,
    addAct, addChapter: addChapterAction, deleteAct: deleteActAction, deleteChapter: deleteChapterAction,
    moveChapter: moveChapterAction, moveChapterToAct: moveChapterToActAction,
    resizeAct,
    addCharacter, deleteCharacter, addRelation, deleteRelation,
    updateAct, updateChapter,
    updateCharacter, updateRelation,
    addSetup, updateSetup: updateSetupById, deleteSetup: deleteSetupById,
    setChapterContent,
    flushChanges, hasPendingChanges,
    enqueueChange,
  }
}
