import { useState, useEffect, useRef, useCallback } from 'react'
import { useToast } from '../components/Toast'
import type { Chapter } from '../types'

interface ChapterGoalModalProps {
  chapter: Chapter
  onSave: (goal: string) => Promise<void> | void
  onClose: () => void
}

export default function ChapterGoalModal({ chapter, onSave, onClose }: ChapterGoalModalProps) {
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={handleClose}>
      <div
        className="bg-gray-900 border border-amber-700/50 rounded-2xl shadow-2xl w-[800px] max-w-[90vw] h-[85vh] flex flex-col p-6 overflow-hidden backdrop-blur-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex justify-between items-start mb-3">
          <div className="flex-1 min-w-0 mr-4">
            <div className="text-xs text-gray-500 mb-0.5">{chapter.title}</div>
            <h4 className="text-amber-600 font-medium">📝 本章目标</h4>
          </div>
          <div className="flex gap-2 items-center shrink-0">
            <span className="text-[10px] text-gray-600 px-2 py-1 rounded-lg bg-gray-800/60">{charCount} 字</span>
            <button onClick={handleClose} className="text-gray-400 hover:text-white text-lg">✕</button>
          </div>
        </div>

        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 mb-3 pb-3 border-b border-gray-800">
          <span>{chapter.scenes.length} 场</span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] ${
            chapter.status === 'final' ? 'bg-green-900/30 text-green-400' :
            chapter.status === 'revising' ? 'bg-amber-900/30 text-amber-400' :
            'bg-gray-800 text-gray-400'
          }`}>
            {chapter.status === 'final' ? '定稿' : chapter.status === 'revising' ? '修改' : '草稿'}
          </span>
          <span className="italic text-gray-600">写一段话概括本章要完成什么</span>
        </div>

        <div className="flex-1 min-h-0 relative">
          <textarea
            ref={textareaRef}
            value={goal}
            onChange={e => setGoal(e.target.value)}
            className="w-full h-full bg-gray-950 border border-gray-700 rounded-xl p-6 text-base text-gray-200 leading-relaxed resize-none focus:outline-none focus:border-amber-600"
            placeholder="写一段话概括本章要完成什么..."
          />
        </div>

        <div className="flex gap-2 mt-3 justify-end">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 rounded-lg bg-amber-600 text-sm font-medium text-black hover:bg-amber-500 transition-colors disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存'}
          </button>
          <button
            onClick={handleClose}
            className="px-5 py-2 rounded-lg bg-gray-800 text-sm text-gray-300 border border-gray-700 hover:bg-gray-700 transition-colors"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  )
}
