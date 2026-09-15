import { useState, useEffect, useRef } from 'react'
import type { Act, Chapter, Setup, SetupStatus } from '../../types'
import { chapterNumbers } from '../../data/orderUtils'
import { useToast } from '../../components/Toast'

interface SetupDetailProps {
  setup: Setup
  chapters: Chapter[]
  acts: Act[]
  onUpdate: (id: string, updates: Partial<Pick<Setup, 'note' | 'plantedIn' | 'payOffIn' | 'status'>>) => Promise<void>
  onDelete: (id: string) => Promise<void> | void
}

const STATUS_OPTIONS: { value: SetupStatus; label: string }[] = [
  { value: 'open', label: '未收' },
  { value: 'paid', label: '已收' },
  { value: 'dropped', label: '放弃' },
]

const STATUS_CLASS: Record<SetupStatus, string> = {
  open: 'bg-amber-900/30 text-amber-400',
  paid: 'bg-green-900/30 text-green-400',
  dropped: 'bg-gray-800 text-gray-500',
}

/**
 * 单条伏笔的详情：在情节幕布上点那条虚线，这里显示它埋的是什么、埋在哪一章、
 * 打算在哪一章收。
 *
 * 伏笔在数据层是**一对配对引用**（planted_in → pay_off_in），不是一条边：
 * 幕布上那条线是渲染出来的，所以"选中伏笔"选中的是 ``setups`` 里的一行。
 */
export default function SetupDetail({ setup, chapters, acts, onUpdate, onDelete }: SetupDetailProps) {
  const { addToast } = useToast()
  const numbers = chapterNumbers(chapters, acts)
  const [note, setNote] = useState(setup.note)
  const noteRef = useRef<HTMLTextAreaElement>(null)

  // 外部（AI 写入 / 重载）改了这条伏笔时跟随，除非用户正在这个框里打字。
  useEffect(() => {
    if (document.activeElement !== noteRef.current) setNote(setup.note)
  }, [setup.note])

  const chapterLabel = (id: string | null) => {
    if (!id) return '未定'
    const n = numbers.get(id)
    const ch = chapters.find(c => c.id === id)
    if (!ch) return '（已删除）'
    return `第 ${n ?? '?'} 章 · ${ch.title}`
  }

  const handleDelete = async () => {
    if (!window.confirm('删除这条伏笔？')) return
    try {
      await onDelete(setup.id)
    } catch {
      addToast('删除伏笔失败，请重试', 'error')
    }
  }

  const saveNote = () => {
    if (note === setup.note) return
    onUpdate(setup.id, { note })?.catch?.(() => addToast('保存失败，请重试', 'error'))
  }

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-medium text-gray-100">🪢 伏笔</h4>
          <div className="mt-0.5 truncate text-[11px] text-gray-500">
            {chapterLabel(setup.plantedIn)} → {chapterLabel(setup.payOffIn)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_CLASS[setup.status]}`}>
            {STATUS_OPTIONS.find(o => o.value === setup.status)?.label ?? setup.status}
          </span>
          <button
            onClick={handleDelete}
            title="删除这条伏笔"
            className="text-[10px] text-gray-600 transition-colors hover:text-red-400"
          >✕</button>
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-gray-800/60 bg-gray-900/50 px-4 py-2 text-[11px] text-gray-500">
        <label className="flex items-center gap-1">
          埋在哪
          <select
            value={setup.plantedIn}
            onChange={e => onUpdate(setup.id, { plantedIn: e.target.value })}
            className="max-w-[13rem] cursor-pointer truncate rounded border border-gray-700 bg-gray-950 px-1.5 py-0.5 text-gray-300 outline-none focus:border-amber-600"
          >
            {chapters.map(ch => (
              <option key={ch.id} value={ch.id}>{chapterLabel(ch.id)}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          打算在哪收
          <select
            value={setup.payOffIn ?? ''}
            onChange={e => onUpdate(setup.id, { payOffIn: e.target.value || null })}
            className="max-w-[13rem] cursor-pointer truncate rounded border border-gray-700 bg-gray-950 px-1.5 py-0.5 text-gray-300 outline-none focus:border-amber-600"
          >
            <option value="">未定</option>
            {chapters.map(ch => (
              <option key={ch.id} value={ch.id}>{chapterLabel(ch.id)}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          状态
          <select
            value={setup.status}
            onChange={e => onUpdate(setup.id, { status: e.target.value as SetupStatus })}
            className={`cursor-pointer rounded px-1.5 py-0.5 outline-none border border-transparent focus:border-amber-600/50 ${STATUS_CLASS[setup.status]}`}
          >
            {STATUS_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value} className="bg-gray-900 text-gray-300">{opt.label}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <section className="rounded-xl border border-gray-700/50 bg-gray-800/40 p-3">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[10px] text-gray-400">这条伏笔埋的是什么</span>
            <span className="text-[9px] text-gray-600">{note !== setup.note ? '未保存' : '已保存'}</span>
          </div>
          <textarea
            ref={noteRef}
            value={note}
            onChange={e => setNote(e.target.value)}
            onBlur={saveNote}
            rows={7}
            placeholder="埋的是什么东西 / 什么事，读者当时看到的是什么..."
            className="w-full resize-none rounded-lg border border-gray-700 bg-gray-950 p-2 text-xs leading-relaxed text-gray-300 focus:border-amber-600 focus:outline-none"
          />
        </section>
        {!setup.payOffIn && (
          <p className="mt-2 px-1 text-[10px] leading-relaxed text-gray-600">
            还没定在哪收——幕布上不会画线（线要有两端）。
          </p>
        )}
      </div>
    </div>
  )
}
