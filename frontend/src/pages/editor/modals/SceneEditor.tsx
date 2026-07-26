import { useState, useEffect, useRef, useCallback } from 'react'
import { loadSceneContent, saveSceneContent, aiInline, aiContinue } from '../../../api/editor'
import { useToast } from '../components/Toast'
import type { Scene } from '../types'

interface SceneEditorProps {
  projectId: string
  scene: Scene | null
  chapterTitle: string
  onClose: () => void
  onSaved: (sceneId: string, content: string, wordCount: number) => void
  onOpenAiPanel?: (contextView: string, contextId: string) => void
}

export default function SceneEditor({ projectId, scene, chapterTitle, onClose, onSaved, onOpenAiPanel }: SceneEditorProps) {
  const { addToast } = useToast()
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const [selectionRange, setSelectionRange] = useState<{ start: number; end: number; text: string } | null>(null)
  const [selectionToolbar, setSelectionToolbar] = useState<{ top: number; left: number } | null>(null)
  const [diffState, setDiffState] = useState<{ start: number; end: number; oldText: string; newText: string } | null>(null)
  const [lastReplacement, setLastReplacement] = useState<{ start: number; end: number; oldText: string } | null>(null)
  const [continueSuggestions, setContinueSuggestions] = useState<{ id: string; text: string }[]>([])
  const [aiLoading, setAiLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const continueTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const contentRef = useRef(content)
  const abortRef = useRef<AbortController | null>(null)
  const selectionRangeRef = useRef(selectionRange)
  const savedContentRef = useRef('')
  const MIN_CONTINUE_LENGTH = 50

  useEffect(() => {
    if (!scene) return
    if (scene.content) {
      setContent(scene.content)
      savedContentRef.current = scene.content
      return
    }
    setLoading(true)
    setLoadError(null)
    loadSceneContent(projectId, scene.id)
      .then(text => { setContent(text); setLoading(false); savedContentRef.current = text })
      .catch(() => { setLoadError('加载场景内容失败'); setLoading(false) })
  }, [scene, projectId])

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
      if (content === contentRef.current) return
      contentRef.current = content

      try {
        const res = await aiContinue(projectId, scene.id, content)
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
    setLastReplacement({ start: diffState.start, end: diffState.start + diffState.newText.length, oldText: cur.substring(diffState.start, diffState.end) })
    setContent(newContent)
    setDiffState(null)
  }

  const handleDiscardDiff = () => {
    setDiffState(null)
  }

  const handleUndo = () => {
    if (!lastReplacement) return
    const cur = contentRef.current
    const restored =
      cur.substring(0, lastReplacement.start) +
      lastReplacement.oldText +
      cur.substring(lastReplacement.end)
    setContent(restored)
    setLastReplacement(null)
  }

  const handleContinueSelect = (suggestion: { id: string; text: string }) => {
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={handleClose}>
      <div className="bg-gray-900 border border-amber-700/50 rounded-2xl shadow-2xl w-[800px] max-w-[90vw] h-[85vh] flex flex-col p-6 overflow-hidden backdrop-blur-xl" onClick={e => e.stopPropagation()}>
        <div className="flex justify-between items-start mb-3">
          <div>
            <div className="text-xs text-gray-500 mb-0.5">{chapterTitle}</div>
            <h4 className="text-amber-600 font-medium">✎ {scene.title}</h4>
          </div>
          <div className="flex gap-2 items-center">
            <button
              onClick={handleOpenAiPanel}
              className="px-2 py-1 rounded-lg text-xs bg-amber-700/30 text-amber-400 hover:bg-amber-700/50 transition-colors"
              title="打开 AI 助手"
            >
              AI
            </button>
            <button onClick={handleClose} className="text-gray-400 hover:text-white text-lg">✕</button>
          </div>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 mb-3 pb-3 border-b border-gray-800">
          <span>🎭 {scene.povCharacter}</span>
          <span>📍 {scene.setting}</span>
          <span>⏰ {scene.time}</span>
          <span className="italic text-gray-600">—— {scene.summary}</span>
        </div>

        {selectionToolbar && (
          <div
            className="fixed z-[60] flex items-center gap-1 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 shadow-xl"
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
          <div className="flex-1 min-h-0 flex items-center justify-center text-red-400 text-sm">{loadError}</div>
        ) : loading ? (
          <div className="flex-1 min-h-0 flex items-center justify-center text-gray-500 text-sm">加载中...</div>
        ) : (
          <div className="flex-1 min-h-0 relative">
            <textarea
              ref={textareaRef}
              value={content}
              onChange={e => setContent(e.target.value)}
              onSelect={handleSelect}
              onMouseUp={handleSelect}
              onKeyUp={handleSelect}
              className="w-full h-full bg-gray-950 border border-gray-700 rounded-xl p-6 text-base text-gray-200 font-mono leading-relaxed resize-none focus:outline-none focus:border-amber-600"
              placeholder="在这里写小说正文..."
            />
          </div>
        )}

        {diffState && (
          <div className="mt-2 bg-gray-800/95 border border-amber-600/40 rounded-lg p-3 shadow-xl backdrop-blur-sm">
            <p className="text-[10px] text-gray-500 mb-2 px-1">AI 修改确认</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-gray-900/80 rounded-md p-2.5 border border-gray-700">
                <p className="text-[10px] text-gray-500 mb-1">原文</p>
                <p className="text-xs text-gray-400 leading-relaxed">
                  {content.substring(0, diffState.start)}
                  <span className="line-through text-red-400">{diffState.oldText}</span>
                  {content.substring(diffState.end)}
                </p>
              </div>
              <div className="bg-gray-900/80 rounded-md p-2.5 border border-amber-700/40">
                <p className="text-[10px] text-gray-500 mb-1">AI 结果</p>
                <p className="text-xs text-gray-200 leading-relaxed">
                  {content.substring(0, diffState.start)}
                  <span className="bg-amber-700/30 text-amber-300 rounded px-0.5">{diffState.newText}</span>
                  {content.substring(diffState.end)}
                </p>
              </div>
            </div>
            <div className="flex gap-2 mt-2 justify-end">
              <button onClick={handleApplyDiff}
                className="px-3 py-1 rounded text-xs bg-amber-600 text-black font-medium hover:bg-amber-500 transition-colors"
              >应用</button>
              <button onClick={handleDiscardDiff}
                className="px-3 py-1 rounded text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
              >放弃</button>
            </div>
          </div>
        )}

        {continueSuggestions.length > 0 && !aiLoading && (
          <div className="bg-gray-800/95 border border-gray-700 rounded-lg p-2 shadow-xl backdrop-blur-sm mt-2">
            <p className="text-[10px] text-gray-500 mb-1.5 px-1">续写建议：</p>
            <div className="flex gap-1.5">
              {continueSuggestions.map(s => (
                <button
                  key={s.id}
                  onClick={() => handleContinueSelect(s)}
                  className="flex-1 text-[11px] text-left px-2 py-1.5 rounded-md bg-gray-700/50 text-gray-300 hover:bg-amber-600/20 hover:text-amber-400 transition-colors"
                >
                  {s.text}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex gap-2 mt-3 justify-end">
          {lastReplacement && (
            <button onClick={handleUndo}
              className="px-3 py-2 rounded-lg bg-gray-800 text-xs text-orange-400 border border-orange-700/40 hover:bg-orange-900/20 transition-colors mr-auto"
            >撤销上次 AI 修改</button>
          )}
          <button onClick={handleSave} disabled={saving || loading} className="px-5 py-2 rounded-lg bg-amber-600 text-sm font-medium text-black hover:bg-amber-500 transition-colors disabled:opacity-50">
            {saving ? '保存中...' : '保存'}
          </button>
          <button onClick={handleClose} className="px-5 py-2 rounded-lg bg-gray-800 text-sm text-gray-300 border border-gray-700 hover:bg-gray-700 transition-colors">取消</button>
        </div>
      </div>
    </div>
  )
}
