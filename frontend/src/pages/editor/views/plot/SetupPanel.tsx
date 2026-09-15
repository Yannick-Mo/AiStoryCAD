import { useState } from 'react'
import type { Act, Chapter, Setup, SetupStatus } from '../../types'
import { chapterNumbers } from '../../data/orderUtils'
import { useToast } from '../../components/Toast'

interface SetupPanelProps {
  setups: Setup[]
  chapters: Chapter[]
  acts: Act[]
  onCreate: (plantedIn: string, note: string) => Promise<unknown>
  onUpdate: (id: string, updates: Partial<Pick<Setup, 'note' | 'plantedIn' | 'payOffIn' | 'status'>>) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

const STATUS_OPTIONS: { value: SetupStatus; label: string }[] = [
  { value: 'open', label: '未收' },
  { value: 'paid', label: '已收' },
  { value: 'dropped', label: '放弃' },
]

/**
 * 伏笔编辑：伏笔是一对配对引用（埋在哪一章、打算在哪一章收），不是一条线，
 * 所以它在这里是一个清单，画布上只是一条渲染出来的线。
 *
 * 走通用 ``setups`` CRUD 端点（POST / PUT / DELETE），不走增量同步。
 */
export default function SetupPanel({ setups, chapters, acts, onCreate, onUpdate, onDelete }: SetupPanelProps) {
  const [busy, setBusy] = useState(false)
  const { addToast } = useToast()
  const numbers = chapterNumbers(chapters, acts)
  const chapterLabel = (id: string | null) => {
    if (!id) return '未定'
    const n = numbers.get(id)
    const ch = chapters.find(c => c.id === id)
    if (!ch) return '（已删除）'
    return `第 ${n ?? '?'} 章 · ${ch.title}`
  }

  const handleCreate = async () => {
    const first = chapters.length > 0 ? chapters[0].id : null
    if (!first) {
      addToast('先建一章，伏笔要埋在某一章里', 'warning')
      return
    }
    setBusy(true)
    try {
      await onCreate(first, '')
    } catch {
      addToast('新建伏笔失败，请重试', 'error')
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await onDelete(id)
    } catch {
      addToast('删除伏笔失败，请重试', 'error')
    }
  }

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-medium text-gray-100">🪢 伏笔</h4>
          <div className="mt-0.5 truncate text-[11px] text-gray-500">有什么还没回收、埋在哪、打算在哪收</div>
        </div>
        <button
          onClick={handleCreate}
          disabled={busy || chapters.length === 0}
          className={`shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
            busy || chapters.length === 0
              ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
              : 'bg-amber-600 text-black hover:bg-amber-500'
          }`}
        >
          ＋ 新建伏笔
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {setups.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-700/50 bg-gray-800/20 px-4 py-10 text-center text-xs text-gray-600">
            {chapters.length === 0
              ? '还没有章：伏笔必须埋在某一章里'
              : '还没有伏笔。埋下一个伏笔之后，情节幕布上会从埋设章画一条线到收束章'}
          </div>
        ) : (
          setups.map(setup => (
            <div key={setup.id} className="space-y-2 rounded-xl border border-gray-700/40 bg-gray-800/40 p-3">
              <div className="flex items-start gap-2">
                <textarea
                  value={setup.note}
                  onChange={e => onUpdate(setup.id, { note: e.target.value })}
                  placeholder="这个伏笔埋的是什么..."
                  rows={2}
                  className="flex-1 resize-none rounded-lg border border-gray-700 bg-gray-950 p-2 text-xs leading-relaxed text-gray-300 focus:border-amber-600 focus:outline-none"
                />
                <button
                  onClick={() => handleDelete(setup.id)}
                  title="删除伏笔"
                  className="shrink-0 px-1 text-[10px] text-gray-600 transition-colors hover:text-red-400"
                >✕</button>
              </div>

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[10px] text-gray-500">
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
                <select
                  value={setup.status}
                  onChange={e => onUpdate(setup.id, { status: e.target.value as SetupStatus })}
                  className={`cursor-pointer rounded px-1.5 py-0.5 outline-none border border-transparent focus:border-amber-600/50 ${
                    setup.status === 'paid' ? 'bg-green-900/30 text-green-400' :
                    setup.status === 'dropped' ? 'bg-gray-800 text-gray-500' :
                    'bg-amber-900/30 text-amber-400'
                  }`}
                >
                  {STATUS_OPTIONS.map(opt => (
                    <option key={opt.value} value={opt.value} className="bg-gray-900 text-gray-300">{opt.label}</option>
                  ))}
                </select>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
