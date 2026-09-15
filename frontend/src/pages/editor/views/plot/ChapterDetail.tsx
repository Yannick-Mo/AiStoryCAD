import { useState, useEffect, useRef, useCallback } from 'react'
import type { Act, Chapter } from '../../types'
import { aiContinue, aiInline, type InlineAction } from '../../../../api/editor'
import { countWords } from '../../data/wordCount'
import { useToast } from '../../components/Toast'

interface ChapterDetailProps {
  chapter: Chapter
  acts: Act[]
  projectId: string
  /** 章的正文（章就是正文层；正文是延迟列，由 store 取回来）。 */
  content: string
  /** store 里是否已经有这一章的正文；批量取正文失败时为 false。 */
  contentLoaded: boolean
  /** 回退路径：用 GET /chapters/{id}/content 单独把这一章的正文取回来。 */
  onContentLoad?: (chapterId: string) => Promise<void> | void
  onContentSave: (chapterId: string, content: string) => Promise<void> | void
  onUpdateChapter: (id: string, updates: Partial<Pick<Chapter, 'title' | 'goal' | 'result' | 'status' | 'actId'>>) => void
  onEditGoal?: (chapter: Chapter) => void
  /** 在详情面板里全屏编辑「本章写成了什么」 */
  onEditResult?: (chapter: Chapter) => void
  /** 在详情面板里全屏编辑正文 */
  onEditBody?: (chapter: Chapter) => void
  onOpenAiPanel?: (contextView: string, contextId?: string) => void
}

const STATUS_OPTIONS = [
  { value: 'draft' as const, label: '草稿' },
  { value: 'revising' as const, label: '修改' },
  { value: 'final' as const, label: '定稿' },
]

const INLINE_TOAST: Record<InlineAction, string> = {
  polish: '润色完成，请确认',
  expand: '扩写完成，请确认',
  compress: '压缩完成，请确认',
}

/** 正文太短时不给续写方向：没有上下文，建议只会是噪音。 */
const MIN_CONTINUE_LENGTH = 50

/**
 * 复制到剪贴板。``navigator.clipboard`` 需要安全上下文（https / localhost），
 * 用 IP 走 http 打开时它是 undefined，所以退回 ``execCommand``；两条都失败就返回
 * false，由调用方提示用户手动选择。
 */
async function writeToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* 无权限 / 非安全上下文：走下面的兜底 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

/**
 * 章详情：一章 = 计划（本章要写什么）+ 正文 + 结果（本章写成了什么）+ 状态。
 *
 * 下面没有"场"了——章是最底层，也是正文层。换幕走章自己的 ``act_id``。
 *
 * 正文区的 AI 辅助都是**显式动作**：
 * - 选中一段 → 润色 / 扩写 / 压缩（``POST /chapters/{id}/ai-inline``），结果先给
 *   差异预览，确认后才改进正文；
 * - 「💡 续写建议」按钮 → ``POST /chapters/{id}/ai-continue``。返回的是**方向**
 *   （服务端提示词要求"每个建议用一句话描述，15字以内"），所以点一下只把方向
 *   **复制到剪贴板**，绝不往正文里塞字——正文只能由用户或 AI 改写来改。
 */
