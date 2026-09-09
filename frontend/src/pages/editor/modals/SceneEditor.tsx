import { useState, useEffect, useRef, useCallback } from 'react'
import { loadSceneContent, saveSceneContent, aiInline, aiContinue } from '../../../api/editor'
import SceneGoalEditor from './SceneGoalEditor'
import { useToast } from '../components/Toast'
import type { Scene } from '../types'

interface SceneEditorProps {
  projectId: string
  scene: Scene | null
  chapterTitle: string
  onClose: () => void
  onSaved: (sceneId: string, content: string, wordCount: number) => void
  onOpenAiPanel?: (contextView: string, contextId: string) => void
  /** the goal editor takes over this panel in place; the parent persists it */
  onSaveGoal: (summary: string) => Promise<void> | void
}

export default function SceneEditor({ projectId, scene, chapterTitle, onClose, onSaved, onOpenAiPanel, onSaveGoal }: SceneEditorProps) {
  const { addToast } = useToast()
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const [selectionRange, setSelectionRange] = useState<{ start: number; end: number; text: string } | null>(null)
  const [selectionToolbar, setSelectionToolbar] = useState<{ top: number; left: number } | null>(null)
  const [diffState, setDiffState] = useState<{ start: number; end: number; oldText: string; newText: string } | null>(null)
  const [lastReplacement, setLastReplacement] = useState<{ start: number; end: number; oldText: string; newText: string } | null>(null)
  const [continueSuggestions, setContinueSuggestions] = useState<{ id: string; text: string }[]>([])
  const [aiLoading, setAiLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  // the goal editor replaces this whole panel, then hands it back here
  const [goalOpen, setGoalOpen] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const continueTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const contentRef = useRef(content)
  const lastContinueSnapshotRef = useRef('')
  const abortRef = useRef<AbortController | null>(null)
  const selectionRangeRef = useRef(selectionRange)
  const savedContentRef = useRef('')
  const MIN_CONTINUE_LENGTH = 50

  useEffect(() => {
    if (!scene) return
    if (scene.content) {
      setContent(scene.content)
      savedContentRef.current = scene.content
      lastContinueSnapshotRef.current = scene.content
      return
    }
    setLoading(true)
    setLoadError(null)
    loadSceneContent(projectId, scene.id)
      .then(text => { setContent(text); setLoading(false); savedContentRef.current = text; lastContinueSnapshotRef.current = text })
      .catch(() => { setLoadError('加载场景内容失败'); setLoading(false) })
    // keyed by id, not by the object: the parent derives the scene from live
    // project data, and a data refresh must not overwrite in-progress text
  }, [scene?.id, projectId])

  useEffect(() => {
    if (content.length < MIN_CONTINUE_LENGTH || loading || aiLoading || !scene) return

    if (continueTimerRef.current) {
      clearTimeout(continueTimerRef.current)
    }

    if (abortRef.current) abortRef.current.abort()
    abortRef.current = new AbortController()
    const signal = abortRef.current.signal

    continueTimerRef.current = setTimeout(async () => {
      if (signal.aborted) return
      if (contentRef.current === lastContinueSnapshotRef.current) return
      lastContinueSnapshotRef.current = contentRef.current

      try {
        const res = await aiContinue(projectId, scene.id, contentRef.current)
        if (signal.aborted) return
        setContinueSuggestions(
          res.suggestions.map((text, i) => ({ id: String(i), text }))
        )
      } catch (err) {
        if (signal.aborted) return
        console.warn('Continue suggestions failed:', err)
        setContinueSuggestions([])
      }
    }, 2000)

    return () => {
      if (continueTimerRef.current) clearTimeout(continueTimerRef.current)
      if (abortRef.current) abortRef.current.abort()
    }
  }, [content, loading, aiLoading, scene, projectId])

  useEffect(() => { selectionRangeRef.current = selectionRange }, [selectionRange])
  // Sync contentRef with latest content for AI inline operations (Bug 3 fix)
  useEffect(() => { contentRef.current = content }, [content])

  const handleSelect = useCallback(() => {
    const ta = textareaRef.current
    if (!ta) return
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const selectedText = content.substring(start, end).trim()

    if (selectedText && start !== end) {
      setSelectionRange({ start, end, text: selectedText })
      const rect = ta.getBoundingClientRect()
      const lh = getComputedStyle(ta).lineHeight
      const lineHeight = lh === 'normal' ? 20 : parseInt(lh, 10) || 20
      const linesBeforeSelection = content.substring(0, start).split('\n').length
      const estimatedTop = rect.top + (linesBeforeSelection - 1) * lineHeight - 40
      setSelectionToolbar({
        top: Math.max(rect.top, estimatedTop),
        left: rect.left + 20,
      })
    } else {
      setSelectionRange(null)
      setSelectionToolbar(null)
    }
  }, [content])

  async function handleSave() {
    if (!scene) return
    setSaving(true)
    try {
      const result = await saveSceneContent(projectId, scene.id, content)
      savedContentRef.current = content
      onSaved(scene.id, content, result.word_count)
      onClose()
    } catch (e) {
      console.warn('Save failed:', e)
      addToast('保存失败，请重试', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleAiInline = async (action: 'polish' | 'expand' | 'compress') => {
    const range = selectionRangeRef.current
    if (!range || !scene) return
    setAiLoading(true)
    try {
      const res = await aiInline(projectId, scene.id, action, range.text, contentRef.current)
      const newText = res.result
      setDiffState({ start: range.start, end: range.end, oldText: range.text, newText })
      addToast({ polish: '润色完成，请确认', expand: '扩写完成，请确认', compress: '压缩完成，请确认' }[action], 'success')
    } catch {
      addToast('AI 处理失败，请重试', 'error')
    } finally {
      setAiLoading(false)
      setSelectionRange(null)
      setSelectionToolbar(null)
    }
  }

  const handleApplyDiff = () => {
    if (!diffState) return
    const cur = contentRef.current
    const newContent =
      cur.substring(0, diffState.start) +
      diffState.newText +
      cur.substring(diffState.end)
    setLastReplacement({ start: diffState.start, end: diffState.start + diffState.newText.length, oldText: diffState.oldText, newText: diffState.newText })
    setContent(newContent)
    setDiffState(null)
  }

  const handleDiscardDiff = () => {
    setDiffState(null)
  }

  const handleUndo = () => {
    if (!lastReplacement) return
    const cur = contentRef.current
    // 偏移可能已失效（应用 AI 修改后用户又编辑过文本）：
    // 仅当偏移区域内容仍是当时的 AI 结果时才按偏移还原，
    // 否则回退为按内容查找首次出现位置，避免错位拼接损坏内容。
    if (!lastReplacement.newText) {
      addToast('无法撤销：内容为空', 'error')
      return
    }
    let start = lastReplacement.start
    let end = lastReplacement.end
    if (cur.substring(start, end) !== lastReplacement.newText) {
      const idx = cur.indexOf(lastReplacement.newText)
      if (idx === -1) {
        addToast('无法撤销：原文已改变', 'error')
        return
      }
      start = idx
      end = idx + lastReplacement.newText.length
    }
    const restored =
      cur.substring(0, start) +
      lastReplacement.oldText +
      cur.substring(end)
    setContent(restored)
    setLastReplacement(null)
  }

  const handleContinueSelect = (suggestion: { id: string; text: string }) => {
    // AI 修改预览期间禁止追加续写文本，否则会改变文本长度使 diff 偏移失效
    if (diffState) {
      addToast('请先处理 AI 修改，再选择续写建议', 'error')
      return
    }
    setContent(prev => prev + '\n\n' + suggestion.text)
    setContinueSuggestions([])
  }

  const handleClose = useCallback(() => {
    if (content !== savedContentRef.current) {
      if (!window.confirm('有未保存的修改，确定关闭吗？')) return
    }
    onClose()
  }, [content, onClose])

  const handleOpenAiPanel = () => {
    if (scene && onOpenAiPanel) {
      onOpenAiPanel('scene', scene.id)
    }
  }

  if (!scene) return null

  const charCount = content.length
  const paragraphs = content.split(/\n+/).filter(s => s.trim()).length
  const dirty = content !== savedContentRef.current

  if (goalOpen) {
    return <SceneGoalEditor scene={scene} onSave={onSaveGoal} onClose={() => setGoalOpen(false)} />
  }

  return (
    // no modal chrome: this editor fills the detail panel body it is rendered into
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-[11px] text-gray-500">{chapterTitle}</div>
          <h4 className="truncate text-sm font-medium text-gray-100">✎ {scene.title}</h4>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            onClick={() => setGoalOpen(true)}
            className="rounded-lg bg-gray-800/80 px-2 py-1 text-xs text-amber-400/90 transition-colors hover:bg-amber-700/40 hover:text-amber-300"
            title="编辑场景目标 / 创作蓝图"
          >
            🎯 场景目标
          </button>
          <button
            onClick={handleOpenAiPanel}
            className="rounded-lg bg-amber-700/30 px-2 py-1 text-xs text-amber-400 transition-colors hover:bg-amber-700/50"
            title="打开 AI 助手"
          >
            AI
          </button>
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-800/60 bg-gray-900/50 px-4 py-1.5 text-[11px] text-gray-500">
        <span>🎭 {scene.povCharacter || '未设置 POV'}</span>
        <span>📍 {scene.setting || '未设置场景'}</span>
        <span>⏰ {scene.time || '未设置时间'}</span>
        <button
          onClick={() => setGoalOpen(true)}
          className="group flex min-w-0 items-center gap-1.5 italic text-gray-600 transition-colors hover:text-amber-300"
          title="编辑场景目标 / 创作蓝图"
        >
          <span className="truncate">📝 {scene.summary ? (scene.summary.length > 48 ? scene.summary.slice(0, 48) + '…' : scene.summary) : '写场景目标：开场状态 → 冲突 → 目标达成'}</span>
          <span className="shrink-0 text-[9px] text-amber-400/80 opacity-0 transition-opacity group-hover:opacity-100">⛶ 编辑</span>
        </button>
      </div>

        {selectionToolbar && (
          <div
            className="fixed z-[60] flex items-center gap-1 rounded-lg border border-gray-700 bg-gray-800/95 px-2 py-1.5 shadow-xl backdrop-blur"
            style={{ top: selectionToolbar.top, left: selectionToolbar.left }}
          >
            <button onClick={() => handleAiInline('polish')} disabled={aiLoading}
              className="px-2 py-0.5 rounded text-xs text-gray-300 hover:bg-blue-600/20 hover:text-blue-400 transition-colors"
            >润色</button>
            <div className="w-px h-4 bg-gray-700" />
            <button onClick={() => handleAiInline('expand')} disabled={aiLoading}
              className="px-2 py-0.5 rounded text-xs text-gray-300 hover:bg-green-600/20 hover:text-green-400 transition-colors"
            >扩写</button>
            <div className="w-px h-4 bg-gray-700" />
            <button onClick={() => handleAiInline('compress')} disabled={aiLoading}
              className="px-2 py-0.5 rounded text-xs text-gray-300 hover:bg-orange-600/20 hover:text-orange-400 transition-colors"
            >压缩</button>
          </div>
        )}

        {loadError ? (
          <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-red-400">{loadError}</div>
        ) : loading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-gray-500">加载中...</div>
        ) : (
          /* writing surface: a centred measure of prose instead of an input box */
          <div className="min-h-0 flex-1 overflow-y-auto bg-gray-950/40">
            <div className="mx-auto flex min-h-full w-full max-w-[46rem] flex-col px-6 py-6">
              <textarea
                ref={textareaRef}
                value={content}
                onChange={e => setContent(e.target.value)}
                onSelect={handleSelect}
                onMouseUp={handleSelect}
                onKeyUp={handleSelect}
                disabled={!!diffState}
                spellCheck={false}
                className="w-full flex-1 resize-none border-0 bg-transparent text-[15px] leading-[1.95] tracking-[0.01em] text-gray-100 caret-amber-400 outline-none selection:bg-amber-500/25 placeholder:text-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
                placeholder="在这里写小说正文..."
              />
            </div>
          </div>
        )}

        {diffState && (
          <div className="shrink-0 border-t border-amber-600/30 bg-gray-900/95 px-4 py-3">
            <div className="mx-auto w-full max-w-[46rem]">
              <p className="mb-2 text-[10px] uppercase tracking-wider text-amber-500/70">AI 修改确认</p>
              <div className="space-y-2">
                <div className="rounded-lg border border-gray-800 bg-gray-950/60 p-2.5">
                  <p className="mb-1 text-[10px] text-gray-500">原文</p>
                  <p className="max-h-28 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-gray-500">
                    {content.substring(0, diffState.start)}
                    <span className="line-through text-red-400/90">{diffState.oldText}</span>
                    {content.substring(diffState.end)}
                  </p>
                </div>
                <div className="rounded-lg border border-amber-700/40 bg-gray-950/60 p-2.5">
                  <p className="mb-1 text-[10px] text-gray-500">AI 结果</p>
                  <p className="max-h-28 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-gray-200">
                    {content.substring(0, diffState.start)}
                    <span className="rounded bg-amber-700/30 px-0.5 text-amber-300">{diffState.newText}</span>
                    {content.substring(diffState.end)}
                  </p>
                </div>
              </div>
              <div className="mt-2 flex justify-end gap-2">
                <button onClick={handleDiscardDiff}
                  className="rounded-md bg-gray-800 px-3 py-1 text-xs text-gray-300 transition-colors hover:bg-gray-700"
                >放弃</button>
                <button onClick={handleApplyDiff}
                  className="rounded-md bg-amber-600 px-3 py-1 text-xs font-medium text-black transition-colors hover:bg-amber-500"
                >应用</button>
              </div>
            </div>
          </div>
        )}

        {continueSuggestions.length > 0 && !aiLoading && !diffState && (
          <div className="shrink-0 border-t border-gray-800/70 bg-gray-900/95 px-4 py-2">
            <div className="mx-auto w-full max-w-[46rem]">
              <p className="mb-1.5 text-[10px] text-gray-500">续写建议</p>
              <div className="flex gap-1.5">
                {continueSuggestions.map(s => (
                  <button
                    key={s.id}
                    onClick={() => handleContinueSelect(s)}
                    className="flex-1 rounded-md bg-gray-800/60 px-2 py-1.5 text-left text-[11px] text-gray-300 transition-colors hover:bg-amber-600/20 hover:text-amber-400"
                  >
                    {s.text}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* status bar */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-gray-800/80 bg-gray-900/70 px-4 py-2">
          <div className="flex min-w-0 items-center gap-3 text-[11px] text-gray-600">
            <span className="tabular-nums">{charCount} 字</span>
            <span className="tabular-nums">{paragraphs} 段</span>
            {dirty
              ? <span className="flex items-center gap-1 text-amber-500/80"><span className="h-1.5 w-1.5 rounded-full bg-amber-500/80" />未保存</span>
              : <span className="text-gray-700">已保存</span>}
            {lastReplacement && (
              <button onClick={handleUndo}
                className="rounded-md border border-orange-700/40 px-2 py-0.5 text-[11px] text-orange-400 transition-colors hover:bg-orange-900/20"
              >撤销上次 AI 修改</button>
            )}
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={handleClose} className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-1.5 text-xs text-gray-300 transition-colors hover:bg-gray-700">取消</button>
            <button onClick={handleSave} disabled={saving || loading} className="rounded-lg bg-amber-600 px-4 py-1.5 text-xs font-medium text-black transition-colors hover:bg-amber-500 disabled:opacity-50">
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
    </div>
  )
}
