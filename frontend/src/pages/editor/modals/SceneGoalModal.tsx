import { useState, useEffect, useRef, useCallback } from 'react'
import { useToast } from '../components/Toast'
import type { Scene } from '../types'

interface SceneGoalModalProps {
  scene: Scene
  onSave: (summary: string) => Promise<void> | void
  onClose: () => void
}

export default function SceneGoalModal({ scene, onSave, onClose }: SceneGoalModalProps) {
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={handleClose}>
      <div
        className="bg-gray-900 border border-amber-700/50 rounded-2xl shadow-2xl w-[800px] max-w-[90vw] h-[85vh] flex flex-col p-6 overflow-hidden backdrop-blur-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex justify-between items-start mb-3">
          <div className="flex-1 min-w-0 mr-4">
            <div className="text-xs text-gray-500 mb-0.5">{scene.title}</div>
            <h4 className="text-amber-600 font-medium">🎯 场景目标 / 创作蓝图</h4>
          </div>
          <div className="flex gap-2 items-center shrink-0">
            <span className="text-[10px] text-gray-600 px-2 py-1 rounded-lg bg-gray-800/60">{charCount} 字</span>
            <button onClick={handleClose} className="text-gray-400 hover:text-white text-lg">✕</button>
          </div>
        </div>

        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 mb-3 pb-3 border-b border-gray-800">
          <span>🎭 {scene.povCharacter || '未设置 POV'}</span>
          <span>📍 {scene.setting || '未设置场景'}</span>
          <span>⏰ {scene.time || '未设置时间'}</span>
          <span className="italic text-gray-600">建议按【目标】【节拍】【关键信息】【结尾状态】组织</span>
        </div>

        <div className="flex-1 min-h-0 relative">
          <textarea
            ref={textareaRef}
            value={summary}
            onChange={e => setSummary(e.target.value)}
            className="w-full h-full bg-gray-950 border border-gray-700 rounded-xl p-6 text-base text-gray-200 leading-relaxed resize-none focus:outline-none focus:border-amber-600"
            placeholder={'写本场景要完成什么...\n\n例如：\n【目标】林夏在拍卖会上拿到地图碎片，同时与宿敌首次正面交锋\n【节拍】入场 → 竞价 → 冲突升级 → 夺图逃离\n【关键信息】碎片共 7 片；拍卖会由李家暗中操办\n【结尾状态】林夏负伤但得手，暴露了会古武的事实'}
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
