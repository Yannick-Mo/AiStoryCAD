import { useState, useEffect, useRef, useCallback } from 'react'
import { useToast } from '../components/Toast'
import type { Chapter } from '../types'

interface ChapterGoalEditorProps {
  chapter: Chapter
  onSave: (goal: string) => Promise<void> | void
  onClose: () => void
}

const STATUS_LABEL: Record<Chapter['status'], string> = {
  draft: '草稿',
  revising: '修改',
  final: '定稿',
}

const STATUS_CLASS: Record<Chapter['status'], string> = {
  draft: 'bg-gray-800 text-gray-400',
  revising: 'bg-amber-900/30 text-amber-400',
  final: 'bg-green-900/30 text-green-400',
}

/**
 * Chapter-goal editor. It fills whatever container hosts it (the detail panel
 * body), so it is not a modal: saving or cancelling just returns the detail
 * panel to the selected chapter.
 */
export default function ChapterGoalEditor({ chapter, onSave, onClose }: ChapterGoalEditorProps) {
  const { addToast } = useToast()
  const [goal, setGoal] = useState(chapter.goal ?? '')
  const [saving, setSaving] = useState(false)
  const savedGoalRef = useRef(chapter.goal ?? '')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const goalRef = useRef(goal)
  goalRef.current = goal

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const handleClose = useCallback(() => {
    if (goalRef.current !== savedGoalRef.current) {
      if (!window.confirm('有未保存的修改，确定关闭吗？')) return
    }
    onCloseRef.current()
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleClose])

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave(goal)
      savedGoalRef.current = goal
      addToast('章节目标已保存', 'success')
      onCloseRef.current()
    } catch {
      addToast('保存失败，请重试', 'error')
      setSaving(false)
    }
  }

  const charCount = goal.length
  const dirty = goal !== savedGoalRef.current

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-[11px] text-gray-500">{chapter.title}</div>
          <h4 className="truncate text-sm font-medium text-gray-100">📝 本章目标</h4>
        </div>
        <span className="shrink-0 rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">{charCount} 字</span>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-800/60 bg-gray-900/50 px-4 py-1.5 text-[11px] text-gray-500">
        <span>{chapter.scenes.length} 场</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_CLASS[chapter.status]}`}>{STATUS_LABEL[chapter.status]}</span>
        <span className="italic text-gray-600">写一段话概括本章要完成什么</span>
      </div>

      {/* writing surface: a centred measure of prose instead of an input box */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-gray-950/40">
        <div className="mx-auto flex min-h-full w-full max-w-[42rem] flex-col px-6 py-6">
          <textarea
            ref={textareaRef}
            value={goal}
            onChange={e => setGoal(e.target.value)}
            spellCheck={false}
            className="w-full flex-1 resize-none border-0 bg-transparent text-[15px] leading-[1.95] tracking-[0.01em] text-gray-100 caret-amber-400 outline-none selection:bg-amber-500/25 placeholder:text-gray-600"
            placeholder="写一段话概括本章要完成什么..."
          />
        </div>
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-gray-800/80 bg-gray-900/70 px-4 py-2">
        <span className="text-[11px] text-gray-600">
          {dirty
            ? <span className="flex items-center gap-1 text-amber-500/80"><span className="h-1.5 w-1.5 rounded-full bg-amber-500/80" />未保存</span>
            : '已保存'}
        </span>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={handleClose}
            className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-1.5 text-xs text-gray-300 transition-colors hover:bg-gray-700"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-lg bg-amber-600 px-4 py-1.5 text-xs font-medium text-black transition-colors hover:bg-amber-500 disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}