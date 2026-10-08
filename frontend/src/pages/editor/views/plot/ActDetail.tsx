import type { Act, Chapter } from '../../types'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { orderActChapters } from '../../data/orderUtils'
import { countWords } from '../../data/wordCount'
import { useSessionState } from '../../../../hooks/useSessionState'

interface ActDetailProps {
  act: Act
  chapters: Chapter[]
  contents: Record<string, string>
  onSelectChapter: (chapterId: string) => void
  onUpdateAct: (id: string, updates: Partial<Pick<Act, 'name' | 'purpose' | 'color'>>) => void
  onUpdateChapter: (id: string, updates: Partial<Pick<Chapter, 'title' | 'goal' | 'status' | 'actId'>>) => void
  onAddChapter: (actId: string) => void
  /** 幕内上移 / 下移一章：sort_order 是顺序的唯一真相 */
  onMoveChapter: (chapterId: string, direction: -1 | 1) => void
}

const STATUS_OPTIONS = [
  { value: 'draft' as const, label: '草稿' },
  { value: 'revising' as const, label: '修改' },
  { value: 'final' as const, label: '定稿' },
]

/** 幕详情：幕不是"章的分组标签"，它必须有自己「这一幕要干什么」。 */
export default function ActDetail({
  act, chapters, contents, onSelectChapter, onUpdateAct, onUpdateChapter, onAddChapter, onMoveChapter,
}: ActDetailProps) {
  // session state: the panel remounts when it floats / docks / moves in the dock
  const [expandedId, setExpandedId] = useSessionState<string | null>('act.expandedId', null)

  // 幕内顺序的唯一真相是 sortOrder：按它排一遍，箭头才能用下标判断首尾。
  const orderedChapters = orderActChapters(chapters)
  const totalWords = orderedChapters.reduce((sum, c) => sum + countWords(contents[c.id]), 0)

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-800 space-y-2" style={{ borderLeft: `3px solid ${act.color}` }}>
        <div className="flex items-center justify-between gap-2">
          <input
            value={act.name}
            onChange={e => onUpdateAct(act.id, { name: e.target.value })}
            className="font-medium text-amber-100 bg-transparent border-b border-transparent focus:border-amber-600/50 outline-none flex-1"
          />
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={act.color}
              onChange={e => onUpdateAct(act.id, { color: e.target.value })}
              className="w-16 bg-transparent text-[10px] text-gray-400 border-b border-transparent focus:border-amber-600/50 outline-none font-mono text-center"
            />
            <span className="w-4 h-4 rounded-full border border-gray-700 shrink-0" style={{ backgroundColor: act.color }} />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 text-[10px] text-gray-500">
            <span>{chapters.length} 章</span>
            <span>{totalWords > 0 ? `${totalWords} 字` : '未开始'}</span>
            <span>{chapters.filter(c => c.status === 'final').length}/{chapters.length} 完成</span>
          </div>
          <button
            onClick={() => onAddChapter(act.id)}
            className="text-[10px] px-2 py-1 rounded bg-amber-600/20 text-amber-400 hover:bg-amber-600/30 transition-colors"
          >
            + 添加章节
          </button>
        </div>
      </div>

      {/* 这一幕要干什么 */}
      <div className="shrink-0 border-b border-gray-800 px-4 py-3">
        <div className="text-[10px] text-gray-500 mb-1">🎯 这一幕要干什么</div>
        <textarea
          value={act.purpose}
          onChange={e => onUpdateAct(act.id, { purpose: e.target.value })}
          placeholder="这一幕承担什么转变（第一幕建立与触发、第二幕上半升级、第二幕下半崩塌、第三幕解决）..."
          rows={3}
          className="w-full resize-none rounded-lg border border-gray-700 bg-gray-950 p-2 text-xs leading-relaxed text-gray-300 focus:border-amber-600 focus:outline-none"
        />
      </div>

      {/* Chapter list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {chapters.length === 0 && (
          <div className="text-center py-12">
            <div className="text-gray-600 text-sm mb-3">该幕暂无章节</div>
            <button
              onClick={() => onAddChapter(act.id)}
              className="px-4 py-2 rounded-lg bg-amber-600/20 text-amber-400 text-sm hover:bg-amber-600/30 transition-colors"
            >
              + 创建首个章节
            </button>
          </div>
        )}
        {orderedChapters.map((ch, index) => {
          const isExpanded = expandedId === ch.id
          const words = countWords(contents[ch.id])
          return (
            <div key={ch.id} className="bg-gray-800/40 border border-gray-700/40 rounded-xl overflow-hidden">
              <div className="flex items-center gap-2 pl-3 pr-2 py-2.5 hover:bg-gray-700/30 transition-colors">
                <button
                  onClick={() => setExpandedId(expandedId === ch.id ? null : ch.id)}
                  aria-expanded={isExpanded}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <span className="text-xs text-gray-500 w-4 shrink-0">{isExpanded ? '▾' : '▸'}</span>
                  <span className="text-sm font-medium text-gray-200 flex-1 truncate">{ch.title}</span>
                </button>
                <select
                  value={ch.status}
                  onChange={e => onUpdateChapter(ch.id, { status: e.target.value as Chapter['status'] })}
                  className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] outline-none border border-transparent focus:border-amber-600/50 ${
                    ch.status === 'final' ? 'bg-green-900/30 text-green-400' :
                    ch.status === 'revising' ? 'bg-amber-900/30 text-amber-400' :
                    'bg-gray-800 text-gray-500'
                  }`}
                >
                  {STATUS_OPTIONS.map(opt => (
                    <option key={opt.value} value={opt.value} className="bg-gray-900 text-gray-300">{opt.label}</option>
                  ))}
                </select>
                <span className="shrink-0 text-[10px] text-gray-600 w-10 text-right">{words > 0 ? `${words}w` : '-'}</span>
                {/* 与大纲面板一致：箭头就在这里把该章在本幕内上移 / 下移 */}
                <div className="flex shrink-0 flex-col gap-0.5">
                  <button
                    onClick={(e) => { e.stopPropagation(); onMoveChapter(ch.id, -1) }}
                    disabled={index === 0}
                    title="上移"
                    aria-label={`把「${ch.title}」上移`}
                    className="rounded p-0.5 text-gray-500 transition-colors hover:bg-white/10 hover:text-gray-200 disabled:cursor-default disabled:opacity-25"
                  >
                    <ChevronUp size={12} />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onMoveChapter(ch.id, 1) }}
                    disabled={index === orderedChapters.length - 1}
                    title="下移"
                    aria-label={`把「${ch.title}」下移`}
                    className="rounded p-0.5 text-gray-500 transition-colors hover:bg-white/10 hover:text-gray-200 disabled:cursor-default disabled:opacity-25"
                  >
                    <ChevronDown size={12} />
                  </button>
                </div>
                <button
                  onClick={() => onSelectChapter(ch.id)}
                  className="shrink-0 px-1 text-[10px] text-gray-600 transition-colors hover:text-amber-400"
                  title="聚焦到本章"
                >🔍</button>
              </div>

              {isExpanded && (
                <div className="border-t border-gray-700/30 px-3 pb-3 pt-2">
                  <div className="text-[10px] text-gray-500 mb-1">📝 本章要写什么</div>
                  <textarea
                    value={ch.goal}
                    onChange={e => onUpdateChapter(ch.id, { goal: e.target.value })}
                    placeholder="动笔前的计划..."
                    rows={2}
                    className="w-full resize-none rounded-lg border border-gray-700 bg-gray-950 p-2 text-[11px] leading-relaxed text-gray-300 focus:border-amber-600 focus:outline-none"
                  />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