export default function ChapterDetail({
  chapter, acts, projectId, content, contentLoaded, onContentLoad, onContentSave,
  onUpdateChapter, onEditGoal, onEditResult, onEditBody, onOpenAiPanel,
}: ChapterDetailProps) {
  const [goal, setGoal] = useState(chapter.goal)
  const [result, setResult] = useState(chapter.result)
  const [draft, setDraft] = useState(content)
  const [savedText, setSavedText] = useState(content)
  const [savingBody, setSavingBody] = useState(false)

  // ── AI 辅助状态 ──────────────────────────────────────────────────
  const [selectionRange, setSelectionRange] = useState<{ start: number; end: number; text: string } | null>(null)
  const [selectionToolbar, setSelectionToolbar] = useState<{ top: number; left: number } | null>(null)
  const [diffState, setDiffState] = useState<{ start: number; end: number; oldText: string; newText: string } | null>(null)
  const [lastReplacement, setLastReplacement] = useState<{ start: number; end: number; oldText: string; newText: string } | null>(null)
  const [continueSuggestions, setContinueSuggestions] = useState<{ id: string; text: string }[]>([])
  const [suggesting, setSuggesting] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [aiLoading, setAiLoading] = useState(false)

  const goalRef = useRef<HTMLTextAreaElement>(null)
  const resultRef = useRef<HTMLTextAreaElement>(null)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const contentRequestedFor = useRef<string | null>(null)
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const draftRef = useRef(draft)
  draftRef.current = draft
  const { addToast } = useToast()

  useEffect(() => () => { if (copyTimerRef.current) clearTimeout(copyTimerRef.current) }, [])

  // 整包正文没取到时（批量请求失败），退回单章端点，每个章只试一次。
  useEffect(() => {
    if (contentLoaded || contentRequestedFor.current === chapter.id) return
    contentRequestedFor.current = chapter.id
    Promise.resolve(onContentLoad?.(chapter.id)).catch(() => {})
  }, [contentLoaded, chapter.id, onContentLoad])

  // 外部（AI 写入 / 重载）改了 goal / result 时跟随，除非用户正在那个框里打字。
  useEffect(() => {
    if (document.activeElement !== goalRef.current) setGoal(chapter.goal)
  }, [chapter.goal])
  useEffect(() => {
    if (document.activeElement !== resultRef.current) setResult(chapter.result)
  }, [chapter.result])

  // 正文是异步取回来的：服务端的值到了、且本地没有未保存的修改时，重置草稿。
  useEffect(() => {
    if (draft === savedText && content !== savedText) {
      setDraft(content)
      setSavedText(content)
    }
  }, [content, draft, savedText])

  const bodyDirty = draft !== savedText
  const canSuggest = !diffState && draft.trim().length >= MIN_CONTINUE_LENGTH

  const handleSaveBody = async () => {
    setSavingBody(true)
    try {
      await onContentSave(chapter.id, draft)
      setSavedText(draft)
      addToast('正文已保存', 'success')
    } catch {
      addToast('保存失败，请重试', 'error')
    } finally {
      setSavingBody(false)
    }
  }

  /**
   * 全屏编辑正文。本地有未保存的草稿时**先保存再切**：全屏编辑器读的是 store 里
   * 已保存的正文，直接切过去就看不见刚才改的内容了。
   */
  const handleOpenFullscreenBody = async () => {
    if (bodyDirty) {
      try {
        await onContentSave(chapter.id, draftRef.current)
        setSavedText(draftRef.current)
      } catch {
        addToast('保存失败，未进入全屏编辑', 'error')
        return
      }
    }
    onEditBody?.(chapter)
  }

  const handleSelect = useCallback(() => {
    const ta = bodyRef.current
    if (!ta) return
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const slice = draftRef.current.substring(start, end)

    if (start !== end && slice.trim()) {
      setSelectionRange({ start, end, text: slice })
      const rect = ta.getBoundingClientRect()
      const lh = getComputedStyle(ta).lineHeight
      const lineHeight = lh === 'normal' ? 20 : parseInt(lh, 10) || 20
      const linesBeforeSelection = draftRef.current.substring(0, start).split('\n').length
      const estimatedTop = rect.top + (linesBeforeSelection - 1) * lineHeight - 40
      setSelectionToolbar({ top: Math.max(rect.top, estimatedTop), left: rect.left + 20 })
    } else {
      setSelectionRange(null)
      setSelectionToolbar(null)
    }
  }, [])

  const handleAiInline = async (action: InlineAction) => {
    if (!selectionRange) return
    setAiLoading(true)
    try {
      const res = await aiInline(projectId, chapter.id, action, selectionRange.text, draftRef.current)
      setDiffState({
        start: selectionRange.start,
        end: selectionRange.end,
        oldText: selectionRange.text,
        newText: res.result,
      })
      addToast(INLINE_TOAST[action], 'success')
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
    const cur = draftRef.current
    const next = cur.substring(0, diffState.start) + diffState.newText + cur.substring(diffState.end)
    setLastReplacement({
      start: diffState.start,
      end: diffState.start + diffState.newText.length,
      oldText: diffState.oldText,
      newText: diffState.newText,
    })
    setDraft(next)
    setDiffState(null)
  }

  const handleUndo = () => {
    if (!lastReplacement) return
    const cur = draftRef.current
    // 偏移可能已失效（应用 AI 修改后用户又编辑过文本）：仅当偏移区域内容仍是
    // 当时的 AI 结果时才按偏移还原，否则回退为按内容查找首次出现位置。
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
    setDraft(cur.substring(0, start) + lastReplacement.oldText + cur.substring(end))
    setLastReplacement(null)
  }

  /** 显式动作：不再"打字停下 2 秒就问一次"，避免每次停顿都打一发 LLM。 */
  const handleRequestSuggestions = async () => {
    if (!canSuggest || suggesting) return
    setSuggesting(true)
    try {
      const res = await aiContinue(projectId, chapter.id, draftRef.current)
      setContinueSuggestions(res.suggestions.map((text, i) => ({ id: String(i), text })))
      if (res.suggestions.length === 0) addToast('这次没有拿到续写方向，再试一次', 'info')
    } catch {
      addToast('续写建议获取失败，请重试', 'error')
    } finally {
      setSuggesting(false)
    }
  }

  /** 方向只复制，不动正文。 */
  const handleCopySuggestion = async (suggestion: { id: string; text: string }) => {
    const ok = await writeToClipboard(suggestion.text)
    if (!ok) {
      addToast('复制失败，请手动选择这段文字', 'error')
      return
    }
    setCopiedId(suggestion.id)
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current)
    copyTimerRef.current = setTimeout(() => {
      setCopiedId(prev => (prev === suggestion.id ? null : prev))
    }, 1600)
  }

  return (
    <div className="h-full flex flex-col">
      {/* 选中文字的浮动工具栏（fixed：正文区是可滚动的） */}
      {selectionToolbar && (
        <div
          className="fixed z-[60] flex items-center gap-1 rounded-lg border border-gray-700 bg-gray-800/95 px-2 py-1.5 shadow-xl backdrop-blur"
          style={{ top: selectionToolbar.top, left: selectionToolbar.left }}
        >
          <button onClick={() => handleAiInline('polish')} disabled={aiLoading}
            className="px-2 py-0.5 rounded text-xs text-gray-300 hover:bg-blue-600/20 hover:text-blue-400 transition-colors disabled:opacity-40"
          >润色</button>
          <div className="w-px h-4 bg-gray-700" />
          <button onClick={() => handleAiInline('expand')} disabled={aiLoading}
            className="px-2 py-0.5 rounded text-xs text-gray-300 hover:bg-green-600/20 hover:text-green-400 transition-colors disabled:opacity-40"
          >扩写</button>
          <div className="w-px h-4 bg-gray-700" />
          <button onClick={() => handleAiInline('compress')} disabled={aiLoading}
            className="px-2 py-0.5 rounded text-xs text-gray-300 hover:bg-orange-600/20 hover:text-orange-400 transition-colors disabled:opacity-40"
          >压缩</button>
        </div>
      )}

      {/* Header */}
      <div className="p-4 border-b border-gray-800 space-y-2">
        <div className="flex items-center gap-2">
          <input
            value={chapter.title}
            onChange={e => onUpdateChapter(chapter.id, { title: e.target.value })}
            className="flex-1 bg-transparent font-medium text-amber-100 outline-none border-b border-transparent focus:border-amber-600/50"
          />
          <select
            value={chapter.status}
            onChange={e => onUpdateChapter(chapter.id, { status: e.target.value as Chapter['status'] })}
            className={`px-1.5 py-0.5 rounded text-[10px] outline-none border border-transparent focus:border-amber-600/50 ${
              chapter.status === 'final' ? 'bg-green-900/30 text-green-400' :
              chapter.status === 'revising' ? 'bg-amber-900/30 text-amber-400' :
              'bg-gray-800 text-gray-400'
            }`}
          >
            {STATUS_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value} className="bg-gray-900 text-gray-300">{opt.label}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-gray-500">
          <label className="flex items-center gap-1">
            所属幕
            <select
              value={chapter.actId}
              onChange={e => onUpdateChapter(chapter.id, { actId: e.target.value })}
              className="bg-transparent text-gray-400 outline-none border-b border-transparent focus:border-amber-600/50 cursor-pointer"
              title="换幕走章自己的 act_id"
            >
              {acts.map(a => (
                <option key={a.id} value={a.id} className="bg-gray-900 text-gray-300">{a.name}</option>
              ))}
            </select>
          </label>
          <span>{countWords(draft)} 字</span>
          <span>{draft ? '已有正文' : '未开始'}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* 计划 */}
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="flex items-center justify-between mb-1.5">
            <button
              onClick={() => onEditGoal?.(chapter)}
              className="text-[10px] text-gray-400 hover:text-amber-300 transition-colors flex items-center gap-1 group cursor-pointer"
              title="在本面板中放大编辑本章要写什么"
            >
              <span>📝 本章要写什么</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity text-amber-400/80 text-[9px]">⛶ 编辑</span>
            </button>
            {goal && (
              <button
                onClick={() => onEditGoal?.(chapter)}
                className="text-[9px] text-gray-600 hover:text-amber-400 transition-colors shrink-0"
                title="在本面板中放大编辑本章要写什么"
              >
                {goal.length} 字 ↗
              </button>
            )}
          </div>
          <textarea
            ref={goalRef}
            value={goal}
            onChange={e => setGoal(e.target.value)}
            onBlur={() => { if (goal !== chapter.goal) onUpdateChapter(chapter.id, { goal }) }}
            placeholder="动笔前的计划：这一章要推进什么、章末跟章初有什么不同、留什么钩子..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>

        {/* 结果 */}
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="flex items-center justify-between mb-1.5">
            <button
              onClick={() => onEditResult?.(chapter)}
              className="text-[10px] text-gray-400 hover:text-amber-300 transition-colors flex items-center gap-1 group cursor-pointer"
              title="在详情页里放大编辑本章写成了什么"
            >
              <span>🏁 本章写成了什么</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity text-amber-400/80 text-[9px]">⛶ 编辑</span>
            </button>
            <button
              onClick={() => onEditResult?.(chapter)}
              className="text-[9px] text-gray-600 hover:text-amber-400 transition-colors shrink-0"
              title="在详情页里放大编辑本章写成了什么"
            >
              下一章的前情 · {result.length} 字 ↗
            </button>
          </div>
          <textarea
            ref={resultRef}
            value={result}
            onChange={e => setResult(e.target.value)}
            onBlur={() => { if (result !== chapter.result) onUpdateChapter(chapter.id, { result }) }}
            placeholder="写完之后的事实：结束时局面变成什么样、谁欠着谁、什么没交代完..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>

        {/* 正文 */}
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <button
              onClick={handleOpenFullscreenBody}
              className="text-[10px] text-gray-400 hover:text-amber-300 transition-colors flex items-center gap-1 group cursor-pointer"
              title="在详情页里全屏编辑正文"
            >
              <span>✍️ 正文</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity text-amber-400/80 text-[9px]">⛶ 全屏</span>
            </button>
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-[9px] text-gray-600">选中一段可润色 / 扩写 / 压缩</span>
              <button
                onClick={handleOpenFullscreenBody}
                className="shrink-0 rounded border border-gray-700 px-1.5 py-0.5 text-[9px] text-gray-400 transition-colors hover:border-amber-600/50 hover:text-amber-400"
                title="在详情页里全屏编辑正文"
              >⛶ 全屏</button>
            </div>
          </div>
          <textarea
            ref={bodyRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onSelect={handleSelect}
            onMouseUp={handleSelect}
            onKeyUp={handleSelect}
            disabled={!!diffState}
            spellCheck={false}
            className="w-full h-56 bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 font-mono leading-relaxed disabled:opacity-50 disabled:cursor-not-allowed"
            placeholder="在这里写这一章的正文..."
          />

          {diffState && (
            <div className="mt-2 space-y-2 rounded-lg border border-amber-600/30 bg-gray-900/80 p-2.5">
              <p className="text-[10px] uppercase tracking-wider text-amber-500/70">AI 修改确认</p>
              <div className="rounded-lg border border-gray-800 bg-gray-950/60 p-2">
                <p className="mb-1 text-[10px] text-gray-500">原文</p>
                <p className="max-h-24 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-gray-500">
                  {draft.substring(0, diffState.start)}
                  <span className="line-through text-red-400/90">{diffState.oldText}</span>
                  {draft.substring(diffState.end)}
                </p>
              </div>
              <div className="rounded-lg border border-amber-700/40 bg-gray-950/60 p-2">
                <p className="mb-1 text-[10px] text-gray-500">AI 结果</p>
                <p className="max-h-24 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-gray-200">
                  {draft.substring(0, diffState.start)}
                  <span className="rounded bg-amber-700/30 px-0.5 text-amber-300">{diffState.newText}</span>
                  {draft.substring(diffState.end)}
                </p>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setDiffState(null)}
                  className="rounded-md bg-gray-800 px-3 py-1 text-xs text-gray-300 transition-colors hover:bg-gray-700"
                >放弃</button>
                <button
                  onClick={handleApplyDiff}
                  className="rounded-md bg-amber-600 px-3 py-1 text-xs font-medium text-black transition-colors hover:bg-amber-500"
                >应用</button>
              </div>
            </div>
          )}

          {/* 续写方向：显式点击才请求；点方向只复制，不改正文 */}
          {!diffState && (
            <div className="mt-2">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={handleRequestSuggestions}
                  disabled={!canSuggest || suggesting}
                  title={canSuggest
                    ? '根据当前正文问 AI 要几个续写方向'
                    : `正文至少 ${MIN_CONTINUE_LENGTH} 字才有足够的上下文`}
                  className={`rounded-md px-2 py-1 text-[11px] transition-colors ${
                    !canSuggest || suggesting
                      ? 'bg-gray-800/40 text-gray-600 cursor-not-allowed'
                      : 'bg-gray-800/80 text-amber-400/90 hover:bg-amber-700/30 hover:text-amber-300'
                  }`}
                >{suggesting ? '⟳ 生成中…' : '💡 续写建议'}</button>
                {continueSuggestions.length > 0 && (
                  <span className="text-[9px] text-gray-600">点击方向复制到剪贴板（不会改动正文）</span>
                )}
              </div>
              {continueSuggestions.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {continueSuggestions.map(s => (
                    <button
                      key={s.id}
                      onClick={() => handleCopySuggestion(s)}
                      title="复制到剪贴板"
                      className="flex items-center gap-1 rounded-md bg-gray-800/60 px-2 py-1 text-left text-[11px] text-gray-300 transition-colors hover:bg-amber-600/20 hover:text-amber-400"
                    >
                      {s.text}
                      {copiedId === s.id && <span className="text-green-400">✓ 已复制</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="mt-2 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2 text-[9px] text-gray-600">
              <span>{aiLoading ? 'AI 处理中…' : (bodyDirty ? '未保存' : '已保存')}</span>
              {lastReplacement && !diffState && (
                <button
                  onClick={handleUndo}
                  className="rounded-md border border-orange-700/40 px-2 py-0.5 text-[10px] text-orange-400 transition-colors hover:bg-orange-900/20"
                >撤销上次 AI 修改</button>
              )}
            </div>
            <button
              onClick={handleSaveBody}
              disabled={!bodyDirty || savingBody}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors shrink-0 ${
                !bodyDirty || savingBody
                  ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                  : 'bg-amber-600 text-black hover:bg-amber-500'
              }`}
            >
              {savingBody ? '保存中...' : '保存正文'}
            </button>
          </div>
        </section>

        {/* AI Assist section */}
        <section className="bg-gray-800/20 border border-gray-700/30 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-2">🤖 AI 辅助</div>
          <button
            onClick={() => onOpenAiPanel?.('chapter', chapter.id)}
            className="w-full text-left px-3 py-2 rounded-lg text-xs text-gray-400 hover:text-amber-300 hover:bg-gray-700/40 transition-colors border border-gray-700/30"
          >
            ✨ 打开 AI 助手
          </button>
        </section>
      </div>
    </div>
  )
}
