import { useState, useEffect, useRef, useCallback } from 'react'
import { useToast } from '../components/Toast'
import type { Scene } from '../types'

interface SceneGoalEditorProps {
  scene: Scene
  onSave: (summary: string) => Promise<void> | void
  onClose: () => void
}

const PLACEHOLDER = '写本场景要完成什么...\n\n例如：\n【目标】林夏在拍卖会上拿到地图碎片，同时与宿敌首次正面交锋\n【节拍】入场 → 竞价 → 冲突升级 → 夺图逃离\n【关键信息】碎片共 7 片；拍卖会由李家暗中操办\n【结尾状态】林夏负伤但得手，暴露了会古武的事实'

/**
 * Scene-goal / blueprint editor. Like the chapter goal editor it fills the
 * detail panel body instead of opening a modal, so cancelling returns to
 * whatever was underneath (the scene editor, or the chapter selection).
 */
export default function SceneGoalEditor({ scene, onSave, onClose }: SceneGoalEditorProps) {
  const { addToast } = useToast()
  const [summary, setSummary] = useState(scene.summary ?? '')
  const [saving, setSaving] = useState(false)
  const savedSummaryRef = useRef(scene.summary ?? '')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const summaryRef = useRef(summary)
  summaryRef.current = summary

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const handleClose = useCallback(() => {
    if (summaryRef.current !== savedSummaryRef.current) {
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
      await onSave(summary)
      savedSummaryRef.current = summary
      addToast('场景目标已保存', 'success')
      onCloseRef.current()
    } catch {
      addToast('保存失败，请重试', 'error')
      setSaving(false)
    }
  }

  const charCount = summary.length
  const dirty = summary !== savedSummaryRef.current

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-[11px] text-gray-500">{scene.title}</div>
          <h4 className="truncate text-sm font-medium text-gray-100">🎯 场景目标 / 创作蓝图</h4>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">{charCount} 字</span>
          <button onClick={handleClose} title="取消（Esc）" className="px-1 text-gray-400 hover:text-white">✕</button>
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-800/60 bg-gray-900/50 px-4 py-1.5 text-[11px] text-gray-500">
        <span>🎭 {scene.povCharacter || '未设置 POV'}</span>
        <span>📍 {scene.setting || '未设置场景'}</span>
        <span>⏰ {scene.time || '未设置时间'}</span>
        <span className="italic text-gray-600">建议按【目标】【节拍】【关键信息】【结尾状态】组织</span>
      </div>

      {/* writing surface: a centred measure of prose instead of an input box */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-gray-950/40">
        <div className="mx-auto flex min-h-full w-full max-w-[42rem] flex-col px-6 py-6">
          <textarea
            ref={textareaRef}
            value={summary}
            onChange={e => setSummary(e.target.value)}
            spellCheck={false}
            className="w-full flex-1 resize-none border-0 bg-transparent text-[15px] leading-[1.95] tracking-[0.01em] text-gray-100 caret-amber-400 outline-none selection:bg-amber-500/25 placeholder:text-gray-600"
            placeholder={PLACEHOLDER}
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