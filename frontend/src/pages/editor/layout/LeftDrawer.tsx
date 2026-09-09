import { useEffect, useRef, useState } from 'react'
import type { Chapter, Act } from '../types'

interface LeftDrawerProps {
  open: boolean
  acts: Act[]
  chapters: Chapter[]
  selectedActId?: string | null
  selectedChapterId?: string | null
  onClose: () => void
  onSelectAct: (id: string) => void
  onSelectChapter: (id: string) => void
}

export default function LeftDrawer({
  open,
  acts,
  chapters,
  selectedActId,
  selectedChapterId,
  onClose,
  onSelectAct,
  onSelectChapter,
}: LeftDrawerProps) {
  const selectedActRef = useRef<HTMLDivElement | null>(null)
  const selectedChapterRef = useRef<HTMLDivElement | null>(null)
  const [collapsedActs, setCollapsedActs] = useState<Set<string>>(() => new Set<string>())

  const toggleAct = (id: string) => {
    setCollapsedActs(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Esc 收起大纲
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 选中的章若在折叠的幕里，自动展开该幕
  useEffect(() => {
    if (!open || !selectedChapterId) return
    const ch = chapters.find(c => c.id === selectedChapterId)
    if (!ch) return
    setCollapsedActs(prev => {
      if (!prev.has(ch.actId)) return prev
      const next = new Set(prev)
      next.delete(ch.actId)
      return next
    })
  }, [open, selectedChapterId, chapters])

  // 展开时把当前选中的幕 / 章滚动到可视区域
  useEffect(() => {
    if (!open) return
    const el = selectedChapterRef.current ?? selectedActRef.current
    if (el) el.scrollIntoView({ block: 'center' })
  }, [open, selectedActId, selectedChapterId, collapsedActs])

  return (
    <div
      className={`fixed left-0 top-0 h-full w-64 bg-gray-900/95 backdrop-blur-xl border-r border-gray-800 z-30 transition-transform duration-200 shadow-2xl flex flex-col ${
        open ? 'translate-x-0' : '-translate-x-full pointer-events-none'
      }`}
    >
      <div className="flex items-center justify-between gap-2 pl-4 pr-2 py-3 border-b border-gray-800">
        <h3 className="text-amber-600/80 text-xs uppercase tracking-wider truncate">📋 全章节大纲</h3>
        <button
          onClick={onClose}
          title="收起大纲（Esc）"
          aria-label="收起大纲"
          className="shrink-0 flex items-center gap-1 pl-1 pr-2 py-1 rounded-lg text-[11px] text-gray-400 hover:text-gray-100 bg-gray-800/60 hover:bg-gray-700 border border-gray-700/60 transition-colors"
        >
          <svg
            width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
          收起
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {[...acts].sort((a, b) => a.order - b.order).map(act => {
          const actChs = chapters.filter(c => c.actId === act.id)
          const isActSelected = act.id === selectedActId
          const collapsed = collapsedActs.has(act.id)
          const canCollapse = actChs.length > 0
          const actWordCount = actChs.reduce((sum, c) => sum + c.wordCount, 0)
          return (
            <div key={act.id}>
              <div
                ref={isActSelected ? selectedActRef : null}
                className={`flex items-center gap-1 pl-1 pr-2 py-1.5 rounded-lg transition-colors ${
                  isActSelected ? 'bg-green-500/15' : 'hover:bg-gray-800/60'
                }`}
                style={isActSelected ? { boxShadow: 'inset 2px 0 0 0 #22c55e' } : undefined}
              >
                <button
                  onClick={() => toggleAct(act.id)}
                  disabled={!canCollapse}
                  aria-expanded={!collapsed}
                  aria-label={collapsed ? `展开「${act.name}」` : `折叠「${act.name}」`}
                  title={canCollapse ? (collapsed ? '展开该幕' : '折叠该幕') : '该幕暂无章节'}
                  className={`shrink-0 p-0.5 rounded transition-colors ${
                    canCollapse ? 'text-gray-500 hover:text-gray-200 hover:bg-white/10' : 'text-gray-700 cursor-default'
                  }`}
                >
                  <svg
                    width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    className={`transition-transform duration-150 ${collapsed ? '-rotate-90' : ''}`}
                    aria-hidden="true"
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <button
                  onClick={() => onSelectAct(act.id)}
                  aria-pressed={isActSelected}
                  title={`选中「${act.name}」`}
                  className="flex-1 min-w-0 flex items-center gap-2 text-left"
                >
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: act.color }} />
                  <span className={`flex-1 min-w-0 truncate text-xs font-medium ${isActSelected ? 'text-green-100 font-semibold' : 'text-gray-400'}`}>
                    {act.name}
                  </span>
                </button>
                <span className="shrink-0 text-[10px] text-gray-500 tabular-nums">
                  {actChs.length} 章{actWordCount > 0 ? ` · ${actWordCount} 字` : ''}
                </span>
              </div>
              {!collapsed && (
                <div className="ml-4 mt-0.5 space-y-0.5">
                  {actChs.map(ch => {
                    const isChapterSelected = ch.id === selectedChapterId
                    return (
                      <div
                        key={ch.id}
                        ref={isChapterSelected ? selectedChapterRef : null}
                        onClick={() => onSelectChapter(ch.id)}
                        className={`px-3 py-2 rounded-lg border-l-2 cursor-pointer transition-colors ${
                          isChapterSelected
                            ? 'bg-amber-500/15 ring-1 ring-amber-500/40'
                            : isActSelected
                            ? 'bg-green-500/10 hover:bg-green-500/20'
                            : 'bg-gray-800/30 hover:bg-gray-700/50'
                        }`}
                        style={{ borderLeftColor: isActSelected ? '#22c55e' : act.color }}
                      >
                        <div className={`text-sm truncate ${
                          isChapterSelected ? 'text-amber-100 font-medium' : isActSelected ? 'text-green-100' : 'text-gray-200'
                        }`}>
                          {ch.title}
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-gray-500 mt-0.5">
                          <span>{ch.scenes.length} 场</span>
                          <span>{ch.wordCount > 0 ? `${ch.wordCount} 字` : '空'}</span>
                          <span className={`px-1 rounded ${
                            ch.status === 'final' ? 'bg-green-900/30 text-green-500' :
                            ch.status === 'revising' ? 'bg-amber-900/30 text-amber-500' :
                            'bg-gray-800 text-gray-500'
                          }`}>
                            {ch.status === 'draft' ? '草稿' : ch.status === 'revising' ? '修改' : '定稿'}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="p-3 border-t border-gray-800 text-gray-600 text-[10px] text-center">
        点击幕或章节跳转 · 点「收起」或按 Esc 关闭
      </div>
    </div>
  )
}